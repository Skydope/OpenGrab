"""Tests de export/import de backup JSON.

Regresión principal: el import de history pasaba ``created`` a update_job,
que no la tiene en la whitelist _UPDATABLE → ValueError → CADA entrada de
history caía al bucket de errores y el import reportaba history=0 siempre.
"""
from __future__ import annotations


def _export(client):
    r = client.get("/api/backup/export")
    assert r.status_code == 200
    return r.json()


def test_export_shape(client):
    body = _export(client)
    assert body["version"] == 1
    assert set(body) >= {"settings", "history", "channels", "exported_at"}


def test_import_history_roundtrip(client):
    """Un backup con history debe importarse entero, con created preservado."""
    payload = {
        "version": 1,
        "settings": {},
        "channels": [],
        "history": [
            {
                "id": "bkp00000001", "url": "https://example.com/v1",
                "quality": "best", "title": "Video uno",
                "filename": "uno.mp4", "filepath": "/x/uno.mp4",
                "mime": "video/mp4", "size": 111,
                "created": 1700000000.0, "completed": 1700000100,
            },
            {
                "id": "bkp00000002", "url": "https://example.com/v2",
                "quality": "720p", "title": "Video dos",
                "filename": "dos.mp4", "filepath": "/x/dos.mp4",
                "mime": "video/mp4", "size": 222,
                "created": 1700001000.0, "completed": 1700001100,
            },
        ],
    }
    r = client.post("/api/backup/import", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body.get("errors") in (None, [],), f"import con errores: {body}"
    assert body["imported"]["history"] == 2

    db = client.app.state.opengrab.db
    row = db.get_job("bkp00000001")
    assert row is not None
    assert row["status"] == "done"
    assert row["title"] == "Video uno"
    assert row["created"] == 1700000000.0  # preservado del backup, no time.time()


def test_import_history_existing_entries_skipped(client):
    db = client.app.state.opengrab.db
    db.insert_job("bkp00000009", "https://example.com/ya", "best")
    payload = {
        "version": 1, "settings": {}, "channels": [],
        "history": [{"id": "bkp00000009", "url": "https://example.com/ya",
                     "quality": "best", "created": 1.0}],
    }
    r = client.post("/api/backup/import", json=payload)
    assert r.status_code == 200
    assert r.json()["imported"]["history"] == 0  # ya existía: skip, no error


def test_import_bad_version_rejected(client):
    r = client.post("/api/backup/import", json={"version": 99})
    assert r.status_code == 400
