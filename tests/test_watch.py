"""Tests para watch_loop (deteccion de videos nuevos en canales vigilados).

Regresion principal: watch_loop debe actualizar el modulo-global
``state._latest_watch_ts`` cuando despacha videos nuevos. Sin la declaracion
``global`` la asignacion creaba una variable local y el global quedaba en 0.0,
dejando muerta la notificacion del tray de desktop (desktop.py lo lee).
"""

import asyncio
from unittest.mock import patch

import pytest

import state as state_mod
from db import Database
from state import AppState


@pytest.fixture
def watch_state(tmp_path):
    db = Database(":memory:")
    st = AppState(db, tmp_path)
    yield st
    db.close()


def _stop_after_one_tick():
    """asyncio.sleep que deja correr el cuerpo una vez y luego corta el loop."""
    call_count = 0

    async def fake_sleep(delay):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError("stop after one tick")

    return fake_sleep


@pytest.mark.asyncio
async def test_watch_loop_actualiza_latest_watch_ts_global(watch_state, monkeypatch):
    """Un dispatch de watch debe escribir el modulo-global, no una local."""
    watch_state.db.insert_channel("https://example.com/canal", quality="best", interval_minutes=60)

    # El canal recien insertado tiene last_checked NULL -> esta 'due'.
    # Fakeamos la deteccion para devolver un video nuevo y el spawn para no bajar nada.
    monkeypatch.setattr(
        "download._check_channel_watch",
        lambda st, ch: [{"url": "https://example.com/v1", "extractor": "Generic",
                         "video_id": "v1", "title": "Nuevo"}],
    )
    monkeypatch.setattr(type(watch_state), "_spawn_download", lambda *a, **kw: None)

    # Reset del global a un centinela conocido para detectar la escritura real.
    monkeypatch.setattr(state_mod, "_latest_watch_ts", 0.0, raising=False)

    with patch("asyncio.sleep", side_effect=_stop_after_one_tick()):
        try:
            await watch_state.watch_loop()
        except asyncio.CancelledError:
            pass

    assert state_mod._latest_watch_ts > 0.0, (
        "watch_loop no actualizo el modulo-global _latest_watch_ts; "
        "falta la declaracion 'global' (el tray de desktop nunca veria el dispatch)"
    )


@pytest.mark.asyncio
async def test_watch_loop_no_despacha_si_no_hay_videos(watch_state, monkeypatch):
    """Sin videos nuevos, el global no se toca (no hay falso positivo)."""
    watch_state.db.insert_channel("https://example.com/canal", quality="best", interval_minutes=60)
    monkeypatch.setattr("download._check_channel_watch", lambda st, ch: [])
    monkeypatch.setattr(type(watch_state), "_spawn_download", lambda *a, **kw: None)
    monkeypatch.setattr(state_mod, "_latest_watch_ts", 0.0, raising=False)

    with patch("asyncio.sleep", side_effect=_stop_after_one_tick()):
        try:
            await watch_state.watch_loop()
        except asyncio.CancelledError:
            pass

    assert state_mod._latest_watch_ts == 0.0
