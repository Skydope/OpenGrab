"""Tests del logging estructurado (logging_setup)."""

import json
import logging
import sys

import pytest

from logging_setup import JsonFormatter, configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Snapshot/restore del root logger: configure_logging muta global state."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _record(msg="hola", level=logging.INFO, args=(), exc_info=None, extra=None):
    rec = logging.LogRecord(
        name="opengrab", level=level, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=exc_info,
    )
    for key, val in (extra or {}).items():
        setattr(rec, key, val)
    return rec


def test_json_formatter_base_keys():
    data = json.loads(JsonFormatter().format(_record(msg="hola mundo")))
    assert data["level"] == "INFO"
    assert data["logger"] == "opengrab"
    assert data["msg"] == "hola mundo"
    assert data["ts"].endswith("Z")  # UTC con sufijo Z


def test_json_formatter_renders_printf_args():
    out = JsonFormatter().format(_record(msg="%s %d", args=("GET", 200)))
    assert json.loads(out)["msg"] == "GET 200"


def test_json_formatter_promotes_extra_to_top_level():
    data = json.loads(JsonFormatter().format(
        _record(extra={"path": "/api/x", "status": 200, "duration_ms": 12.3})
    ))
    assert data["path"] == "/api/x"
    assert data["status"] == 200
    assert data["duration_ms"] == 12.3


def test_json_formatter_preserves_non_ascii():
    out = JsonFormatter().format(_record(msg="descargá señor ñandú"))
    assert "descargá señor ñandú" in out  # ensure_ascii=False
    assert json.loads(out)["msg"] == "descargá señor ñandú"


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        out = JsonFormatter().format(
            _record(msg="falló", level=logging.ERROR, exc_info=sys.exc_info())
        )
    data = json.loads(out)
    assert "ValueError" in data["exc"]
    assert "boom" in data["exc"]


def test_json_formatter_emits_single_line():
    out = JsonFormatter().format(_record(msg="línea\ncon salto"))
    # El JSON serializa el \n escapado: el output no contiene un salto real.
    assert "\n" not in out
    assert json.loads(out)["msg"] == "línea\ncon salto"


def test_configure_logging_json_installs_json_formatter():
    configure_logging("json", "INFO")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_configure_logging_text_is_not_json():
    configure_logging("text", "DEBUG")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.DEBUG


def test_configure_logging_is_idempotent():
    configure_logging("json", "INFO")
    configure_logging("json", "INFO")
    assert len(logging.getLogger().handlers) == 1  # no acumula


def test_configure_logging_unknown_level_falls_back_to_info():
    configure_logging("text", "NONSENSE")
    assert logging.getLogger().level == logging.INFO
