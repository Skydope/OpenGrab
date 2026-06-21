from __future__ import annotations

import os
from pathlib import Path

HOST = os.environ.get("OPENGRAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENGRAB_PORT", "8800"))
OUT_DIR = Path(os.environ.get("OPENGRAB_DIR", "./downloads")).resolve()
TOKEN = os.environ.get("OPENGRAB_TOKEN", "").strip()
MAX_JOBS = int(os.environ.get("OPENGRAB_MAX_JOBS", "2"))
MAX_SIZE_MB = max(0, int(os.environ.get("OPENGRAB_MAX_SIZE_MB", "0")))
HISTORY_FILE = OUT_DIR / ".opengrab_history.json"
HISTORY_MAX = 500

FORMATS = {
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bv*+ba/b",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]",
    "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=480][ext=mp4]/bv*[height<=480]+ba/b[height<=480]",
    "audio": "bestaudio/best",
}

_STATIC_DIR = Path(__file__).parent / "static"
