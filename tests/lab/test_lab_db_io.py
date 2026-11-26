"""db.py: which database (lab helper vs local fallback), the round trip, Touchstone, backups."""

import functools
import importlib.util
import os
import types
from pathlib import Path

import numpy as np
import pytest
import qcodes

from conftest import HERE, LAB, db, ecal, vna


@pytest.fixture(autouse=True)
def keep_qcodes_db():
    before = qcodes.config["core"]["db_location"]
    yield
    qcodes.config["core"]["db_location"] = before


def _fake_helper(root):
    """Stand-in for scripts/database_helper with the same layout (as tests/test_lab_databases.py)."""
    from qcodes.dataset import initialise_or_create_database_at
    calls = []

    def database_path(project, cooldown, name=None):
        stem = f"{project}_Cooldown_{int(cooldown):02d}" + (f"_{name}" if name else "")
        return Path(root) / project / f"Cooldown_{int(cooldown):02d}" / f"{stem}.db"

    def setup_database(project, cooldown, name=None):
        calls.append((project, cooldown, name))
        path = database_path(project, cooldown, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        initialise_or_create_database_at(path)
        qcodes.config.core.db_location = str(path)
        return path

    return types.SimpleNamespace(setup_database=setup_database, database_path=database_path,
                                 calls=calls)


def test_lab_helper_is_used_when_present(cfg, tmp_path, capsys):
    helper = _fake_helper(tmp_path / "QCoDeS")
    vna_db = db.open_database(cfg, "vna", helper=helper)
    stuck_db = db.open_database(cfg, "stuck", helper=helper)
    assert vna_db == (tmp_path / "QCoDeS/MM4250/Cooldown_05/MM4250_Cooldown_05_vna.db").resolve()
    assert stuck_db.name == "MM4250_Cooldown_05_stuck.db" and stuck_db.parent == vna_db.parent
    assert helper.calls == [("MM4250", 5, "vna"), ("MM4250", 5, "stuck")]
    assert "NO LAB DATABASE HELPER" not in capsys.readouterr().out


def test_create_false_only_works_out_the_path(cfg, tmp_path):
    helper = _fake_helper(tmp_path / "QCoDeS")
    p = db.open_database(cfg, "stuck", create=False, helper=helper)
    assert p.name == "MM4250_Cooldown_05_stuck.db" and not p.exists() and helper.calls == []
    local = db.open_database(cfg, "stuck", create=False, helper=None, local_dir=tmp_path / "loc")
    assert not local.exists()


def test_fallback_warns_loudly_and_says_where(cfg, tmp_path, capsys):
    p = db.open_database(cfg, "vna", helper=None, local_dir=tmp_path / "loc")
    out = capsys.readouterr().out
    assert "NO LAB DATABASE HELPER FOUND" in out and str(p) in out and p.is_file()


def test_helper_found_on_disk_above_the_code(cfg, tmp_path, monkeypatch):
    """A scripts/database_helper.py in a folder above (the DAQ's framework layout) is found."""
    root = tmp_path / "framework"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "__init__.py").write_text("")
    (root / "scripts" / "database_helper.py").write_text(
        "def setup_database(project, cooldown, name=None):\n    raise RuntimeError('found me')\n")
    import sys
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k.split(".")[0] == "scripts"}
    monkeypatch.setattr(sys, "path", list(sys.path))    # open_database adds to it
    cfg = db.load_cooldown(LAB / "cooldowns" / "CD05.json")
    try:
        monkeypatch.setattr(db, "HERE", root / "Users" / "Charlie_Ferrari" / "lab")
        with pytest.raises(RuntimeError, match="found me"):        # it used the helper above it
            db.open_database(cfg, "vna")
        for k in [k for k in sys.modules if k.split(".")[0] == "scripts"]:
            del sys.modules[k]
        monkeypatch.setattr(db, "HERE", tmp_path / "elsewhere")
        monkeypatch.setattr(db, "LOCAL_DB_DIR", tmp_path / "local")
        assert db.open_database(cfg, "vna", create=False).parent == tmp_path / "local"   # none: local
    finally:   # don't leave this fake in sys.modules for other tests (tests/test_lab_databases.py)
        for k in [k for k in sys.modules if k.split(".")[0] == "scripts"]:
            del sys.modules[k]
        sys.modules.update(saved)


def test_helper_that_changed_is_refused(cfg, tmp_path):
    helper = _fake_helper(tmp_path / "QCoDeS")
    helper.setup_database = lambda project, cooldown, name=None: tmp_path / "somewhere_else.db"
    with pytest.raises(RuntimeError, match="the lab helper has changed"):
        db.open_database(cfg, "vna", helper=helper)


