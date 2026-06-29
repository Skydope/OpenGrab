# Installing OpenGrab

## 1. Docker (recommended for servers & homelab)

**Prerequisites:** Docker >= 24.x, Docker Compose

```bash
git clone https://github.com/Skydope/OpenGrab.git
cd OpenGrab
cp .env.example .env
docker compose up -d
```

Open **http://localhost:8800**

Optional: set `OPENGRAB_TOKEN` in `.env` to require authentication on all `/api/*` routes.

For persistent storage, mount a volume or set `OPENGRAB_DIR` in `.env` to a host path.

## 2. Desktop App

Download the installer from [Releases](https://github.com/Skydope/OpenGrab/releases/latest):

| Platform | File | Notes |
|----------|------|-------|
| Windows  | `OpenGrab-Setup.exe` | Bilingual wizard (Recommended / Advanced) |
| Linux    | `OpenGrab-x86_64.AppImage` | `chmod +x` and run |
| macOS    | `OpenGrab-macos.zip` | Unzip and run `.app` |

All desktop builds include bundled ffmpeg and yt-dlp with hot-swap support.

**Linux runtime dependencies:**

| Dependency | Package (apt) | Why |
|------------|---------------|-----|
| WebKit2GTK | `libwebkit2gtk-4.1-0` | Native window via pywebview (falls back to browser if missing) |
| AppIndicator | `libayatana-appindicator3-1` | System tray icon (falls back silently if missing) |
| FUSE 2 | `libfuse2` | Required to mount the AppImage on Ubuntu 24.04+ |

Install on Debian/Ubuntu:
```bash
sudo apt install libwebkit2gtk-4.1-0 libayatana-appindicator3-1 libfuse2
```

## 3. Bare Metal (development / manual)

**Prerequisites:** Python 3.12+, ffmpeg on PATH

```bash
git clone https://github.com/Skydope/OpenGrab.git
cd OpenGrab
pip install -e .
python app.py
```

Make sure ffmpeg is available:
- Debian/Ubuntu: `apt install ffmpeg`
- macOS: `brew install ffmpeg`
- Arch: `pacman -S ffmpeg`

See [docs/DEPLOY.md](DEPLOY.md) for production deployments (systemd, reverse proxy, TLS).

---

## Español

### 1. Docker (recomendado para servidores y homelab)

**Requisitos:** Docker >= 24.x, Docker Compose

```bash
git clone https://github.com/Skydope/OpenGrab.git
cd OpenGrab
cp .env.example .env
docker compose up -d
```

Abrí **http://localhost:8800**

Opcional: configurá `OPENGRAB_TOKEN` en `.env` para requerir autenticación en todas las rutas `/api/*`.

### 2. Aplicación de Escritorio

Descargá el instalador desde [Releases](https://github.com/Skydope/OpenGrab/releases/latest):

| Plataforma | Archivo | Notas |
|------------|---------|-------|
| Windows    | `OpenGrab-Setup.exe` | Wizard bilingüe (Recomendada / Avanzada) |
| Linux      | `OpenGrab-x86_64.AppImage` | `chmod +x` y ejecutar |
| macOS      | `OpenGrab-macos.zip` | Descomprimir y ejecutar `.app` |

Todos los builds incluyen ffmpeg y yt-dlp con hot-swap.

**Dependencias runtime en Linux:**

| Dependencia | Paquete (apt) | Motivo |
|-------------|---------------|--------|
| WebKit2GTK | `libwebkit2gtk-4.1-0` | Ventana nativa via pywebview (cae a navegador si falta) |
| AppIndicator | `libayatana-appindicator3-1` | Icono en la bandeja del sistema (cae silenciosamente si falta) |
| FUSE 2 | `libfuse2` | Necesario para montar el AppImage en Ubuntu 24.04+ |

Instalar en Debian/Ubuntu:
```bash
sudo apt install libwebkit2gtk-4.1-0 libayatana-appindicator3-1 libfuse2
```

### 3. Bare Metal (desarrollo / manual)

**Requisitos:** Python 3.12+, ffmpeg en el PATH

```bash
git clone https://github.com/Skydope/OpenGrab.git
cd OpenGrab
pip install -e .
python app.py
```

Consultá [docs/DEPLOY.md](DEPLOY.md) para despliegues en producción (systemd, reverse proxy, TLS).
