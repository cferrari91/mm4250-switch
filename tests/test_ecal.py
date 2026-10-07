"""Hardware-free tests for the e-cal path: sweep_db.run_ecal_set and ecal.py.

The VNA and switch are replaced by a fake measure_s11 that returns what a
VNA would read through a known (made-up) error box, so the correction has
a right answer to hit. Two tests also run on NIST's own dilution-fridge
data when the nist_MM4250_calibration_data_2025 repo sits beside this one,
and one checks ecal.py against scikit-rf's OnePort if that's installed.

    python -m pytest tests/test_ecal.py
"""

import sys
from pathlib import Path

import numpy as np
import pytest

MEAS = Path(__file__).resolve().parents[1] / "measurements"
sys.path.insert(0, str(MEAS))

import ecal  # noqa: E402
import sweep_db  # noqa: E402
from read_db import list_runs  # noqa: E402

NIST = Path(__file__).resolve().parents[2] / "nist_MM4250_calibration_data_2025"
STDS = ("open", "short", "load")
STATE = {"open": "ALL_OPEN", "short": "INTERNAL_SHORT", "load": "INTERNAL_LOAD"}


# ---------------------------------------------------------------------------
# A made-up fridge: per-port error boxes and the matching ideals
# ---------------------------------------------------------------------------

def _box(rng, n):
    """Random but physical-looking one-port error terms on n points."""
    c = lambda s: s * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return {"e00": c(0.1), "e11": c(0.1), "e01e10": 0.3 * np.exp(1j * rng.uniform(0, 6, n))}


def _through(box, ga):
    """What the VNA reads for true reflection `ga` through `box`."""
    return box["e00"] + box["e01e10"] * ga / (1 - box["e11"] * ga)


def _back(box, gm):
    """Inverse of _through: the true reflection that reads as `gm`."""
    return (gm - box["e00"]) / (box["e01e10"] + box["e11"] * (gm - box["e00"]))


class Fridge:
    """
    Raw standards are one measurement shared by all ports (they're read at
    RFC), so each port's ideals are defined as whatever that port's error
    box maps those readings back to -- exactly the relationship NIST's
    tier-2 files encode. A DUT on port n reads through port n's box.
    """

    def __init__(self, n=201, seed=1):
        rng = np.random.default_rng(seed)
        self.freq = np.linspace(100e6, 10e9, n)
        self.raw_std = {"open": _through(_box(rng, n), np.full(n, 1 + 0j)),
                        "short": _through(_box(rng, n), np.full(n, -1 + 0j)),
                        "load": _through(_box(rng, n), np.full(n, 0.02 + 0.01j))}
        self.boxes = {ch: _box(rng, n) for ch in range(1, 7)}
        self.dut = {ch: 0.9 * np.exp(-1j * np.linspace(0, 3 * ch, n)) for ch in range(1, 7)}
        self.drift = 0.0              # added to the standards after the ports are measured
        self.fail_on = None           # channel whose measurement raises
        self.positions = []           # every position the fake switch was sent to
        self.ports_done = False

    def ideals(self, ch):
        return {s: _back(self.boxes[ch], self.raw_std[s]) for s in STDS}

    def write_ideals(self, folder):
        # Full precision, like NIST's files -- save_touchstone's 7 digits
        # would put a 1e-5 floor under every comparison.
        Path(folder).mkdir(parents=True, exist_ok=True)
        for ch in range(1, 7):
            for s, g in self.ideals(ch).items():
                rows = "\n".join(f"{f:.1f} {z.real:.17e} {z.imag:.17e}"
                                  for f, z in zip(self.freq, g))
                (Path(folder) / f"port{ch}_{s}_tier2.s1p").write_text(
                    f"# HZ S RI R 50\n{rows}\n")

    def measure_s11(self, channel=None, state=None, vna=None, switch=None):
        self.positions.append(channel or state)
        if channel is not None:
            if channel == self.fail_on:
                raise RuntimeError("simulated VNA timeout")
            self.ports_done = True
            return self.freq, _through(self.boxes[channel], self.dut[channel])
        std = {v: k for k, v in STATE.items()}[state]
        return self.freq, self.raw_std[std] + (self.drift if self.ports_done else 0)


