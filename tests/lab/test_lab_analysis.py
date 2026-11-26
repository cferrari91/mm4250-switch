"""
analysis.py: reading Touchstone files, calibrating raw files with their own
standards (must agree with the database path, ecal.correct_take, on the
same take), traces, the resonance quick look, temperatures, and the plots.
"""

import numpy as np
import pytest

from conftest import LAB, db, ecal, plots, vna

import importlib.util
import sys

# analysis.py does `import db, ecal`: hand it the lab modules (conftest loads them as
# lab_db / lab_ecal because the older tests own the plain names), then put back
# whatever had those names.
_saved = {n: sys.modules.get(n) for n in ("db", "ecal")}
sys.modules.update(db=db, ecal=ecal)
try:
    _spec = importlib.util.spec_from_file_location("lab_analysis", LAB / "analysis.py")
    analysis = importlib.util.module_from_spec(_spec)
    sys.modules["lab_analysis"] = analysis
    _spec.loader.exec_module(analysis)
finally:
    for _n, _m in _saved.items():
        if _m is None:
            sys.modules.pop(_n, None)
        else:
            sys.modules[_n] = _m


def _take_with_files(cfg, rig, dbpath, ideals, channels=None, repeats=None):
    if repeats:
        cfg["repeats"] = repeats
    save = db.Saver(dbpath, "take", files=True)
    t = vna.take(cfg, rig.vna, rig.switch, rig.fridge, channels=channels, save=save)
    take = db.load_take(t["take_id"], dbpath)
    result = ecal.correct_take(take, definitions=ideals)
    db.save_corrected(dbpath, result, take, files=True)
    return db.load_take(t["take_id"], dbpath), result, save.folder


def test_files_calibrate_like_the_database(cfg, rig, dbpath, ideals):
    take, result, folder = _take_with_files(cfg, rig, dbpath, ideals)
    files = analysis.load_folder(folder)
    assert {f["position"] for f in files} == {"ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD",
                                              "RF1", "RF2", "RF3", "RF4", "RF5", "RF6"}
    assert sum(f["kind"] == "corrected" for f in files) == 6
    cal = analysis.calibrate_files(files, ideals, sparam="S21")
    assert sorted(cal["corrected"]) == [1, 2, 3, 4, 5, 6]
    for ch, g in cal["corrected"].items():
        np.testing.assert_allclose(g, result["corrected"][0][ch], atol=1e-8)   # files carry 10 digits
        np.testing.assert_allclose(g, rig.chain.dut[ch], atol=1e-8)


def test_first_and_last_standards_and_one_channel(cfg, rig, dbpath, ideals):
    _, _, folder = _take_with_files(cfg, rig, dbpath, ideals, channels=[4])
    files = analysis.load_folder(folder, verbose=False)
    for use in ("first", "last", "mean"):
        cal = analysis.calibrate_files(files, ideals, channels=[4], standards=use, verbose=False)
        np.testing.assert_allclose(cal["corrected"][4], rig.chain.dut[4], atol=1e-8)
    with pytest.raises(ValueError, match="'mean', 'first' or 'last'"):
        analysis.calibrate_files(files, ideals, standards="median", verbose=False)
    with pytest.raises(LookupError, match="No RF<n> files"):
        analysis.calibrate_files(files, ideals, channels=[2], verbose=False)


def test_missing_standard_and_wrong_sparam(cfg, rig, dbpath, ideals):
    _, _, folder = _take_with_files(cfg, rig, dbpath, ideals, channels=[4])
    files = [f for f in analysis.load_folder(folder, verbose=False) if f["position"] != "INTERNAL_LOAD"]
    with pytest.raises(LookupError, match="INTERNAL_LOAD"):
        analysis.calibrate_files(files, ideals, verbose=False)
    one_port = [f for f in analysis.load_folder(folder, verbose=False) if f["kind"] == "corrected"]
    with pytest.raises(LookupError):
        analysis.calibrate_files(one_port, ideals, verbose=False)


def test_save_calibrated_never_overwrites(cfg, rig, dbpath, ideals, tmp_path):
    _, _, folder = _take_with_files(cfg, rig, dbpath, ideals, channels=[4])
    cal = analysis.calibrate_files(analysis.load_folder(folder, verbose=False), ideals, verbose=False)
    out = tmp_path / "cal"
    (p,) = analysis.save_calibrated(cal, out)
    first = p.read_text()
    analysis.save_calibrated(cal, out)                      # again: left alone
    assert p.read_text() == first and p.name.startswith("RF4_calibrated_")
    f, d = ecal.read_touchstone(p)
    np.testing.assert_allclose(d["S11"], cal["corrected"][4], atol=1e-9)


