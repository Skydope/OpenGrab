"""Feature Clips: descarga de rangos de tiempo (clips.md).

Cubre: construcción de opts de yt-dlp, formato de timestamp para filename,
validación del endpoint, persistencia/migración de schema v6, propagación por
spawn_download y requeue, y serialización para la UI.
"""
import sqlite3
from unittest.mock import patch

from db import SCHEMA_VERSION, Database
from download import DownloadContext, _build_ydl_opts, _fmt_ts


def _ctx(**kw):
    base = dict(job_id="j1", url="https://youtu.be/abc", quality="best")
    base.update(kw)
    return DownloadContext(**base)


# ------------------------------------------------------------------ #
# _build_ydl_opts
# ------------------------------------------------------------------ #
def test_opts_sin_clip_no_agrega_ranges(tmp_path):
    opts = _build_ydl_opts(_ctx(), tmp_path, 0, lambda d: None)
    assert "download_ranges" not in opts
    assert "force_keyframes_at_cuts" not in opts


def test_opts_con_clip_configura_ranges_y_keyframes(tmp_path):
    ctx = _ctx(clip_start=90.0, clip_end=165.5)
    opts = _build_ydl_opts(ctx, tmp_path, 0, lambda d: None)
    assert opts["force_keyframes_at_cuts"] is True
    ranges = opts["download_ranges"]
    # Contrato de yt-dlp: callback (info_dict, ydl) -> [{start_time, end_time}]
    sections = list(ranges({"duration": 600}, None))
    assert len(sections) == 1
    assert sections[0]["start_time"] == 90.0
    assert sections[0]["end_time"] == 165.5


def test_opts_clip_y_audio_coexisten(tmp_path):
    ctx = _ctx(quality="audio", clip_start=10.0, clip_end=20.0)
    opts = _build_ydl_opts(ctx, tmp_path, 0, lambda d: None)
    assert "download_ranges" in opts
    pps = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in pps


def test_is_clip_requiere_ambos_extremos():
    assert _ctx(clip_start=1.0, clip_end=2.0).is_clip is True
    assert _ctx(clip_start=1.0).is_clip is False
    assert _ctx(clip_end=2.0).is_clip is False
    assert _ctx().is_clip is False


# ------------------------------------------------------------------ #
# _fmt_ts (sufijo de filename)
# ------------------------------------------------------------------ #
def test_fmt_ts():
    assert _fmt_ts(0) == "00.00"
    assert _fmt_ts(5) == "00.05"
    assert _fmt_ts(90) == "01.30"
    assert _fmt_ts(3725) == "1.02.05"
    # float trunca al segundo entero
    assert _fmt_ts(90.9) == "01.30"


def test_fmt_ts_es_filename_safe():
    from download import _safe_name
    suffix = f"[{_fmt_ts(90)}-{_fmt_ts(165)}]"
    # _safe_name no debe mutilar el sufijo (sin ':' ni chars prohibidos)
    assert _safe_name(f"Video {suffix}") == f"Video {suffix}"


# ------------------------------------------------------------------ #
# Validación del endpoint (POST /api/jobs)
# ------------------------------------------------------------------ #
def _post_job(client, **extra):
    body = {"url": "https://youtu.be/abc", "quality": "best"}
    body.update(extra)
    return client.post("/api/jobs", json=body)


def test_api_clip_solo_un_extremo_400(client):
    r = _post_job(client, clip_start=10.0)
    assert r.status_code == 400
    r = _post_job(client, clip_end=10.0)
    assert r.status_code == 400


def test_api_clip_rango_invalido_400(client):
    assert _post_job(client, clip_start=-1.0, clip_end=10.0).status_code == 400
    assert _post_job(client, clip_start=10.0, clip_end=10.0).status_code == 400
    assert _post_job(client, clip_start=20.0, clip_end=10.0).status_code == 400


def test_api_clip_menor_a_1s_400(client):
    assert _post_job(client, clip_start=10.0, clip_end=10.5).status_code == 400


