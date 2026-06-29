"""i18n: traducciones sin build step. JSON plano, ContextVar, sin gettext.

Carga archivos ``static/i18n/{lang}.json`` con caché en memoria.
En modo DEBUG (``OPENGRAB_LOG_LEVEL=DEBUG``) hace hot-reload automático.
Usa ``ContextVar`` para thread-safety sin modificar firmas de endpoints.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_lang_ctx: ContextVar[str] = ContextVar("lang", default="es")
_cache: dict[str, dict[str, str]] = {}
_fallback_lang = "en"
_DEFAULT_LANG = "es"


def _i18n_dir() -> Path:
    """Resuelve ``static/i18n/`` incluso bajo PyInstaller."""
    try:
        from config import resource_path
        return resource_path("static/i18n")
    except ImportError:
        return Path(__file__).parent / "static" / "i18n"


def set_lang(lang: str) -> None:
    """Setea el idioma activo para el contexto actual (request)."""
    _lang_ctx.set(lang)


def get_lang() -> str:
    """Devuelve el idioma activo para el contexto actual."""
    return _lang_ctx.get()


def load_translations(lang: str) -> dict[str, str]:
    """Carga traducciones desde JSON con caché en memoria.

    En modo DEBUG invalida la caché en cada llamada (hot-reload).
    Si el archivo no existe, usa fallback al inglés.
    """
    if os.environ.get("OPENGRAB_LOG_LEVEL", "") == "DEBUG":
        _cache.pop(lang, None)
    if lang in _cache:
        return _cache[lang]
    path = _i18n_dir() / f"{lang}.json"
    if not path.is_file():
        if lang != _fallback_lang:
            return load_translations(_fallback_lang)
        return {}
    with open(path, encoding="utf-8") as f:
        data: dict[str, str] = json.load(f)
    _cache[lang] = data
    return data


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Traduce una key. Interpola ``**kwargs`` con ``.format()``.

    Cadena de fallback: lang solicitado → inglés → key cruda.
    """
    lang = lang or get_lang()
    text = load_translations(lang).get(key)
    if text is None and lang != _fallback_lang:
        text = load_translations(_fallback_lang).get(key)
    if text is None:
        return key
    return text.format(**kwargs) if kwargs else text


def detect_lang(accept_language: str | None) -> str:
    """Detecta idioma del header ``Accept-Language``. Default español."""
    if not accept_language:
        return _DEFAULT_LANG
    primary = accept_language.split(",")[0].split(";")[0].strip().lower()
    return "en" if primary.startswith("en") else _DEFAULT_LANG
