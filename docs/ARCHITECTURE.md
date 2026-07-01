# Architecture — OpenGrab

Decisiones de diseño, trade-offs y deuda diferida. Documento vivo — se
actualiza cuando cambia el contexto técnico.

---

## Estructura del proyecto (post-Fase 2)

```
app.py                  Entrypoint FastAPI + uvicorn
config.py               Settings: env > table > ini > default
db.py                   SQLite wrapper (conn + threading.Lock)
download.py             yt-dlp: inspect, download, watch
desktop.py              Pywebview + pystray tray app (entrypoint .exe)
engine_update.py        Hot-swap de yt-dlp desde PyPI
i18n.py                 Traducciones server-side
models.py               Pydantic models (Job, BatchReq, etc.)
secure_delete.py        3-pass file wipe (funciones puras)
storage_manager.py      Uso, cleanup de workdirs, eviction de jobs
library_path_resolver.py  Templates, dedup, file movement
history_store.py        Historial CRUD

routers/                FastAPI routers (por dominio)
static/                 Frontend (Alpine.js, sin bundler)
```

---

## Lock global de SQLite (`db.py`)

**Decisión**: una conexión SQLite compartida con `threading.Lock` que
serializa todo acceso. Las escrituras ocurren solo en transiciones de
estado de jobs (crear, completar, cancelar), no en cada tick de
progreso.

**Justificación actual**:
- Escrituras infrecuentes y de baja latencia.
- El lock de Python evita `SQLITE_BUSY` sin necesidad de WAL
  retry loops.
- Single-user / homelab: el p50 de jobs activos es < 5.

**Condiciones para re-evaluar**:
- p50 de jobs activos simultáneos > 20 (lock contention visible).
- Migración a acceso multi-usuario real (varios clientes HTTP
  concurrentes compitiendo por escrituras).
- Observabilidad: si `SQLITE_BUSY` aparece en logs aun con el
  lock de Python, pasar a WAL mode con busy_timeout.

**Alternativas evaluadas**: WAL mode, aiosqlite, SQLAlchemy. Se
descartaron porque el volumen actual no las justifica y agregan
complejidad de deployment (WAL necesita checkpoint, aiosqlite
requiere loop, SQLAlchemy es overkill para 6 tablas).

---

## Frontend sin bundler

**Decisión**: Alpine.js vanilla cargado como `<script>` estático,
HTML servido desde FastAPI con template substitution para variables
de servidor (AUTH_REQUIRED, FORMATS, IS_DESKTOP). Cero build step.

**Justificación actual**:
- El proyecto es chico (~15 archivos JS de 20-270 líneas cada
  uno, Fase 4).
- Evitar un bundler (webpack/vite/esbuild) elimina un paso de
  build, dependencias npm, y configuración de asset pipeline.
- Alpine.js + vanilla JS cubre el 100% de los casos de uso sin
  necesidad de componentes, state management library, o SPA
  routing.

**Trade-off**: sin bundler, los scripts cargan sincrónicamente al
final del `<body>`, bloqueando el render hasta que todos los
recursos JS están disponibles. En LAN (uso típico), la latencia
es < 5ms por archivo.

**Condiciones para re-evaluar**:
- Más de 25 archivos JS (el costo de carga secuencial se vuelve
  perceptible).
- Necesidad de TypeScript (type safety en lógica de frontend
  compleja).
- Migración a SPA con routing (varias páginas lógicas sin
  recarga completa).
- Hot module replacement necesario para velocidad de desarrollo.

---

## Deuda técnica identificada y NO resuelta

### `desktop.py` — 604 líneas monolíticas

El entrypoint de escritorio (pywebview + pystray) no fue
descompuesto en Fase 2. Contiene `_grabber_window` (~200 líneas),
`_serve` (~90 líneas), y `_system_tray` (~60 líneas). Razón para
diferir:

- **Alto riesgo de regresión**: el testeo del escritorio es manual
  en 3 plataformas (Windows WebView2, Linux WebKit2GTK, macOS
  WKWebView). Un refactor sin cobertura de tests automatizados
  puede introducir bugs silenciosos que solo se detectan en
  producción.
- **Baja frecuencia de cambio**: el escritorio es estable, las
  features nuevas son raras. La deuda no crece activamente.
- **Prioridad**: Fase 1-3 eliminaron el 100% de la deuda de alto
  impacto (download.py, AppState, excepciones). `desktop.py` es
  deuda de mantenibilidad, no de corrección.

### `config.py` — duplicación parcial

`_load_ini()` en `config.py` y `_setup_env()` en `desktop.py`
comparten lógica de búsqueda de `config.ini`. La duplicación es
deliberada: `config.py` se carga en modo server (sin `APPDATA`),
`desktop.py` lo hace en modo binario (con `APPDATA`). Unificar
requiere inyectar la ruta del INI como dependencia explícita en
lugar de leerla de `os.environ`, lo cual es un cambio de API en
`config` que tocaría `app.py` y `desktop.py`. Diferido por
prioridad baja.

### Tests de integración — acoplados a FastAPI

Los 413 tests existentes usan `TestClient` de Starlette, que
levanta la app completa. No hay tests unitarios de las funciones
puras extraídas en Fase 1 (`_build_ydl_opts`, `_handle_termination`,
etc.). Las funciones de `secure_delete.py` sí tienen tests
unitarios (`test_secure_delete.py`). La cobertura de integración es
buena pero lenta (~25s). Agregar tests unitarios de las funciones
de `download.py` reduciría el feedback loop y mejoraría la
experiencia de bisect.

---

## Patrones establecidos

### Conventional commits en español

Todos los commits siguen [Conventional
Commits](https://www.conventionalcommits.org/) con tipos en inglés
y descripción en español: `refactor(download): extraer
_build_ydl_opts`.

### Facade pattern para descomposición

Fase 2 usó el patrón facade para descomponer `AppState` sin romper
callsites externos durante la transición. Los wrappers temporales
se removieron en el commit 5. Este patrón es reutilizable para
futuras descomposiciones (p.ej. `desktop.py`).

### Ruff + mypy strict + pytest como gate pre-commit

Cada commit debe pasar las 3 gates. `scripts/check.py` ejecuta las
3 en orden y reporta resultado consolidado. mypy tiene
`stages: [manual]` en `.pre-commit-config.yaml` — no corre
automáticamente, hay que invocarlo explícitamente.
