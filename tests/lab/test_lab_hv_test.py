"""The gate (HV line) test, vna.hv_test, on fakes.make_gate_rig (a switch with gates)."""

import csv
import sqlite3
import types

import numpy as np
import pytest

from conftest import db, ecal, plots, vna
from fakes import DRV, PINS, TAU, WIRE, _Device, make_gate_rig


@pytest.fixture
def gate_rig(cfg):
    s = cfg["sweep"]
    return make_gate_rig(s["start_hz"], s["stop_hz"], s["points"])


def _run(cfg, rig, save=None, channels=(4,), reply="", combos=None):
    return vna.hv_test(cfg, rig.vna, rig.switch, rig.fridge, channels=channels, save=save,
                       ask=lambda prompt: reply, combos=combos)


def _row(result, label):
    return next(r for r in result["rows"] if r["label"] == label)


# ---------------------------------------------------------------------------

def test_bytes_rebuild_every_menlo_state_and_rf4_combos():
    for state, lines in PINS.items():
        assert vna.hv_wire_bytes(lines) == WIRE[state], state
    assert vna.hv_wire_bytes(["HV2", "HV8"]) == (0x00, 0x00, 0x82) * 2
    assert vna.hv_wire_bytes([2, 8, 10]) == (0x00, 0x02, 0x82) * 2
    assert vna.hv_wire_bytes([2, 8, 16]) == (0x00, 0x80, 0x82) * 2
    assert vna.hv_wire_bytes([]) == WIRE["ALL_OPEN"]
    for bad in (0, 23, "HV0", "HV23", "X5"):
        with pytest.raises(ValueError):
            vna.hv_wire_bytes([bad])


def test_plan_for_rf4():
    combos, shared, own = vna.hv_plan(4, PINS)
    assert shared == [2, 8] and own == [10, 16]
    assert combos == [(2,), (8,), (10,), (16,), (2, 8), (2, 8, 10), (2, 8, 16)]


def test_sequence_saving_and_no_hot_switching(cfg, gate_rig, tmp_path):
    dbp = db.open_database(cfg, "hv_lines", helper=None, local_dir=tmp_path / "dbs")
    save = db.Saver(dbp, "hv_lines", files=True, files_root=dbp.parent)
    r = _run(cfg, gate_rig, save=save)
    labels = [x["label"] for x in r["rows"]]
    combos = ["RF4: HV2", "RF4: HV8", "RF4: HV10", "RF4: HV16", "RF4: HV2+HV8",
              "RF4: HV2+HV8+HV10", "RF4: HV2+HV8+HV16", "RF4 (HV2+HV8+HV10+HV16)"]
    assert labels[:5] == ["ALL_OPEN", "INTERNAL_SHORT", "ALL_OPEN", "INTERNAL_LOAD", "ALL_OPEN"]
    assert labels[5:21:2] == combos and set(labels[6:22:2]) == {"ALL_OPEN"}
    assert labels[21:] == ["INTERNAL_SHORT", "ALL_OPEN", "INTERNAL_LOAD", "ALL_OPEN"]
    sw = gate_rig.switch
    assert sw.last == "ALL_OPEN" and sw.lines == frozenset()            # ends open
    assert not any(hot for _, hot in sw.moves) and not any(hot for _, hot in sw.raw_moves)
    assert len(sw.raw_moves) == 7
    # database: its own file, one run per position, the lines and bytes on each gate run
    assert dbp.name.endswith("_hv_lines.db")
    con = sqlite3.connect(str(dbp))
    cols = [c[1] for c in con.execute("PRAGMA table_info(runs)")]
    got = con.execute("SELECT name, hv_lines, hv_wire_bytes, check_type FROM runs ORDER BY run_id").fetchall()
    con.close()
    assert "hv_wire_bytes" in cols and len(got) == len(r["rows"])
    assert ("RF4_HV2+HV8+HV10", "HV2+HV8+HV10", "00 02 82 00 02 82", "hv_lines") in got
    assert ("RF4", "HV2+HV8+HV10+HV16", None, "hv_lines") in got
    # files + csv
    path = db.write_check_csv(r, save.folder)
    assert save.folder.parent.name == "hv_lines" and len(list(save.folder.glob("*.s2p"))) == len(r["rows"])
    with open(path) as f:
        assert len(list(csv.reader(f))) == len(r["rows"]) + 1


