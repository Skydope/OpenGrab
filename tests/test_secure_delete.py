"""Tests unitarios de secure_delete.wipe_file y secure_delete.wipe_workdir."""

from pathlib import Path

from secure_delete import wipe_file, wipe_workdir


def test_wipe_file_deletes(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    assert f.exists()
    wipe_file(str(f))
    assert not f.exists()


def test_wipe_file_noop_on_missing(tmp_path: Path):
    f = tmp_path / "nope.txt"
    wipe_file(str(f))  # no raise


def test_wipe_file_force_overwrite(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("secure_delete.config.SECURE_DELETE", False)
    f = tmp_path / "big.bin"
    f.write_bytes(b"A" * 2048)
    wipe_file(str(f), force=True)
    assert not f.exists()


def test_wipe_file_zero_size(tmp_path: Path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    wipe_file(str(f))
    assert not f.exists()


def test_wipe_workdir_deletes_tree(tmp_path: Path):
    d = tmp_path / "wd"
    d.mkdir()
    (d / "a.txt").write_text("a")
    sub = d / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("b")

    wipe_workdir(str(d))
    assert not d.exists()


def test_wipe_workdir_noop_on_missing(tmp_path: Path):
    wipe_workdir(str(tmp_path / "nope"))  # no raise


def test_wipe_workdir_noop_on_file(tmp_path: Path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    wipe_workdir(str(f))
    assert f.exists()  # no-op, no borra archivos sueltos
