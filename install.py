#!/usr/bin/env python3
"""
OpenGrab Installer — Interactive CLI setup wizard.

Usage:
    python install.py

Guides you through installing OpenGrab on your machine.
Two modes: Recommended (2 prompts, defaults) or Advanced (step by step).
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

W = 62
ENV_FILE = Path(".env")

# ─── Helpers visuales ────────────────────────────────────────────────────────


def _top(title: str = "") -> None:
    print(f"+{'─' * (W - 2)}+")
    if title:
        print(f"│{title.center(W - 2)}│")


def _bot() -> None:
    print(f"+{'─' * (W - 2)}+")


def _line(text: str = "") -> None:
    print(f"│ {text.ljust(W - 4)} │")


def _blank() -> None:
    _line()


def _ok(msg: str) -> None:
    print(f"  [ \u2713  ]  {msg}")


def _err(msg: str) -> None:
    print(f"  [ \u2717  ]  {msg}")


def _warn(msg: str) -> None:
    print(f"  [  !  ]  {msg}")


def _info(msg: str) -> None:
    print(f"  [  i  ]  {msg}")


def _div() -> None:
    print(f"  {'─' * (W - 6)}")


def _banner() -> None:
    _top("OPENGRAB INSTALLER   v1.1.0")
    _line("Self-hosted YouTube downloader para tu LAN")
    _bot()
    print()


def _success(url: str, token: str, host: str, port: int) -> None:
    print()
    _top()
    _ok("OpenGrab esta listo")
    _blank()
    _line(f"URL:       http://{host}:{port}")
    if token:
        _line(f"Token:     {token}")
    _blank()
    _line(f"Abri {url} en tu navegador.")
    if token:
        _line("Guarda el token — lo vas a necesitar para entrar.")
    _bot()


# ─── Input helpers ───────────────────────────────────────────────────────────


def _prompt_choice(label: str, options: list[str], default: int = 1) -> int:
    print(f"  {label}:")
    print()
    for i, opt in enumerate(options, 1):
        tag = f" [{i}]"
        print(f"  {tag:<4} {opt}")
    print()
    while True:
        raw = input(f"  Elegi una opcion [{default}]: ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if 1 <= val <= len(options):
                return val
            print(f"  [!]  Ingresa un numero entre 1 y {len(options)}")
        except ValueError:
            print(f"  [!]  Ingresa un numero valido")


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    yn = "S/n" if default else "s/N"
    while True:
        raw = input(f"  {question} [{yn}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("s", "si", "y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print(f"  [!]  Responde si o no")


def _prompt_text(
    label: str, default: str = "", validator=None, allow_empty: bool = False
) -> str:
    while True:
        hint = f" [{default}]" if default else ""
        raw = input(f"  {label}{hint}: ").strip()
        if not raw:
            if default:
                return default
            if allow_empty:
                return ""
            print("  [!]  Este campo no puede quedar vacio")
            continue
        if validator:
            err = validator(raw)
            if err:
                print(f"  [!]  {err}")
                continue
        return raw


def _prompt_port(default: int = 8800) -> int:
    while True:
        raw = input(f"  Puerto HTTP [{default}]: ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if 1 <= val <= 65535:
                return val
            print("  [!]  El puerto debe estar entre 1 y 65535")
        except ValueError:
            print("  [!]  Ingresa un numero valido")


def _prompt_int(label: str, default: int, lo: int, hi: int) -> int:
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if lo <= val <= hi:
                return val
            print(f"  [!]  Debe estar entre {lo} y {hi}")
        except ValueError:
            print("  [!]  Ingresa un numero valido")


# ─── Prerequisites ───────────────────────────────────────────────────────────


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _check_docker_daemon() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except Exception:
        return False


def _check_prerequisites() -> dict:
    ok_docker = _has("docker")
    ok_daemon = _check_docker_daemon() if ok_docker else False
    ok_compose = _has("docker-compose") or _docker_compose_v2()
    ok_python = _has("python3") or _has("python") or sys.version_info >= (3, 11)
    ok_ffmpeg = _has("ffmpeg")
    ok_git = _has("git")

    print()
    _top("VERIFICANDO REQUISITOS")
    _blank()

    if ok_docker and ok_daemon:
        _ok("Docker instalado y corriendo")
    elif ok_docker:
        _warn("Docker instalado pero el daemon no esta corriendo")
    else:
        _err("Docker no encontrado")

    if ok_compose:
        _ok("Docker Compose disponible")
    else:
        _err("Docker Compose no encontrado")

    if ok_python:
        _ok("Python 3.11+ disponible")
    else:
        _err("Python 3.11+ no encontrado")

    if ok_ffmpeg:
        _ok("ffmpeg disponible")
    else:
        _warn("ffmpeg no encontrado (necesario solo para bare metal)")

    if ok_git:
        _ok("Git disponible")
    else:
        _warn("Git no encontrado (no bloquea, pero necesitas tener el repo)")

    _bot()
    print()
    return {
        "docker": ok_docker and ok_daemon,
        "compose": ok_compose,
        "python": ok_python,
        "ffmpeg": ok_ffmpeg,
        "git": ok_git,
    }


def _docker_compose_v2() -> bool:
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except Exception:
        return False


def _get_compose_cmd() -> list[str] | None:
    if _docker_compose_v2():
        return ["docker", "compose"]
    if _has("docker-compose"):
        return ["docker-compose"]
    return None


# ─── Token ───────────────────────────────────────────────────────────────────


def _generate_token() -> str:
    return secrets.token_urlsafe(24)


# ─── Config ──────────────────────────────────────────────────────────────────


def _detect_lan_ip() -> str:
    try:
        r = subprocess.run(
            ["docker", "info", "--format", "{{ .Name }}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "localhost"
    except Exception:
        return "localhost"


def _write_env(config: dict) -> None:
    if ENV_FILE.exists():
        print()
        if not _prompt_yes_no(".env ya existe. Sobreescribir?", default=False):
            print("  [i]  Se mantiene el .env existente.")
            print()
            return
    mapping = {
        "host": "OPENGRAB_HOST",
        "port": "OPENGRAB_PORT",
        "out_dir": "OPENGRAB_DIR",
        "token": "OPENGRAB_TOKEN",
        "max_jobs": "OPENGRAB_MAX_JOBS",
        "max_size_mb": "OPENGRAB_MAX_SIZE_MB",
        "auto_update": "OPENGRAB_AUTOUPDATE",
    }
    lines = []
    for key, env_key in mapping.items():
        val = config.get(key)
        if val is None or val == "":
            val = ""
        lines.append(f"{env_key}={val}")
    lines.append("")
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")
    _ok(".env generado")
    print()


# ─── Docker / Bare metal start ───────────────────────────────────────────────


def _start_docker(config: dict) -> None:
    compose = _get_compose_cmd()
    if not compose:
        _err("Docker Compose no disponible. Instala Docker Desktop.")
        sys.exit(1)

    project_dir = Path(__file__).parent
    print()
    _info("Construyendo imagen Docker...")
    print()
    try:
        subprocess.run(
            compose + ["build"],
            cwd=project_dir,
            check=True,
        )
    except subprocess.CalledProcessError:
        _err("Fallo la construccion de la imagen.")
        _info("Revisa los mensajes de arriba y volve a intentar.")
        sys.exit(1)

    print()
    _info("Iniciando contenedor...")
    print()
    try:
        subprocess.run(
            compose + ["up", "-d", "--remove-orphans"],
            cwd=project_dir,
            check=True,
        )
    except subprocess.CalledProcessError:
        _err("Fallo al iniciar el contenedor.")
        sys.exit(1)


def _start_baremetal(config: dict) -> None:
    project_dir = Path(__file__).parent
    req = project_dir / "requirements.txt"
    print()
    _info("Instalando dependencias Python...")
    print()
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req)],
            cwd=project_dir,
            check=True,
        )
    except subprocess.CalledProcessError:
        _err("Fallo la instalacion de dependencias.")
        sys.exit(1)

    print()
    _info("Iniciando OpenGrab...")
    print()
    _info("Ejecuta manualmente: python app.py")
    _info(f"Desde el directorio: {project_dir}")


# ─── Display helpers ─────────────────────────────────────────────────────────


def _show_summary(config: dict) -> None:
    mode = "Docker" if config.get("use_docker", True) else "Bare metal (Python)"
    host_label = config.get("host", "127.0.0.1")
    host_desc = "solo local" if host_label == "127.0.0.1" else "accesible desde LAN"
    token = config.get("token", "")
    auto = "si" if config.get("auto_update", 1) in ("1", 1, True) else "no"
    size = config.get("max_size_mb", "0")
    size_desc = "sin limite" if size in ("0", 0) else f"{size} MB"

    print()
    _top("RESUMEN DE CONFIGURACION")
    _blank()
    _line(f"  Despliegue:        {mode}")
    _line(f"  Host:              {host_label} ({host_desc})")
    _line(f"  Puerto:            {config.get('port', 8800)}")
    _line(f"  Descargas:         {config.get('out_dir', './downloads')}")
    _line(f"  Token:             {token if token else '(sin proteccion)'}")
    _line(f"  Jobs simultaneos:  {config.get('max_jobs', 2)}")
    _line(f"  Tamano maximo:     {size_desc}")
    _line(f"  Auto-update:       {auto}")
    _bot()
    print()


# ─── Modes ───────────────────────────────────────────────────────────────────


def _recommended_mode(reqs: dict) -> dict:
    print()
    _top("CONFIGURACION RAPIDA")
    _blank()
    _info("Vas a configurar solo lo esencial. Todo lo demas usa defaults.")
    _blank()
    _line(f"  Host:              0.0.0.0 (accesible desde LAN)")
    _line(f"  Directorio:        ./downloads")
    _line(f"  Jobs simultaneos:  2")
    _line(f"  Tamano maximo:     sin limite")
    _line(f"  Auto-update:       si")
    _bot()
    print()

    port = _prompt_port(8800)
    print()
    print("  Token de acceso:")
    token_choice = _prompt_choice(
        "",
        [
            "Generar token aleatorio (recomendado)",
            "Sin token (acceso libre)",
        ],
        default=1,
    )
    token = _generate_token() if token_choice == 1 else ""
    if token:
        print()
        print(f"  Token generado: {token}")
    print()

    config = {
        "host": "0.0.0.0",
        "port": port,
        "out_dir": "./downloads",
        "token": token,
        "max_jobs": 2,
        "max_size_mb": 0,
        "auto_update": 1,
        "use_docker": True,
    }
    return config


def _advanced_mode(reqs: dict) -> dict:
    print()
    _top("CONFIGURACION AVANZADA")
    _bot()
    print()

    step = 0
    total = 8

    # Step 1: Deployment type
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Tipo de despliegue")
    print()
    dep = _prompt_choice(
        "",
        [
            "Docker (recomendado)",
            "Bare metal (Python 3.11+ + ffmpeg)",
        ],
        default=1,
    )
    use_docker = dep == 1
    if use_docker and not reqs["docker"]:
        _warn("Docker no esta disponible. Se usara bare metal como fallback.")
        use_docker = False
    if not use_docker and not reqs["python"]:
        _err("Python 3.11+ no encontrado. No se puede continuar.")
        _info("Instala Python desde https://python.org y volve a ejecutar.")
        sys.exit(1)
    print()

    # Step 2: Host
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Direccion de escucha")
    print()
    host_choice = _prompt_choice(
        "",
        [
            "localhost (127.0.0.1) — solo tu maquina",
            "Todas las interfaces (0.0.0.0) — accesible desde LAN",
        ],
        default=1,
    )
    host = "127.0.0.1" if host_choice == 1 else "0.0.0.0"
    print()

    # Step 3: Port
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Puerto HTTP")
    print()
    port = _prompt_port(8800)
    print()

    # Step 4: Download dir
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Directorio de descargas")
    print()

    def _validate_dir(raw: str) -> str | None:
        try:
            p = Path(raw)
            if p.is_absolute() or not raw.startswith(("..", "~")):
                return None
        except Exception:
            pass
        return None

    out_dir = _prompt_text("Directorio", default="./downloads")
    if not out_dir:
        out_dir = "./downloads"
    print()

    # Step 5: Token
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Proteccion con token")
    print()
    tok_choice = _prompt_choice(
        "",
        [
            "Generar token aleatorio (recomendado)",
            "Escribir mi propio token",
            "Sin token (acceso libre)",
        ],
        default=1,
    )
    if tok_choice == 1:
        token = _generate_token()
        print()
        print(f"  Token generado: {token}")
    elif tok_choice == 2:

        def _validate_token(raw: str) -> str | None:
            if len(raw) < 8:
                return "El token debe tener al menos 8 caracteres"
            return None

        token = _prompt_text(
            "Token (min 8 caracteres)", default="", validator=_validate_token
        )
    else:
        token = ""
    print()

    # Step 6: Max jobs
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Maximo de descargas simultaneas")
    print()
    max_jobs = _prompt_int("Cantidad", 2, 1, 99)
    print()

    # Step 7: Max size
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Tamano maximo por archivo (MB)")
    print()
    _info("0 = sin limite. yt-dlp rechaza formatos que excedan este tamano.")
    print()
    max_size = _prompt_int("Tamano maximo MB", 0, 0, 999999)
    print()

    # Step 8: Auto update
    step += 1
    _div()
    print(f"  Paso {step}/{total} — Auto-actualizar yt-dlp al iniciar")
    print()
    _info("YouTube cambia seguido. Actualizar yt-dlp evita que la app se rompa.")
    print()
    auto_update = 1 if _prompt_yes_no("Auto-actualizar yt-dlp?", default=True) else 0
    print()

    config = {
        "host": host,
        "port": port,
        "out_dir": out_dir,
        "token": token,
        "max_jobs": max_jobs,
        "max_size_mb": max_size,
        "auto_update": auto_update,
        "use_docker": use_docker,
    }
    return config


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    _banner()

    reqs = _check_prerequisites()

    mode = _prompt_choice(
        "Modo de instalacion",
        [
            "Recomendado — Solo token y puerto, todo lo demas por defecto",
            "Avanzado — Configurar paso a paso",
        ],
        default=1,
    )

    if mode == 1:
        config = _recommended_mode(reqs)
    else:
        config = _advanced_mode(reqs)

    _show_summary(config)

    if not _prompt_yes_no("Proceder con la instalacion?", default=True):
        _warn("Instalacion cancelada.")
        _info(f"Edita {ENV_FILE} manualmente y ejecuta 'python app.py' o 'docker compose up -d'.")
        return

    _write_env(config)

    if not _prompt_yes_no("Iniciar OpenGrab ahora?", default=True):
        print()
        _info("Para iniciar manualmente:")
        if config.get("use_docker", True):
            _info("  docker compose up -d")
        else:
            _info("  pip install -r requirements.txt")
            _info("  python app.py")
        print()
        return

    if config.get("use_docker", True):
        _start_docker(config)
    else:
        _start_baremetal(config)

    host = config.get("host", "127.0.0.1")
    if host == "0.0.0.0":
        host = "localhost"
    port = config.get("port", 8800)
    url = f"http://{host}:{port}"
    if host == "localhost":
        url_alt = f"http://<tu-ip-local>:{port}"
        url = f"{url}  (o {url_alt})"
    token = config.get("token", "")
    _success(url, token, host, port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print()
        _info("Instalacion cancelada por el usuario.")
        sys.exit(0)
    except EOFError:
        print()
        print()
        _info("Entrada no disponible. Ejecuta en una terminal interactiva.")
        sys.exit(1)
