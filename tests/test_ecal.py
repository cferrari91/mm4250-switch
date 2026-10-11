"""Hardware-free tests for the e-cal path: sweep_db.run_ecal_set and ecal.py.

The VNA and switch are replaced by a fake measure_s11/measure_2port that
return what a VNA would read through a known (made-up) error box, so the
correction has a right answer to hit. The 2-port fake is the fridge's
circulator wiring: the switch's reflection is on S21, S11 is a fixed
trace the switch doesn't reach, and S12 is S21 through one more fixed
bilinear map, so correcting on S12 has a right answer too. Two tests also run on NIST's own dilution-fridge
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
from read_db import list_runs, load_run  # noqa: E402

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
        self.s11_input = 0.2 * np.exp(1j * np.linspace(0, 40, n))   # circulator input side
        self.s12_map = _box(rng, n)                                   # S12 = this applied to S21
        self.drift = 0.0              # added to the standards after the ports are measured
        self.fail_on = None           # channel whose measurement raises
        self.positions = []           # every position the fake switch was sent to
        self.ports_done = False
        self.attached = None          # a kit standard's true G on the channel (run_kit_set)

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
            if self.attached is not None:
                return self.freq, _through(self.boxes[channel], self.attached)
            self.ports_done = True
            return self.freq, _through(self.boxes[channel], self.dut[channel])
        std = {v: k for k, v in STATE.items()}[state]
        return self.freq, self.raw_std[std] + (self.drift if self.ports_done else 0)

    def measure_2port(self, channel=None, state=None, vna=None, switch=None):
        f, s21 = self.measure_s11(channel, state)
        return f, {"S11": self.s11_input, "S12": _through(self.s12_map, s21),
                   "S21": s21, "S22": 0.1 * np.ones_like(s21)}


@pytest.fixture
def fridge(monkeypatch, tmp_path):
    fake = Fridge()
    monkeypatch.setattr(sweep_db, "measure_s11", fake.measure_s11)
    monkeypatch.setattr(sweep_db, "measure_2port", fake.measure_2port)
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


def _vna_cal_on(monkeypatch):
    monkeypatch.setattr(sweep_db, "_cal_state", lambda vna: "cal")
    monkeypatch.setattr(sweep_db, "instrument_state",
                        lambda sparams, vna=None: {"vna_correction_enabled": 1})


def test_vna_cal_underneath_on_purpose(fridge, monkeypatch):
    import matplotlib
    matplotlib.use("Agg")
    _vna_cal_on(monkeypatch)
    cal = _run(fridge, channels=[1, 3], vna_cal=True)
    assert {r["experiment"] for r in list_runs(fridge.db)} == {"20261015_3K_SN0077_ecal_cal"}
    result = ecal.correct_set(cal, fridge.ideals_dir, db_path=fridge.db)
    assert result["vna_cal"] is True
    np.testing.assert_allclose(result["ports"][3], fridge.dut[3], atol=1e-9)
    ax_m, _ = ecal.plot_compare([result], show_raw=True)
    assert ax_m.get_legend().get_texts()[0].get_text() == "S21, VNA cal only"
    ax_m, _ = ecal.plot_ecal(result)
    assert "from VNA-corrected S21" in ax_m.get_title()


def test_vna_cal_true_refuses_with_correction_off(fridge, monkeypatch):
    with pytest.raises(RuntimeError, match="vna_cal=True but VNA correction is OFF"):
        _run(fridge, vna_cal=True)
    with pytest.raises(RuntimeError, match="vna_cal=True but VNA correction is OFF"):
        _run_kit(fridge, monkeypatch, vna_cal=True)
    assert fridge.positions == []


def test_switch_opened_even_if_the_set_dies(fridge):
    fridge.fail_on = 3
    with pytest.raises(RuntimeError, match="simulated"):
        _run(fridge)
    assert fridge.positions[-1] == "ALL_OPEN"


@pytest.mark.parametrize("kw", [{"channels": [0]}, {"channels": [1, 1]},
                                {"channels": []}, {"repeats": 0}, {"n_ports": 3}])
def test_bad_arguments(fridge, kw):
    with pytest.raises(ValueError):
        _run(fridge, **kw)


def test_two_port_set_saves_all_four_and_defaults_to_s21(fridge):
    cal = _run(fridge, channels=[1, 4])
    _, data = load_run(cal["repeats"][0]["ports"][4], fridge.db)
    assert sorted(data) == ["S11", "S12", "S21", "S22"]
    result = ecal.correct_set(cal, fridge.ideals_dir, db_path=fridge.db)
    assert result["sparam"] == "S21" and result["vna_cal"] is False
    np.testing.assert_allclose(result["ports"][4], fridge.dut[4], atol=1e-9)
    assert ecal.drift(cal, db_path=fridge.db, verbose=False)["repeats"][0]["open"].max() < 1e-12


def test_one_port_set_still_corrects_on_s11(fridge):
    cal = _run(fridge, channels=[2], n_ports=1)
    _, data = load_run(cal["repeats"][0]["ports"][2], fridge.db)
    assert list(data) == ["S11"]
    result = ecal.correct_set(cal, fridge.ideals_dir, db_path=fridge.db)
    assert result["sparam"] == "S11"
    np.testing.assert_allclose(result["ports"][2], fridge.dut[2], atol=1e-9)


def test_any_trace_the_reflection_reaches_can_be_corrected(fridge):
    cal = _run(fridge, channels=[3])
    result = ecal.correct_set(cal, fridge.ideals_dir, sparam="S12", db_path=fridge.db)
    assert result["sparam"] == "S12"
    np.testing.assert_allclose(result["ports"][3], fridge.dut[3], atol=1e-8)


@pytest.mark.parametrize("n_ports, sparam, match", [(2, "S13", "one of"), (2, "s21", "one of"),
                                                    (1, "S21", "no S21")])
def test_bad_sparam(fridge, n_ports, sparam, match):
    cal = _run(fridge, channels=[1], n_ports=n_ports)
    with pytest.raises(ValueError, match=match):
        ecal.correct_set(cal, fridge.ideals_dir, sparam=sparam, db_path=fridge.db)
    with pytest.raises(ValueError, match=match):
        ecal.drift(cal, db_path=fridge.db, sparam=sparam)


def test_correct_and_plot_is_correct_set_plus_checks(fridge, capsys):
    import matplotlib
    matplotlib.use("Agg")
    cal = _run(fridge, channels=[1, 6], repeats=2)
    result = ecal.correct_and_plot(cal, fridge.ideals_dir, sparam="S21", db_path=fridge.db)
    out = capsys.readouterr().out
    assert "drift |after - before| (S21)" in out and "RF6: median" in out
    expected = ecal.correct_set(cal, fridge.ideals_dir, sparam="S21", db_path=fridge.db)
    np.testing.assert_allclose(result["ports"][6], expected["ports"][6])


# ---------------------------------------------------------------------------
# Per-channel labels
# ---------------------------------------------------------------------------

def test_labels_go_on_their_own_channels_and_survive_the_round_trip(fridge):
    import matplotlib
    matplotlib.use("Agg")
    cal = _run(fridge, channels=[1, 4, 5], repeats=2,
               labels={1: "resonator", 4: "  50 ohm load ", 5: ""})
    assert cal["labels"] == {1: "resonator", 4: "50 ohm load"}   # stripped, blank dropped

    by_id = {r["run_id"]: r["dut_label"] for r in list_runs(fridge.db)}
    for block in cal["repeats"]:
        assert [by_id[block["ports"][ch]] for ch in (1, 4, 5)] == ["resonator", "50 ohm load", None]
        for role in ("before", "after"):
            assert all(by_id[i] is None for i in block[role].values())   # standards stay unlabelled

    rebuilt = ecal.find_set(None, fridge.db)
    assert rebuilt["labels"] == cal["labels"]
    result = ecal.correct_set(rebuilt, fridge.ideals_dir, db_path=fridge.db)
    assert result["labels"] == {1: "resonator", 4: "50 ohm load"}
    np.testing.assert_allclose(result["ports"][4], fridge.dut[4], atol=1e-9)

    ax_m, _ = ecal.plot_ecal(result)
    assert [t.get_text() for t in ax_m.get_legend().get_texts()] == \
        ["RF1 (resonator)", "RF4 (50 ohm load)", "RF5"]


def test_unlabelled_sets_still_work(fridge):
    import matplotlib
    matplotlib.use("Agg")
    cal = _run(fridge, channels=[2])
    assert cal["labels"] == {}
    assert ecal.find_set(None, fridge.db)["labels"] == {}
    # A set dict from before labels existed has no "labels" key at all.
    old = {k: v for k, v in cal.items() if k != "labels"}
    result = ecal.correct_set(old, fridge.ideals_dir, db_path=fridge.db)
    assert result["labels"] == {}
    ax_m, _ = ecal.plot_ecal(result)
    assert [t.get_text() for t in ax_m.get_legend().get_texts()] == ["RF2"]


@pytest.mark.parametrize("labels, err, match", [
    ({3: "SNTJ"}, ValueError, "isn't one of"),          # channel not in the set
    ({"1": "resonator"}, ValueError, "isn't one of"),   # string, not the channel number
    ({1: 5}, TypeError, "must be text"),
    (["resonator"], TypeError, "must be a dict"),
])
def test_bad_labels_fail_before_anything_is_measured(fridge, labels, err, match):
    with pytest.raises(err, match=match):
        _run(fridge, channels=[1], labels=labels)
    assert fridge.positions == []
    assert not fridge.db.exists() or list_runs(fridge.db) == []


def test_run_sweep_labels_a_state_too(fridge):
    ids = sweep_db.run_sweep([1, "ALL_OPEN"], "20261015", "3K", "SN0077", db_path=fridge.db,
                             labels={"ALL_OPEN": "isolation"}, vna=object(), switch=object())
    by_id = {r["run_id"]: r["dut_label"] for r in list_runs(fridge.db)}
    assert [by_id[i] for i in ids] == [None, "isolation"]


# ---------------------------------------------------------------------------
# What's on the ports: terminations
# ---------------------------------------------------------------------------

def _runs_table(db, *cols):
    """{run_id: (name, col, ...)} straight from the runs table."""
    import sqlite3
    con = sqlite3.connect(db)
    try:
        rows = con.execute(f"SELECT run_id, name, {', '.join(cols)} FROM runs").fetchall()
    finally:
        con.close()
    return {r[0]: r[1:] for r in rows}


def test_terminations_tag_each_port_and_the_whole_set(fridge):
    import matplotlib
    matplotlib.use("Agg")
    cal = _run(fridge, channels=[2, 3, 5], terminations={5: "load", 2: "short"})
    assert cal["terminations"] == {2: "short", 5: "load"}
    rows = _runs_table(fridge.db, "termination", "ecal_terminations", "ecal_role")
    by_name = {name: term for name, term, _, role in rows.values() if role == "port"}
    assert by_name == {"RF2": "short", "RF3": "none", "RF5": "load"}
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
    ax_m, _ = ecal.plot_ecal(result)
    assert [t.get_text() for t in ax_m.get_legend().get_texts()] == \
        ["RF2 (short)", "RF3", "RF5 (load)"]


def test_a_label_wins_over_a_termination_in_the_legend(fridge):
    import matplotlib
    matplotlib.use("Agg")
    _run(fridge, channels=[1, 2], labels={1: "resonator"}, terminations={1: "cable", 2: "short"})
    result = ecal.correct_set(None, fridge.ideals_dir, db_path=fridge.db)
    ax_m, _ = ecal.plot_ecal(result)
    assert [t.get_text() for t in ax_m.get_legend().get_texts()] == \
        ["RF1 (resonator)", "RF2 (short)"]


def test_terminations_can_name_a_port_the_set_skips(fridge):
    cal = _run(fridge, channels=[1], terminations={6: "short"})
    assert cal["terminations"] == {6: "short"}
    rows = _runs_table(fridge.db, "termination")
    assert [t for name, t in rows.values() if name == "RF1"] == ["none"]


def test_no_terminations_records_nothing_new(fridge):
    cal = _run(fridge)
    assert cal["terminations"] == {} and cal["cal_standards"] == {} and cal["cooldown"] is None
    assert ecal.find_set(None, fridge.db) == cal
    import sqlite3
    con = sqlite3.connect(fridge.db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
    con.close()
    assert not cols & {"termination", "ecal_terminations", "cal_std", "cal_standards",
                       "cooldown"}


@pytest.mark.parametrize("terms", [{0: "short"}, {7: "load"}, {"2": "short"},
                                   {True: "short"}, {2: ""}, {2: None}, ["short"]])
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


# ---------------------------------------------------------------------------
# Known calibration standards on the ports -> the calibration database
# ---------------------------------------------------------------------------

REGISTRY = {
    "flat_short": {"type": "short", "description": "flush SMA short", "part": "P-1",
                   "serial": "S1", "offset_mm": 0},
    "open_1": {"type": "open", "description": "SMA open", "part": "P-2", "serial": "S2"},
    "offset_60": {"type": "short", "description": "offset short", "offset_mm": 60.0},
}


@pytest.fixture
def caldirs(fridge, monkeypatch, tmp_path):
    """Point sweep_db's default Sweeps/ at a temp folder; return the calibration paths."""
    sweeps = tmp_path / "Sweeps"
    monkeypatch.setattr(sweep_db, "_default_out_root", lambda: sweeps)
    return sweeps / "calibration", sweeps / "calibration" / "mm4250_calibration.db"


