from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def _int_env(key: str, default: int, min_val: int | None = None) -> int:
    raw = os.environ.get(key, str(default))
    try:
        val = int(raw)
    except ValueError:
        print(
            f"[opengrab] WARNING: {key}={raw!r} invalido, usando {default}",
            file=sys.stderr,
        )
        return default
    if min_val is not None and val < min_val:
        print(
            f"[opengrab] WARNING: {key}={val} menor que minimo {min_val}, usando {min_val}",
            file=sys.stderr,
        )
        return min_val
    return val


HOST = os.environ.get("OPENGRAB_HOST", "127.0.0.1")
PORT = _int_env("OPENGRAB_PORT", 8800, min_val=1)
OUT_DIR = Path(os.environ.get("OPENGRAB_DIR", "./downloads")).resolve()
_raw_token = os.environ.get("OPENGRAB_TOKEN")
if _raw_token is None:
    TOKEN = secrets.token_urlsafe(16)
    TOKEN_WAS_GENERATED = True
else:
    TOKEN = _raw_token.strip()
    TOKEN_WAS_GENERATED = False
MAX_JOBS = _int_env("OPENGRAB_MAX_JOBS", 2, min_val=1)
MAX_SIZE_MB = _int_env("OPENGRAB_MAX_SIZE_MB", 0, min_val=0)
TRUST_XFF = os.environ.get("OPENGRAB_TRUST_XFF", "").strip() == "1"
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
