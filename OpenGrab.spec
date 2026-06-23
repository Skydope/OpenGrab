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

# --------------------------------------------------------------------------- #
# DLLs de WebView2 / pythonnet — forzar como binaries (raíz plana de _internal/)
# --------------------------------------------------------------------------- #
# collect_all() clasifica los .dll como datas → preservan estructura anidada de
# paquetes (e.g. _internal/webview/lib/foo.dll). Pero pywebview busca los DLLs en
# sys._MEIPASS sin recursión (interop_dll_path en util.py) y pythonnet/clr_loader
# necesita ClrLoader.dll y Python.Runtime.dll en la raíz para inicializar.
# Forzamos binaries para que PyInstaller los copie planos.
_site = next((p for p in sys.path if p.endswith("site-packages")), "")
if _site:
    _wv_lib = os.path.join(_site, "webview", "lib")
    if os.path.isdir(_wv_lib):
        binaries += [
            (os.path.join(_wv_lib, "Microsoft.Web.WebView2.Core.dll"), "."),
            (os.path.join(_wv_lib, "Microsoft.Web.WebView2.WinForms.dll"), "."),
        ]
        for _dll_name in ("WebBrowserInterop.x64.dll", "WebBrowserInterop.x86.dll"):
            _p = os.path.join(_wv_lib, _dll_name)
            if os.path.exists(_p):
                binaries += [(_p, ".")]

    # WebView2Loader.dll por arquitectura (edgechromium.py:50 agrega el dir al PATH)
    for _arch in ("win-x64", "win-x86", "win-arm64"):
        _p = os.path.join(_site, "webview", "lib", "runtimes", _arch, "native", "WebView2Loader.dll")
        if os.path.exists(_p):
            binaries += [(_p, _arch)]

    # ClrLoader.dll — puente nativo pythonnet ↔ .NET
    _clr_dir = os.path.join(_site, "clr_loader", "ffi", "dlls")
    if os.path.isdir(_clr_dir):
        for _arch in ("amd64", "x86"):
            _p = os.path.join(_clr_dir, _arch, "ClrLoader.dll")
            if os.path.exists(_p):
                binaries += [(_p, ".")]

    # Python.Runtime.dll — runtime bridge de pythonnet
    _runtime = os.path.join(_site, "pythonnet", "runtime", "Python.Runtime.dll")
    if os.path.exists(_runtime):
        binaries += [(_runtime, ".")]

# UI embebida.
datas += [("static", "static")]
# pyproject.toml para que config._get_version() lo encuentre en el binario.
datas += [("pyproject.toml", ".")]

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
