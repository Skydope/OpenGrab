#!/usr/bin/env python3
"""Genera ``vendor/opengrab.ico`` con el diseño del tray icon:
rectángulo redondeado oscuro + triángulo play ámbar, 256x256 RGBA.
Windows 10+ escala automáticamente a todos los tamaños de taskbar/explorer."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("ERROR: Pillow no está instalado → pip install Pillow", file=sys.stderr)
    sys.exit(1)

BG_COLOR = (28, 33, 43, 255)   # panel oscuro
AMBER = (232, 160, 44, 255)    # triángulo play

_SIZE = 256
_INSET = 16       # 4/64 * 256
_RADIUS = 48      # 12/64 * 256
# Triángulo play escalado del diseño base 64x64
_TRI = [
    (_SIZE * 24 // 64, _SIZE * 18 // 64),
    (_SIZE * 24 // 64, _SIZE * 46 // 64),
    (_SIZE * 44 // 64, _SIZE * 32 // 64),
]


def _png_to_ico_entry(png_data: bytes) -> bytes:
    """Embed a PNG as an ICO entry (Windows Vista+ format)."""
    # ICO header: reserved(2) + type(2) + count(2)
    header = struct.pack("<HHH", 0, 1, 1)
    # Directory entry: w(1) h(1) colors(1) reserved(1) planes(2) bpp(2) size(4) offset(4)
    offset = 6 + 16  # header + 1 directory entry
    entry = struct.pack(
        "<BBBBHHII",
        0, 0,          # 0 = 256px
        0, 0,          # colors, reserved
        1, 32,         # planes, bpp
        len(png_data),
        offset,
    )
    return header + entry + png_data


def generate_ico(output: Path) -> None:
    img = Image.new("RGBA", (_SIZE, _SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [_INSET, _INSET, _SIZE - _INSET, _SIZE - _INSET],
        radius=_RADIUS,
        fill=BG_COLOR,
    )
    draw.polygon(_TRI, fill=AMBER)

    # Save as PNG bytes
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_data = buf.getvalue()

    ico = _png_to_ico_entry(png_data)
    output.write_bytes(ico)
    print(f"Icono generado: {output} ({len(ico):,} bytes, 256x256 PNG)")


if __name__ == "__main__":
    vendor = (Path(__file__).parent.parent / "vendor").resolve()
    vendor.mkdir(parents=True, exist_ok=True)
    generate_ico(vendor / "opengrab.ico")
