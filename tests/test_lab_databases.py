"""
sweep_db.use_lab_databases: the switch's and the resonator's runs go to the
lab's per-project, per-cooldown databases (the framework's
scripts/database_helper.py), the calibration database stays put.

The helper is the lab's, not in this repo. Most tests use a stand-in with
the same signature and layout:
<root>/<project>/Cooldown_<NN>/<project>_Cooldown_<NN>_<name>.db, set as
QCoDeS's database, path returned. test_with_the_real_helper runs against
the real file when it can find it (the framework checkout this repo sits in
or beside, or $MM4250_DB_HELPER), and is skipped otherwise.
"""

import functools
import importlib.util
import os
import sys
from pathlib import Path

import pytest
import qcodes
from qcodes.dataset import initialise_or_create_database_at

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "measurements"))

import duts  # noqa: E402
import read_db  # noqa: E402
import sweep_db  # noqa: E402

from test_ecal import fridge  # noqa: E402,F401  (the fake fridge fixture)


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts and ends with the databases beside the code."""
    before = qcodes.config["core"]["db_location"]
    read_db.use_databases()
    yield
    read_db.use_databases()
    qcodes.config["core"]["db_location"] = before


def fake_helper(root):
    """Stand-in for scripts/database_helper.setup_database, same layout, under `root`."""
    calls = []

    def setup_database(project, cooldown, name=None, experiment=None, sample=None):
        calls.append((project, cooldown, name))
        stem = f"{project}_Cooldown_{int(cooldown):02d}" + (f"_{name}" if name else "")
        path = Path(root) / project / f"Cooldown_{int(cooldown):02d}" / f"{stem}.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        initialise_or_create_database_at(path)
        qcodes.config.core.db_location = str(path)
        return path

    setup_database.calls = calls
    return setup_database


def _real_helper_file():
    env = os.environ.get("MM4250_DB_HELPER")
    if env:
        return Path(env)
    for p in HERE.parents:
        for cand in (p / "scripts" / "database_helper.py",
                     p / "QCoDeS-Measurement-Framework" / "scripts" / "database_helper.py"):
            if cand.is_file():
                return cand
    return None


def test_with_the_real_helper(tmp_path):
    f = _real_helper_file()
    if f is None:
        pytest.skip("the framework's scripts/database_helper.py isn't here")
    spec = importlib.util.spec_from_file_location("_lab_database_helper", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    helper = functools.partial(mod.setup_database, root=tmp_path / "QCoDeS")   # not the real E: drive

    paths = sweep_db.use_lab_databases(5, "MM4250", "Project8_Resonator", setup_database=helper)
    assert paths["main"] == (tmp_path / "QCoDeS" / "MM4250" / "Cooldown_05"
                             / "MM4250_Cooldown_05_vna.db").resolve()
    assert paths["resonator"] == (tmp_path / "QCoDeS" / "Project8_Resonator" / "Cooldown_05"
                                  / "Project8_Resonator_Cooldown_05_vna.db").resolve()
    assert paths["main"].is_file() and paths["resonator"].is_file()
    assert mod.database_is_set(root=tmp_path / "QCoDeS")
    assert mod.list_projects(root=tmp_path / "QCoDeS") == ["MM4250", "Project8_Resonator"]


def test_switch_and_resonator_go_to_their_own_projects(tmp_path):
    helper = fake_helper(tmp_path / "E" / "QCoDeS")
    paths = sweep_db.use_lab_databases(5, "MM4250", "Project8_Resonator", setup_database=helper)

    main = tmp_path / "E" / "QCoDeS" / "MM4250" / "Cooldown_05" / "MM4250_Cooldown_05_vna.db"
    res = tmp_path / "E" / "QCoDeS" / "Project8_Resonator" / "Cooldown_05" / "Project8_Resonator_Cooldown_05_vna.db"
    assert paths == {"main": main.resolve(), "resonator": res.resolve()}
    assert read_db.default_db_path() == main.resolve()
    assert sweep_db._default_db_path() == main.resolve()
    assert duts.resonator_db_path() == res.resolve()
    # the switch project last, so it's QCoDeS's current database afterwards
    assert helper.calls == [("Project8_Resonator", 5, "vna"), ("MM4250", 5, "vna")]
    assert Path(qcodes.config["core"]["db_location"]).resolve() == main.resolve()
    # untouched: the calibration database and the old runs' database
    assert sweep_db._default_cal_db_path().parent.name == read_db.CAL_DIR_NAME
    assert read_db.code_db_path() == HERE.parent / "measurements" / read_db.DEFAULT_DB_NAME


def test_name_is_passed_through(tmp_path):
    paths = sweep_db.use_lab_databases(5, "S", "R", name="ecal", setup_database=fake_helper(tmp_path))
    assert paths["main"].name == "S_Cooldown_05_ecal.db"


def test_runs_backups_and_files_land_in_the_switch_project(fridge, tmp_path):  # noqa: F811
    sweep_db.use_lab_databases(5, "MM4250", "Project8_Resonator", setup_database=fake_helper(tmp_path))
    main = read_db.default_db_path()
    sweep_db.run_ecal_set("20261027", "295K", "SN0077", channels=[1], repeats=1,
                                touchstone=True, vna=object(), switch=object())
    assert main.is_file()
    assert len(read_db.list_runs()) > 0                              # db_path=None reads the lab db
    assert (main.parent / read_db.DEFAULT_BACKUP_DIR_NAME).is_dir()  # backups beside it
    assert list((main.parent / read_db.DEFAULT_OUT_DIR_NAME).rglob("*.s2p"))   # Touchstone beside it


def test_reset_goes_back_beside_the_code(tmp_path):
    sweep_db.use_lab_databases(5, "S", "R", setup_database=fake_helper(tmp_path))
    read_db.use_databases()
    assert read_db.default_db_path() == read_db.code_db_path()
    assert duts.resonator_db_path() == duts.HERE / "Sweeps" / "duts" / "resonator.db"


def test_helper_that_changed_is_refused(tmp_path):
    def silent(project, cooldown, name=None):
        return None                                   # no path back: not the helper we know

    with pytest.raises(RuntimeError, match="helper has changed"):
        sweep_db.use_lab_databases(5, "S", "R", setup_database=silent)
    assert read_db.default_db_path() == read_db.code_db_path()      # nothing redirected


def test_same_database_for_both_projects_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="same database"):
        sweep_db.use_lab_databases(5, "S", "S", setup_database=fake_helper(tmp_path))
    assert read_db.default_db_path() == read_db.code_db_path()


def test_find_setup_database_in_the_framework_scripts(tmp_path, monkeypatch):
    root = tmp_path / "framework"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "QCodesStationSetup.py").write_text("def setupDevices():\n    pass\n")
    (root / "scripts" / "database_helper.py").write_text(
        "def setup_database(project, cooldown, name=None):\n    return f'{project}/{cooldown}/{name}'\n")
    monkeypatch.delitem(sys.modules, "scripts", raising=False)
    monkeypatch.syspath_prepend(str(root))
    fn = sweep_db.find_setup_database(root)
    assert fn("P", cooldown=5, name="vna") == "P/5/vna"


def test_find_setup_database_old_framework(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "QCodesStationSetup.py").write_text("def setupDevices():\n    pass\n")
    with pytest.raises(ImportError, match="predates"):
        sweep_db.find_setup_database(tmp_path)


def test_sections_a_and_b_with_every_default(monkeypatch, tmp_path):
    """A4 -> B1 -> B2 with no db_path anywhere: the switch project gets the set,
    the resonator project the copies and the fit, and QCoDeS stays on the switch's."""
    import numpy as np

    import ecal
    from test_duts import resonator
    from test_ecal import Fridge

    fake = Fridge(n=2001)
    fake.freq = np.linspace(490e6, 630e6, 2001)
    fake.dut[1] = resonator(ql=40.0, beta=0.8)[1]
    monkeypatch.setattr(sweep_db, "measure_2port", fake.measure_2port)
    monkeypatch.setattr(sweep_db, "instrument_state",
                        lambda sparams, vna=None: {"vna_correction_enabled": 0})
    monkeypatch.setattr(sweep_db, "_cal_state", lambda vna: "uncal")
    monkeypatch.setattr(sweep_db, "_select", lambda channel=None, state=None, switch=None, vna=None: None)
    fake.write_ideals(tmp_path / "ideals_3K")

    paths = sweep_db.use_lab_databases(5, "MM4250", "Project8_Resonator", setup_database=fake_helper(tmp_path))
    cal = sweep_db.run_ecal_set("20261027", "3K", "SN0077", channels=[1, 2],
                                labels={1: "resonator", 2: "SNTJ"}, repeats=1, cooldown="CD5",
                                vna=object(), switch=object())
    assert ecal.find_set(cal["set"])["labels"] == {1: "resonator", 2: "SNTJ"}   # in the switch db

    set_id = duts.copy_set(cal)
    fit = duts.fit_resonator(duts.correct_resonator(set_id, tmp_path / "ideals_3K"), verbose=False)
    duts.save_resonator(fit)
    assert [r["dut_label"] for r in duts.list_resonator(verbose=False)] == ["resonator"]
    assert [s["set"] for s in ecal.list_sets(paths["resonator"])] == [cal["set"]]
    assert "RF2" not in [r["name"] for r in read_db.list_runs(paths["resonator"])]
    assert Path(qcodes.config["core"]["db_location"]).resolve() == paths["main"]
    assert (paths["resonator"].parent / "db_backups").is_dir()