def _run_std(fake, **kw):
    kw.setdefault("standards_file", REGISTRY)
    kw.setdefault("cooldown", "CD1")
    kw.setdefault("repeats", 1)
    return sweep_db.run_ecal_set("20261020", "3K", "SN0077", vna=object(), switch=object(), **kw)


def test_cal_standards_go_to_the_calibration_database(fridge, caldirs):
    cal_dir, cal_db = caldirs
    cal = _run_std(fridge, channels=[1, 3, 5], repeats=2,
                   cal_standards={3: "open_1", 1: "flat_short"},
                   terminations={5: "resonator"}, mxc_temp_k=3.1, touchstone=True)
    assert cal_db.is_file() and not fridge.db.exists()           # its own database
    assert (cal_dir / "db_backups").is_dir()                      # and its own backups
    assert sorted(p.name for p in (cal_dir / "SN0077" / "20261020" / "3K" / "ecal_uncal")
                  .glob("RF1_run*.s2p")) != []                    # files beside it too
    assert cal["cal_standards"] == {1: "flat_short", 3: "open_1"}
    assert cal["terminations"] == {1: "flat_short", 3: "open_1", 5: "resonator"}
    assert cal["labels"] == {1: "flat_short (short)", 3: "open_1 (open)"}
    assert cal["cooldown"] == "CD1"
    assert ecal.find_set(None, cal_db) == cal

    rows = _runs_table(cal_db, "cal_std", "cal_std_type", "cal_std_part", "cal_std_offset_mm",
                       "cooldown", "ecal_role")
    ports = {name: r for name, *r in rows.values() if r[-1] == "port"}
    assert ports["RF1"][:4] == ["flat_short", "short", "P-1", 0]
    assert ports["RF3"][:4] == ["open_1", "open", "P-2", None]
    assert ports["RF5"][0] is None                                # not a standard
    assert {r[5] for r in rows.values()} == {"CD1"}               # cooldown on every run

    listed = ecal.list_standard_runs(cal_db)
    assert [(r["standard"], r["channel"], r["repeat"]) for r in listed] == \
        [("flat_short", 1, 1), ("open_1", 3, 1), ("flat_short", 1, 2), ("open_1", 3, 2)]
    first = listed[0]
    assert (first["cooldown"], first["switch_serials"], first["temp_str"], first["mxc_temp_k"],
            first["type"], first["serial"], first["set"]) == \
        ("CD1", "SN0077", "3K", 3.1, "short", "S1", cal["set"])
    import re
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", first["measured_at"])
    assert ecal.list_standard_runs(cal_db, standard="open_1")[0]["channel"] == 3
    assert ecal.list_standard_runs(cal_db, cooldown="CD2") == []

    # The set itself corrects like any other, from the calibration database.
    result = ecal.correct_set(None, fridge.ideals_dir, db_path=cal_db)
    np.testing.assert_allclose(result["ports"][3], fridge.dut[3], atol=1e-9)
    assert result["cal_standards"] == {1: "flat_short", 3: "open_1"}
    assert result["cooldown"] == "CD1"
    paths = ecal.export_corrected(result, db_path=cal_db)
    assert paths[0].parent.parent.parent.parent.parent == cal_dir   # not Sweeps/calibration/Sweeps


