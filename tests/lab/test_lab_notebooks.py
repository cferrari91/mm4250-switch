"""
take_data.ipynb and stuck_check.ipynb, executed top to bottom in a real
kernel against the fakes, in a DAQ-like folder (see notebook_runner.py for
exactly how). The notebooks themselves aren't changed; `edits` flip options
in the temporary copy the way a person would.
"""

import json

import pytest

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")

from notebook_runner import NOTEBOOK_POINTS, make_daq_layout, preamble, run  # noqa: E402

CHECK_TAKE = '''
import numpy as np
assert rig.switch.last == "ALL_OPEN", rig.switch.moves[-3:]
measuring = rig.switch.moves[:-1]           # Close's own move is the last
assert not any(hot for _, hot in measuring), "moved with the source on"
assert rig.vna.closed and rig.switch.closed and rig.fridge.closed
assert DB.name == "MM4250_Cooldown_05_vna.db" and DB.parent.name == "Cooldown_05"
assert "QCoDeS" in str(DB) and "local_databases" not in str(DB)
t2 = db.load_take(None, DB)
assert t2["channels"] == [1, 2, 3, 4, 5, 6] and len(t2["corrected"]) == 6
for c in t2["corrected"]:
    np.testing.assert_allclose(c["data"]["S11"], rig.chain.dut[c["channel"]], atol=1e-9)
assert {c["definitions"] for c in t2["corrected"]} == {"NIST 295K"}
print("CHECKS OK")
'''


def test_take_data_notebook(tmp_path):
    lab, qroot, which = make_daq_layout(tmp_path)
    nb = run(lab / "take_data.ipynb", lab, preamble(lab), post=CHECK_TAKE,
             out=tmp_path / "take_data.executed.ipynb")
    log = nb.metadata["runner_log"]
    assert "CHECKS OK" in log and "NO LAB DATABASE HELPER" not in log
    assert "repeat 1 drift (S21)" in log and "Saved 6 corrected run(s)" in log
    assert "RF2 held" in log and "MXP 295 K" in log                       # hold / open cells
    assert (qroot / "MM4250" / "Cooldown_05" / "db_backups").is_dir()


def test_take_data_notebook_files_export_and_one_channel(tmp_path):
    lab, qroot, _ = make_daq_layout(tmp_path)
    edits = [("CHANNELS = None ", "CHANNELS = [4] "),         # SAVE_TOUCHSTONE is True by default
             ("SAVE_FIGURE = False", "SAVE_FIGURE = True"),
             ("EXPORT = False", "EXPORT = True")]
    post = ('assert len(db.load_take(None, DB)["corrected"]) == 1\n'
            'assert rig.vna.reverse_sweeps == 0     # SWEEP_MODE "forward" by default\n'
            'assert sorted(db.load_take(None, DB)["repeats"][0]["ports"][4]["data"]) == ["S11", "S21"]\n'
            'print("CHECKS OK")')
    nb = run(lab / "take_data.ipynb", lab, preamble(lab), post=post, edits=edits)
    assert "CHECKS OK" in nb.metadata["runner_log"]
    cd = qroot / "MM4250" / "Cooldown_05"
    takes = list((cd / "Sweeps" / "takes").iterdir())
    assert len(takes) == 1 and (takes[0] / "take.png").is_file()
    assert len(list(takes[0].glob("*.s2p"))) == 7 and len(list(takes[0].glob("*.s1p"))) == 1
    assert all("forward-only sweep" in f.read_text() for f in takes[0].glob("*.s2p"))
    assert len(list((cd / "Sweeps" / "export").glob("*_run*.s?p"))) == 8
    (temps,) = (cd / "Sweeps" / "thru_hold").glob("*_temps.csv")
    lines = temps.read_text().splitlines()                 # SAVE_CSV is on by default
    assert lines[0] == "time,mxp,mxp2,stp,cmn,stp2,cfp,prp" and len(lines) >= 2
    assert lines[1].split(",")[1:4] == ["295.0"] * 3       # the fakes have mxp/mxp2/stp only
    assert lines[1].split(",")[4:] == [""] * 4


