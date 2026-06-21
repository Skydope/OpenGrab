FROM python:3.12-slim

# ffmpeg → muxear video+audio a mp4 | curl → healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

# Entrypoint: yt-dlp rompe seguido cuando YouTube cambia el player.
# Por eso, salvo que pinees, se actualiza al arrancar. La versión baked en la
# imagen es el piso si el update falla o no hay red.
RUN printf '%s\n' \
  '#!/bin/sh' \
  'set -e' \
  'if [ "${YTGRAB_AUTOUPDATE:-1}" = "1" ]; then' \
  '  echo "[ytgrab] actualizando yt-dlp..."' \
  '  pip install --no-cache-dir -q -U yt-dlp || echo "[ytgrab] update fallo, uso version baked"' \
  'fi' \
  'exec python app.py' \
  > /entrypoint.sh && chmod +x /entrypoint.sh

ENV YTGRAB_HOST=0.0.0.0 \
    YTGRAB_PORT=8800 \
    YTGRAB_DIR=/downloads \
    YTGRAB_AUTOUPDATE=1 \
    HOME=/tmp

EXPOSE 8800
VOLUME ["/downloads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8800/health >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
