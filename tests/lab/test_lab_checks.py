"""The stuck check and the thru check on the fake rig."""

import csv
import sqlite3

import numpy as np
import pytest

from conftest import db, plots, vna

PASS_DB = 10


def _stuck(cfg, rig, save=None):
    rig.vna.noise = 1e-4             # an ALL_OPEN level to compare with, as on the real chain
    return vna.stuck_check(cfg, rig.vna, rig.switch, rig.fridge, save=save)


def test_order_switch_left_open_no_hot_switching(cfg, rig):
    r = _stuck(cfg, rig)
    seq = ["ALL_OPEN", "INTERNAL_SHORT", "ALL_OPEN", "INTERNAL_LOAD", "ALL_OPEN"]
    for ch in range(1, 7):
        seq += [ch, "ALL_OPEN"]
    assert [row["position"] for row in r["rows"]] == seq
    assert rig.switch.last == "ALL_OPEN" and not any(hot for _, hot in rig.switch.moves)
    assert r["rows"][0]["diff"] == 0.0


def test_healthy_switch_flags_nothing(cfg, rig, capsys):
    r = _stuck(cfg, rig)
    assert vna.print_check(r, PASS_DB) == []
    assert "Nothing flagged" in capsys.readouterr().out


def test_flags_a_stuck_closed_channel(cfg, rig):
    rig.switch.stuck_closed = {3}
    r = _stuck(cfg, rig)
    flags = vna.print_check(r, PASS_DB)
    rf3 = next(row for row in r["rows"] if row["position"] == 3)
    assert rf3["flag"] == ""                                     # RF3 itself closed fine
    assert flags[0] == (f"step {rf3['step'] + 1} ALL_OPEN: not open after "
                        "RF3 (50 ohm load (ANNE-50X+) via bias tee): STUCK CLOSED?")
    assert all(row["flag"] == "" for row in r["rows"][:rf3["step"]])   # nothing before it


def test_contrast_ignores_slow_drift_of_the_chain(cfg, rig):
    # The chain's gain creeping up during the check (cooling) must not flag anything.
    sweep, n = rig.vna.sweep, [0]

    def drifting():
        sweep()
        n[0] += 1
        rig.vna._data = {k: v * (1 + 0.02 * n[0]) for k, v in rig.vna._data.items()}
    rig.vna.sweep = drifting
    assert vna.print_check(_stuck(cfg, rig), PASS_DB) == []


def test_flags_a_stuck_open_channel(cfg, rig):
    rig.switch.stuck_open = {5}
    r = _stuck(cfg, rig)
    flags = vna.print_check(r, PASS_DB)
    assert flags == [f"step {next(x['step'] for x in r['rows'] if x['position'] == 5)} "
                     "RF5 (load_1): STUCK OPEN?"]


def test_no_pass_mark_reports_numbers_and_flags_nothing(cfg, rig, capsys):
    rig.switch.stuck_open = {5}
    r = _stuck(cfg, rig)
    assert vna.print_check(r, None) == []
    out = capsys.readouterr().out
    assert "No pass mark" in out and "contrast" in out


def test_stuck_check_dies_switch_opens(cfg, rig):
    rig.chain.fail_on = 4
    with pytest.raises(RuntimeError):
        _stuck(cfg, rig)
    assert rig.switch.last == "ALL_OPEN"


def test_stuck_check_runs_without_a_fridge(cfg, rig):
    r = vna.stuck_check(cfg, rig.vna, rig.switch, None)
    assert r["temp_k"] is None and all(row["temp_k"] is None for row in r["rows"])


