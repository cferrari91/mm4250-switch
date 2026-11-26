"""
Every position of a take or check saves the fridge's MXP, MXP2 and STP
readings just before and just after it (<sensor>_k_before/_after), on the
raw run, on the corrected run made from it, and in a check's positions.csv.
"""

import csv
import types

import pytest

from conftest import db, ecal, vna

sv = vna.sv                         # scripts/switch_vna.py
KEYS = [f"{s}_k_{w}" for w in ("before", "after") for s in sv.FRIDGE_SENSORS]


class TickingFridge:
    """mxp / mxp2 / stp at 0.010 / 0.020 / 0.900 K, each read 1 mK warmer than the last."""

    def __init__(self, broken=()):
        self.reads = 0
        self.broken = set(broken)
        for name, base in (("mxp", 0.010), ("mxp2", 0.020), ("stp", 0.900)):
            setattr(self, name, types.SimpleNamespace(temperature=self._reader(name, base)))

    def _reader(self, name, base):
        def read():
            if name in self.broken:
                raise OSError(f"{name} offline")
            self.reads += 1
            return base + 0.001 * self.reads
        return read


def test_take_saves_before_and_after_for_every_sensor(cfg, rig, dbpath, ideals):
    fridge = TickingFridge()
    t = vna.take(cfg, rig.vna, rig.switch, fridge, channels=[4], save=db.Saver(dbpath, "take"))
    take = db.load_take(t["take_id"], dbpath)
    runs = [r for part in ("before", "ports", "after") for r in take["repeats"][0][part].values()]
    assert len(runs) == 7
    for run in runs:
        m = run["meta"]
        assert all(m[k] is not None for k in KEYS), m
        for s in sv.FRIDGE_SENSORS:
            assert m[f"{s}_k_after"] > m[f"{s}_k_before"]       # read after the sweep, so later
        assert m["mxc_temp_k"] == m["mxp_k_before"]             # unchanged meaning
        assert m["mxp_k_before"] < m["mxp2_k_before"] < m["stp_k_before"]   # the right sensors
    raw = take["repeats"][0]["ports"][4]["meta"]
    result = ecal.correct_take(take, definitions=ideals)
    db.save_corrected(dbpath, result, take)
    corr = db.load_take(t["take_id"], dbpath)["corrected"]
    assert len(corr) == 1
    assert all(corr[0]["meta"][k] == raw[k] for k in KEYS)     # carried over from the raw run


def test_one_broken_sensor_saves_the_others_and_warns_once(cfg, rig, dbpath, capsys):
    sv._warned.clear()
    fridge = TickingFridge(broken={"mxp2"})
    t = vna.take(cfg, rig.vna, rig.switch, fridge, channels=[4], save=db.Saver(dbpath, "take"))
    m = db.load_take(t["take_id"], dbpath)["repeats"][0]["ports"][4]["meta"]
    assert m["mxp2_k_before"] is None and m["mxp2_k_after"] is None
    assert m["mxp_k_before"] is not None and m["stp_k_after"] is not None
    assert capsys.readouterr().out.count("no mxp2 reading") == 1
    assert rig.switch.last == "ALL_OPEN"


def test_no_fridge_leaves_them_empty(cfg, rig, dbpath):
    t = vna.take(cfg, rig.vna, rig.switch, None, channels=[4], save=db.Saver(dbpath, "take"))
    m = db.load_take(t["take_id"], dbpath)["repeats"][0]["ports"][4]["meta"]
    assert all(m[k] is None for k in KEYS) and m["mxc_temp_k"] is None


def test_check_rows_db_and_csv_have_them(cfg, rig, tmp_path):
    stuck_db = db.open_database(cfg, "stuck", helper=None, local_dir=tmp_path / "dbs")
    save = db.Saver(stuck_db, "stuck_check", files=True)
    r = vna.stuck_check(cfg, rig.vna, rig.switch, TickingFridge(), save=save)
    assert all(row["temps"]["stp_k_after"] > row["temps"]["stp_k_before"] for row in r["rows"])
    first_id = r["rows"][0]["run_id"]
    first = db.load_runs([first_id], stuck_db)[first_id]["meta"]
    assert all(first[k] == r["rows"][0]["temps"][k] for k in KEYS)
    path = db.write_check_csv(r, save.folder)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(r["rows"])
    assert all(rows[0][k] not in ("", None) for k in KEYS)
    assert float(rows[0]["mxp_k_before"]) == pytest.approx(r["rows"][0]["temps"]["mxp_k_before"])


# --- the hold's temperature log (sv._TemperatureLog, used by sv.hold_state) ---

class AllSensorFridge:
    """Every ALL_FRIDGE_SENSORS thermometer, at 0.01 K x its index + 1; `broken` ones raise."""

    def __init__(self, broken=()):
        for i, name in enumerate(sv.ALL_FRIDGE_SENSORS):
            def read(name=name, k=0.01 * (i + 1)):
                if name in broken:
                    raise OSError(f"{name} offline")
                return k
            setattr(self, name, types.SimpleNamespace(temperature=read))


def _log(fridge, interval_s=10, csv_path=None):
    return sv._TemperatureLog(fridge, interval_s, csv_path, 6, sv.ALL_FRIDGE_SENSORS, "test")


def _run_log(log, n):
    import time
    log.start()
    deadline = time.time() + 10
    while len(log.rows) < n and time.time() < deadline:
        time.sleep(0.005)
    log.stop()
    assert not log._thread.is_alive()


def test_temperature_log_writes_every_reading_as_it_goes(tmp_path):
    path = tmp_path / "Sweeps" / "thru_hold" / "t_temps.csv"
    log = _log(AllSensorFridge(), interval_s=0.01, csv_path=path)
    assert path.read_text().splitlines() == ["time,mxp,mxp2,stp,cmn,stp2,cfp,prp"]  # header before start
    _run_log(log, 3)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(log.rows) >= 3
    assert [float(rows[0][c]) for c in log.columns[1:]] == pytest.approx([0.01 * (i + 1) for i in range(7)])
    assert "CMN 40 mK" in log.summary() and f"saved {path}" in log.summary()
    table = log.table().splitlines()
    assert table[1].split() == ["time", "MXP", "MXP2", "STP", "CMN", "STP2", "CFP", "PRP"]
    assert len(table) == 2 + min(len(log.rows), log.show_last)


def test_temperature_log_keeps_going_past_a_broken_thermometer(tmp_path, capsys):
    path = tmp_path / "t.csv"
    log = _log(AllSensorFridge(broken={"CMN"}), interval_s=0.01, csv_path=path)
    _run_log(log, 3)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert all(r["cmn"] == "" and r["mxp"] == "0.01" for r in rows)
    assert "CMN --" in log.summary()
    assert capsys.readouterr().out.count("no CMN reading") <= 1     # warned once (or not at all if already warned)


def test_temperature_log_without_a_fridge_or_a_file():
    log = _log(None, interval_s=0.01)
    _run_log(log, 2)
    assert all(v is None for r in log.rows for k, v in r.items() if k != "time")
    assert "MXP --" in log.summary() and "not saved" in log.summary()


def test_temperature_log_refuses_an_existing_file_and_a_bad_interval(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("keep me")
    with pytest.raises(FileExistsError):
        _log(None, csv_path=path)
    assert path.read_text() == "keep me"
    with pytest.raises(ValueError):
        _log(None, interval_s=0)


def test_temperature_log_stopped_before_start_closes_its_file(tmp_path):
    """The hold cell stops it this way when the switch refuses to move."""
    log = _log(None, csv_path=tmp_path / "t.csv")
    log.stop()
    assert log._file.closed and log.rows == []
