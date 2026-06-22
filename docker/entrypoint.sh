#!/bin/sh
set -e
trap "exit 0" TERM INT

if [ "${OPENGRAB_AUTOUPDATE:-0}" = "1" ]; then
  if [ -n "${OPENGRAB_YTDLP_VERSION:-}" ]; then
    echo "[opengrab] instalando yt-dlp==${OPENGRAB_YTDLP_VERSION}..."
    pip install --no-cache-dir -q --user "yt-dlp==${OPENGRAB_YTDLP_VERSION}" || echo "[opengrab] update fallo, uso version baked"
  else
    echo "[opengrab] actualizando yt-dlp a la ultima version..."
    pip install --no-cache-dir -q -U --user yt-dlp || echo "[opengrab] update fallo, uso version baked"
  fi
fi

exec python app.py