def test_read_touchstone_formats(tmp_path):
    p = tmp_path / "a.s1p"
    p.write_text("! comment\n# MHz S DB R 50\n500 -6.0206 90\n600 0 180\n")
    f, d = ecal.read_touchstone(p)
    np.testing.assert_allclose(f, [500e6, 600e6])
    np.testing.assert_allclose(d["S11"], [0.5j, -1], atol=1e-6)
    q = tmp_path / "b.s2p"                                   # MA, GHz default units, wrapped line
    q.write_text("# GHz S MA\n1 0.5 0 0.1 90\n 0.1 90 0.2 180\n")
    f, d = ecal.read_touchstone(q)
    assert f[0] == 1e9 and np.isclose(d["S21"][0], 0.1j) and np.isclose(d["S22"][0], -0.2)
    with pytest.raises(ValueError):
        ecal.read_touchstone(tmp_path / "c.csv")


@pytest.mark.parametrize("name, pos", [("06_RF1.s2p", "RF1"), ("03_ALL_OPEN_run12.s2p", "ALL_OPEN"),
                                       ("INTERNAL_SHORT_run34.s2p", "INTERNAL_SHORT"),
                                       ("RF4_corrected_rep1_run20.s1p", "RF4"), ("notes.s2p", None),
                                       ("RF12.s2p", None)])
def test_position_from_file_name(name, pos):
    assert analysis.position_of(name) == pos


def _resonator(f0=550e6, q0=2000.0, qe=500.0, delay=2e-9, n=4001, span=10e6):
    f = np.linspace(f0 - span / 2, f0 + span / 2, n)
    x = 2 * (f - f0) / f0
    g = (1 / qe - 1 / q0 - 1j * x) / (1 / qe + 1 / q0 + 1j * x)    # one-port reflection resonator
    return {"label": "test", "freq": f, "gamma": -g * np.exp(-2j * np.pi * f * delay), "kind": "corrected",
            "channel": 4, "meta": {}}


def test_resonance_quick_look_over_and_undercoupled():
    over = analysis.resonance(_resonator(q0=2000, qe=500))         # beta 4
    under = analysis.resonance(_resonator(q0=500, qe=2000))        # beta 0.25
    for r in (over, under):
        assert abs(r["f_min_hz"] - 550e6) < 5e3
        ql = 1 / (1 / 2000 + 1 / 500)
        assert 0.5 * ql < r["q_loaded_rough"] < 2 * ql
    assert over["coupling_guess"] == "over" and under["coupling_guess"] == "under"
    assert over["phase_span_deg"] > 180 > under["phase_span_deg"]
    flat = analysis.resonance({**_resonator(), "gamma": np.exp(1j * np.linspace(0, 1, 4001))})
    assert flat["coupling_guess"] is None and flat["q_loaded_rough"] is None and not flat["clear_dip"]
    wiggle = np.exp(1j * np.linspace(0, 1, 4001)) * (1 - 0.05 * np.exp(-np.linspace(-5, 5, 4001) ** 2))
    shallow = analysis.resonance({**_resonator(), "gamma": wiggle})      # 0.4 dB: noise-sized
    assert shallow["width_hz"] is None and shallow["q_loaded_rough"] is None
    high_q_over = analysis.resonance(_resonator(q0=1e6, qe=500))         # |G| ~ 0 dB, phase wraps
    assert not high_q_over["clear_dip"] and high_q_over["coupling_guess"] == "over"


def test_traces_temperatures_spread_and_plots(cfg, rig, dbpath, ideals, tmp_path):
    take, result, folder = _take_with_files(cfg, rig, dbpath, ideals, channels=[2, 4], repeats=2)
    corr = analysis.traces_from_take(take)
    raw = analysis.traces_from_take(take, which="raw")
    assert [t["channel"] for t in corr] == [2, 4] and all(t["kind"] == "corrected" for t in corr)
    assert all(t["kind"] == "raw" for t in raw)
    runs = analysis.traces_from_runs(db.load_runs([corr_run["run_id"] for corr_run in take["corrected"][:2]], dbpath))
    assert len(runs) == 2
    files = analysis.traces_from_files(sorted(folder.glob("*.s?p"))[:3])
    assert len(files) == 3
    spread = analysis.repeat_spread(take)
    assert set(spread) == {2, 4}
    rows = analysis.temperature_log(dbpath)                 # newest take
    assert len(rows) == 2 * (3 + 2 + 3) and rows[0]["mxp_k_before"] == 295.0
    figs = plots.plot_mag_smith(corr, save=tmp_path / "figs")
    assert len(figs) == 2 and len(list((tmp_path / "figs").glob("*.png"))) == 2
    assert len(plots.plot_mag_smith(corr + raw, together=True, mark_hz=560e6)) == 1
    plots.plot_temperatures(rows, save=tmp_path / "t.png")
    assert (tmp_path / "t.png").is_file()
    zoomed = analysis.zoom(corr[0], 540, 560)
    assert zoomed["freq"].min() >= 540e6 and zoomed["freq"].max() <= 560e6
    analysis.list_runs(dbpath, last=5)
    assert analysis.newest_folder(dbpath.parent / "Sweeps") == folder
