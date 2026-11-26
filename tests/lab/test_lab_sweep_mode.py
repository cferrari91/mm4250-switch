"""
Sweep mode and the port 2 lock, in scripts/switch_vna.py (sv) and in the lab code on top of it.

"forward" (the default) measures S11 and S21 with only VNA port 1 sourcing, and sets port 2's
source state OFF on the VNA, so nothing goes into the LNA's output; "2port" measures all four.
The fake VNA sources port 2 the way the real one does: AUTO only when a trace needs S12/S22,
OFF never, ON always (rig.vna.reverse_sweeps counts sweeps where it sourced).
"""

import json
import sqlite3

import numpy as np
import pytest

from conftest import LAB, db, ecal, vna

sv = vna.sv


def _analysis():
    """measurements/lab/analysis.py, loaded the way test_lab_analysis does (it imports db, ecal)."""
    import importlib.util
    import sys
    if "lab_analysis" in sys.modules:
        return sys.modules["lab_analysis"]
    saved = {n: sys.modules.get(n) for n in ("db", "ecal")}
    sys.modules.update(db=db, ecal=ecal)
    try:
        spec = importlib.util.spec_from_file_location("lab_analysis", LAB / "analysis.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["lab_analysis"] = mod
        spec.loader.exec_module(mod)
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
    return mod


def _params(rig):
    return sorted(tr.trace() for tr in rig.vna.traces)


def _setup(cfg, rig, mode="forward"):
    vna.setup_sweep(rig.vna, db.with_sweep(cfg, 201, -20, mode)["sweep"])


def _measure(rig, pos=4, mode="forward"):
    return sv.measure(rig.switch, rig.vna, pos, mode=mode)


# --- with_sweep and the cooldown file -----------------------------------------------

def test_with_sweep_defaults_to_forward_and_takes_2port():
    base = db.load_cooldown(LAB / "cooldowns" / "CD05.json")
    assert db.with_sweep(base, 201, -20)["sweep"]["mode"] == "forward"
    assert db.with_sweep(base, 201, -20, "2port")["sweep"]["mode"] == "2port"
    assert "mode" not in base["sweep"]


@pytest.mark.parametrize("mode", ["2-port", "Forward", None, "reverse"])
def test_with_sweep_refuses_an_unknown_mode(mode):
    base = db.load_cooldown(LAB / "cooldowns" / "CD05.json")
    with pytest.raises(ValueError, match="SWEEP_MODE must be one of"):
        db.with_sweep(base, 201, -20, mode)


def test_forward_refused_when_the_file_corrects_from_a_reverse_parameter(tmp_path):
    data = json.loads((LAB / "cooldowns" / "CD05.json").read_text())
    data["correct_sparam"] = "S22"
    path = tmp_path / "CD_s22.json"
    path.write_text(json.dumps(data))
    cfg = db.load_cooldown(path)
    with pytest.raises(ValueError, match="correct_sparam is S22"):
        db.with_sweep(cfg, 201, -20)
    assert db.with_sweep(cfg, 201, -20, "2port")["sweep"]["mode"] == "2port"


def test_mode_in_the_cooldown_file_is_refused(tmp_path):
    data = json.loads((LAB / "cooldowns" / "CD05.json").read_text())
    data["sweep"]["mode"] = "2port"
    path = tmp_path / "CD_mode.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="mode now set in the notebook.*SWEEP_MODE"):
        db.load_cooldown(path)


# --- one measurement (sv) ------------------------------------------------------------

def test_forward_locks_port2_off_before_the_source_goes_on(cfg, rig, monkeypatch):
    order = []
    real_write = rig.vna.write
    monkeypatch.setattr(rig.vna, "write", lambda cmd: (order.append(cmd), real_write(cmd))[1])
    rig.vna.output = type(rig.vna.output)(False, on_set=lambda v: order.append(f"OUTPUT {v}"))
    _setup(cfg, rig)
    assert rig.vna.port2_mode == "OFF"
    assert order.index("SOUR1:POW2:MODE OFF") < order.index("OUTPUT True")


def test_forward_measures_only_s11_s21_and_port2_never_sources(cfg, rig):
    _setup(cfg, rig)
    r = _measure(rig)
    assert sorted(r["data"]) == ["S11", "S21"] and _params(rig) == ["S11", "S21"]
    assert rig.vna.directions == [("forward",)] and rig.vna.reverse_sweeps == 0
    assert r["labels"]["vna_sweep_mode"] == "forward" and r["labels"]["vna_port2_source"] == "OFF"
    assert len(r["freq"]) == 201


def test_2port_measures_all_four_with_port2_auto(cfg, rig):
    _setup(cfg, rig, "2port")
    assert rig.vna.port2_mode == "AUTO"
    r = _measure(rig, mode="2port")
    assert sorted(r["data"]) == ["S11", "S12", "S21", "S22"]
    assert rig.vna.directions == [("forward", "reverse")]


def test_forward_and_2port_give_the_same_s11_s21(cfg, rig):
    _setup(cfg, rig)
    fwd = _measure(rig)["data"]
    _setup(cfg, rig, "2port")
    full = _measure(rig, mode="2port")["data"]
    for s in ("S11", "S21"):
        np.testing.assert_array_equal(fwd[s], full[s])


def test_mismatched_modes_are_refused_before_the_switch_moves(cfg, rig):
    _setup(cfg, rig)
    with pytest.raises(RuntimeError, match="reads 'OFF', not AUTO"):
        _measure(rig, mode="2port")
    _setup(cfg, rig, "2port")
    with pytest.raises(RuntimeError, match="reads 'AUTO', not OFF"):
        _measure(rig)
    assert rig.switch.moves == [] and rig.vna.sweeps == 0


def test_port2_turned_back_on_by_hand_is_refused(cfg, rig):
    _setup(cfg, rig)
    rig.vna.port2_mode = "AUTO"                               # someone at the VNA's screen
    with pytest.raises(RuntimeError, match="Nothing measured"):
        _measure(rig)
    assert rig.switch.moves == [] and rig.vna.sweeps == 0


def test_port2_off_stops_it_even_with_a_reverse_trace_on_screen(cfg, rig):
    _setup(cfg, rig)
    try:
        rig.vna.add_trace()                                   # someone adds an S22 trace by hand
    except RuntimeError:
        pass
    rig.vna.traces[-1].trace("S22")
    rig.vna.sweep()                                           # the VNA sweeping on its own
    assert rig.vna.reverse_sweeps == 0 and rig.vna.directions[-1] == ("forward", "reverse, port 2 off")


def test_leftover_reverse_traces_are_refused_then_removed(cfg, rig):
    _setup(cfg, rig, "2port")
    _measure(rig, mode="2port")                               # leaves S12/S22 traces behind
    with pytest.raises(RuntimeError, match=r"S12.*S22.*remove_traces\(ksvna\)"):
        _setup(cfg, rig)
    gone = sv.remove_traces(rig.vna)
    assert len(gone) == 2 and _params(rig) == ["S11", "S21"]
    assert all(w.startswith('CALC:PAR:DEL "CH1_TR') for w in rig.vna.writes if "DEL" in w)
    _setup(cfg, rig)
    assert sorted(_measure(rig)["data"]) == ["S11", "S21"]


def test_a_vna_that_ignores_the_command_refuses_unless_allowed(cfg, rig, monkeypatch):
    rig.vna.port2_supported = False
    with pytest.raises(RuntimeError, match="didn't answer.*REQUIRE_PORT2_OFF = False"):
        _setup(cfg, rig)
    assert rig.switch.moves == []
    monkeypatch.setattr(sv, "REQUIRE_PORT2_OFF", False)
    _setup(cfg, rig)                                          # warns, goes on
    assert sorted(_measure(rig)["data"]) == ["S11", "S21"] and rig.vna.reverse_sweeps == 0


def test_an_unknown_mode_is_refused(rig):
    with pytest.raises(ValueError, match="mode must be one of"):
        sv.setup_vna(rig.vna, 510e6, 610e6, points=201, mode="both")
    with pytest.raises(ValueError, match="mode must be one of"):
        _measure(rig, mode="both")
    assert rig.switch.moves == []


# --- sequences, saving, reading back (lab code) ---------------------------------------

def test_forward_take_saves_s11_s21_records_the_mode_and_corrects(cfg, rig, dbpath, ideals, tmp_path):
    save = db.Saver(dbpath, "take", files=True)
    t = vna.take(cfg, rig.vna, rig.switch, rig.fridge, channels=[4], save=save)
    assert rig.vna.reverse_sweeps == 0 and rig.vna.sweeps == 7 and rig.vna.port2_mode == "OFF"
    take = db.load_take(t["take_id"], dbpath)
    run = take["repeats"][0]["ports"][4]
    assert sorted(run["data"]) == ["S11", "S21"] and run["meta"]["vna_sweep_mode"] == "forward"
    con = sqlite3.connect(dbpath)
    assert {r for r in con.execute("SELECT n_ports, vna_port2_source FROM runs")} == {(2, "OFF")}
    con.close()
    result = ecal.correct_take(take, definitions=ideals)
    np.testing.assert_allclose(result["corrected"][0][4], rig.chain.dut[4], atol=1e-9)
    ids = db.save_corrected(dbpath, result, take)
    corr = db.load_take(t["take_id"], dbpath)["corrected"][0]
    assert corr["run_id"] == ids[0] and corr["meta"]["vna_sweep_mode"] == "forward"

    # Touchstone: .s2p with NaN S12/S22, read back as S11/S21
    files = sorted(save.folder.glob("*.s2p"))
    assert len(files) == 7
    text = files[0].read_text()
    assert "S12 S22 not measured (forward-only sweep)" in text and " nan " in text
    f, d = ecal.read_touchstone(next(p for p in files if "RF4" in p.name))
    assert sorted(d) == ["S11", "S21"]
    np.testing.assert_allclose(d["S21"], run["data"]["S21"], rtol=1e-9)
    out = db.export_touchstone([run["run_id"]], dbpath, tmp_path / "export")
    assert out[0].suffix == ".s2p" and sorted(ecal.read_touchstone(out[0])[1]) == ["S11", "S21"]

    # the folder calibration (analysis 2b) gives the same answer as the database one
    an = _analysis()
    files_result = an.calibrate_files(an.load_folder(save.folder), ideals, sparam="S21")
    np.testing.assert_allclose(files_result["corrected"][4], result["corrected"][0][4], atol=1e-8)


def test_2port_take_still_saves_all_four(cfg, rig, dbpath):
    save = db.Saver(dbpath, "take", files=True)
    t = vna.take(db.with_sweep(cfg, 201, -20, "2port"), rig.vna, rig.switch, rig.fridge,
                 channels=[4], save=save)
    assert rig.vna.reverse_sweeps == 7
    run = db.load_take(t["take_id"], dbpath)["repeats"][0]["ports"][4]
    assert sorted(run["data"]) == ["S11", "S12", "S21", "S22"]
    assert run["meta"]["vna_sweep_mode"] == "2port"
    f = next(p for p in save.folder.glob("*.s2p") if "RF4" in p.name)
    assert "not measured" not in f.read_text()
    assert sorted(ecal.read_touchstone(f)[1]) == ["S11", "S12", "S21", "S22"]


def test_stuck_and_thru_checks_run_forward_only(cfg, rig):
    stuck = vna.stuck_check(cfg, rig.vna, rig.switch, rig.fridge)
    assert rig.vna.reverse_sweeps == 0 and len(stuck["rows"]) == 17
    from test_lab_checks import _mover                          # moves the fake's port 1 cable
    thru = vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=_mover(rig))
    assert rig.vna.reverse_sweeps == 0 and thru["gap_db"] > 20