def test_healthy_gates_land_where_the_tree_says(cfg, gate_rig, capsys):
    r = _run(cfg, gate_rig)
    vna.print_check(r)
    pos = {v["label"]: v for v in ecal.hv_positions(r, definitions=None, verbose=True).values()}
    assert "from internal stds" in capsys.readouterr().out
    d = lambda lab: pos[lab]["delay_in_ns"]
    for lab in ("RF4: HV2", "RF4: HV8", "RF4: HV10", "RF4: HV16"):
        assert abs(d(lab)) < 0.01, lab                       # one gate alone: still at the standards
    assert d("RF4: HV2+HV8") == pytest.approx(TAU["HV10"], abs=0.01)
    assert d("RF4: HV2+HV8+HV10") == pytest.approx(TAU["HV16"], abs=0.01)
    assert d("RF4: HV2+HV8+HV16") == pytest.approx(TAU["HV10"], abs=0.01)
    assert d("RF4 (HV2+HV8+HV10+HV16)") == pytest.approx(TAU["beyond"], abs=0.01)
    assert all(v["mag_db"] == pytest.approx(0, abs=0.05) for v in pos.values())
    # a lone gate looks like ALL_OPEN in the contrast too; the shared pair doesn't
    vna.stuck_verdicts(r)
    assert _row(r, "RF4: HV10")["contrast_db"] < 6 < _row(r, "RF4: HV2+HV8")["contrast_db"]


@pytest.mark.parametrize("dead, full_at", [(10, "HV10"), (16, "HV16")])
def test_a_dead_own_gate_shows_where_rf4_stops(cfg, gate_rig, dead, full_at):
    gate_rig.switch.dead = {dead}
    r = _run(cfg, gate_rig)
    pos = {v["label"]: v["delay_in_ns"] for v in ecal.hv_positions(r, verbose=False).values()}
    assert pos["RF4 (HV2+HV8+HV10+HV16)"] == pytest.approx(TAU[full_at], abs=0.01)
    if dead == 10:   # adding HV10 changes nothing
        assert pos["RF4: HV2+HV8+HV10"] == pytest.approx(pos["RF4: HV2+HV8"], abs=0.01)
    else:            # HV10 works (moves it out), HV16 doesn't
        assert pos["RF4: HV2+HV8+HV10"] == pytest.approx(TAU["HV16"], abs=0.01)


def test_a_gate_frozen_closed_shows_up_on_its_partner_alone(cfg, gate_rig):
    gate_rig.switch.frozen = {2}
    r = _run(cfg, gate_rig)
    vna.stuck_verdicts(r)
    assert _row(r, "RF4: HV8")["contrast_db"] > 20        # HV8 alone now opens the trunk
    gate_rig.switch.frozen = set()
    healthy = _run(cfg, gate_rig)
    vna.stuck_verdicts(healthy)
    assert _row(healthy, "RF4: HV8")["contrast_db"] < 6


def test_answer_n_moves_nothing(cfg, gate_rig):
    assert _run(cfg, gate_rig, reply="n") is None
    assert gate_rig.switch.moves == [] and gate_rig.switch.raw_moves == []


def test_refuses_without_the_driver_tables(cfg, rig):
    rig.switch._device = _Device(rig.switch)               # plain FakeSwitch: no tables in its module
    with pytest.raises(RuntimeError, match="STATE_HV_PINS"):
        vna.hv_test(cfg, rig.vna, rig.switch, rig.fridge, ask=lambda p: "")
    assert rig.switch.moves == []


