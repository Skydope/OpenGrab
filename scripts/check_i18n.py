#!/usr/bin/env python3
"""Verifica que todas las keys de i18n existan en ambos archivos JSON.

Keys faltantes o extras en cualquier idioma generan un error.
Usado en CI para evitar traducciones incompletas.
"""

import json
import sys
from pathlib import Path

I18N_DIR = Path("static/i18n")
LANGS = ("es", "en")


def main() -> int:
    errors = 0
    data: dict[str, dict[str, str]] = {}

    for lang in LANGS:
        path = I18N_DIR / f"{lang}.json"
        if not path.exists():
            print(f"MISSING: {path}")
            errors += 1
            continue
        try:
            data[lang] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"INVALID JSON [{lang}]: {e}")
            errors += 1

    if not data:
        print("ERROR: no se pudo cargar ningún archivo de i18n.")
        return 1

    all_keys: set[str] = set()
    for d in data.values():
        all_keys.update(d.keys())

    for key in sorted(all_keys):
        for lang in LANGS:
            if lang not in data:
                continue
            if key not in data[lang]:
                print(f"MISSING [{lang}]: {key}")
                errors += 1

    for lang in LANGS:
        if lang not in data:
            continue
        extras = set(data[lang].keys()) - all_keys
        for key in sorted(extras):
            print(f"EXTRA [{lang}]: {key}")
            errors += 1

    # Verificar que ningún valor esté vacío
    for lang in LANGS:
        if lang not in data:
            continue
        for key, val in data[lang].items():
            if not val or not val.strip():
                print(f"EMPTY [{lang}]: {key}")
                errors += 1

    if errors:
        print(f"\n{errors} error(es) encontrados.")
        return 1

    print(f"OK: {len(all_keys)} keys en {len(LANGS)} idiomas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
