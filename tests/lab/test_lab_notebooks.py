"""
take_data.ipynb and stuck_check.ipynb, executed top to bottom in a real
kernel against the fakes, in a DAQ-like folder (see notebook_runner.py for
exactly how). The notebooks themselves aren't changed; `edits` flip options
in the temporary copy the way a person would.
"""

import pytest

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")

from notebook_runner import make_daq_layout, preamble, run  # noqa: E402

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
    assert (qroot / "MM4250" / "Cooldown_05" / "db_backups").is_dir()


def test_take_data_notebook_files_export_and_one_channel(tmp_path):
    lab, qroot, _ = make_daq_layout(tmp_path)
    edits = [("CHANNELS = None ", "CHANNELS = [4] "),
             ("SAVE_TOUCHSTONE = False", "SAVE_TOUCHSTONE = True"),
             ("SAVE_FIGURE = False", "SAVE_FIGURE = True"),
             ("EXPORT = False", "EXPORT = True")]
    post = 'assert len(db.load_take(None, DB)["corrected"]) == 1; print("CHECKS OK")'
    nb = run(lab / "take_data.ipynb", lab, preamble(lab), post=post, edits=edits)
    assert "CHECKS OK" in nb.metadata["runner_log"]
    cd = qroot / "MM4250" / "Cooldown_05"
    takes = list((cd / "Sweeps" / "takes").iterdir())
    assert len(takes) == 1 and (takes[0] / "take.png").is_file()
    assert len(list(takes[0].glob("*.s2p"))) == 7 and len(list(takes[0].glob("*.s1p"))) == 1
    assert len(list((cd / "Sweeps" / "export").glob("*_run*.s?p"))) == 8


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
    assert log.count("[prompt]") == 2
    assert not list(qroot.rglob("*.db")) and not list(qroot.rglob("Sweeps"))   # nothing saved by default


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
