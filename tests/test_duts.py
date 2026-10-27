"""Hardware-free tests for measurements/duts.py (the resonator part).

A synthetic reflection resonator with a known Q_L and coupling, plus a bit
of cable (a linear phase), on A's default sweep (490-630 MHz, 2001 points),
stands in for an e-cal result. Needs scikit-rf.

The last tests run section B end to end on test_ecal's fake fridge (a
known error box per port): an A4 set with the resonator on RF1 and an
SNTJ on RF2, copied into a resonator database, corrected from that copy,
fitted and saved.

    python -m pytest tests/test_duts.py
"""

import sys
from pathlib import Path

import numpy as np
import pytest

MEAS = Path(__file__).resolve().parents[1] / "measurements"
sys.path.insert(0, str(MEAS))

pytest.importorskip("skrf")
import duts  # noqa: E402


def resonator(f0=549e6, ql=40.0, beta=0.8, start=490e6, stop=630e6, n=2001, delay_ns=1.45,
              s_v=0.95 * np.exp(0.4j)):
    """S11 of a reflection resonator: S_V (1 - d / (1 + 2j Q_L t)), d = 2 beta / (1 + beta)."""
    f = np.linspace(start, stop, n)
    d = 2 * beta / (1 + beta)
    t = (f - f0) / f0
    return f, s_v * (1 - d / (1 + 2j * ql * t)) * np.exp(-2j * np.pi * f * delay_ns * 1e-9)


def ecal_result(ports, labels=None, set_id="20261015T120000.000", temp="3K"):
    """What ecal.correct_set returns, as far as duts reads it."""
    freq = next(iter(ports.values()))[0]
    return {"set": set_id, "switch_serials": "SN0077", "date_str": set_id[:8], "temp_str": temp,
            "sparam": "S21", "ideals_dir": "perfect", "freq": freq,
            "ports": {ch: g for ch, (_, g) in ports.items()},
            "labels": labels or {}, "cooldown": "CD1", "vna_cal": False}


def expected(ql, beta):
    q0 = ql * (1 + beta)
    return q0, q0 / beta


@pytest.mark.parametrize("ql, beta", [(40.0, 0.8), (40.0, 1.6), (200.0, 0.5)])
def test_fit_recovers_known_q_and_coupling(ql, beta):
    res = ecal_result({1: resonator(ql=ql, beta=beta)}, {1: "resonator"})
    fit = duts.fit_resonator(res, verbose=False)
    q0, qe = expected(ql, beta)
    assert fit["f0_MHz"] == pytest.approx(549.0, abs=0.01)
    assert fit["Q_L"] == pytest.approx(ql, rel=0.01)
    assert fit["Q_0"] == pytest.approx(q0, rel=0.02)
    assert fit["Q_e"] == pytest.approx(qe, rel=0.02)
    assert fit["beta"] == pytest.approx(beta, rel=0.03)
    assert fit["channel"] == 1 and fit["set"] == res["set"] and fit["ideals"] == "perfect"
    # the plotted curve follows the data (line-length term included)
    assert np.max(np.abs(fit["fit"] - fit["data"])) < 1e-3


def test_guess_lands_near_the_truth():
    f, g = resonator(ql=120.0)
    f0, q = duts.guess_resonance(f, g)
    assert f0 == pytest.approx(549e6, abs=0.2e6)
    assert 30 < q < 500


def test_channel_comes_from_the_label_or_must_be_given():
    ports = {1: resonator(f0=520e6), 2: resonator(f0=600e6)}
    fit = duts.fit_resonator(ecal_result(ports, {1: "SNTJ", 2: "Resonator"}), verbose=False)
    assert fit["channel"] == 2 and fit["f0_MHz"] == pytest.approx(600.0, abs=0.01)
    with pytest.raises(ValueError, match="Which channel"):
        duts.fit_resonator(ecal_result(ports, {1: "SNTJ"}), verbose=False)
    with pytest.raises(KeyError):
        duts.fit_resonator(ecal_result(ports), channel=5, verbose=False)
    assert duts.fit_resonator(ecal_result(ports), channel=1, verbose=False)["channel"] == 1


def test_s1p_and_tuple_sources(tmp_path):
    f, g = resonator()
    path = tmp_path / "alone.s1p"
    with open(path, "w") as fh:
        fh.write("# HZ S RI R 50\n")
        for fi, gi in zip(f, g):
            fh.write(f"{fi:.6f} {gi.real:.9f} {gi.imag:.9f}\n")
    a = duts.fit_resonator(path, verbose=False)
    b = duts.fit_resonator((f, g), label="tuple", verbose=False)
    assert a["label"] == "alone.s1p" and b["label"] == "tuple"
    assert a["Q_0"] == pytest.approx(b["Q_0"], rel=1e-4)


