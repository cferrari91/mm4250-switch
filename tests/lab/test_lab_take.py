"""
A take on the fake rig, end to end: vna.take -> db -> ecal.correct_take ->
db.save_corrected -> db.load_take, plus the cross-check against the older
measurements/ecal.py on the very same runs.
"""

import importlib.util
import json
import sys

import numpy as np
import pytest

from conftest import LAB, db, ecal, plots, vna

STD = list(vna.STANDARDS)


def _take(cfg, rig, dbpath, **kw):
    return vna.take(cfg, rig.vna, rig.switch, rig.fridge, save=db.Saver(dbpath, "take"), **kw)


def _correct(dbpath, take_id, ideals):
    take = db.load_take(take_id, dbpath)
    result = ecal.correct_take(take, definitions=ideals)
    ids = db.save_corrected(dbpath, result, take)
    return take, result, ids


def test_full_take_order_metadata_and_switch_left_open(cfg, rig, dbpath):
    t = _take(cfg, rig, dbpath, note="first take")
    chans = [1, 2, 3, 4, 5, 6]
    assert [p for p, _ in rig.switch.moves] == STD + chans + STD + ["ALL_OPEN"]
    assert rig.switch.last == "ALL_OPEN"
    assert not any(hot for _, hot in rig.switch.moves), "the switch moved with the source on"
    assert t["channels"] == chans and len(t["repeats"]) == 1
    take = db.load_take(None, dbpath)                       # None = newest
    assert take["take_id"] == t["take_id"]
    run = take["repeats"][0]["ports"][4]
    assert sorted(run["data"]) == ["S11", "S21"]             # forward-only by default
    assert rig.vna.reverse_sweeps == 0
    m = run["meta"]
    assert m["vna_sweep_mode"] == "forward"
    assert (m["kind"], m["position"], m["port"], m["repeat"]) == ("raw", "RF4", "resonator", 1)
    assert m["mxc_temp_k"] == 295.0 and m["temp_str"] == "295K" and m["note"] == "first take"
    assert m["switch_serial"] == "SN0077"
    assert json.loads(m["cooldown_json"])["ports"]["RF4"] == "resonator"   # the whole file, on every run
    assert take["repeats"][0]["after"]["INTERNAL_LOAD"]["meta"]["standards_when"] == "after"
    assert take["repeats"][0]["before"]["ALL_OPEN"]["meta"]["kind"] == "standard"


def test_vna_settings_are_recorded_on_every_run(cfg, rig, dbpath):
    _take(cfg, rig, dbpath, channels=[4])
    import sqlite3
    con = sqlite3.connect(dbpath)
    rows = con.execute("SELECT vna_points, vna_power_dbm, vna_correction_enabled, vna_start_hz "
                       "FROM runs").fetchall()
    con.close()
    assert len(rows) == 7 and set(rows) == {(201, -20, 0, 510e6)}


def test_single_channel_take_still_measures_both_sets_of_standards(cfg, rig, dbpath, ideals):
    t = _take(cfg, rig, dbpath, channels=[4])
    assert [p for p, _ in rig.switch.moves] == STD + [4] + STD + ["ALL_OPEN"]
    take, result, ids = _correct(dbpath, t["take_id"], ideals)
    assert list(result["corrected"][0]) == [4] and len(ids) == 1
    np.testing.assert_allclose(result["corrected"][0][4], rig.chain.dut[4], atol=1e-9)


def test_repeats_from_the_cooldown_file(cfg, rig, dbpath, ideals):
    cfg["repeats"] = 2
    t = _take(cfg, rig, dbpath, channels=[1, 4])
    block = STD + [1, 4] + STD
    assert [p for p, _ in rig.switch.moves] == block + block + ["ALL_OPEN"]
    take, result, ids = _correct(dbpath, t["take_id"], ideals)
    assert len(ids) == 4                                    # one per channel per repeat
    corr = db.load_take(t["take_id"], dbpath)["corrected"]
    assert sorted((c["repeat"], c["channel"]) for c in corr) == [(1, 1), (1, 4), (2, 1), (2, 4)]


def test_take_dies_halfway_switch_still_opens_and_runs_so_far_are_kept(cfg, rig, dbpath):
    rig.chain.fail_on = 3
    with pytest.raises(RuntimeError, match="simulated"):
        _take(cfg, rig, dbpath)
    assert rig.switch.last == "ALL_OPEN"
    assert len(db.load_take(None, dbpath)["repeats"][0]["ports"]) == 2      # RF1, RF2 saved


