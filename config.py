from __future__ import annotations

import configparser
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


def _load_ini() -> dict[str, str]:
    """Lee ``config.ini`` como fuente de defaults para desktop.

    El instalador Inno Setup escribe ``%APPDATA%\\OpenGrab\\config.ini``.
    En Docker no existe → dict vacío → comportamiento sin cambios.
    Las variables de entorno siempre tienen precedencia sobre el INI.
    """
    if sys.platform == "win32":
        base = Path(
            os.environ.get(
                "APPDATA", str(Path.home() / "AppData" / "Roaming")
            )
        )
    else:
        base = Path(
            os.environ.get(
                "XDG_CONFIG_HOME", str(Path.home() / ".config")
            )
        )
    ini_path = os.environ.get(
        "OPENGRAB_CONFIG", str(base / "OpenGrab" / "config.ini")
    )
    try:
        cp = configparser.ConfigParser()
        cp.read(ini_path, encoding="utf-8")
        return dict(cp["opengrab"]) if "opengrab" in cp else {}
    except Exception:
        return {}


_ini = _load_ini()


def _ini_int(key: str, default: int) -> int:
    raw = _ini.get(key, "")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# Constants (env vars override INI values)
# --------------------------------------------------------------------------- #
HOST = os.environ.get("OPENGRAB_HOST", "127.0.0.1")
PORT = _int_env("OPENGRAB_PORT", _ini_int("port", 8800), min_val=1)

OUT_DIR = Path(
    os.environ.get(
        "OPENGRAB_DIR",
        _ini.get("download_dir", "./downloads"),
    )
).resolve()

# --- Auth ---
_raw_token = os.environ.get("OPENGRAB_TOKEN", "").strip()
if not _raw_token:
    _raw_token = _ini.get("token", "").strip()

_no_auth_env = os.environ.get("OPENGRAB_NO_AUTH", "").strip()
_ini_no_auth = _ini.get("no_auth", "").strip().lower()

if _no_auth_env == "1":
    TOKEN = ""
    TOKEN_WAS_GENERATED = False
elif _ini_no_auth in ("true", "1", "yes"):
    TOKEN = ""
    TOKEN_WAS_GENERATED = False
elif not _raw_token:
    TOKEN = secrets.token_urlsafe(16)
    TOKEN_WAS_GENERATED = True
else:
    TOKEN = _raw_token
    TOKEN_WAS_GENERATED = False

MAX_JOBS = _int_env("OPENGRAB_MAX_JOBS", _ini_int("max_jobs", 2), min_val=1)
MAX_SIZE_MB = _int_env("OPENGRAB_MAX_SIZE_MB", _ini_int("max_size_mb", 0), min_val=0)
MAX_TOTAL_MB = _int_env("OPENGRAB_MAX_TOTAL_MB", _ini_int("max_total_mb", 0), min_val=0)

TRUST_XFF = os.environ.get("OPENGRAB_TRUST_XFF", "").strip() == "1"
DB_PATH = OUT_DIR / "opengrab.db"
HISTORY_FILE = OUT_DIR / ".opengrab_history.json"
HISTORY_MAX = 500

VERSION = "1.9.0"

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


def resource_path(rel: str) -> Path:
    """Resuelve recursos bundleados tanto en dev como bajo PyInstaller.

    En un binario congelado, los recursos se extraen a ``sys._MEIPASS`` (onefile)
    o viven junto al ejecutable (onedir); fuera del binario, son relativos a este
    archivo. No rompe el modo Docker/dev: si ``_MEIPASS`` no existe, cae al path normal.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / rel


_STATIC_DIR = resource_path("static")