def test_too_few_points_warns(capsys):
    # Q_L 600 on A's 70 kHz steps: the 2 f/Q window is ~1.8 MHz, ~26 points
    duts.fit_resonator(ecal_result({1: resonator(ql=600.0)}), verbose=True)
    assert "points in the fit window" in capsys.readouterr().out


def test_save_list_load_and_refit(tmp_path):
    import qcodes as qc

    db = tmp_path / "duts" / "resonator.db"
    before = qc.config.core.db_location
    fit = duts.fit_resonator(ecal_result({1: resonator()}, {1: "resonator"}), verbose=False)
    run = duts.save_resonator(fit, note="test", db_path=db, main_db_path=tmp_path / "none.db")
    assert qc.config.core.db_location == before          # QCoDeS left where it was
    assert (db.parent / "db_backups").is_dir()

    rows = duts.list_resonator(db, verbose=False)
    assert len(rows) == 1 and rows[0]["run_id"] == run
    row = rows[0]
    assert row["fit_set"] == fit["set"] and row["channel"] == 1 and row["temp_str"] == "3K"
    assert row["Q_0"] == pytest.approx(fit["Q_0"]) and row["note"] == "test"
    assert row["mxc_temp_k"] is None

    freq, gamma, meta = duts.load_resonator(run, db)
    assert np.allclose(freq, fit["trace_freq"]) and np.allclose(gamma, fit["trace"])
    again = duts.fit_resonator((freq, gamma), verbose=False)
    assert again["Q_0"] == pytest.approx(fit["Q_0"], rel=1e-6)

    with pytest.raises(ValueError, match="Already saved"):
        duts.save_resonator(fit, db_path=db, main_db_path=tmp_path / "none.db")
    duts.save_resonator(fit, db_path=db, main_db_path=tmp_path / "none.db", allow_duplicate=True)
    assert len(duts.list_resonator(db, verbose=False)) == 2

    with pytest.raises(ValueError, match="isn't a fit of an e-cal result"):
        duts.save_resonator(again, db_path=db)


def test_vs_temp_and_plots(tmp_path):
    db = tmp_path / "resonator.db"
    for i, (temp, ql) in enumerate((("295K", 40.0), ("3K", 120.0), ("20mK", 300.0))):
        res = ecal_result({1: resonator(ql=ql)}, {1: "resonator"},
                          set_id=f"2026101{i}T120000.000", temp=temp)
        duts.save_resonator(duts.fit_resonator(res, verbose=False), db_path=db,
                            main_db_path=tmp_path / "none.db")
    fig, rows = duts.plot_resonator_vs_temp(db, save=tmp_path / "vs_temp")
    assert sorted(duts.temperature_k(r) for r in rows) == [0.02, 3.0, 295.0]
    assert (tmp_path / "vs_temp.pdf").is_file() and (tmp_path / "vs_temp.png").is_file()

    fit = duts.fit_resonator(ecal_result({1: resonator()}), verbose=False)
    ref = duts.fit_resonator(resonator(ql=41.0), label="alone", verbose=False)
    duts.plot_resonator_fit([fit, ref], save=tmp_path / "page.pdf")
    duts.plot_resonator_detail(fit, save=tmp_path / "detail")
    assert (tmp_path / "page.pdf").is_file() and (tmp_path / "detail.png").is_file()
    import matplotlib.pyplot as plt
    plt.close("all")


def test_temperature_labels():
    assert duts.temperature_k({"temp_str": "25mK"}) == pytest.approx(0.025)
    assert duts.temperature_k({"temp_str": "3K"}) == 3.0
    assert duts.temperature_k({"temp_str": "base"}) is None
    assert duts.temperature_k({"temp_str": "3K", "mxc_temp_k": 3.21}) == 3.21


# ---------------------------------------------------------------------------
# Section B end to end: A4 set -> copy_set -> correct_resonator -> fit -> save
# ---------------------------------------------------------------------------

import sweep_db  # noqa: E402
import ecal  # noqa: E402
from test_ecal import Fridge  # noqa: E402


