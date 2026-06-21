FROM python:3.12-slim

# ffmpeg → muxear video+audio a mp4 | curl → healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --no-log-init opengrab \
    && mkdir -p /downloads && chown opengrab:opengrab /downloads

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY static/ static/

# Entrypoint. yt-dlp se pinea exacto en requirements.txt (imagen reproducible).
# El auto-update en runtime esta DESACTIVADO por default (OPENGRAB_AUTOUPDATE=0)
# por supply-chain: jalar la ultima version de PyPI sin pin en cada arranque es
# un riesgo. Si lo activas (=1), podes fijar la version con OPENGRAB_YTDLP_VERSION;
# si la dejas vacia, instala la ultima. La version baked es el piso si falla.
RUN printf '%s\n' \
  '#!/bin/sh' \
  'set -e' \
  'trap "exit 0" TERM INT' \
  'if [ "${OPENGRAB_AUTOUPDATE:-0}" = "1" ]; then' \
  '  if [ -n "${OPENGRAB_YTDLP_VERSION:-}" ]; then' \
  '    echo "[opengrab] instalando yt-dlp==${OPENGRAB_YTDLP_VERSION}..."' \
  '    pip install --no-cache-dir -q --user "yt-dlp==${OPENGRAB_YTDLP_VERSION}" || echo "[opengrab] update fallo, uso version baked"' \
  '  else' \
  '    echo "[opengrab] actualizando yt-dlp a la ultima version..."' \
  '    pip install --no-cache-dir -q -U --user yt-dlp || echo "[opengrab] update fallo, uso version baked"' \
  '  fi' \
  'fi' \
  'exec python app.py' \
  > /entrypoint.sh && chmod +x /entrypoint.sh

USER opengrab

ENV OPENGRAB_HOST=0.0.0.0 \
    OPENGRAB_PORT=8800 \
    OPENGRAB_DIR=/downloads \
    OPENGRAB_AUTOUPDATE=0 \
    HOME=/tmp

EXPOSE 8800
VOLUME ["/downloads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8800/health >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
