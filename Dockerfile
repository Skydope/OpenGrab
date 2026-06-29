FROM python:3.13-slim

# ffmpeg → muxear video+audio a mp4 | curl → healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --no-log-init opengrab \
    && mkdir -p /downloads && chown opengrab:opengrab /downloads

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY *.py ./
COPY static/ static/

# Entrypoint. yt-dlp se pinea exacto en pyproject.toml (imagen reproducible).
# El auto-update en runtime esta DESACTIVADO por default (OPENGRAB_AUTOUPDATE=0)
# por supply-chain: jalar la ultima version de PyPI sin pin en cada arranque es
# un riesgo. Si lo activas (=1), podes fijar la version con OPENGRAB_YTDLP_VERSION;
# si la dejas vacia, instala la ultima. La version baked es el piso si falla.
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER opengrab

ENV OPENGRAB_HOST=0.0.0.0 \
    OPENGRAB_PORT=8800 \
    OPENGRAB_DIR=/downloads \
    OPENGRAB_AUTOUPDATE=0 \
    OPENGRAB_CONFIG=/downloads/config.ini \
    HOME=/tmp

EXPOSE 8800
VOLUME ["/downloads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8800/health >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
