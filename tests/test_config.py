"""Tests para la lectura de config.ini (config.py — desktop)."""

from __future__ import annotations

from pathlib import Path


import config


def _write_ini(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.ini"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------- _load_ini ---------------------------------- #
def test_load_ini_parses_valid_file(tmp_path, monkeypatch):
    ini = _write_ini(tmp_path, "[opengrab]\ndownload_dir = C:\\Videos\nport = 8080\n")
    monkeypatch.setenv("OPENGRAB_CONFIG", str(ini))
    assert config._load_ini() == {"download_dir": "C:\\Videos", "port": "8080"}


def test_load_ini_returns_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENGRAB_CONFIG", str(tmp_path / "nonexistent.ini"))
    assert config._load_ini() == {}


def test_load_ini_returns_empty_when_section_missing(tmp_path, monkeypatch):
    ini = _write_ini(tmp_path, "[other]\nkey = val\n")
    monkeypatch.setenv("OPENGRAB_CONFIG", str(ini))
    assert config._load_ini() == {}


def test_load_ini_returns_empty_on_malformed_file(tmp_path, monkeypatch):
    ini = _write_ini(tmp_path, "esto no es un ini valido {{{")
    monkeypatch.setenv("OPENGRAB_CONFIG", str(ini))
    assert config._load_ini() == {}


# ---------------------------- _ini_int ----------------------------------- #
def test_ini_int_returns_default_when_key_missing():
    assert config._ini_int("no_existe", 42) == 42


def test_ini_int_returns_default_when_empty():
    assert config._ini_int("", 100) == 100


# ----------------------- _default_download_dir --------------------------- #
def test_default_download_dir_desktop_is_absolute_home(monkeypatch):
    """En modo desktop el fallback es absoluto (home), nunca CWD-relative.

    Regresion AppImage Errno 30: si fuera "./downloads" relativo, en un
    AppImage resolveria contra el squashfs read-only. Blinda OUT_DIR contra
    el orden de imports (config puede importarse antes de _setup_env).
    """
    monkeypatch.setattr(config, "IS_DESKTOP", True)
    result = config._default_download_dir()
    assert Path(result).is_absolute()
    assert result.endswith("OpenGrab")
    assert "Downloads" in result


def test_default_download_dir_dev_is_cwd_relative(monkeypatch):
    """Sin desktop (Docker/dev) se mantiene el contrato: ./downloads."""
    monkeypatch.setattr(config, "IS_DESKTOP", False)
    assert config._default_download_dir() == "./downloads"


# ---------------------------- _load_dotenv -------------------------------- #
_DOTENV_KEYS = ("OG_T_PLAIN", "OG_T_QUOTED", "OG_T_SINGLE", "OG_T_EXPORT", "OG_T_EMPTY", "OG_T_KEEP")


def _dotenv(tmp_path: Path, monkeypatch, content: str) -> None:
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    monkeypatch.setenv("OPENGRAB_ENV_FILE", str(p))
    monkeypatch.setenv("OPENGRAB_DESKTOP", "")
    for k in _DOTENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_load_dotenv_parses_values(tmp_path, monkeypatch):
    import os

    _dotenv(
        tmp_path,
        monkeypatch,
        "# comentario\n\nOG_T_PLAIN=abc\nOG_T_QUOTED=\"con espacios\"\n"
        "OG_T_SINGLE='x'\nexport OG_T_EXPORT=1\nOG_T_EMPTY=\nlinea sin igual\n",
    )
    config._load_dotenv()
    assert os.environ["OG_T_PLAIN"] == "abc"
    assert os.environ["OG_T_QUOTED"] == "con espacios"
    assert os.environ["OG_T_SINGLE"] == "x"
    assert os.environ["OG_T_EXPORT"] == "1"
    assert os.environ["OG_T_EMPTY"] == ""


def test_load_dotenv_real_env_wins(tmp_path, monkeypatch):
    import os

    _dotenv(tmp_path, monkeypatch, "OG_T_KEEP=desde_dotenv\n")
    monkeypatch.setenv("OG_T_KEEP", "desde_entorno")
    config._load_dotenv()
    assert os.environ["OG_T_KEEP"] == "desde_entorno"


def test_load_dotenv_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENGRAB_ENV_FILE", str(tmp_path / "no-existe.env"))
    monkeypatch.setenv("OPENGRAB_DESKTOP", "")
    config._load_dotenv()  # no debe lanzar


def test_load_dotenv_skipped_in_desktop(tmp_path, monkeypatch):
    import os

    _dotenv(tmp_path, monkeypatch, "OG_T_PLAIN=abc\n")
    monkeypatch.setenv("OPENGRAB_DESKTOP", "1")
    config._load_dotenv()
    assert "OG_T_PLAIN" not in os.environ