def test_cal_standards_store_the_whole_entry_on_every_run(fridge, caldirs):
    import json
    _, cal_db = caldirs
    _run_std(fridge, channels=[2], cal_standards={2: "offset_60"})
    stored = {v for _, v in _runs_table(cal_db, "cal_standards").values()}
    assert len(stored) == 1
    assert json.loads(stored.pop()) == {"2": dict(REGISTRY["offset_60"], name="offset_60")}


def test_cal_standards_respect_an_explicit_db_path(fridge, caldirs):
    _, cal_db = caldirs
    cal = _run_std(fridge, channels=[1], cal_standards={1: "flat_short"}, db_path=fridge.db)
    assert fridge.db.is_file() and not cal_db.exists()
    assert ecal.list_standard_runs(fridge.db)[0]["set"] == cal["set"]


@pytest.mark.parametrize("kw, match", [
    ({"cal_standards": {1: "flat_short"}, "cooldown": None}, "cooldown"),
    ({"cal_standards": {1: "nope"}}, "isn't in the standards file"),
    ({"cal_standards": {4: "flat_short"}}, "doesn't measure"),
    ({"cal_standards": {"1": "flat_short"}}, "channel numbers"),
    ({"cal_standards": {}}, "non-empty"),
    ({"cal_standards": {1: "flat_short"}, "terminations": {1: "load"}}, "terminations"),
    ({"cal_standards": {1: "flat_short"}, "standards_file": {"x": {"type": "lossy"}}}, "type"),
    ({"cal_standards": {1: "flat_short"},
      "standards_file": {"flat_short": {"type": "short", "offset_mm": "5"}}}, "offset_mm"),
    ({"cooldown": "  "}, "cooldown"),
])
def test_bad_cal_standards_fail_before_anything_moves(fridge, caldirs, kw, match):
    with pytest.raises(ValueError, match=match):
        _run_std(fridge, channels=[1, 2], **kw)
    assert fridge.positions == []
    assert not caldirs[1].exists()