def test_take_data_notebook_without_the_fridge(tmp_path):
    """No VPN: raw saved, nothing corrected, said loudly; Recall can correct it after."""
    lab, _, _ = make_daq_layout(tmp_path)
    edits = [("CORRECT_WITH = None ", 'CORRECT_WITH = "295K" ')]
    post = '''
t2 = db.load_take(None, DB)
assert t2["temp_str"] == "noT" and len(t2["corrected"]) == 6   # corrected by Recall
assert fridge is None
print("CHECKS OK")
'''
    nb = run(lab / "take_data.ipynb", lab, preamble(lab, fridge="novpn"), post=post, edits=edits)
    log = nb.metadata["runner_log"]
    assert "no fridge (ConnectionError" in log and "NOTHING WAS CORRECTED" in log
    assert "CHECKS OK" in log


CHECK_STUCK = '''
assert rig.switch.last == "ALL_OPEN"
assert stuck["temp_k"] is None and len(stuck["rows"]) == 17
assert thru["passed"] is True and rig.vna.port1_line == "RF6"
assert len(PROMPTS) == 2 and "to line RF3" in PROMPTS[0] and "back to line RF6" in PROMPTS[1]
print("CHECKS OK")
'''


def test_stuck_check_notebook(tmp_path):
    lab, qroot, _ = make_daq_layout(tmp_path)
    nb = run(lab / "stuck_check.ipynb", lab, preamble(lab, fridge="novpn", noise=1e-4),
             post=CHECK_STUCK, out=tmp_path / "stuck_check.executed.ipynb")
    log = nb.metadata["runner_log"]
    assert "CHECKS OK" in log and "No pass mark" in log and "OK: it closes and opens" in log
    assert "RF2 held" in log and "MXP --" in log
    assert log.count("[prompt]") == 2
    assert not list(qroot.rglob("*.db"))                                        # no database by default
    saved = [f for f in qroot.rglob("*") if f.is_file() and "Sweeps" in f.parts]
    assert [f.parent.name for f in saved] == ["thru_hold"]    # only the hold cell's temperature CSV


def test_stuck_check_notebook_saving_and_a_stuck_channel(tmp_path):
    lab, qroot, _ = make_daq_layout(tmp_path)
    edits = [('''SAVE_DB = False          # True: every position into the "stuck" database
SAVE_TOUCHSTONE = False  # True: one .s2p per position + positions.csv in Sweeps/stuck_check/''',
              '''SAVE_DB = True          # True: every position into the "stuck" database
SAVE_TOUCHSTONE = True  # True: one .s2p per position + positions.csv in Sweeps/stuck_check/''')]
    pre = preamble(lab, fridge="60", noise=1e-4) + "\nrig.switch.stuck_open = {5}\n"
    nb = run(lab / "stuck_check.ipynb", lab, pre, edits=edits)
    log = nb.metadata["runner_log"]
    assert "RF5 (load_1)" in log and "60K" in log
    cd = qroot / "MM4250" / "Cooldown_05"
    assert (cd / "MM4250_Cooldown_05_stuck.db").is_file()
    assert not (cd / "MM4250_Cooldown_05_vna.db").exists()       # the stuck notebook never touches it
    folder = next((cd / "Sweeps" / "stuck_check").iterdir())
    assert len(list(folder.glob("*.s2p"))) == 17 and (folder / "positions.csv").is_file()
    assert "STUCK OPEN?" not in log           # pass_db is null in CD05.json: numbers only


def test_analysis_notebook(tmp_path):
    """take_data first (with files), then analysis.ipynb top to bottom on what it saved."""
    lab, qroot, _ = make_daq_layout(tmp_path)
    run(lab / "take_data.ipynb", lab, preamble(lab))      # SAVE_TOUCHSTONE is True by default
    edits = [('DEFINITIONS = "3K" ', 'DEFINITIONS = "295K" '),
             ("SAVE_FILES = False", "SAVE_FILES = True"),
             ("EXPORT = False", "EXPORT = True")]
    post = '''
import numpy as np
for ch, g in cal_files["corrected"].items():
    np.testing.assert_allclose(g, rig.chain.dut[ch], atol=1e-8)
assert sorted(cal_files["corrected"]) == [1, 2, 3, 4, 5, 6]
assert len(traces) == 6 and res["label"].startswith("RF4")
print("CHECKS OK")
'''
    nb = run(lab / "analysis.ipynb", lab, preamble(lab), post=post, edits=edits,
             out=tmp_path / "analysis.executed.ipynb")
    log = nb.metadata["runner_log"]
    assert "CHECKS OK" in log
    cd = qroot / "MM4250" / "Cooldown_05"
    take_folder = next((cd / "Sweeps" / "takes").iterdir())
    assert len(list((take_folder / "calibrated").glob("RF*_calibrated_*.s1p"))) == 6
    assert len(list((cd / "Sweeps" / "export").glob("*_run*.s?p"))) == 18   # 12 raw + 6 corrected


