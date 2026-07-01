"""Funciones puras de borrado seguro (3 pasadas: 0x00, 0xFF, random).

Sin dependencia en AppState ni Database — solo stdlib + config.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import config


def wipe_file(filepath: str, force: bool = False) -> None:
    """Sobrescribe el archivo (0x00 / 0xFF / random) y lo borra.

    Activo cuando ``OPENGRAB_SECURE_DELETE=1`` (opt-in global) **o** cuando
    ``force=True`` (modo incógnito: el wipe del residuo no es opcional).
    Por defecto usa ``os.unlink()`` — la sobreescritura en SSD/CoW
    no da garantias forenses, y el fast path evita 3x escrituras
    innecesarias por cada delete/cleanup.

    CAVEAT: el overwrite in-place solo da garantias reales sobre medios que
    reescriben el mismo sector (HDD magnetico). En SSD/NVMe (wear-leveling),
    filesystems copy-on-write (Btrfs, ZFS, APFS) o con snapshots, los datos
    viejos pueden persistir en bloques no mapeados que estas pasadas no tocan.
    En esos medios esto reduce la recuperacion casual pero NO es un borrado
    forense garantizado; para eso hace falta cifrado en reposo o TRIM/secure-erase
    a nivel de dispositivo. Mantenemos las 3 pasadas porque no hacen daño y
    ayudan en el caso HDD, sin venderlas como mas de lo que son.
    """
    path = Path(filepath)
    if not path.is_file():
        return
    if not (config.SECURE_DELETE or force):
        path.unlink()
        return
    size = path.stat().st_size
    if size == 0:
        path.unlink()
        return
    try:
        with open(path, "r+b") as f:
            # Pass 1: zeros
            f.seek(0)
            remaining = size
            while remaining > 0:
                chunk = min(remaining, 1024 * 1024)
                f.write(b"\x00" * chunk)
                remaining -= chunk
            f.flush()
            os.fsync(f.fileno())
            # Pass 2: ones (0xFF)
            f.seek(0)
            remaining = size
            while remaining > 0:
                chunk = min(remaining, 1024 * 1024)
                f.write(b"\xFF" * chunk)
                remaining -= chunk
            f.flush()
            os.fsync(f.fileno())
            # Pass 3: random
            f.seek(0)
            remaining = size
            while remaining > 0:
                chunk = min(remaining, 1024 * 1024)
                f.write(os.urandom(chunk))
                remaining -= chunk
            f.flush()
            os.fsync(f.fileno())
        path.unlink()
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass


def wipe_workdir(workdir: str, force: bool = False) -> None:
    """Recorre el directorio, wipea cada archivo y borra el árbol."""
    wd = Path(workdir)
    if not wd.is_dir():
        return
    for f in wd.rglob("*"):
        if f.is_file():
            wipe_file(str(f), force=force)
    shutil.rmtree(wd, ignore_errors=True)
