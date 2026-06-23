#!/usr/bin/env bash
#
# egress-lockdown.sh — Capa 2 anti-SSRF para OpenGrab (network-level).
#
# Bloquea el egress del contenedor OpenGrab hacia rangos privados, reservados,
# link-local y el endpoint de metadata cloud (169.254.169.254), insertando
# reglas en la chain DOCKER-USER del HOST. Complementa la capa 1 (el gate de
# download.py que resuelve DNS y bloquea destinos privados): la capa 1 cierra
# el caso normal, esta capa cierra el TOCTOU/DNS-rebinding residual a nivel de
# red.
#
# Por qué en el host y no en el contenedor:
#   - No requiere CAP_NET_ADMIN en el contenedor ni rompe el modelo USER
#     opengrab (no-root).
#   - DOCKER-USER es la chain que Docker reserva para reglas del operador y
#     evalúa ANTES de sus propias reglas; no se la pisa el daemon en runtime.
#   - Funciona igual con Docker rootless mientras iptables corra en el host.
#
# Diseño:
#   - Reglas SCOPEADAS por subnet de origen (la red del contenedor), para NO
#     afectar el egress de otros contenedores del homelab.
#   - Se EXIME la propia subnet (RETURN) para no romper el acceso al gateway
#     (egress NAT) ni a peers de la misma red.
#   - NO se bloquea 127.0.0.0/8: el DNS embebido de Docker vive en 127.0.0.11
#     dentro del netns del contenedor y no atraviesa DOCKER-USER. El loopback
#     como destino SSRF ya lo cubre la capa 1.
#   - Idempotente: -C (check) antes de -I (insert).
#
# Uso:
#   sudo ./scripts/egress-lockdown.sh [--apply|--remove|--list] [--dry-run]
#                                     [--container NAME] [--log]
#
#   --apply       (default) inserta las reglas DROP.
#   --remove      borra las reglas que este script haya puesto.
#   --list        muestra la chain DOCKER-USER (v4 y v6) y sale.
#   --dry-run     imprime los comandos iptables sin ejecutarlos.
#   --container   nombre del contenedor (default: opengrab, o $OPENGRAB_CONTAINER).
#   --log         además de DROP, loguea los intentos (kern.log, rate-limit).
#
set -euo pipefail

CONTAINER="${OPENGRAB_CONTAINER:-opengrab}"
ACTION="apply"
DRY=0
LOG=0
CHAIN="DOCKER-USER"
TAG="OPENGRAB-SSRF"  # comentario para identificar/limpiar nuestras reglas

# Rangos a bloquear. (Consumidos vía nameref en apply_family/remove_family.)
# shellcheck disable=SC2034
V4_RANGES=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16)
# IPv6: ULA, link-local, loopback. (is_private en la capa 1 ya los cubre; esto
# es defensa en profundidad. Alternativa más simple: deshabilitar IPv6 en el
# contenedor — ver docker-compose.yml.)
# shellcheck disable=SC2034
V6_RANGES=(fc00::/7 fe80::/10 ::1/128)

# --------------------------------------------------------------------------- #
# Args
# --------------------------------------------------------------------------- #
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)     ACTION="apply" ;;
        --remove)    ACTION="remove" ;;
        --list)      ACTION="list" ;;
        --dry-run)   DRY=1 ;;
        --log)       LOG=1 ;;
        --container) CONTAINER="${2:?--container requiere un nombre}"; shift ;;
        -h|--help)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opción desconocida: $1 (usá --help)" >&2; exit 2 ;;
    esac
    shift
done

die() { echo "error: $*" >&2; exit 1; }

run() {
    if [[ $DRY -eq 1 ]]; then
        printf '+ %s\n' "$*"
    else
        "$@"
    fi
}

# --------------------------------------------------------------------------- #
# Pre-flight
# --------------------------------------------------------------------------- #
[[ $EUID -eq 0 || $DRY -eq 1 ]] || die "se necesita root (sudo). Para previsualizar: --dry-run"
command -v iptables >/dev/null || die "iptables no encontrado en el host"
HAS_IP6=0
command -v ip6tables >/dev/null && HAS_IP6=1

if [[ "$ACTION" == "list" ]]; then
    echo "=== iptables -S $CHAIN (IPv4) ==="
    iptables -S "$CHAIN" 2>/dev/null || echo "(la chain $CHAIN no existe; ¿Docker está corriendo?)"
    if [[ $HAS_IP6 -eq 1 ]]; then
        echo "=== ip6tables -S $CHAIN (IPv6) ==="
        ip6tables -S "$CHAIN" 2>/dev/null || echo "(sin $CHAIN en ip6tables; IPv6 deshabilitado en Docker)"
    fi
    exit 0
fi

command -v docker >/dev/null || die "docker no encontrado en el host"

# Derivamos las subnets reales (no la IP del contenedor) desde la config IPAM
# de cada red a la que está conectado el contenedor.
mapfile -t NET_IDS < <(docker inspect "$CONTAINER" \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.NetworkID}}{{"\n"}}{{end}}' 2>/dev/null) || true

