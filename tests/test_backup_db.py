"""Tests for read_db.backup_db. No QCoDeS or hardware needed: a small
SQLite file with the same runs/experiments tables stands in for
mm4250_sweeps.db. Run from the repo root:

    python -m pytest tests/test_backup_db.py
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "measurements"))

from read_db import _existing_backups, backup_db  # noqa: E402


def make_db(path):
    """A WAL-mode database shaped like QCoDeS's, left with an open writer."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE experiments (exp_id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("CREATE TABLE runs (run_id INTEGER PRIMARY KEY, exp_id INTEGER, "
                "name TEXT, guid TEXT, result_counter INTEGER, touchstone_path TEXT)")
    con.execute("INSERT INTO experiments VALUES (1, '20261015_3K_SN0077_cal')")
    con.commit()
    return con


def add_run(con, run_id, points=1001):
    con.execute("INSERT INTO runs VALUES (?, 1, ?, ?, ?, NULL)",
                (run_id, f"RF{run_id}", f"guid-{run_id}", points))
    con.commit()


def n_runs(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        con.close()


def test_backup_includes_runs_still_in_the_wal(tmp_path):
    db = tmp_path / "mm4250_sweeps.db"
    writer = make_db(db)
    add_run(writer, 1)
    add_run(writer, 2)
    # The writer is still open, so these runs are in the -wal, not the .db yet.
    assert (tmp_path / "mm4250_sweeps.db-wal").stat().st_size > 0

    out = backup_db(db)

    assert out.parent == tmp_path / "db_backups"
    assert n_runs(out) == 2
    con = sqlite3.connect(out)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    con.close()
    assert not Path(str(out) + "-wal").exists()
    assert not list((tmp_path / "db_backups").glob("*.partial"))
    writer.close()


def test_unchanged_database_writes_nothing(tmp_path):
    db = tmp_path / "mm4250_sweeps.db"
    writer = make_db(db)
    add_run(writer, 1)
    assert backup_db(db) is not None
    assert backup_db(db) is None
    assert len(_existing_backups(tmp_path / "db_backups", "mm4250_sweeps")) == 1
    assert backup_db(db, force=True) is not None
    writer.close()


def test_new_run_or_new_metadata_triggers_a_backup(tmp_path):
    db = tmp_path / "mm4250_sweeps.db"
    writer = make_db(db)
    add_run(writer, 1)
    first = backup_db(db)

    add_run(writer, 2)
    second = backup_db(db)
    assert second is not None and second != first and n_runs(second) == 2

    writer.execute("UPDATE runs SET touchstone_path='Sweeps/x.s2p' WHERE run_id=2")
    writer.commit()
    assert backup_db(db) is not None
    writer.close()


def test_keeps_only_the_newest(tmp_path):
    db = tmp_path / "mm4250_sweeps.db"
    writer = make_db(db)
    made = []
    for i in range(1, 4):
        add_run(writer, i)
        made.append(backup_db(db, keep=2))
    left = _existing_backups(tmp_path / "db_backups", "mm4250_sweeps")
    assert left == made[1:]
    assert n_runs(left[-1]) == 3
    writer.close()


def test_other_files_in_the_backup_folder_are_left_alone(tmp_path):
    db = tmp_path / "mm4250_sweeps.db"
    writer = make_db(db)
    folder = tmp_path / "db_backups"
    folder.mkdir()
    keepme = folder / "mm4250_sweeps_before_cooldown.db"
    keepme.write_bytes(b"not a timestamped backup")
    for i in range(1, 3):
        add_run(writer, i)
        backup_db(db, keep=1)
    assert keepme.exists()
    writer.close()


def test_custom_folder_and_missing_database(tmp_path):
    db = tmp_path / "mm4250_sweeps.db"
    writer = make_db(db)
    add_run(writer, 1)
    out = backup_db(db, backup_dir=tmp_path / "elsewhere")
    assert out.parent == tmp_path / "elsewhere"
    writer.close()
    with pytest.raises(FileNotFoundError):
        backup_db(tmp_path / "nope.db")