@pytest.fixture
def fridge(monkeypatch, tmp_path):
    fake = Fridge()
    monkeypatch.setattr(sweep_db, "measure_s11", fake.measure_s11)
    monkeypatch.setattr(sweep_db, "instrument_state",
                        lambda sparams, vna=None: {"vna_correction_enabled": 0})
    monkeypatch.setattr(sweep_db, "_cal_state", lambda vna: "uncal")
    monkeypatch.setattr(sweep_db, "_select",
                        lambda channel=None, state=None, switch=None, vna=None:
                        fake.positions.append(channel or state))
    fake.db = tmp_path / "test.db"
    fake.ideals_dir = tmp_path / "ideals_3K"
    fake.write_ideals(fake.ideals_dir)
    return fake


def _run(fake, **kw):
    kw.setdefault("repeats", 1)
    return sweep_db.run_ecal_set("20261015", "3K", "SN0077", db_path=fake.db,
                                 vna=object(), switch=object(), **kw)


# ---------------------------------------------------------------------------
# The math
# ---------------------------------------------------------------------------

def test_solve_and_apply_recover_a_known_error_box():
    rng = np.random.default_rng(0)
    n = 50
    box = _box(rng, n)
    ideal = {"open": np.full(n, 1 + 0j), "short": np.full(n, -1 + 0j),
             "load": np.full(n, 0.01 + 0j)}
    measured = {s: _through(box, g) for s, g in ideal.items()}
    terms = ecal.solve_error_terms(measured, ideal)
    for k in ("e00", "e11", "e01e10"):
        np.testing.assert_allclose(terms[k], box[k], atol=1e-12)
    dut = 0.5 * np.exp(1j * np.linspace(0, 6, n))
    np.testing.assert_allclose(ecal.apply_correction(terms, _through(box, dut)), dut, atol=1e-12)


def test_read_s1p_handles_units_and_formats(tmp_path):
    p = tmp_path / "x.s1p"
    p.write_text("! c\n# MHZ S DB R 50\n100 -6.0206 90\n200 0 180\n")
    f, g = ecal.read_s1p(p)
    np.testing.assert_allclose(f, [100e6, 200e6])
    np.testing.assert_allclose(g, [0.5j, -1], atol=1e-6)


# ---------------------------------------------------------------------------
# run_ecal_set -> database -> correct_set, end to end
# ---------------------------------------------------------------------------

def test_set_order_tags_and_switch_left_open(fridge):
    cal = _run(fridge, repeats=2, mxc_temp_k=3.2, note="first cold set")
    std = list(sweep_db.ECAL_STANDARDS)
    block = std + [1, 2, 3, 4, 5, 6] + std
    assert fridge.positions == block + block + ["ALL_OPEN"]
    assert len(cal["repeats"]) == 2
    assert set(cal["repeats"][0]["ports"]) == {1, 2, 3, 4, 5, 6}
    runs = list_runs(fridge.db)
    assert len(runs) == 24
    assert {r["experiment"] for r in runs} == {"20261015_3K_SN0077_ecal_uncal"}


def test_correct_set_recovers_the_dut(fridge):
    cal = _run(fridge, repeats=2)
    result = ecal.correct_set(cal, fridge.ideals_dir, db_path=fridge.db)
    for ch in range(1, 7):
        np.testing.assert_allclose(result["ports"][ch], fridge.dut[ch], atol=1e-9)
    assert len(result["per_repeat"]) == 2


