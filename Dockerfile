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

# Entrypoint: yt-dlp rompe seguido cuando YouTube cambia el player.
# Por eso, salvo que pinees, se actualiza al arrancar. La version baked en la
# imagen es el piso si el update falla o no hay red.
RUN printf '%s\n' \
  '#!/bin/sh' \
  'set -e' \
  'trap "exit 0" TERM INT' \
  'if [ "${OPENGRAB_AUTOUPDATE:-1}" = "1" ]; then' \
  '  echo "[opengrab] actualizando yt-dlp..."' \
  '  pip install --no-cache-dir -q -U --user yt-dlp || echo "[opengrab] update fallo, uso version baked"' \
  'fi' \
  'exec python app.py' \
  > /entrypoint.sh && chmod +x /entrypoint.sh

USER opengrab

ENV OPENGRAB_HOST=0.0.0.0 \
    OPENGRAB_PORT=8800 \
    OPENGRAB_DIR=/downloads \
    OPENGRAB_AUTOUPDATE=1 \
    HOME=/tmp

EXPOSE 8800
VOLUME ["/downloads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8800/health >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