@pytest.fixture
def res_fridge(monkeypatch, tmp_path):
    """test_ecal's fake fridge on A's sweep, with a known resonator on RF1."""
    fake = Fridge(n=2001)
    fake.freq = np.linspace(490e6, 630e6, 2001)
    fake.dut[1] = resonator(ql=40.0, beta=0.8)[1]
    monkeypatch.setattr(sweep_db, "measure_s11", fake.measure_s11)
    monkeypatch.setattr(sweep_db, "measure_2port", fake.measure_2port)
    monkeypatch.setattr(sweep_db, "instrument_state",
                        lambda sparams, vna=None: {"vna_correction_enabled": 0})
    monkeypatch.setattr(sweep_db, "_cal_state", lambda vna: "uncal")
    monkeypatch.setattr(sweep_db, "_select",
                        lambda channel=None, state=None, switch=None, vna=None: None)
    fake.db = tmp_path / "mm4250_sweeps.db"
    fake.res_db = tmp_path / "Sweeps" / "duts" / "resonator.db"
    fake.ideals_dir = tmp_path / "ideals_3K"
    fake.write_ideals(fake.ideals_dir)
    fake.cal = sweep_db.run_ecal_set("20261015", "3K", "SN0077", channels=[1, 2],
                                     labels={1: "resonator", 2: "SNTJ"}, repeats=2,
                                     mxc_temp_k=3.1, cooldown="CD1", db_path=fake.db,
                                     vna=object(), switch=object())
    return fake


def _names(db):
    import sqlite3
    con = sqlite3.connect(db)
    try:
        return [r[0] for r in con.execute("SELECT name FROM runs ORDER BY run_id")]
    finally:
        con.close()


def test_copy_set_takes_the_standards_and_the_resonator_only(res_fridge):
    import qcodes as qc

    f = res_fridge
    main_before = _names(f.db)
    before = qc.config.core.db_location
    set_id = duts.copy_set(f.cal, main_db_path=f.db, db_path=f.res_db)
    assert set_id == f.cal["set"]
    assert qc.config.core.db_location == before
    assert _names(f.db) == main_before                       # main db only read
    names = _names(f.res_db)
    assert len(names) == 2 * (6 + 1) and "RF2" not in names  # 2 repeats x (6 standards + RF1)
    assert (f.res_db.parent / "db_backups").is_dir()

    copied = ecal.find_set(set_id, f.res_db)
    assert copied["labels"] == {1: "resonator"} and copied["cooldown"] == "CD1"
    assert [sorted(b["ports"]) for b in copied["repeats"]] == [[1], [1]]

    # again: nothing new
    duts.copy_set(set_id, main_db_path=f.db, db_path=f.res_db)
    assert len(_names(f.res_db)) == len(names)


def test_correcting_the_copy_matches_the_main_db_and_the_truth(res_fridge):
    f = res_fridge
    set_id = duts.copy_set(f.cal, main_db_path=f.db, db_path=f.res_db)
    from_copy = duts.correct_resonator(set_id, f.ideals_dir, db_path=f.res_db)
    from_main = ecal.correct_set(f.cal, f.ideals_dir, sparam="S21", db_path=f.db)
    np.testing.assert_allclose(from_copy["ports"][1], from_main["ports"][1], atol=1e-12)
    np.testing.assert_allclose(from_copy["ports"][1], f.dut[1], atol=1e-9)
    # A4's cal (main-db run ids) is taken by id only, never by its run ids
    again = duts.correct_resonator(f.cal, f.ideals_dir, db_path=f.res_db)
    np.testing.assert_allclose(again["ports"][1], from_copy["ports"][1])


def test_section_h_end_to_end(res_fridge):
    f = res_fridge
    set_id = duts.copy_set(f.cal, main_db_path=f.db, db_path=f.res_db)
    fit = duts.fit_resonator(duts.correct_resonator(set_id, f.ideals_dir, db_path=f.res_db),
                             verbose=False)
    q0, qe = expected(40.0, 0.8)
    assert fit["channel"] == 1 and fit["dut_label"] == "resonator" and fit["cooldown"] == "CD1"
    assert fit["Q_0"] == pytest.approx(q0, rel=0.02) and fit["Q_e"] == pytest.approx(qe, rel=0.02)

    run = duts.save_resonator(fit, db_path=f.res_db, main_db_path=f.db)
    rows = duts.list_resonator(f.res_db, verbose=False)
    assert [r["run_id"] for r in rows] == [run]               # raw copies aren't listed as fits
    assert rows[0]["mxc_temp_k"] == pytest.approx(3.1)        # from the copied raw runs
    assert [s["set"] for s in ecal.list_sets(f.res_db)] == [set_id]   # the fit isn't a "set"
    assert sorted(ecal.find_set(set_id, f.res_db)["repeats"][0]["ports"]) == [1]


def test_save_copies_the_raw_runs_if_they_are_missing(res_fridge):
    f = res_fridge
    fit = duts.fit_resonator(ecal.correct_set(f.cal, f.ideals_dir, sparam="S21", db_path=f.db),
                             verbose=False)                   # A5's result, from the main db
    assert not f.res_db.exists()
    duts.save_resonator(fit, db_path=f.res_db, main_db_path=f.db)
    assert sorted(ecal.find_set(f.cal["set"], f.res_db)["repeats"][0]["ports"]) == [1]
    assert len(duts.list_resonator(f.res_db, verbose=False)) == 1
