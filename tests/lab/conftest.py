"""
Shared setup for the measurements/lab tests.

measurements/lab/ has its own ecal.py and plots.py, and the older tests
import measurements/ecal.py and plots.py under those same names in the
same pytest process. So the lab modules are loaded here from their files
under the names lab_ecal / lab_plots; vna and db keep their own names
(nothing else uses them; db imports vna).
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1] / "measurements" / "lab"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The lab code runs on the framework's scripts/switch_vna.py: use the framework checkout
# beside this repo (or $MM4250_FRAMEWORK).
import os  # noqa: E402
FRAMEWORK = Path(os.environ.get("MM4250_FRAMEWORK", HERE.parents[2] / "QCoDeS-Measurement-Framework"))
if not (FRAMEWORK / "scripts" / "switch_vna.py").is_file():
    raise RuntimeError(f"No scripts/switch_vna.py in {FRAMEWORK}: set MM4250_FRAMEWORK to the "
                       "QCoDeS-Measurement-Framework checkout")
if str(FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK))


def _load(name, filename):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, LAB / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vna = _load("vna", "vna.py")
db = _load("lab_db", "db.py")
ecal = _load("lab_ecal", "ecal.py")
plots = _load("lab_plots", "plots.py")


@pytest.fixture(autouse=True)
def _agg():
    import matplotlib
    matplotlib.use("Agg")
    yield
    import matplotlib.pyplot as plt
    plt.close("all")


@pytest.fixture
def cfg(tmp_path):
    """CD05.json loaded through load_cooldown, with the notebooks' sweep added
    (db.with_sweep): 201 points (quicker than the notebooks' 2001), -20 dBm."""
    data = json.loads((LAB / "cooldowns" / "CD05.json").read_text())
    path = tmp_path / "CD_test.json"
    path.write_text(json.dumps(data, indent=2))
    return db.with_sweep(db.load_cooldown(path), 201, -20)


@pytest.fixture
def rig(cfg):
    """Fake VNA + switch + fridge (295 K) on cfg's sweep; the thru on line RF3 -> RF2."""
    from fakes import make_rig
    s = cfg["sweep"]
    return make_rig(s["start_hz"], s["stop_hz"], s["points"], thru=(cfg["lines"]["thru"],
                                                                  cfg["thru_channel"]))


@pytest.fixture
def dbpath(cfg, tmp_path):
    """A fresh database for cfg, through the local fallback (in tmp)."""
    return db.open_database(cfg, "vna", helper=None, local_dir=tmp_path / "dbs")


@pytest.fixture
def ideals(rig, tmp_path):
    """The fake chain's per-port definitions, written as NIST-style files."""
    folder = tmp_path / "ideals_fake"
    rig.chain.write_ideals(folder)
    return folder


def fresh_lab_copy(dest):
    """measurements/lab copied to `dest` (notebook runs), without generated files."""
    shutil.copytree(LAB, dest, ignore=shutil.ignore_patterns(
        "__pycache__", "local_databases", "Sweeps", "db_backups", ".ipynb_checkpoints",
        "ideals_*"))
    return dest