def test_api_clip_valido_crea_job_y_persiste(client, app_state):
    with patch.object(app_state, "spawn_download") as spawn:
        r = _post_job(client, clip_start=90.0, clip_end=165.5)
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        # Persistencia en DB (la fuente del requeue)
        row = app_state.db.get_job(job_id)
        assert row["clip_start"] == 90.0
        assert row["clip_end"] == 165.5
        # Propagación a spawn_download
        _, kwargs = spawn.call_args
        assert kwargs["clip_start"] == 90.0
        assert kwargs["clip_end"] == 165.5


def test_api_sin_clip_persiste_null(client, app_state):
    with patch.object(app_state, "spawn_download"):
        r = _post_job(client)
        assert r.status_code == 200
        row = app_state.db.get_job(r.json()["job_id"])
        assert row["clip_start"] is None
        assert row["clip_end"] is None


# ------------------------------------------------------------------ #
# Migración v5 -> v6
# ------------------------------------------------------------------ #
def test_migracion_v5_a_v6(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    # Tabla jobs mínima como existía en v5 (sin columnas de clip)
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, url TEXT NOT NULL, "
        "quality TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, "
        "workdir TEXT, incognito INTEGER NOT NULL DEFAULT 0, "
        "playlist_subdir TEXT, subs INTEGER NOT NULL DEFAULT 0, "
        "thumb INTEGER NOT NULL DEFAULT 0, infojson INTEGER NOT NULL DEFAULT 0)"
    )
    con.execute(
        "INSERT INTO jobs (id, url, quality, status, created) "
        "VALUES ('old1', 'https://youtu.be/x', 'best', 'queued', 1.0)"
    )
    con.execute("PRAGMA user_version=5")
    con.commit()
    con.close()

    db = Database(path)
    try:
        assert db.schema_version() == SCHEMA_VERSION
        row = db.get_job("old1")
        assert row["clip_start"] is None
        assert row["clip_end"] is None
        # insert con clip funciona post-migración
        db.insert_job("new1", "https://youtu.be/y", "best",
                      clip_start=5.0, clip_end=15.0)
        row = db.get_job("new1")
        assert row["clip_start"] == 5.0
        assert row["clip_end"] == 15.0
    finally:
        db.close()


# ------------------------------------------------------------------ #
# Requeue: get_queued expone el clip para dispatch_loop
# ------------------------------------------------------------------ #
def test_get_queued_incluye_clip(tmp_path):
    db = Database(tmp_path / "q.db")
    try:
        db.insert_job("c1", "https://youtu.be/x", "best",
                      clip_start=30.0, clip_end=60.0)
        rows = db.get_queued(limit=5)
        assert rows and rows[0]["clip_start"] == 30.0
        assert rows[0]["clip_end"] == 60.0
    finally:
        db.close()


# ------------------------------------------------------------------ #
# Serialización para la UI
# ------------------------------------------------------------------ #
def test_serialize_job_expone_clip():
    from models import Job
    from routers.jobs import _serialize_job

    job = Job(id="s1", clip_start=1.5, clip_end=9.0)
    data = _serialize_job(job)
    assert data["clip_start"] == 1.5
    assert data["clip_end"] == 9.0

    data = _serialize_job(Job(id="s2"))
    assert data["clip_start"] is None
    assert data["clip_end"] is None


# ------------------------------------------------------------------ #
# Bug del build desktop: FFmpegFD.available() ignora ffmpeg_location
# ------------------------------------------------------------------ #
def test_ensure_ffmpeg_on_path_prependea(tmp_path, monkeypatch):
    from download import _ensure_ffmpeg_on_path

    fake = tmp_path / "bundle" / "ffmpeg"
    fake.parent.mkdir()
    fake.write_bytes(b"")
    monkeypatch.setenv("PATH", "/usr/bin")

    _ensure_ffmpeg_on_path(fake)
    import os
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == str(fake.parent)
    assert "/usr/bin" in parts

    # Idempotente: segunda llamada no duplica
    _ensure_ffmpeg_on_path(fake)
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts.count(str(fake.parent)) == 1


