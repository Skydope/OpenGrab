# OpenGrab — PyInstaller spec (build de escritorio, onedir).
#
# collect_all("yt_dlp") es CRÍTICO: (1) trae todos los extractors que yt-dlp carga
# dinámicamente, y (2) deja yt-dlp suelto (PathFinder), que es lo que hace funcionar el
# hot-swap por sys.path (ver spike en binary-plan.md §2.3).
#
# Build:  pyinstaller OpenGrab.spec --noconfirm
# Requiere: pip install -e ".[desktop,build]" y colocar vendor/ffmpeg(.exe)

from PyInstaller.utils.hooks import collect_all, collect_submodules

import os
import sys

datas, binaries, hiddenimports = [], [], []
for pkg in ("yt_dlp", "pydantic", "pydantic_core", "pythonnet", "clr_loader", "webview"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("webview")
hiddenimports += ["anyio._backends._asyncio"]

# UI embebida.
datas += [("static", "static")]

# ffmpeg bundleado. El nombre depende de la plataforma (.exe en Windows).
_ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
if os.path.exists(f"vendor/{_ffmpeg_name}"):
    binaries += [(f"vendor/{_ffmpeg_name}", ".")]

# Icono: ICO en Windows, ICNS en macOS. Linux no tiene icono de binario nativo.
_icon = None
if sys.platform == "win32" and os.path.exists("vendor/opengrab.ico"):
    _icon = "vendor/opengrab.ico"
elif sys.platform == "darwin" and os.path.exists("vendor/opengrab.icns"):
    _icon = "vendor/opengrab.icns"

a = Analysis(
    ["desktop.py"],
    pathex=["."],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    # Peso: extras opcionales de uvicorn que no usamos en loopback, y tkinter.
    excludes=["tkinter", "watchfiles", "httptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenGrab",
    console=False,          # --windowed: sin consola negra
    icon=_icon,
    # Sin UPX a propósito: empeora los falsos positivos de antivirus.
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="OpenGrab",
)