def _real_helper_file():
    env = os.environ.get("MM4250_DB_HELPER")
    if env:
        return Path(env)
    for p in HERE.parents:
        cand = p / "QCoDeS-Measurement-Framework" / "scripts" / "database_helper.py"
        if cand.is_file():
            return cand
    return None


def test_with_the_real_helper(cfg, tmp_path):
    f = _real_helper_file()
    if f is None:
        pytest.skip("the framework's scripts/database_helper.py isn't here")
    spec = importlib.util.spec_from_file_location("_lab_v2_database_helper", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "QCoDeS"                         # not the real E: drive
    helper = types.SimpleNamespace(setup_database=functools.partial(mod.setup_database, root=root),
                                   database_path=functools.partial(mod.database_path, root=root))
    p = db.open_database(cfg, "vna", helper=helper)
    assert p == (root / "MM4250" / "Cooldown_05" / "MM4250_Cooldown_05_vna.db").resolve()
    assert db.open_database(cfg, "stuck", create=False, helper=helper).parent == p.parent


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_take_round_trip_export_reread_no_overwrite(cfg, rig, dbpath, ideals, capsys):
    from conftest import ecal
    t = vna.take(cfg, rig.vna, rig.switch, rig.fridge, channels=[4],
                 save=db.Saver(dbpath, "take", files=True))
    take = db.load_take(t["take_id"], dbpath)
    db.save_corrected(dbpath, ecal.correct_take(take, definitions=ideals), take, files=True)
    take = db.load_take(t["take_id"], dbpath)

    # the files written during the take sit beside the database
    folder = db.take_folder(dbpath, take)
    assert folder.parent.parent == dbpath.parent / "Sweeps"
    written = sorted(p.name for p in folder.iterdir())
    assert len(written) == 8 and any(n.startswith("RF4_corrected_rep1_run") for n in written)

    # listing and recall by id
    takes = db.list_takes(dbpath)
    assert [x["take_id"] for x in takes] == [t["take_id"]]
    assert takes[0]["raw"] == 1 and takes[0]["standard"] == 6 and takes[0]["corrected"] == 1
    ids = db.take_run_ids(take)
    assert len(ids) == 8

    # export, re-read: the same numbers (10 significant digits in the file)
    out = dbpath.parent / "exported"
    paths = db.export_touchstone(ids, dbpath, out_dir=out)
    runs = db.load_runs(ids, dbpath)
    for run_id, path in zip(ids, paths):
        f, data = ecal.read_touchstone(path)
        np.testing.assert_allclose(f, runs[run_id]["freq"], rtol=1e-12)
        for s, v in data.items():
            np.testing.assert_allclose(v, runs[run_id]["data"][s], rtol=1e-9, atol=1e-12)
    assert paths[-1].suffix == ".s1p" and paths[0].suffix == ".s2p"

    # no overwrite: change a file, export again, file unchanged
    paths[0].write_text("hand-edited\n")
    capsys.readouterr()
    db.export_touchstone(ids[:1], dbpath, out_dir=out)
    assert paths[0].read_text() == "hand-edited\n" and "not overwritten" in capsys.readouterr().out


def test_save_touchstone_never_overwrites(tmp_path):
    p = tmp_path / "x.s1p"
    assert db.save_touchstone(np.array([1e9]), np.array([0.5 + 0j]), p)
    assert not db.save_touchstone(np.array([1e9]), np.array([0.9 + 0j]), p)
    assert ecal.read_touchstone(p)[1]["S11"][0] == 0.5


def test_load_take_errors(dbpath):
    with pytest.raises(LookupError, match="No takes"):
        db.load_take(None, dbpath)


def test_backup_written_once_then_skipped(cfg, rig, dbpath, capsys):
    vna.take(cfg, rig.vna, rig.switch, rig.fridge, channels=[1], save=db.Saver(dbpath, "take"))
    first = db.backup_db(dbpath)
    assert first is not None and first.parent == dbpath.parent / "db_backups"
    assert db.backup_db(dbpath) is None and "nothing written" in capsys.readouterr().out
    assert db.backup_db(dbpath.parent / "missing.db") is None      # never raises


def test_metadata_values_are_cleaned(dbpath):
    """sv.store turns numpy numbers, bools and lists into plain columns and skips None."""
    import sqlite3
    labels = {"a_num": np.float64(1.5), "a_bool": True, "a_list": [1, 2], "a_none": None}
    db.sv.store(dbpath, "x", np.array([1e9, 2e9]), {"S11": np.array([0.5, 0.4 + 0j])}, labels)
    con = sqlite3.connect(dbpath)
    cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
    row = con.execute("SELECT a_num, a_bool, a_list FROM runs").fetchone()
    con.close()
    assert row == (1.5, 1, "[1, 2]") and "a_none" not in cols