def test_ensure_ffmpeg_on_path_noop_sin_binario(tmp_path, monkeypatch):
    from download import _ensure_ffmpeg_on_path

    monkeypatch.setenv("PATH", "/usr/bin")
    _ensure_ffmpeg_on_path(tmp_path / "no-existe" / "ffmpeg")
    import os
    assert os.environ["PATH"] == "/usr/bin"


# ------------------------------------------------------------------ #
# Estrategia de corte: clip_precise / audio / copy
# ------------------------------------------------------------------ #
def test_opts_clip_audio_nunca_reencodea(tmp_path):
    # Audio: -c copy ya es casi exacto (frames ~23ms); force_keyframes es
    # puro costo porque el extract a mp3 re-encodea después igual.
    ctx = _ctx(quality="audio", clip_start=10.0, clip_end=20.0,
               clip_precise=True)
    opts = _build_ydl_opts(ctx, tmp_path, 0, lambda d: None)
    assert "download_ranges" in opts
    assert "force_keyframes_at_cuts" not in opts
    assert "external_downloader_args" not in opts


def test_opts_clip_video_preciso_inyecta_preset(tmp_path):
    ctx = _ctx(clip_start=10.0, clip_end=20.0, clip_precise=True)
    opts = _build_ydl_opts(ctx, tmp_path, 0, lambda d: None)
    assert opts["force_keyframes_at_cuts"] is True
    assert opts["external_downloader_args"] == {"ffmpeg": ["-preset", "veryfast"]}


def test_opts_clip_video_rapido_usa_copy(tmp_path):
    # Sin precise, no seteamos force_keyframes: FFmpegFD cae a -c copy.
    ctx = _ctx(clip_start=10.0, clip_end=20.0, clip_precise=False)
    opts = _build_ydl_opts(ctx, tmp_path, 0, lambda d: None)
    assert "download_ranges" in opts
    assert "force_keyframes_at_cuts" not in opts
    assert "external_downloader_args" not in opts


# ------------------------------------------------------------------ #
# clip_precise: persistencia, migración y propagación
# ------------------------------------------------------------------ #
def test_migracion_v6_a_v7(tmp_path):
    path = tmp_path / "v6.db"
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, url TEXT NOT NULL, "
        "quality TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, "
        "workdir TEXT, incognito INTEGER NOT NULL DEFAULT 0, "
        "playlist_subdir TEXT, subs INTEGER NOT NULL DEFAULT 0, "
        "thumb INTEGER NOT NULL DEFAULT 0, infojson INTEGER NOT NULL DEFAULT 0, "
        "clip_start REAL, clip_end REAL)"
    )
    con.execute(
        "INSERT INTO jobs (id, url, quality, status, created, clip_start, clip_end) "
        "VALUES ('c6', 'https://youtu.be/x', 'best', 'queued', 1.0, 5.0, 15.0)"
    )
    con.execute("PRAGMA user_version=6")
    con.commit()
    con.close()

    db = Database(path)
    try:
        assert db.schema_version() == SCHEMA_VERSION
        # Fila vieja: default 1 = corte exacto (comportamiento previo al toggle)
        row = db.get_job("c6")
        assert row["clip_precise"] == 1
        db.insert_job("c7", "https://youtu.be/y", "best",
                      clip_start=1.0, clip_end=9.0, clip_precise=False)
        assert db.get_job("c7")["clip_precise"] == 0
    finally:
        db.close()


def test_api_clip_precise_persiste_y_propaga(client, app_state):
    with patch.object(app_state, "spawn_download") as spawn:
        r = _post_job(client, clip_start=5.0, clip_end=15.0, clip_precise=False)
        assert r.status_code == 200
        row = app_state.db.get_job(r.json()["job_id"])
        assert row["clip_precise"] == 0
        _, kwargs = spawn.call_args
        assert kwargs["clip_precise"] is False


def test_get_queued_incluye_clip_precise(tmp_path):
    db = Database(tmp_path / "q7.db")
    try:
        db.insert_job("p1", "https://youtu.be/x", "best",
                      clip_start=1.0, clip_end=9.0, clip_precise=False)
        rows = db.get_queued(limit=5)
        assert rows and rows[0]["clip_precise"] == 0
    finally:
        db.close()
