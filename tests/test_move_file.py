"""Tests de la feature "Guardar en…": ``AppState.move_job_file`` y el endpoint
``POST /api/jobs/{id}/move``.

El move es server-side (el archivo ya vive en el FS del servidor) y conserva el
nombre original deduplicando si hay colisión.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from db import Database
from models import Job
from state import AppState


@pytest.fixture
def state(tmp_path: Path) -> AppState:
    db = Database(":memory:")
    return AppState(db, tmp_path / "out")


def _done_job(state: AppState, job_id: str, name: str = "video.mp4") -> Path:
    """Crea un archivo en out_dir y un Job ``done`` que lo apunta."""
    state.out_dir.mkdir(parents=True, exist_ok=True)
    src = state.out_dir / name
    src.write_bytes(b"contenido de prueba")
    job = Job(id=job_id, status="done", filename=name, filepath=str(src),
              created=time.time(), finished=time.time())
    state.jobs[job_id] = job
    return src


# --------------------------------------------------------------------------- #
# move_job_file (unidad)
# --------------------------------------------------------------------------- #

class TestMoveJobFile:
    def test_mueve_y_actualiza_filepath(self, state: AppState, tmp_path: Path) -> None:
        src = _done_job(state, "j1")
        dest = tmp_path / "destino"

        target = state.library.move_job_file("j1", dest)

        assert target == dest / "video.mp4"
        assert target.exists()
        assert not src.exists()
        assert state.jobs["j1"].filepath == str(target)

    def test_crea_directorio_destino_si_no_existe(self, state: AppState, tmp_path: Path) -> None:
        _done_job(state, "j1")
        dest = tmp_path / "a" / "b" / "c"
        assert not dest.exists()

        target = state.library.move_job_file("j1", dest)

        assert target.parent == dest
        assert dest.is_dir()

    def test_deduplica_si_hay_colision(self, state: AppState, tmp_path: Path) -> None:
        src = _done_job(state, "j1", "video.mp4")
        dest = tmp_path / "destino"
        dest.mkdir()
        (dest / "video.mp4").write_bytes(b"ya existe")

        target = state.library.move_job_file("j1", dest)

        assert target == dest / "video (1).mp4"
        assert target.exists()
        assert (dest / "video.mp4").read_bytes() == b"ya existe"  # no se pisó
        assert not src.exists()

    def test_idempotente_si_ya_esta_en_destino(self, state: AppState) -> None:
        src = _done_job(state, "j1")
        dest = state.out_dir  # mismo directorio donde ya vive

        target = state.library.move_job_file("j1", dest)

        assert target == src
        assert src.exists()

    def test_persiste_en_db_si_la_fila_existe(self, state: AppState, tmp_path: Path) -> None:
        src = _done_job(state, "j1")
        # Insertar la fila del job y marcarla done con su filepath.
        state.db.insert_job("j1", "https://x/y", "best", status="done")
        state.db.update_job("j1", filepath=str(src), filename="video.mp4")
        dest = tmp_path / "destino"

        target = state.library.move_job_file("j1", dest)

        row = state.db.get_job("j1")
        assert row is not None
        assert row["filepath"] == str(target)

    def test_rechaza_job_no_terminado(self, state: AppState, tmp_path: Path) -> None:
        src = _done_job(state, "j1")
        state.jobs["j1"].status = "downloading"

        with pytest.raises(ValueError):
            state.library.move_job_file("j1", tmp_path / "destino")
        assert src.exists()  # no se tocó

    def test_rechaza_job_inexistente(self, state: AppState, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            state.library.move_job_file("nope", tmp_path / "destino")

    def test_rechaza_archivo_ausente_en_disco(self, state: AppState, tmp_path: Path) -> None:
        src = _done_job(state, "j1")
        src.unlink()

        with pytest.raises(FileNotFoundError):
            state.library.move_job_file("j1", tmp_path / "destino")

    def test_rechaza_destino_que_es_archivo(self, state: AppState, tmp_path: Path) -> None:
        _done_job(state, "j1")
        dest = tmp_path / "soy_un_archivo"
        dest.write_bytes(b"x")

        with pytest.raises(NotADirectoryError):
            state.library.move_job_file("j1", dest)


# --------------------------------------------------------------------------- #
# Endpoint POST /api/jobs/{id}/move (integración)
# --------------------------------------------------------------------------- #

@pytest.fixture
def _desktop_mode(client_no_auth, monkeypatch):
    """El endpoint /move es desktop-only: los tests de camino feliz lo simulan."""
    import routers.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "IS_DESKTOP", True)


class TestMoveEndpoint:
    def test_move_ok(self, client_no_auth, _desktop_mode, tmp_path: Path) -> None:
        state: AppState = client_no_auth.app.state.opengrab
        src = _done_job(state, "j1")
        dest = tmp_path / "elegida"

        r = client_no_auth.post("/api/jobs/j1/move", json={"dest": str(dest)})

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["filepath"] == str(dest / "video.mp4")
        assert (dest / "video.mp4").exists()
        assert not src.exists()

    def test_move_falta_dest(self, client_no_auth, _desktop_mode) -> None:
        state: AppState = client_no_auth.app.state.opengrab
        _done_job(state, "j1")
        r = client_no_auth.post("/api/jobs/j1/move", json={})
        assert r.status_code == 400

    def test_move_job_no_listo_409(self, client_no_auth, _desktop_mode, tmp_path: Path) -> None:
        state: AppState = client_no_auth.app.state.opengrab
        _done_job(state, "j1")
        state.jobs["j1"].status = "downloading"
        r = client_no_auth.post("/api/jobs/j1/move", json={"dest": str(tmp_path / "d")})
        assert r.status_code == 409

    def test_move_job_inexistente_410(self, client_no_auth, _desktop_mode, tmp_path: Path) -> None:
        r = client_no_auth.post("/api/jobs/nope/move", json={"dest": str(tmp_path / "d")})
        assert r.status_code == 410

    def test_move_bloqueado_en_modo_server(self, client_no_auth, tmp_path: Path) -> None:
        """Regresión: en modo server, /move seria una primitiva de escritura
        en paths arbitrarios del FS del servidor elegidos por un cliente
        remoto. Debe rechazarse con 409, igual que open-folder."""
        state: AppState = client_no_auth.app.state.opengrab
        src = _done_job(state, "j1")
        dest = tmp_path / "no_deberia_crearse"

        r = client_no_auth.post("/api/jobs/j1/move", json={"dest": str(dest)})

        assert r.status_code == 409
        assert src.exists()  # el archivo no se movió
        assert not dest.exists()  # el directorio no se creó

    def test_move_destino_es_archivo_400(self, client_no_auth, _desktop_mode, tmp_path: Path) -> None:
        state: AppState = client_no_auth.app.state.opengrab
        _done_job(state, "j1")
        bad = tmp_path / "archivo"
        bad.write_bytes(b"x")
        r = client_no_auth.post("/api/jobs/j1/move", json={"dest": str(bad)})
        assert r.status_code == 400
