"""db.load_cooldown: the real files load, every kind of mistake fails with a plain message."""

import copy
import json

import pytest

from conftest import LAB, db

GOOD = json.loads((LAB / "cooldowns" / "CD05.json").read_text())


@pytest.mark.parametrize("name", ["CD05.json", "example.json"])
def test_shipped_files_load(name):
    cfg = db.load_cooldown(LAB / "cooldowns" / name)
    assert cfg["_text"] == (LAB / "cooldowns" / name).read_text()
    assert cfg["sweep"]["power_dbm"] <= 0


def test_cd05_is_the_current_port_map():
    cfg = db.load_cooldown(LAB / "cooldowns" / "CD05.json")
    assert cfg["cooldown"] == 5 and cfg["switch_serial"] == "SN0077"
    assert cfg["ports"]["RF4"] == "resonator" and cfg["thru_channel"] == 2
    assert cfg["lines"] == {"input": "RF6", "return": "RF2", "thru": "RF3",
                            "notes": "one LNA (LNF-LNC0.3_14B s/n 5858H) on the return line"}
    assert (cfg["sweep"]["start_hz"], cfg["sweep"]["stop_hz"]) == (510e6, 610e6)


def test_summary_prints(capsys):
    db.print_cooldown(db.load_cooldown(LAB / "cooldowns" / "CD05.json"))
    out = capsys.readouterr().out
    assert "RF2: thru: fridge line RF3 <- thru check" in out and "510-610 MHz" in out


def _bad(tmp_path, change):
    data = copy.deepcopy(GOOD)
    change(data)
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError) as e:
        db.load_cooldown(p)
    return str(e.value)


CASES = [
    (lambda d: d.pop("sweep"), "missing key(s): sweep"),
    (lambda d: d.update(repeat=2), "unknown key(s): repeat"),
    (lambda d: d["ports"].update(RF7="x"), "'RF7' isn't a switch channel"),
    (lambda d: d["ports"].update(RF1=""), "RF1 needs a description"),
    (lambda d: d.update(ports={}), "at least one port"),
    (lambda d: d.update(thru_channel=7), "thru_channel must be a channel number 1-6"),
    (lambda d: (d["ports"].pop("RF2")), "thru_channel 2: RF2 isn't in ports"),
    (lambda d: d["lines"].pop("thru"), "lines.thru"),
    (lambda d: d["sweep"].update(power_dbm=10), "above the 0 dBm cap"),
    (lambda d: d["sweep"].update(stop_hz=1e6), "start_hz < stop_hz"),
    (lambda d: d["sweep"].update(points=20.5), "sweep.points must be a whole number"),
    (lambda d: d["sweep"].pop("points"), "sweep: missing points"),
    (lambda d: d["sweep"].update(ifbw=1), "sweep: unknown key(s) ifbw"),
    (lambda d: d["sweep"].update(start_hz="510 MHz"), "sweep.start_hz must be a number"),
    (lambda d: d.update(repeats=0), "repeats must be a whole number >= 1"),
    (lambda d: d.update(repeats=True), "repeats must be a whole number >= 1"),
    (lambda d: d.update(correct_sparam="s21"), "correct_sparam must be one of"),
    (lambda d: d.update(cooldown="5"), "cooldown must be a whole number"),
    (lambda d: d.update(switch_serial=""), "switch_serial must be non-empty"),
    (lambda d: d["stuck_check"].update(thru_pass_db=-3), "thru_pass_db must be a positive"),
    (lambda d: d["stuck_check"].pop("pass_db"), "stuck_check must be"),
]


@pytest.mark.parametrize("change, message", CASES)
def test_each_mistake_says_what_is_wrong(tmp_path, change, message):
    assert message in _bad(tmp_path, change)


def test_all_problems_listed_at_once(tmp_path):
    msg = _bad(tmp_path, lambda d: (d.update(repeats=0), d["sweep"].update(power_dbm=5)))
    assert "repeats" in msg and "power_dbm" in msg


def test_thru_line_only_needed_with_a_thru_channel(tmp_path):
    data = copy.deepcopy(GOOD)
    data["thru_channel"] = None
    del data["lines"]["thru"]
    p = tmp_path / "nothru.json"
    p.write_text(json.dumps(data))
    assert db.load_cooldown(p)["thru_channel"] is None


def test_not_json_and_missing_file(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text('{"cooldown": 5,}')
    with pytest.raises(ValueError, match="isn't valid JSON.*line 1"):
        db.load_cooldown(p)
    with pytest.raises(FileNotFoundError, match="No cooldown file"):
        db.load_cooldown(tmp_path / "nope.json")
