from __future__ import annotations

from . import limiter, require_auth, get_state
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from i18n import t as _t

import re
from pathlib import Path
from typing import Any

from state import AppState

router = APIRouter()

# --------------------------------------------------------------------------- #
# Settings catalog + API
# --------------------------------------------------------------------------- #
# Metadata completa por setting:
#   key → (type, scope, default, description, placeholder, options, tokens, validation)
_FULL_CATALOG: dict[str, tuple[str, str, Any, str, str, Any, Any, Any]] = {
    "max_jobs": (
        "int", "runtime", 2,
        "settings.max_jobs",
        "2",
        None, None,
        {"min": 1, "max": 20},
    ),
    "max_total_mb": (
        "int", "runtime", 0,
        "settings.max_total_mb",
        "0",
        None, None,
        {"min": 0},
    ),
    "max_size_mb": (
        "int", "runtime", 0,
        "settings.max_size_mb",
        "0",
        None, None,
        {"min": 0},
    ),
    "history_max": (
        "int", "runtime", 500,
        "settings.history_max",
        "500",
        None, None,
        {"min": 10, "max": 10000},
    ),
    "quality_default": (
        "string", "runtime", "best",
        "settings.quality_default",
        "best",
        [
            {"value": "best", "label": "quality.best"},
            {"value": "1080p", "label": "quality.1080p"},
            {"value": "720p", "label": "quality.720p"},
            {"value": "480p", "label": "quality.480p"},
            {"value": "audio", "label": "quality.audio"},
        ],
        None, None,
    ),
    "theme": (
        "string", "runtime", "auto",
        "settings.theme",
        "auto",
        [
            {"value": "dark", "label": "theme.dark_label"},
            {"value": "light", "label": "theme.light_label"},
            {"value": "auto", "label": "theme.auto"},
        ],
        None, None,
    ),
    "lang": (
        "string", "runtime", "auto",
        "settings.lang",
        "auto",
        [
            {"value": "auto", "label": "lang.auto"},
            {"value": "es", "label": "lang.es"},
            {"value": "en", "label": "lang.en"},
        ],
        None, None,
    ),
    "notifications_enabled": (
        "bool", "runtime", False,
        "settings.notifications_enabled",
        "",
        None, None, None,
    ),
    "subs_default": (
        "bool", "runtime", False,
        "settings.subs_default",
        "",
        None, None, None,
    ),
    "thumb_default": (
        "bool", "runtime", False,
        "settings.thumb_default",
        "",
        None, None, None,
    ),
    "infojson_default": (
        "bool", "runtime", False,
        "settings.infojson_default",
        "",
        None, None, None,
    ),
    "library_dir": (
        "string", "desktop", "",
        "settings.library_dir",
        "C:\\Users\\...\\Downloads\\OpenGrab",
        None, None, None,
    ),
    "name_template": (
        "string", "desktop", "{title}",
        "settings.name_template",
        "{title}",
        None,
        ["{title}", "{channel}", "{upload_year}", "{upload_date}",
         "{extractor}", "{video_id}", "{resolution}"],
        None,
    ),
}

# Agrupación de settings para las pestañas del modal de configuración.
# key → group ∈ {downloads, storage, interface, advanced}
_SETTING_GROUP: dict[str, str] = {
    "quality_default": "downloads",
    "max_jobs": "downloads",
    "subs_default": "downloads",
    "thumb_default": "downloads",
    "infojson_default": "downloads",
    "name_template": "downloads",
    "library_dir": "storage",
    "max_total_mb": "storage",
    "max_size_mb": "storage",
    "history_max": "storage",
    "theme": "interface",
    "lang": "interface",
    "notifications_enabled": "interface",
}

# Catálogo simple para compatibilidad con resolve() y lookup rápido.
_SETTING_CATALOG: dict[str, tuple[str, str, Any]] = {
    k: (vtype, scope, default) for k, (vtype, scope, default, *_rest) in _FULL_CATALOG.items()
}

# Validadores server-side por setting.
_SETTING_VALIDATORS: dict[str, dict[str, Any]] = {
    key: (val[7] or {}) for key, val in _FULL_CATALOG.items() if val[7]
}
# Settings de tipo select (para validación de valores permitidos).
_SETTING_OPTIONS: dict[str, set[str]] = {
    key: {o["value"] for o in val[5]}
    for key, val in _FULL_CATALOG.items() if val[5]
}

_NAME_TEMPLATE_TOKENS = frozenset({
    "{title}", "{channel}", "{upload_year}", "{upload_date}",
    "{extractor}", "{video_id}", "{resolution}",
})

# Tokens aceptados para settings de tipo bool. Fuente unica de verdad: la
# validacion y la coercion comparten estos sets para no divergir.
_BOOL_TRUE = frozenset({"true", "1", "yes"})
_BOOL_FALSE = frozenset({"false", "0", "no", ""})


def _coerce_bool(raw: Any) -> bool:
    """Coerciona un valor (str del resolver, o bool) a bool.

    Un bool se devuelve tal cual; cualquier otra cosa se normaliza y se compara
    contra ``_BOOL_TRUE``. Centraliza la logica que antes estaba duplicada en
    el GET de settings, el GET de defaults y el validador.
    """
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _BOOL_TRUE


