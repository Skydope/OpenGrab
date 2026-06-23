import pytest


# ----------------------------- per-file size ----------------------------- #
def test_enforce_size_deletes_and_raises(tmp_path):
    from download import _enforce_size

    f = tmp_path / "big.mp4"
    f.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB
    with pytest.raises(RuntimeError):
        _enforce_size(f, 1)  # límite 1 MB
    assert not f.exists()  # borrado


def test_enforce_size_ok_under_limit(tmp_path):
    from download import _enforce_size

    f = tmp_path / "ok.mp4"
    f.write_bytes(b"x" * 1024)
    _enforce_size(f, 1)  # bajo el límite → no levanta
    assert f.exists()


def test_enforce_size_disabled_when_zero(tmp_path):
    from download import _enforce_size

    f = tmp_path / "any.mp4"
    f.write_bytes(b"x" * (5 * 1024 * 1024))
    _enforce_size(f, 0)  # 0 = desactivado
    assert f.exists()


# ----------------------------- total budget ------------------------------ #
def test_current_usage_bytes_counts_files(app_state):
    (app_state.out_dir / "a.bin").write_bytes(b"x" * 1000)
    sub = app_state.out_dir / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"x" * 2000)
    assert app_state.current_usage_bytes() >= 3000


def test_max_total_mb_refuses_new_job(client, app_state, monkeypatch):
    # api_create_job now uses state.resolve("max_total_mb")
    monkeypatch.setattr(type(app_state), "resolve", lambda self, k, d, t=int: (1, "env") if k == "max_total_mb" else (d, "default"))
    (app_state.out_dir / "fill.bin").write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB

    r = client.post(
        "/api/jobs", json={"url": "https://youtu.be/abc", "quality": "best"}
    )
    assert r.status_code == 507


def test_max_total_mb_allows_under_budget(client, app_state, monkeypatch):
    monkeypatch.setattr(type(app_state), "resolve", lambda self, k, d, t=int: (100, "env") if k == "max_total_mb" else (d, "default"))
    monkeypatch.setattr("routes._run_download", lambda *a, **kw: None)
    r = client.post(
        "/api/jobs", json={"url": "https://youtu.be/abc", "quality": "best"}
    )
    assert r.status_code == 200


# ------------------------------- config ---------------------------------- #
def test_limits_default_unlimited(monkeypatch):
    import config as _config

    # Clear ini values so defaults apply (env vars already deleted by conftest)
    monkeypatch.delenv("OPENGRAB_MAX_TOTAL_MB", raising=False)
    monkeypatch.delenv("OPENGRAB_MAX_SIZE_MB", raising=False)
    _config._ini.pop("max_total_mb", None)
    _config._ini.pop("max_size_mb", None)
    # Force reload of the computed constants
    _config.MAX_TOTAL_MB = _config._int_env("OPENGRAB_MAX_TOTAL_MB", _config._ini_int("max_total_mb", 0), min_val=0)
    _config.MAX_SIZE_MB = _config._int_env("OPENGRAB_MAX_SIZE_MB", _config._ini_int("max_size_mb", 0), min_val=0)
    assert _config.MAX_TOTAL_MB == 0
    assert _config.MAX_SIZE_MB == 0