def test_find_set_rebuilds_what_run_ecal_set_returned(fridge):
    _run(fridge)
    cal2 = _run(fridge, channels=[2, 5])
    assert ecal.find_set(None, fridge.db) == cal2          # None -> most recent
    assert [s["set"] for s in ecal.list_sets(fridge.db)][-1] == cal2["set"]
    result = ecal.correct_set(None, fridge.ideals_dir, db_path=fridge.db)
    assert sorted(result["ports"]) == [2, 5]


def test_drift_is_reported_and_mean_standards_split_it(fridge):
    fridge.drift = 0.01
    cal = _run(fridge)
    d = ecal.drift(cal, db_path=fridge.db, verbose=False)
    np.testing.assert_allclose(d["repeats"][0]["open"], 0.01, atol=1e-12)
    before = ecal.correct_set(cal, fridge.ideals_dir, standards="before", db_path=fridge.db)
    np.testing.assert_allclose(before["ports"][1], fridge.dut[1], atol=1e-9)
    mean = ecal.correct_set(cal, fridge.ideals_dir, standards="mean", db_path=fridge.db)
    assert np.max(np.abs(mean["ports"][1] - fridge.dut[1])) > 1e-4


def test_export_writes_one_file_per_channel(fridge, tmp_path):
    cal = _run(fridge, channels=[1, 4])
    result = ecal.correct_set(cal, fridge.ideals_dir, db_path=fridge.db)
    paths = ecal.export_corrected(result, db_path=fridge.db)
    assert [p.name for p in paths] == ["RF1.s1p", "RF4.s1p"]
    f, g = ecal.read_s1p(paths[1])
    np.testing.assert_allclose(g, fridge.dut[4], atol=1e-5)


def test_refuses_with_vna_correction_on(fridge, monkeypatch):
    monkeypatch.setattr(sweep_db, "_cal_state", lambda vna: "cal")
    with pytest.raises(RuntimeError, match="correction is ON"):
        _run(fridge)
    assert fridge.positions == []


def test_switch_opened_even_if_the_set_dies(fridge):
    fridge.fail_on = 3
    with pytest.raises(RuntimeError, match="simulated"):
        _run(fridge)
    assert fridge.positions[-1] == "ALL_OPEN"


@pytest.mark.parametrize("kw", [{"channels": [0]}, {"channels": [1, 1]},
                                {"channels": []}, {"repeats": 0}])
def test_bad_arguments(fridge, kw):
    with pytest.raises(ValueError):
        _run(fridge, **kw)


def _runs_table(db, *cols):
    """{run_id: (col, ...)} straight from the runs table."""
    import sqlite3
    con = sqlite3.connect(db)
    try:
        rows = con.execute(f"SELECT run_id, name, {', '.join(cols)} FROM runs").fetchall()
    finally:
        con.close()
    return {r[0]: r[1:] for r in rows}


def test_terminations_tag_each_port_and_the_whole_set(fridge):
    cal = _run(fridge, channels=[2, 3, 5], terminations={5: "load", 2: "short"})
    assert cal["terminations"] == {2: "short", 5: "load"}
    rows = _runs_table(fridge.db, "termination", "ecal_terminations", "ecal_role")
    by_name = {name: (term, role) for name, term, _, role in rows.values() if role == "port"}
    assert by_name == {"RF2": ("short", "port"), "RF3": ("none", "port"),
                       "RF5": ("load", "port")}
    # The standards carry no per-port tag, but every run carries the set's dict.
    for name, term, set_json, role in rows.values():
        assert set_json == '{"2": "short", "5": "load"}'
        if role != "port":
            assert term is None
    # And it all comes back from the database.
    assert ecal.find_set(None, fridge.db) == cal
    assert ecal.list_sets(fridge.db)[-1]["terminations"] == {2: "short", 5: "load"}
    result = ecal.correct_set(None, fridge.ideals_dir, db_path=fridge.db)
    assert result["terminations"] == {2: "short", 5: "load"}


def test_terminations_can_name_a_port_the_set_skips(fridge):
    cal = _run(fridge, channels=[1], terminations={6: "short"})
    assert cal["terminations"] == {6: "short"}
    rows = _runs_table(fridge.db, "termination")
    assert [t for name, t in rows.values() if name == "RF1"] == ["none"]


