# OpenGrab — PyInstaller spec (build de escritorio, onedir).
#
# collect_all("yt_dlp") es CRÍTICO: (1) trae todos los extractors que yt-dlp carga
# dinámicamente, y (2) deja yt-dlp suelto (PathFinder), que es lo que hace funcionar el
# hot-swap por sys.path (ver spike en binary-plan.md §2.3).
#
# Build:  pyinstaller OpenGrab.spec --noconfirm
# Requiere: pip install -e ".[desktop,build]" y colocar vendor/ffmpeg.exe

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("yt_dlp", "pydantic", "pydantic_core"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["anyio._backends._asyncio"]

# UI embebida.
datas += [("static", "static")]

# ffmpeg bundleado (descargado a vendor/ por el CI). Si no está, el build sigue,
# pero el .exe dependería del ffmpeg del PATH — el CI debe garantizar vendor/ffmpeg.exe.
import os

if os.path.exists("vendor/ffmpeg.exe"):
    binaries += [("vendor/ffmpeg.exe", ".")]

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
    icon="vendor/opengrab.ico" if os.path.exists("vendor/opengrab.ico") else None,
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