def test_refuses_if_the_bit_layout_disagrees(cfg, gate_rig, monkeypatch):
    bad = dict(DRV.STATE_WIRE_BYTES)
    rf4 = DRV.SP6TState.RFC_RF4
    bad[rf4] = (0x00, 0x82, 0x83, 0x00, 0x82, 0x83)
    monkeypatch.setattr(DRV, "STATE_WIRE_BYTES", bad)
    with pytest.raises(RuntimeError, match="bit"):
        _run(cfg, gate_rig)
    assert gate_rig.switch.moves == [] and gate_rig.switch.raw_moves == []


def test_dies_halfway_still_ends_open(cfg, gate_rig):
    sweep, n = gate_rig.vna.sweep, [0]

    def flaky():
        n[0] += 1
        if n[0] == 9:
            raise RuntimeError("VNA gone")
        sweep()
    gate_rig.vna.sweep = flaky
    with pytest.raises(RuntimeError, match="VNA gone"):
        _run(cfg, gate_rig)
    assert gate_rig.switch.last == "ALL_OPEN" and gate_rig.switch.lines == frozenset()


def test_plots(cfg, gate_rig):
    r = _run(cfg, gate_rig)
    plots.plot_check(r, "SN_TEST")
    plots.plot_hv_test(r, ecal.hv_positions(r, verbose=False))


# --- your own lines (the "Your own HV lines" cell) -------------------------------

def test_custom_lines_measure_exactly_those(cfg, gate_rig, capsys):
    mine = [["HV2", "HV8"], [8, 2, 10], ["hv16", "HV8", "HV2"]]
    r = _run(cfg, gate_rig, combos=mine)
    labels = [x["label"] for x in r["rows"]]
    assert labels[5:13:2] == ["RF4: HV2+HV8", "RF4: HV2+HV8+HV10", "RF4: HV2+HV8+HV16",
                              "RF4 (HV2+HV8+HV10+HV16)"]
    assert len(r["rows"]) == 5 + 2 * 4 + 4
    assert {x["hv_role"] for x in r["rows"] if x["hv_role"]} == {"custom", "full"}
    assert [w for w, _ in gate_rig.switch.raw_moves] == [vna.hv_wire_bytes(c) for c in mine]
    pos = {v["label"]: v["delay_in_ns"] for v in ecal.hv_positions(r, verbose=True).values()}
    out = capsys.readouterr().out
    assert "round trip" in out and "one way" in out
    assert pos["RF4: HV2+HV8+HV10"] == pytest.approx(TAU["HV16"], abs=0.01)
    assert gate_rig.switch.last == "ALL_OPEN" and gate_rig.switch.lines == frozenset()


def test_custom_lines_without_the_full_channel(cfg, gate_rig):
    r = vna.hv_test(cfg, gate_rig.vna, gate_rig.switch, gate_rig.fridge, channels=[4],
                    combos=[["HV2", "HV8"]], full=False, ask=lambda p: "")
    assert [x["label"] for x in r["rows"]][5:7] == ["RF4: HV2+HV8", "ALL_OPEN"]
    assert len(r["rows"]) == 5 + 2 + 4 and 4 not in [x["position"] for x in r["rows"]]


@pytest.mark.parametrize("combos, channels, match", [
    ([["HV2", "HV5"]], [4], "HV5: no Menlo state uses it"),
    ([["HV22"], ["HV4", "HV6"]], [4], "HV4, HV6, HV22: no Menlo state uses them"),
    ([["HV2"]], [3, 4], "exactly one channel"),
    ([[]], [4], "non-empty"),
    ([["HV23"]], [4], "HV1-HV22"),
])
def test_custom_lines_refused_before_anything_moves(cfg, gate_rig, combos, channels, match):
    with pytest.raises(ValueError, match=match):
        vna.hv_test(cfg, gate_rig.vna, gate_rig.switch, gate_rig.fridge, channels=channels,
                    combos=combos, ask=lambda p: "")
    assert gate_rig.switch.moves == [] and gate_rig.switch.raw_moves == []