def test_no_terminations_records_nothing_new(fridge):
    cal = _run(fridge)
    assert cal["terminations"] == {}
    assert ecal.find_set(None, fridge.db) == cal
    import sqlite3
    con = sqlite3.connect(fridge.db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
    con.close()
    assert "termination" not in cols and "ecal_terminations" not in cols


@pytest.mark.parametrize("terms", [{0: "short"}, {7: "load"}, {"2": "short"},
                                   {True: "short"}, {2: ""}, {2: None}])
def test_bad_terminations(fridge, terms):
    with pytest.raises(ValueError, match="terminations"):
        _run(fridge, terminations=terms)
    assert fridge.positions == []          # refused before the switch moved


def test_position_metadata_rejects_unknown_positions(fridge):
    with pytest.raises(ValueError, match="position_metadata"):
        sweep_db.run_oneport_sweep([1, 2], "20261015", "3K", "SN0077", db_path=fridge.db,
                                   position_metadata={3: {"x": 1}},
                                   vna=object(), switch=object())
    assert fridge.positions == []


def test_ideals_range_is_enforced(fridge):
    with pytest.raises(ValueError, match="covers"):
        ecal.load_ideals(fridge.ideals_dir, 1, np.array([50e6, 1e9]))


# ---------------------------------------------------------------------------
# NIST's own data, when the repo is beside this one
# ---------------------------------------------------------------------------

needs_nist = pytest.mark.skipif(not (NIST / "dilution_refrigerator_data").is_dir(),
                                reason="nist_MM4250_calibration_data_2025 not beside this repo")


def _nist_base():
    d = NIST / "dilution_refrigerator_data" / "base"
    f, _ = ecal.read_s1p(d / "ecal_open_base.s1p")
    meas = {s: ecal.read_s1p(d / f"ecal_{s}_base.s1p")[1] for s in STDS}
    return d, f, meas


@needs_nist
def test_nist_25mK_shorts_come_out_near_0_dB():
    """NIST's 25 mK set, corrected with 3 K ideals: shorts should read ~|G| = 1."""
    d, f, meas = _nist_base()
    ideals = ecal.find_ideals("3K", start=NIST.parent)
    band = (f >= 0.3e9) & (f <= 8e9)
    for port in (1, 2, 3, 4, 6):          # offset shorts and the flat short
        terms = ecal.solve_error_terms(meas, ecal.load_ideals(ideals, port, f))
        g = ecal.apply_correction(terms, ecal.read_s1p(d / f"port{port}_base.s1p")[1])
        db = 20 * np.log10(np.abs(g[band]))
        assert abs(np.median(db)) < 0.1 and np.max(np.abs(db)) < 0.5


@needs_nist
def test_matches_scikit_rf_oneport():
    skrf = pytest.importorskip("skrf")
    from skrf.calibration import OnePort

    d, f, meas = _nist_base()
    ideals = ecal.find_ideals("3K", start=NIST.parent)
    grid = skrf.Frequency.from_f(f, unit="hz")
    for port in (1, 4, 6):
        ours = ecal.apply_correction(
            ecal.solve_error_terms(meas, ecal.load_ideals(ideals, port, f)),
            ecal.read_s1p(d / f"port{port}_base.s1p")[1])
        cal = OnePort(
            ideals=[skrf.Network(str(ideals / f"port{port}_{s}_tier2.s1p")).interpolate(grid)
                    for s in ("short", "open", "load")],
            measured=[skrf.Network(str(d / f"ecal_{s}_base.s1p")) for s in ("short", "open", "load")])
        cal.run()
        theirs = cal.apply_cal(skrf.Network(str(d / f"port{port}_base.s1p"))).s[:, 0, 0]
        np.testing.assert_allclose(ours, theirs, atol=1e-9)