def test_stuck_check_saves_to_db_and_files(cfg, rig, tmp_path):
    stuck_db = db.open_database(cfg, "stuck", helper=None, local_dir=tmp_path / "dbs")
    save = db.Saver(stuck_db, "stuck_check", files=True)
    r = _stuck(cfg, rig, save=save)
    vna.stuck_verdicts(r, PASS_DB)
    csv_path = db.write_check_csv(r, save.folder)
    assert save.folder.parent == stuck_db.parent / "Sweeps" / "stuck_check"
    assert len(list(save.folder.glob("*.s2p"))) == 17
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 17 and rows[5]["position"] == "RF1 (flush short)"
    con = sqlite3.connect(stuck_db)
    n, kinds = con.execute("SELECT COUNT(*), GROUP_CONCAT(DISTINCT check_type) FROM runs").fetchone()
    con.close()
    assert (n, kinds) == (17, "stuck")
    assert db.write_check_csv(r, save.folder) is None            # never overwrites


def test_files_only_no_database(cfg, rig, tmp_path):
    save = db.Saver(None, "stuck_check", files=True, files_root=tmp_path)
    r = _stuck(cfg, rig, save=save)
    assert all(row["run_id"] is None for row in r["rows"])
    assert len(list(save.folder.glob("*.s2p"))) == 17 and not list(tmp_path.glob("*.db"))


def test_plot_stuck(cfg, rig, tmp_path):
    r = _stuck(cfg, rig)
    vna.stuck_verdicts(r)
    plots.plot_check(r, "SN0077", save=tmp_path / "stuck.png")
    assert (tmp_path / "stuck.png").is_file()


# ---------------------------------------------------------------------------
# Thru check
# ---------------------------------------------------------------------------

def _mover(rig, answers=None):
    """input() stand-in: moves the fake's port-1 cable to the line the prompt names."""
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        line = prompt.split(" to line ")[1].split()[0].rstrip(",")
        rig.vna.port1_line = line
        return (answers or {}).get(len(prompts), "")
    ask.prompts = prompts
    return ask


def test_thru_check_passes_on_a_healthy_switch(cfg, rig, capsys):
    ask = _mover(rig)
    r = vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=ask)
    assert [row["position"] for row in r["rows"]] == ["ALL_OPEN", 2, "ALL_OPEN", 2, "ALL_OPEN"]
    assert r["gap_db"] == pytest.approx(54, abs=0.1) and r["passed"] is True
    assert "from fridge line RF6 to line RF3" in ask.prompts[0]
    assert "back to line RF6" in ask.prompts[1] and rig.vna.port1_line == "RF6"
    assert rig.switch.last == "ALL_OPEN"
    vna.print_check(r)
    assert "OK: it closes and opens" in capsys.readouterr().out
    plots.plot_check(r, "SN0077")


@pytest.mark.parametrize("stuck", ["open", "closed"])
def test_thru_check_fails_when_the_thru_channel_sticks(cfg, rig, stuck):
    setattr(rig.switch, f"stuck_{stuck}", {2})
    r = vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=_mover(rig))
    assert r["passed"] is False and abs(r["gap_db"]) < 1


def test_thru_check_fails_if_the_cable_was_not_moved(cfg, rig):
    r = vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=lambda prompt: "")
    assert r["passed"] is False


def test_thru_check_n_stops_before_anything_moves(cfg, rig, capsys):
    r = vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=lambda prompt: "n")
    assert r is None and rig.switch.moves == []


def test_thru_check_without_a_pass_mark_or_thru_channel(cfg, rig):
    cfg["stuck_check"]["thru_pass_db"] = None
    assert vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=_mover(rig))["passed"] is None
    cfg["thru_channel"] = None
    with pytest.raises(ValueError, match="No thru_channel"):
        vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=_mover(rig))


def test_thru_cable_back_prompt_even_if_it_dies(cfg, rig):
    rig.switch.fail_on = 2
    ask = _mover(rig)
    with pytest.raises(RuntimeError):
        vna.thru_check(cfg, rig.vna, rig.switch, rig.fridge, ask=ask)
    assert len(ask.prompts) == 2 and rig.switch.last == "ALL_OPEN"


def test_temperature_label():
    assert vna.temp_label(295.2) == "295K" and vna.temp_label(0.012) == "12mK"
    assert vna.temp_label(None) == "noT"