SUBNETS4=()
SUBNETS6=()
for nid in "${NET_IDS[@]}"; do
    [[ -n "$nid" ]] || continue
    while read -r cidr; do
        [[ -z "$cidr" ]] && continue
        if [[ "$cidr" == *:* ]]; then SUBNETS6+=("$cidr"); else SUBNETS4+=("$cidr"); fi
    done < <(docker network inspect "$nid" \
        --format '{{range .IPAM.Config}}{{.Subnet}}{{"\n"}}{{end}}' 2>/dev/null)
done

[[ ${#SUBNETS4[@]} -gt 0 ]] || die "no pude detectar la subnet IPv4 del contenedor '$CONTAINER' (¿está levantado?)"

echo "Contenedor:      $CONTAINER"
echo "Subnets IPv4:    ${SUBNETS4[*]}"
[[ ${#SUBNETS6[@]} -gt 0 ]] && echo "Subnets IPv6:    ${SUBNETS6[*]}"
echo "Acción:          $ACTION$([[ $DRY -eq 1 ]] && echo ' (dry-run)')"
echo

# --------------------------------------------------------------------------- #
# Helpers de reglas (idempotentes vía -C). Insertamos con -I para quedar ARRIBA
# del RETURN por defecto que Docker pone en DOCKER-USER (si usáramos -A, nuestras
# reglas quedarían después del RETURN y nunca se evaluarían).
# --------------------------------------------------------------------------- #
ensure() {   # ensure <iptables-bin> <args...>
    local ipt="$1"; shift
    if "$ipt" -C "$CHAIN" "$@" 2>/dev/null; then
        echo "  ya existe: $ipt -C $CHAIN $*"
    else
        run "$ipt" -I "$CHAIN" "$@"
    fi
}

remove() {   # remove <iptables-bin> <args...>
    local ipt="$1"; shift
    if [[ $DRY -eq 1 ]]; then
        # En dry-run no podemos borrar de verdad; el while -C no progresaría
        # (loop infinito). Mostramos el -D una sola vez si la regla existe.
        "$ipt" -C "$CHAIN" "$@" 2>/dev/null && printf '+ %s -D %s %s\n' "$ipt" "$CHAIN" "$*"
        return 0
    fi
    while "$ipt" -C "$CHAIN" "$@" 2>/dev/null; do
        "$ipt" -D "$CHAIN" "$@"
    done
}

apply_family() {  # apply_family <bin> <subnet-array-name> <range-array-name>
    local ipt="$1"; local -n subnets="$2"; local -n ranges="$3"
    for src in "${subnets[@]}"; do
        # OJO: -I inserta siempre en posición 1, así que el orden final de la
        # tabla es el INVERSO al de inserción. Para que el orden efectivo quede
        #   RETURN(propia) → LOG(*) → DROP(*)
        # insertamos en orden inverso: DROP primero, LOG después, RETURN al final.
        for dst in "${ranges[@]}"; do
            ensure "$ipt" -s "$src" -d "$dst" -j DROP -m comment --comment "$TAG"
        done
        if [[ $LOG -eq 1 ]]; then
            for dst in "${ranges[@]}"; do
                ensure "$ipt" -s "$src" -d "$dst" -m limit --limit 5/min \
                    -j LOG --log-prefix "[$TAG] " -m comment --comment "$TAG"
            done
        fi
        # La exención de la propia subnet va AL FINAL → queda ARRIBA de todo
        # (egress NAT vía gateway + peers de la misma red).
        ensure "$ipt" -s "$src" -d "$src" -j RETURN -m comment --comment "$TAG"
    done
}

remove_family() {  # remove_family <bin> <subnet-array-name> <range-array-name>
    local ipt="$1"; local -n subnets="$2"; local -n ranges="$3"
    for src in "${subnets[@]}"; do
        remove "$ipt" -s "$src" -d "$src" -j RETURN -m comment --comment "$TAG"
        for dst in "${ranges[@]}"; do
            remove "$ipt" -s "$src" -d "$dst" -j DROP -m comment --comment "$TAG"
            remove "$ipt" -s "$src" -d "$dst" -m limit --limit 5/min \
                -j LOG --log-prefix "[$TAG] " -m comment --comment "$TAG"
        done
    done
}

# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #
if [[ "$ACTION" == "apply" ]]; then
    apply_family iptables SUBNETS4 V4_RANGES
    if [[ $HAS_IP6 -eq 1 && ${#SUBNETS6[@]} -gt 0 ]]; then
        if ip6tables -S "$CHAIN" >/dev/null 2>&1; then
            apply_family ip6tables SUBNETS6 V6_RANGES
        else
            echo "  aviso: sin chain $CHAIN en ip6tables (IPv6 off en Docker). " \
                 "Capa 1 igual bloquea IPv6; o deshabilitá IPv6 en el contenedor."
        fi
    fi
    echo
    echo "Listo. Verificá con: $0 --list"
elif [[ "$ACTION" == "remove" ]]; then
    remove_family iptables SUBNETS4 V4_RANGES
    if [[ $HAS_IP6 -eq 1 && ${#SUBNETS6[@]} -gt 0 ]] && ip6tables -S "$CHAIN" >/dev/null 2>&1; then
        remove_family ip6tables SUBNETS6 V6_RANGES
    fi
    echo
    echo "Reglas de OpenGrab removidas de $CHAIN."
fi