def test_switch_opens_even_when_the_vna_link_dies(cfg, rig, dbpath):
    # select() can't run without the VNA, so leave_switch_open uses the switch's own link.
    def dead(*a):
        raise OSError("VISA connection lost")
    sweep = rig.vna.sweep

    def sweep_then_die():
        if rig.switch.actual() == 2:              # the link drops during RF2's sweep
            rig.vna.output = dead
            raise OSError("VISA connection lost")
        sweep()
    rig.vna.sweep = sweep_then_die
    with pytest.raises(OSError):
        _take(cfg, rig, dbpath)
    assert rig.switch.moves[-1] == ("ALL_OPEN", None)      # opened over the switch's own link


def test_refuses_with_vna_correction_on_and_nothing_moves(cfg, rig, dbpath, monkeypatch):
    real_write = rig.vna.write                                  # DEAC ignored: cal stays on
    monkeypatch.setattr(rig.vna, "write",
                        lambda cmd: None if cmd == "SENS:CORR:CSET:DEAC" else real_write(cmd))
    rig.vna.correction = 1
    with pytest.raises(RuntimeError, match="correction is ON"):
        _take(cfg, rig, dbpath)
    assert rig.switch.moves == []


def test_power_cap(cfg, rig, dbpath):
    cfg["sweep"]["power_dbm"] = 5
    with pytest.raises(ValueError, match="above the 0 dBm cap"):
        _take(cfg, rig, dbpath)
    assert rig.switch.moves == []


@pytest.mark.parametrize("channels, match", [([7], "RF7 isn't in"), ([4, 4], "duplicates"),
                                             ([], "No channels")])
def test_bad_channels_fail_before_anything_moves(cfg, rig, dbpath, channels, match):
    with pytest.raises(ValueError, match=match):
        _take(cfg, rig, dbpath, channels=channels)
    assert rig.switch.moves == []


@pytest.mark.parametrize("reading", [None, OSError("no VPN"), -1.0])
def test_no_temperature_never_stops_a_take(cfg, rig, dbpath, reading):
    rig.fridge.kelvin = reading
    t = _take(cfg, rig, dbpath, channels=[4])
    assert t["temp_str"] == "noT"
    take = db.load_take(t["take_id"], dbpath)
    assert take["temp_k"] is None and take["repeats"][0]["ports"][4]["meta"]["mxc_temp_k"] is None
    assert ecal.correct_take(take) is None                  # no definitions picked: nothing corrected


def test_no_fridge_at_all(cfg, rig, dbpath):
    t = vna.take(cfg, rig.vna, rig.switch, None, channels=[4], save=db.Saver(dbpath, "take"))
    assert t["temp_k"] is None and rig.switch.last == "ALL_OPEN"


def test_definitions_follow_the_temperature(cfg, rig, dbpath):
    nist = LAB.parents[2] / "nist_MM4250_calibration_data_2025"
    if not nist.is_dir():
        pytest.skip("NIST's calibration data isn't beside this repo")
    t = _take(cfg, rig, dbpath, channels=[4])
    take = db.load_take(t["take_id"], dbpath)
    assert ecal.correct_take(take)["definitions"] == "NIST 295K"
    take["temp_k"] = 0.012
    assert ecal.correct_take(take)["definitions"] == "NIST 3K"
    assert ecal.correct_take(take)["chosen"] == "auto"


def test_in_between_temperature_saves_raw_only_unless_chosen_by_hand(cfg, rig, dbpath, ideals, capsys):
    t = _take(cfg, rig, dbpath, channels=[4])
    take = db.load_take(t["take_id"], dbpath)
    take["temp_k"] = 60.0                                   # on the way down: no NIST set fits
    assert ecal.correct_take(take) is None
    assert "NOTHING WAS CORRECTED" in capsys.readouterr().out
    result = ecal.correct_take(take, ideals)                # by hand: corrected, and says so
    assert result["chosen"] == "by hand"
    ids = db.save_corrected(dbpath, result, take)
    again = db.load_take(t["take_id"], dbpath)
    assert [c["meta"]["definitions_chosen"] for c in again["corrected"]] == ["by hand"] * len(ids)


