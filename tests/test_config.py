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
