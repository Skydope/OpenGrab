# Observability — OpenGrab

Guía para ingestar logs y métricas de OpenGrab en Grafana + Loki + Alloy/Prometheus.

## Logs JSON

OpenGrab soporta salida JSON estructurada vía `OPENGRAB_LOG_FORMAT=json`.
Cada línea es un objeto JSON independiente (formato NDJSON).

### Campos base (garantizados en todo log)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `ts` | string | ISO 8601 UTC con ms (`2026-07-01T12:00:00.123Z`) |
| `level` | string | `INFO`, `WARNING`, `ERROR`, `DEBUG` |
| `logger` | string | Nombre del logger (`opengrab`) |
| `msg` | string | Mensaje human-readable |

### Campos contextuales (según componente)

| Campo | Tipo | Aparece en |
|-------|------|-----------|
| `method` | string | Request logging (`GET`, `POST`, ...) |
| `path` | string | Request logging (`/api/jobs`, `/metrics`, ...) |
| `status` | int | Request logging (código HTTP) |
| `duration_ms` | float | Request logging (duración en ms) |
| `job_id` | string | Creación/completado/cancel de jobs |
| `incognito_dropped` | int | Reconcile de arranque (jobs incógnito descartados) |
| `interrupted` | int | Reconcile de arranque (jobs interrumpidos) |
| `requeued` | int | Reconcile de arranque (jobs reencolados) |

El formateador JSON (`logging_setup.JsonFormatter`) promueve automáticamente
cualquier campo pasado vía `extra=` en el `log.info()` a clave top-level del
objeto JSON. Esto significa que Loki puede indexar estos campos sin necesidad
de regex sobre el mensaje.

### Queries Loki (ready-to-copy)

Errores de cualquier tipo:
```logql
{app="opengrab"} | json | level="ERROR"
```

Latencias P95 por endpoint (últimos 5 minutos):
```logql
{app="opengrab"} | json | unwrap duration_ms
| quantile_over_time(0.95, duration_ms, 5m) by (path)
```

Jobs completados con extractor y mensaje:
```logql
{app="opengrab"} | json | job_id != "" and msg =~ "completado.*"
| line_format "{{.job_id}} {{.msg}}"
```

Reconcile de arranque (recuperación post-crash):
```logql
{app="opengrab"} | json | msg =~ "reconcile.*"
```

### Integración con Alloy (Grafana Agent)

```alloy
loki.source.file "opengrab_logs" {
  targets = [
    {__path__ = "/var/log/opengrab/*.json.log", app = "opengrab"},
  ]
  forward_to = [loki.write.default.receiver]
}
```

OpenGrab loguea a stdout. Para capturar logs en archivo con Docker:

```yaml
# docker-compose.yml
services:
  opengrab:
    logging:
      driver: "json-file"
      options:
        tag: "opengrab"
```

## Métricas Prometheus

Endpoint: `GET /metrics` (sin autenticación, protegido por red — ver `SECURITY.md`).

### Métricas expuestas

| Métrica | Tipo | Labels | Descripción |
|---------|------|--------|-------------|
| `opengrab_download_total` | Counter | `status`, `extractor` | Descargas completadas |
| `opengrab_download_duration_seconds` | Histogram | `extractor`, `quality` | Duración de descarga |
| `opengrab_download_bytes_total` | Counter | `extractor` | Bytes descargados |
| `opengrab_jobs_active` | Gauge | — | Jobs actualmente activos |
| `opengrab_storage_bytes_used` | Gauge | — | Consumo de disco |
| `opengrab_http_requests_total` | Counter | `endpoint`, `method`, `status_code` | Requests HTTP |
| `opengrab_ytdlp_version_info` | Info | `version` | Versión de yt-dlp |

### Configuración de scrape (Prometheus)

```yaml
# prometheus.yml
scrape_configs:
  - job_name: "opengrab"
    scrape_interval: 15s
    static_configs:
      - targets: ["opengrab:8800"]
```

Para Docker Compose, poner Prometheus en la misma red que OpenGrab y usar el
nombre del servicio como hostname.

### Dashboard

Ver `docs/examples/grafana-dashboard.json` — dashboard pre-configurado con
12 paneles: overview, performance, errores y métricas de sistema.

## Stack sugerido (homelab)

| Componente | Rol | Puerto |
|-----------|-----|--------|
| OpenGrab | App | 8800 |
| Prometheus | Métricas | 9090 |
| Grafana Alloy | Collector (logs → Loki) | — |
| Loki | Logs | 3100 |
| Grafana | Dashboards | 3000 |
