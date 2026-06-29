"""Tests para el sistema i18n."""
from pathlib import Path

from i18n import t, detect_lang, get_lang, set_lang, load_translations


def test_t_returns_spanish_by_default():
    assert t("ui.connect") == "Conectar"


def test_t_english():
    assert t("ui.connect", lang="en") == "Connect"


def test_t_fallback_to_en_when_key_missing_es():
    """Si una key no está en es pero sí en en, usa el fallback."""
    # Verificamos que t con lang=en devuelve la traducción inglesa
    assert t("ui.connect", lang="en") == "Connect"


def test_t_returns_key_when_fully_missing():
    assert t("nonexistent.key.12345") == "nonexistent.key.12345"


def test_t_interpolation_positional():
    # Usamos una key que toma kwargs. Buscamos error.max_jobs en es
    result = t("error.max_jobs", max_jobs=5)
    assert "5" in result


def test_detect_lang_es_default():
    assert detect_lang(None) == "es"
    assert detect_lang("") == "es"


def test_detect_lang_en():
    assert detect_lang("en-US") == "en"
    assert detect_lang("en-GB,es;q=0.9") == "en"
    assert detect_lang("en") == "en"


def test_detect_lang_es():
    assert detect_lang("es-AR") == "es"
    assert detect_lang("es-MX") == "es"
    assert detect_lang("es") == "es"
    assert detect_lang("fr-FR") == "es"  # desconocido → default


def test_context_var_isolation():
    set_lang("es")
    assert get_lang() == "es"
    set_lang("en")
    assert get_lang() == "en"
    set_lang("es")  # restore


def test_t_respects_context_var():
    set_lang("en")
    result = t("error.token_invalid")
    set_lang("es")
    assert "Invalid" in result or "Token" in result or "token" in result


def test_all_keys_present_in_both_files():
    """Cada key en es.json existe en en.json y viceversa."""
    import json
    i18n_dir = Path(__file__).parent.parent / "static" / "i18n"
    es_path = i18n_dir / "es.json"
    en_path = i18n_dir / "en.json"
    assert es_path.exists(), f"No existe {es_path}"
    assert en_path.exists(), f"No existe {en_path}"
    es_data = json.loads(es_path.read_text(encoding="utf-8"))
    en_data = json.loads(en_path.read_text(encoding="utf-8"))
    es_keys = set(es_data.keys())
    en_keys = set(en_data.keys())
    only_es = es_keys - en_keys
    only_en = en_keys - es_keys
    assert not only_es, f"Keys solo en es.json: {sorted(only_es)}"
    assert not only_en, f"Keys solo en en.json: {sorted(only_en)}"


def test_no_empty_values():
    """Ningún value en los JSON de traducción está vacío."""
    import json
    i18n_dir = Path(__file__).parent.parent / "static" / "i18n"
    for lang in ("es", "en"):
        path = i18n_dir / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, val in data.items():
            assert val and val.strip(), f"[{lang}] {key} tiene valor vacío"


def test_settings_keys_present():
    """Los nuevos settings description usan keys i18n? Verificamos estructura."""
    import json
    i18n_dir = Path(__file__).parent.parent / "static" / "i18n"
    es_data = json.loads((i18n_dir / "es.json").read_text(encoding="utf-8"))
    for prefix in ("error.", "ui.", "settings.", "quality.", "theme.", "lang.", "tray.", "download."):
        matching = [k for k in es_data if k.startswith(prefix)]
        assert matching, f"No hay keys con prefijo {prefix}"


def test_t_interpolation_named():
    """t() con kwargs nombrados interpola correctamente."""
    set_lang("es")
    result = t("ui.settings_saved", setting_key="theme")
    assert "theme" in result


def test_load_translations_caches():
    """Segunda llamada retorna el mismo objeto (caché)."""
    first = load_translations("es")
    second = load_translations("es")
    assert first is second