def test_standards_file_is_read_from_the_calibration_folder(fridge, caldirs, monkeypatch):
    import json
    import read_db
    cal_dir, cal_db = caldirs
    monkeypatch.setattr(read_db, "default_cal_dir", lambda: cal_dir)
    with pytest.raises(FileNotFoundError, match="standards_example.json"):
        _run_std(fridge, channels=[1], cal_standards={1: "flat_short"}, standards_file=None)
    cal_dir.mkdir(parents=True)
    (cal_dir / "standards.json").write_text(json.dumps(REGISTRY))
    cal = _run_std(fridge, channels=[1], cal_standards={1: "flat_short"}, standards_file=None)
    assert cal["cal_standards"] == {1: "flat_short"}


def test_the_example_standards_file_is_valid():
    import read_db
    reg = read_db.load_standards(MEAS / "standards_example.json")
    assert {e["type"] for e in reg.values()} <= set(read_db.STANDARD_TYPES)


# ---------------------------------------------------------------------------
# Reading the fridge temperature per run
# ---------------------------------------------------------------------------

def test_temp_reader_reading_goes_on_every_run(fridge):
    readings = iter(np.linspace(3.0, 3.5, 100))
    cal = _run(fridge, channels=[1, 2], temp_reader=lambda: next(readings))
    rows = _runs_table(fridge.db, "mxc_temp_k")
    temps = [rows[i][1] for i in sorted(rows)]                    # in run order
    assert len(temps) == 8 and all(t is not None for t in temps)
    assert temps == sorted(temps) and temps[0] > 3.0              # one fresh reading per run
    assert ecal.list_sets(fridge.db)[-1]["mxc_temp_k"] == temps[0]
    assert cal["set"]