def test_corrected_runs_link_to_their_raw_runs_and_refuse_duplicates(cfg, rig, dbpath, ideals):
    t = _take(cfg, rig, dbpath, channels=[1, 4])
    take, result, ids = _correct(dbpath, t["take_id"], ideals)
    again = db.load_take(t["take_id"], dbpath)
    c4 = next(c for c in again["corrected"] if c["channel"] == 4)
    src = json.loads(c4["meta"]["source_run_ids"])
    blk = take["repeats"][0]
    assert src[0] == blk["ports"][4]["run_id"]
    assert set(src[1:]) == {r["run_id"] for part in ("before", "after") for r in blk[part].values()}
    assert c4["meta"]["corrected_from"] == "S21" and c4["meta"]["kind"] == "corrected"
    assert c4["definitions"] == "ideals_fake"
    np.testing.assert_allclose(c4["data"]["S11"], rig.chain.dut[4], atol=1e-9)
    assert db.save_corrected(dbpath, result, again) == []          # same definitions: refused
    assert len(db.load_take(t["take_id"], dbpath)["corrected"]) == 2


def test_drift_is_reported(cfg, rig, dbpath, capsys):
    rig.chain.drift = 0.01
    t = _take(cfg, rig, dbpath, channels=[1])
    d = ecal.drift(db.load_take(t["take_id"], dbpath))
    assert np.median(d["repeats"][0]["open"]) > 1e-3
    assert "repeat 1 drift (S21)" in capsys.readouterr().out


def test_plot_take(cfg, rig, dbpath, ideals, tmp_path):
    t = _take(cfg, rig, dbpath, channels=[1, 4])
    take, _, _ = _correct(dbpath, t["take_id"], ideals)
    take = db.load_take(t["take_id"], dbpath)
    fig = plots.plot_take(take, save=tmp_path / "take.png")
    assert (tmp_path / "take.png").is_file()
    assert "RF4 (resonator)" in [x.get_text() for x in fig.axes[0].get_legend().get_texts()]
    plots.plot_take(db.load_take(_take(cfg, rig, dbpath, channels=[4])["take_id"], dbpath))  # uncorrected


# ---------------------------------------------------------------------------
# Cross-check with the older measurements/ecal.py, same runs
# ---------------------------------------------------------------------------

def _old_ecal():
    meas = LAB.parent
    if str(meas) not in sys.path:
        sys.path.append(str(meas))
    spec = importlib.util.spec_from_file_location("old_ecal_for_lab_tests", meas / "ecal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("repeats", [1, 2])
def test_new_correction_matches_the_old_ecal_correct_set(cfg, rig, dbpath, ideals, repeats):
    old = _old_ecal()
    cfg["repeats"] = repeats
    rig.chain.drift = 0.003            # so before/after differ and the mean matters
    t = _take(cfg, rig, dbpath, channels=[2, 4, 6])
    take = db.load_take(t["take_id"], dbpath)
    new = ecal.correct_take(take, definitions=ideals)
    # The same runs, in the shape the old run_ecal_set returned.
    old_set = {"set": t["take_id"], "switch_serials": "SN0077", "date_str": "x", "temp_str": "295K",
               "repeats": [{part: {k: r["run_id"] for k, r in b[part].items()}
                            for part in ("before", "ports", "after")} for b in take["repeats"]]}
    for k in range(1, repeats + 1):
        o = old.correct_set(old_set, ideals, sparam="S21", repeat=k, db_path=dbpath)
        for ch in (2, 4, 6):
            np.testing.assert_allclose(new["corrected"][k - 1][ch], o["ports"][ch], rtol=0, atol=1e-12)
    o_all = old.correct_set(old_set, ideals, sparam="S21", db_path=dbpath)     # mean over repeats
    for ch in (2, 4, 6):
        mean = np.mean([c[ch] for c in new["corrected"]], axis=0)
        np.testing.assert_allclose(mean, o_all["ports"][ch], rtol=0, atol=1e-12)
    od = old.drift(old_set, db_path=dbpath, sparam="S21", verbose=False)
    nd = ecal.drift(take, verbose=False)
    for k in range(repeats):
        for s in ("open", "short", "load"):
            np.testing.assert_allclose(nd["repeats"][k][s], od["repeats_cal"][k][s], rtol=0, atol=1e-12)


def test_without_drift_the_take_recovers_the_known_dut_exactly(cfg, rig, dbpath, ideals):
    t = _take(cfg, rig, dbpath)
    _, result, _ = _correct(dbpath, t["take_id"], ideals)
    for ch in range(1, 7):
        np.testing.assert_allclose(result["corrected"][0][ch], rig.chain.dut[ch], atol=1e-9)