def _validate_setting_value(key: str, raw_value: str) -> str | None:
    """Valida un valor para un setting. Devuelve mensaje de error o None."""
    info = _FULL_CATALOG.get(key)
    if info is None:
        return _t("error.settings_unknown_key")
    vtype = info[0]

    if vtype == "int":
        try:
            ival = int(raw_value)
        except (ValueError, TypeError):
            return _t("error.settings_int_invalid")
        rules = _SETTING_VALIDATORS.get(key, {})
        if "min" in rules and ival < rules["min"]:
            return _t("error.settings_min_value", min=rules["min"])
        if "max" in rules and ival > rules["max"]:
            return _t("error.settings_max_value", max=rules["max"])
        return None

    if vtype == "bool":
        norm = str(raw_value).strip().lower()
        if norm in _BOOL_TRUE or norm in _BOOL_FALSE:
            return None
        return _t("error.settings_bool_invalid")

    if vtype == "string":
        sval = str(raw_value)
        # Validar opciones si es un select
        if key in _SETTING_OPTIONS and sval not in _SETTING_OPTIONS[key]:
            return _t("error.settings_value_not_allowed", options=sorted(_SETTING_OPTIONS[key]))
        # Validar tokens para name_template
        if key == "name_template":
            tokens = re.findall(r"\{[a-z_]+\}", sval)
            invalid = [t for t in tokens if t not in _NAME_TEMPLATE_TOKENS]
            if invalid:
                return _t("error.settings_tokens_invalid", tokens=", ".join(invalid))
        # Validar library_dir: warn si no existe, pero no bloquear
        if key == "library_dir" and sval:
            p = Path(sval)
            if p.exists() and not p.is_dir():
                return _t("error.settings_path_not_dir")
        return None

    return None


@router.get("/api/settings")
async def api_get_settings(
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Devuelve el catálogo completo de settings con valor actual y metadata."""
    catalog: list[dict[str, Any]] = []
    for key, (vtype, scope, default, desc, placeholder,
              options, tokens, validation) in _FULL_CATALOG.items():
        val, origin = state.resolve(key, default, str)
        # Cast para la respuesta
        if vtype == "int":
            try:
                val = int(val)
            except (ValueError, TypeError):
                val = default
        elif vtype == "bool":
            val = _coerce_bool(val)

        # Solo ``env`` bloquea: es un override declarativo de ops (Docker) que
        # no se puede sobrescribir en caliente. El ini ya no bloquea porque la
        # tabla gana sobre él (ver state.resolve), así que toda setting es
        # editable y se aplica al instante desde la UI.
        locked = origin == "env"
        entry: dict[str, Any] = {
            "key": key,
            "type": vtype,
            "scope": scope,
            "value": val,
            "default": default,
            "origin": origin,
            "locked": locked,
            "restart_required": False,
            "description": desc,
            "placeholder": placeholder,
            "group": _SETTING_GROUP.get(key, "advanced"),
        }
        if options:
            entry["options"] = options
        if tokens:
            entry["tokens"] = tokens
        if validation:
            entry["validation"] = validation
        catalog.append(entry)
    return JSONResponse(catalog)


@router.get("/api/settings/defaults")
async def api_get_settings_defaults(
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Devuelve los defaults que el frontend necesita para inicializar chips."""
    quality, _origin_q = state.resolve("quality_default", "best", str)
    theme, _origin_t = state.resolve("theme", "auto", str)
    lang, _origin_l = state.resolve("lang", "auto", str)
    notif, _origin_n = state.resolve("notifications_enabled", False, str)
    notif_enabled = _coerce_bool(notif)

    subs_val = _coerce_bool(state.resolve("subs_default", False, str)[0])
    thumb_val = _coerce_bool(state.resolve("thumb_default", False, str)[0])
    infojson_val = _coerce_bool(state.resolve("infojson_default", False, str)[0])
    return JSONResponse({
        "quality_default": quality,
        "theme": theme,
        "lang": lang,
        "notifications_enabled": notif_enabled,
        "subs_default": subs_val,
        "thumb_default": thumb_val,
        "infojson_default": infojson_val,
    })


@router.put("/api/settings")
@router.patch("/api/settings")
@limiter.limit("10/minute")
async def api_update_settings(
    request: Request,
    _: None = Depends(require_auth),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Actualiza settings (PUT o PATCH). Keys con origin=env retornan 400."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, _t("error.json_invalid"))
    if not isinstance(body, dict):
        raise HTTPException(400, _t("error.settings_body_dict"))
    errors: dict[str, str] = {}
    updated: list[str] = []
    for key, raw_value in body.items():
        if key not in _FULL_CATALOG:
            errors[key] = _t("error.settings_unknown_key")
            continue
        _, origin = state.resolve(key, _FULL_CATALOG[key][2], str)
        if origin == "env":
            errors[key] = _t("error.settings_locked", origin=origin)
            continue
        # Validacion server-side
        str_value = str(raw_value)
        err = _validate_setting_value(key, str_value)
        if err:
            errors[key] = err
            continue
        # Persist: la tabla es la única fuente de verdad para ediciones del
        # usuario. Gana sobre el ini en resolve() (env > tabla > ini), así que
        # escribir el ini sería redundante; dejarlo intacto preserva su rol de
        # semilla del instalador y elimina la divergencia tabla/ini. El valor
        # se aplica en vivo porque resolve() se consulta en cada uso.
        state.db.set_setting(key, str_value)
        updated.append(key)
    if errors and not updated:
        raise HTTPException(
            400,
            {"error": _t("error.settings_all_failed"), "details": errors},
        )
    return JSONResponse({
        "ok": True,
        "updated": updated,
        "errors": errors if errors else None,
    })
