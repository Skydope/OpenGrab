"""Configuración de logging para OpenGrab: texto legible o JSON estructurado.

El formato se elige por env (``OPENGRAB_LOG_FORMAT``):

- ``text`` (default): una línea por registro, pensado para dev local / consola.
- ``json``: un objeto JSON por línea (NDJSON), pensado para ingesta por
  Grafana Alloy/Loki. Los campos pasados vía ``logging``'s ``extra=`` se
  promueven a claves top-level, así Loki los indexa como labels filtrables
  (``status``, ``path``, ``duration_ms``, ...) en vez de regexear el mensaje.

Este módulo no toca red ni disco; es trivialmente testeable en aislamiento.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from typing import Any

# Atributos estándar de LogRecord: todo lo que NO esté acá y venga en
# ``record.__dict__`` se considera un campo "extra" y se promueve a clave JSON.
_RESERVED: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
)

_TEXT_FORMAT = "%(asctime)s %(levelname)s [opengrab] %(message)s"
_TEXT_DATEFMT = "%Y-%m-%d %H:%M:%S"


class JsonFormatter(logging.Formatter):
    """Formatea cada LogRecord como una línea JSON (NDJSON).

    Claves base: ``ts`` (ISO-8601 UTC, ms), ``level``, ``logger``, ``msg``.
    Los campos ``extra`` se mergean como claves top-level. ``exc``/``stack``
    se agregan solo si el registro los trae. ``ensure_ascii=False`` para no
    mojibakear el español; ``default=str`` para no romper ante objetos raros.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(
            record.created, tz=_dt.UTC
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Promover los campos extra (todo lo no-reservado y no-privado).
        for key, val in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = val

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(fmt: str = "text", level: str = "INFO") -> None:
    """Instala un único handler en el root logger con el formato pedido.

    Idempotente: limpia los handlers previos antes de instalar el nuevo, así
    re-llamarla (p.ej. en tests) no acumula handlers ni duplica líneas.
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT, datefmt=_TEXT_DATEFMT))
    root.addHandler(handler)

    root.setLevel(getattr(logging, level.upper(), logging.INFO))