def test_a_forward_take_after_a_2port_one_refuses_until_the_traces_go(cfg, rig, dbpath):
    vna.take(db.with_sweep(cfg, 201, -20, "2port"), rig.vna, rig.switch, rig.fridge,
             channels=[4], save=db.Saver(dbpath, "take"))
    moves = len(rig.switch.moves)
    with pytest.raises(RuntimeError, match="Nothing was measured"):
        vna.take(cfg, rig.vna, rig.switch, rig.fridge, channels=[4], save=db.Saver(dbpath, "take"))
    assert len(rig.switch.moves) == moves                     # refused before anything moved
    sv.remove_traces(rig.vna)
    vna.take(cfg, rig.vna, rig.switch, rig.fridge, channels=[4], save=db.Saver(dbpath, "take"))


# --- the rest of switch_vna (save, load, e-cal, hold) --------------------------------

def test_save_and_load_a_forward_measurement(cfg, rig, tmp_path):
    _setup(cfg, rig)
    r = _measure(rig)
    dbp = tmp_path / "t_vna.db"
    rid = sv.save(dbp, r, port="resonator", touchstone=True)
    freq, data, labels = sv.load(dbp, rid)
    assert sorted(data) == ["S11", "S21"] and labels["vna_port2_source"] == "OFF"
    assert labels["port"] == "resonator"
    (f,) = (tmp_path / "Sweeps" / "switch_vna").rglob("*.s2p")
    assert "S12 S22 not measured" in f.read_text()