def test_take_data_notebook_2port_mode(tmp_path):
    """SWEEP_MODE = "2port": all four S-parameters, port 2 sourcing (reverse sweeps)."""
    lab, qroot, _ = make_daq_layout(tmp_path)
    edits = [("CHANNELS = None ", "CHANNELS = [4] "),
             ('SWEEP_MODE = "forward" ', 'SWEEP_MODE = "2port" ')]
    post = ('assert rig.vna.reverse_sweeps == 7\n'
            'run = db.load_take(None, DB)["repeats"][0]["ports"][4]\n'
            'assert sorted(run["data"]) == ["S11", "S12", "S21", "S22"]\n'
            'assert run["meta"]["vna_sweep_mode"] == "2port"\n'
            'print("CHECKS OK")')
    nb = run(lab / "take_data.ipynb", lab, preamble(lab), post=post, edits=edits)
    assert "CHECKS OK" in nb.metadata["runner_log"]


def test_stuck_check_notebook_gate_test(tmp_path):
    """RUN_GATE_TEST = True on a switch with gates (fakes.make_gate_rig), RF4's HV16 dead:
    saved to its own database + Sweeps/hv_lines/, the full RF4 lands where HV16 is."""
    lab, qroot, _ = make_daq_layout(tmp_path)
    cfg = json.loads((lab / "cooldowns" / "CD05.json").read_text())
    s = cfg["sweep"]
    pre = preamble(lab, fridge="0.058", noise=1e-4) + f'''
_g = fakes.make_gate_rig({s["start_hz"]!r}, {s["stop_hz"]!r}, {NOTEBOOK_POINTS!r},
                         thru=({cfg["lines"]["thru"]!r}, {cfg["thru_channel"]!r}), noise=1e-4)
rig.vna, rig.switch, rig.chain = _g.vna, _g.switch, _g.chain
rig.switch.dead = {{16}}
'''
    edits = [("RUN_GATE_TEST = False", "RUN_GATE_TEST = True"), ("RUN_MY_LINES = False", "RUN_MY_LINES = True")]
    post = '''
assert rig.switch.last == "ALL_OPEN" and rig.switch.lines == frozenset()
assert len(rig.switch.raw_moves) == 7 + 3 and not any(h for _, h in rig.switch.raw_moves)
d = {v["label"]: v["delay_in_ns"] for v in hv_pos.values()}
assert abs(d["RF4 (HV2+HV8+HV10+HV16)"] - fakes.TAU["HV16"]) < 0.01
m = {v["label"]: v["delay_in_ns"] for v in mine_pos.values()}
assert abs(m["RF4: HV2+HV8+HV10"] - fakes.TAU["HV16"]) < 0.01 and len(mine["rows"]) == 17
assert len(PROMPTS) == 4 and "Press Enter to start" in PROMPTS[2] and "Press Enter to start" in PROMPTS[3]
print("CHECKS OK")
'''
    nb = run(lab / "stuck_check.ipynb", lab, pre, post=post, edits=edits)
    log = nb.metadata["runner_log"]
    assert "CHECKS OK" in log
    assert "no 'from connector' column: No 3K definitions" in log     # the runner has only fake 295K ones
    cd = qroot / "MM4250" / "Cooldown_05"
    assert (cd / "MM4250_Cooldown_05_hv_lines.db").is_file()
    assert not (cd / "MM4250_Cooldown_05_stuck.db").exists()     # the stuck check's SAVE_DB is off
    folders = sorted((cd / "Sweeps" / "hv_lines").iterdir())
    assert sorted(len(list(f.glob("*.s2p"))) for f in folders) == [17, 25]
    assert all((f / "positions.csv").is_file() for f in folders)