def test_temp_reader_that_fails_up_front_stops_the_set(fridge):
    def broken():
        raise OSError("fridge driver not connected")
    with pytest.raises(RuntimeError, match="temp_reader failed before"):
        _run(fridge, temp_reader=broken)
    assert fridge.positions == []


def test_temp_reader_that_fails_partway_only_loses_readings(fridge, capsys):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 4:
            raise OSError("timeout")
        return 3.2
    _run(fridge, channels=[1], temp_reader=flaky)
    temps = [t for _, t in _runs_table(fridge.db, "mxc_temp_k").values()]
    assert len(temps) == 7 and temps.count(None) == 1
    assert "saving this run without mxc_temp_k" in capsys.readouterr().out


@pytest.mark.parametrize("value", [None, "warm", -1, 0, True])
def test_temp_reader_must_return_kelvin(fridge, value):
    with pytest.raises(RuntimeError, match="temp_reader"):
        _run(fridge, temp_reader=lambda: value)


def test_mxc_temp_k_and_temp_reader_together_is_an_error(fridge):
    with pytest.raises(ValueError, match="not both"):
        _run(fridge, mxc_temp_k=3.0, temp_reader=lambda: 3.0)


def test_ideals_range_is_enforced(fridge):
    with pytest.raises(ValueError, match="covers"):
        ecal.load_ideals(fridge.ideals_dir, 1, np.array([50e6, 1e9]))