@pytest.mark.parametrize("mode", ["forward", "2port"])
def test_sv_ecal_recovers_the_dut_in_both_modes(cfg, rig, ideals, tmp_path, monkeypatch, mode):
    pytest.importorskip("skrf")
    monkeypatch.setattr(sv, "NIST_DEFINITIONS", {"3K": ideals, "295K": ideals})
    _setup(cfg, rig, mode)
    c = sv.ecal(rig.switch, rig.vna, [4], "3K", db_path=tmp_path / "e.db", touchstone=True, mode=mode)
    keep = rig.chain.freq >= sv.ECAL_FMIN_HZ
    np.testing.assert_allclose(c["corrected"][4], rig.chain.dut[4][keep], atol=1e-5)
    assert rig.vna.reverse_sweeps == (0 if mode == "forward" else 7)
    assert rig.switch.last == "ALL_OPEN"


def test_sv_ecal_forward_refuses_a_reverse_sparam(cfg, rig):
    pytest.importorskip("skrf")
    _setup(cfg, rig)
    with pytest.raises(ValueError, match="for mode 'forward'"):
        sv.ecal(rig.switch, rig.vna, [4], "3K", sparam="S22")


def test_hold_keeps_port2_off_and_release_opens(cfg, rig):
    _setup(cfg, rig)
    h = sv.hold_state(rig.switch, rig.vna, 2, save_csv=False, interval_s=0.05)
    assert rig.switch.last == 2
    rig.vna.sweep()                                           # the VNA sweeping on its own
    sv.release(rig.switch, rig.vna, h)
    assert rig.switch.last == "ALL_OPEN" and rig.vna.port2_mode == "OFF" and rig.vna.reverse_sweeps == 0