# ---------------------------------------------------------------------------
# Other definitions: "perfect", and your own from a kit set
# ---------------------------------------------------------------------------

def test_perfect_ideals_put_the_plane_at_the_internal_standards(fridge):
    rng = np.random.default_rng(7)
    inside = _box(rng, len(fridge.freq))        # VNA -> the internal standards' plane
    fridge.raw_std = {s: _through(inside, np.full(len(fridge.freq), g))
                      for s, g in ecal.PERFECT_VALUES.items()}
    cal = _run(fridge, channels=[2])
    result = ecal.correct_set(cal, "perfect", db_path=fridge.db)
    assert result["ideals_dir"] == "perfect"
    # What's left is the RF2 path plus the DUT, seen from that plane.
    seen = _back(inside, _through(fridge.boxes[2], fridge.dut[2]))
    np.testing.assert_allclose(result["ports"][2], seen, atol=1e-9)
    assert ecal.load_ideals("Perfect", 1, fridge.freq)["short"][0] == -1


def _run_kit(fridge, monkeypatch, kit=None, **kw):
    """run_kit_set with a fake hand: each prompt 'attaches' that kit standard."""
    kit = kit or ecal.PERFECT_VALUES
    prompts = []

    def hand(msg):
        prompts.append(msg)
        fridge.attached = next(g for s, g in kit.items() if f"kit {s} " in msg)

    monkeypatch.setattr("builtins.input", hand)
    out = sweep_db.run_kit_set("20261015", "295K", "SN0077", db_path=fridge.db,
                               vna=object(), switch=object(), **kw)
    fridge.attached = None
    return out, prompts


def test_kit_set_order_prompts_and_switch_left_open(fridge, monkeypatch):
    kit, prompts = _run_kit(fridge, monkeypatch, channels=[1, 3])
    std = list(sweep_db.ECAL_STANDARDS)
    each = ["ALL_OPEN", 1]
    assert fridge.positions == (std + each * 3 + ["ALL_OPEN", 3] * 3 + std + ["ALL_OPEN"])
    assert prompts[0].startswith("Put the kit open on RF1") and len(prompts) == 6
    assert ecal.find_kit_set(None, fridge.db) == kit
    assert ecal.list_kit_sets(fridge.db)[-1]["n_runs"] == 12
    by_id = {r["run_id"]: r["dut_label"] for r in list_runs(fridge.db)}
    assert by_id[kit["kit"][3]["short"]] == "kit short"
    assert ecal.list_sets(fridge.db) == []          # kit runs aren't an e-cal set


def test_make_ideals_recovers_the_switchs_own_definitions(fridge, monkeypatch, tmp_path):
    kit, _ = _run_kit(fridge, monkeypatch, channels=[1, 4])
    mine = ecal.make_ideals(kit, out_dir=tmp_path / "mine", db_path=fridge.db)
    for ch in (1, 4):
        got = ecal.load_ideals(mine, ch, fridge.freq)
        for s, g in fridge.ideals(ch).items():
            np.testing.assert_allclose(got[s], g, atol=1e-9)
    # ...so an e-cal set corrected with them hits the DUT, like NIST's do here.
    cal = _run(fridge, channels=[1, 4])
    result = ecal.correct_set(cal, mine, db_path=fridge.db)
    np.testing.assert_allclose(result["ports"][4], fridge.dut[4], atol=1e-8)
    with pytest.raises(FileExistsError):
        ecal.make_ideals(kit, out_dir=mine, db_path=fridge.db)
    ecal.make_ideals(None, out_dir=mine, overwrite=True, db_path=fridge.db)   # None -> latest


def test_make_ideals_uses_real_kit_definitions(fridge, monkeypatch, tmp_path):
    real = {"open": 0.98 * np.exp(-0.2j), "short": -0.99 * np.exp(0.1j), "load": 0.02 + 0.01j}
    kit, _ = _run_kit(fridge, monkeypatch, kit=real, channels=[2])
    right = ecal.make_ideals(kit, out_dir=tmp_path / "a", kit_defs=real, db_path=fridge.db)
    wrong = ecal.make_ideals(kit, out_dir=tmp_path / "b", db_path=fridge.db)   # assumes perfect
    truth = fridge.ideals(2)["open"]
    np.testing.assert_allclose(ecal.load_ideals(right, 2, fridge.freq)["open"], truth, atol=1e-9)
    assert np.max(np.abs(ecal.load_ideals(wrong, 2, fridge.freq)["open"] - truth)) > 1e-3
    # Default folder: beside the database, named for the set.
    default = ecal.make_ideals(kit, db_path=fridge.db)
    assert default.parent == fridge.db.parent and default.name.startswith("ideals_295K_SN0077_kit")


def test_kit_set_with_vna_cal_on_purpose(fridge, monkeypatch, tmp_path):
    _vna_cal_on(monkeypatch)
    kit, _ = _run_kit(fridge, monkeypatch, channels=[5], vna_cal=True)
    mine = ecal.make_ideals(kit, out_dir=tmp_path / "mine", db_path=fridge.db)
    np.testing.assert_allclose(ecal.load_ideals(mine, 5, fridge.freq)["load"],
                               fridge.ideals(5)["load"], atol=1e-9)


def test_kit_set_refuses_with_vna_correction_on(fridge, monkeypatch):
    monkeypatch.setattr(sweep_db, "_cal_state", lambda vna: "cal")
    with pytest.raises(RuntimeError, match="correction is ON"):
        _run_kit(fridge, monkeypatch)
    assert fridge.positions == []


def test_plot_compare(fridge):
    import matplotlib
    matplotlib.use("Agg")
    cal = _run(fridge, channels=[1, 5], labels={5: "resonator"})
    results = [ecal.correct_set(cal, d, db_path=fridge.db) for d in (fridge.ideals_dir, "perfect")]
    ax_m, _ = ecal.plot_compare(results, channel=5, show_raw=True)
    assert [t.get_text() for t in ax_m.get_legend().get_texts()] == \
        ["raw S21 (uncalibrated)", "ideals_3K", "perfect"]
    assert "RF5 (resonator)" in ax_m.get_title()
    with pytest.raises(ValueError, match="labels"):
        ecal.plot_compare(results, labels=["one"])


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


def test_cal_standards_with_bad_labels_still_says_why(fridge, caldirs):
    with pytest.raises(TypeError, match="must be a dict"):
        _run_std(fridge, channels=[1], cal_standards={1: "flat_short"}, labels=["x"])
    assert fridge.positions == []
