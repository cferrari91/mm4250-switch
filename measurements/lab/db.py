"""
Files and databases: the cooldown file, which database to write to,
saving and reading runs, Touchstone files, backups.

    cfg  = load_cooldown("cooldowns/CD05.json")     # checked; fails with a plain message
    DB   = open_database(cfg, "vna")                 # the lab helper's file (or a local fallback)
    save = Saver(DB, "take", files=False)           # what vna.take() calls after each position
    takes = list_takes(DB); take = load_take(None, DB)
    export_touchstone([12, 13], DB)                  # never overwrites

Databases. On the DAQ the lab's scripts/database_helper.py (found in the
framework folder above this one) files them as
E:\\QCoDeS\\<project>\\Cooldown_<NN>\\<project>_Cooldown_<NN>_<name>.db.
take_data uses name "vna", stuck_check name "stuck": one writer per .db
at a time, so the two notebooks never share a file. Where there is no
helper (a laptop, the tests) the database goes to local_databases/ beside
this file, with a loud warning saying so.

Every run is a QCoDeS dataset (frequency + complex S-parameter arrays,
paramtype "array") plus metadata columns. Each run carries the whole
cooldown file as text (cooldown_json), so a database alone says how the
fridge was wired. Touchstone files and CSVs are only written when asked,
in Sweeps/ beside the database, and never overwrite anything.

Reading uses sqlite3 + numpy (QCoDeS stores each array as an np.save
blob; anything else fails loudly). Copied and trimmed from the older
read_db.py / sweep_db.py (2026-11-08).
"""

import csv
import hashlib
import importlib
import io
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import vna as _vna   # MAX_POWER_DBM, SPARAMS

HERE = Path(__file__).resolve().parent
LOCAL_DB_DIR = HERE / "local_databases"
FILES_DIR = "Sweeps"                      # beside the database; gitignored here
BACKUP_DIR = "db_backups"
BACKUPS_KEPT = 10
TOUCHSTONE_2PORT_ORDER = ("S11", "S21", "S12", "S22")   # NOT the reading order
_NUMPY_MAGIC = b"\x93NUMPY"


# ---------------------------------------------------------------------------
# The cooldown file
# ---------------------------------------------------------------------------

_TOP_KEYS = {"cooldown", "switch_serial", "database_project", "ports", "thru_channel",
             "lines", "sweep", "repeats", "correct_sparam", "stuck_check"}
_OPTIONAL_TOP = {"notes"}
_SWEEP_KEYS = {"start_hz", "stop_hz", "points", "if_bandwidth_hz", "power_dbm"}
_LINE_KEYS = {"input", "return", "thru", "notes"}
_STUCK_KEYS = {"pass_db", "thru_pass_db"}


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _text_ok(x):
    return isinstance(x, str) and x.strip() != ""


def load_cooldown(path):
    """
    Read and check a cooldown file (see README.md and cooldowns/example.json).
    Every problem found is listed in one ValueError, so one run of this
    tells you everything to fix. Returns the dict, plus "_path" and
    "_text" (the file exactly as written, stored on every run).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No cooldown file at {path.resolve()}")
    text = path.read_text()
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} isn't valid JSON: {e.msg} at line {e.lineno}, "
                         f"column {e.colno}") from None
    if not isinstance(cfg, dict):
        raise ValueError(f"{path.name}: the top level must be {{...}}")

    bad = []
    missing = sorted(_TOP_KEYS - set(cfg))
    if missing:
        bad.append(f"missing key(s): {', '.join(missing)}")
    unknown = sorted(set(cfg) - _TOP_KEYS - _OPTIONAL_TOP)
    if unknown:
        bad.append(f"unknown key(s): {', '.join(unknown)} (a typo? allowed: "
                   f"{', '.join(sorted(_TOP_KEYS | _OPTIONAL_TOP))})")

    if "cooldown" in cfg and not (_is_int(cfg["cooldown"]) and cfg["cooldown"] >= 0):
        bad.append(f"cooldown must be a whole number like 5, got {cfg['cooldown']!r}")
    for key in ("switch_serial", "database_project"):
        if key in cfg and not _text_ok(cfg[key]):
            bad.append(f"{key} must be non-empty text, got {cfg[key]!r}")

    ports = cfg.get("ports")
    if "ports" in cfg:
        if not isinstance(ports, dict) or not ports:
            bad.append("ports must be {\"RF1\": \"what's on it\", ...} with at least one port")
            ports = {}
        for name, what in ports.items():
            if not re.fullmatch(r"RF[1-6]", name):
                bad.append(f"ports: {name!r} isn't a switch channel; use RF1 to RF6")
            if not _text_ok(what):
                bad.append(f"ports: {name} needs a description, got {what!r}")
    ports = ports if isinstance(ports, dict) else {}

    thru = cfg.get("thru_channel")
    if "thru_channel" in cfg and thru is not None:
        if not (_is_int(thru) and 1 <= thru <= 6):
            bad.append(f"thru_channel must be a channel number 1-6 or null, got {thru!r}")
        elif f"RF{thru}" not in ports:
            bad.append(f"thru_channel {thru}: RF{thru} isn't in ports")

    lines = cfg.get("lines")
    if "lines" in cfg:
        if not isinstance(lines, dict):
            bad.append("lines must be {\"input\": ..., \"return\": ..., \"thru\": ...}")
        else:
            for key in ("input", "return") + (("thru",) if thru is not None else ()):
                if not _text_ok(lines.get(key)):
                    bad.append(f"lines.{key} must be a fridge line name like \"RF6\""
                               + (" (thru_channel is set)" if key == "thru" else ""))
            extra = sorted(set(lines) - _LINE_KEYS)
            if extra:
                bad.append(f"lines: unknown key(s) {', '.join(extra)}")

    sweep = cfg.get("sweep")
    if "sweep" in cfg:
        if not isinstance(sweep, dict):
            bad.append("sweep must be {\"start_hz\": ..., ...}")
        else:
            if set(sweep) != _SWEEP_KEYS:
                if _SWEEP_KEYS - set(sweep):
                    bad.append(f"sweep: missing {', '.join(sorted(_SWEEP_KEYS - set(sweep)))}")
                if set(sweep) - _SWEEP_KEYS:
                    bad.append(f"sweep: unknown key(s) {', '.join(sorted(set(sweep) - _SWEEP_KEYS))}")
            for key in _SWEEP_KEYS & set(sweep):
                if not _is_num(sweep[key]):
                    bad.append(f"sweep.{key} must be a number, got {sweep[key]!r}")
            if all(_is_num(sweep.get(k)) for k in ("start_hz", "stop_hz")):
                if not 0 < sweep["start_hz"] < sweep["stop_hz"]:
                    bad.append("sweep: need 0 < start_hz < stop_hz")
            if "points" in sweep and not (_is_int(sweep["points"]) and sweep["points"] >= 2):
                bad.append(f"sweep.points must be a whole number >= 2, got {sweep['points']!r}")
            if _is_num(sweep.get("if_bandwidth_hz")) and sweep["if_bandwidth_hz"] <= 0:
                bad.append("sweep.if_bandwidth_hz must be > 0")
            if _is_num(sweep.get("power_dbm")) and sweep["power_dbm"] > _vna.MAX_POWER_DBM:
                bad.append(f"sweep.power_dbm {sweep['power_dbm']:g} is above the "
                           f"{_vna.MAX_POWER_DBM:g} dBm cap (vna.MAX_POWER_DBM)")

    if "repeats" in cfg and not (_is_int(cfg["repeats"]) and cfg["repeats"] >= 1):
        bad.append(f"repeats must be a whole number >= 1, got {cfg['repeats']!r}")
    if "correct_sparam" in cfg and cfg["correct_sparam"] not in _vna.SPARAMS:
        bad.append(f"correct_sparam must be one of {', '.join(_vna.SPARAMS)}, "
                   f"got {cfg['correct_sparam']!r}")

    stuck = cfg.get("stuck_check")
    if "stuck_check" in cfg:
        if not isinstance(stuck, dict) or set(stuck) != _STUCK_KEYS:
            bad.append("stuck_check must be {\"pass_db\": number or null, "
                       "\"thru_pass_db\": number or null}")
        else:
            for key in _STUCK_KEYS:
                if stuck[key] is not None and not (_is_num(stuck[key]) and stuck[key] > 0):
                    bad.append(f"stuck_check.{key} must be a positive number (dB) or null, "
                               f"got {stuck[key]!r}")

    if bad:
        raise ValueError(f"{path.name} has problems:\n  - " + "\n  - ".join(bad))
    cfg["_path"] = str(path.resolve())
    cfg["_text"] = text
    return cfg


def print_cooldown(cfg):
    """A short summary of a loaded cooldown file."""
    s = cfg["sweep"]
    print(f"Cooldown {cfg['cooldown']}, switch {cfg['switch_serial']}, "
          f"database project {cfg['database_project']}  ({Path(cfg['_path']).name})")
    for name in sorted(cfg["ports"]):
        thru = " <- thru check" if cfg["thru_channel"] == int(name[2:]) else ""
        print(f"  {name}: {cfg['ports'][name]}{thru}")
    lines = cfg["lines"]
    print(f"  fridge lines: input {lines['input']}, return {lines['return']}"
          + (f", thru {lines['thru']}" if lines.get("thru") else "")
          + (f"  ({lines['notes']})" if lines.get("notes") else ""))
    print(f"  sweep {s['start_hz'] / 1e6:g}-{s['stop_hz'] / 1e6:g} MHz, {s['points']} points, "
          f"IF {s['if_bandwidth_hz']:g} Hz, {s['power_dbm']:g} dBm; repeats {cfg['repeats']}; "
          f"corrected on {cfg['correct_sparam']}")


# ---------------------------------------------------------------------------
# Which database
# ---------------------------------------------------------------------------

def find_database_helper(start=None):
    """
    The lab's scripts/database_helper.py, imported as scripts.database_helper
    from the first folder at or above `start` (default: this file's folder)
    that has it (on the DAQ, the framework checkout). None if there isn't one.
    """
    start = Path(start).resolve() if start is not None else HERE
    root = next((p for p in (start, *start.parents)
                 if (p / "scripts" / "database_helper.py").is_file()), None)
    if root is None:
        return None
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module("scripts.database_helper")


def open_database(cfg, name, create=True, helper="find", local_dir=None):
    """
    The database for this cooldown file and `name` ("vna" for takes,
    "stuck" for the stuck/thru checks). Returns its Path.

    With the lab helper (found with find_database_helper, or `helper`, a
    module with setup_database and database_path): its file, made and set
    as QCoDeS's database by setup_database. Without one: a local file in
    local_databases/ beside this file (or `local_dir`), with a loud
    warning. create=False only works out the path (nothing is made), for
    a notebook that may save files beside the database but nothing in it.
    """
    import qcodes
    from qcodes.dataset import initialise_or_create_database_at

    project, cooldown = cfg["database_project"], cfg["cooldown"]
    if helper == "find":
        helper = find_database_helper()
    if helper is not None:
        if not create:
            return Path(helper.database_path(project, cooldown, name)).resolve()
        ret = helper.setup_database(project, cooldown=cooldown, name=name)
        if isinstance(ret, tuple):
            ret = ret[0]
        active = qcodes.config["core"]["db_location"]
        if not isinstance(ret, (str, os.PathLike)) or Path(ret).resolve() != Path(active).resolve():
            raise RuntimeError(f"setup_database returned {ret!r} but QCoDeS records to {active!r}: "
                               "the lab helper has changed. Nothing was saved.")
        path = Path(ret).resolve()
        print(f"Database ({name}): {path}")
        return path

    folder = Path(local_dir) if local_dir is not None else LOCAL_DB_DIR
    path = (folder / f"{project}_Cooldown_{cooldown:02d}_{name}.db").resolve()
    bar = "!" * 78
    print(f"{bar}\nNO LAB DATABASE HELPER FOUND (scripts/database_helper.py above this folder).\n"
          f"{'DATA WILL GO TO A LOCAL FILE' if create else 'FILES WILL GO BESIDE A LOCAL PATH'}:\n"
          f"    {path}\n"
          f"On the DAQ this means the code isn't in the framework folder; stop and check.\n{bar}")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        initialise_or_create_database_at(path)
    return path


def take_folder(db_path, take):
    """Where a take's Touchstone files and saved figures go (files_folder for a take)."""
    return files_folder(Path(db_path).parent, "takes", take["take_id"],
                        take["cfg"]["switch_serial"], take["temp_str"])


def take_run_ids(take):
    """Every run id of a loaded take: standards, raw channels, corrected."""
    ids = [run["run_id"] for block in take["repeats"]
           for part in ("before", "ports", "after") for run in block[part].values()]
    return sorted(ids + [c["run_id"] for c in take["corrected"]])


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def _clean_value(value):
    """Metadata values QCoDeS can store: text and numbers; None means skip."""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value)
    return value


def save_run(db_path, experiment, sample, name, freq, data, metadata):
    """
    One run (frequency + complex arrays, e.g. {"S11": ..., "S21": ...}) as
    a QCoDeS dataset named `name` in `experiment`, with `metadata` as
    columns (None values skipped). Returns the run id. The write-ahead log
    is folded into the .db afterwards, so the file is complete on its own.
    """
    from qcodes.dataset import Measurement, connect, load_or_create_experiment

    conn = connect(str(db_path))
    try:
        exp = load_or_create_experiment(experiment, sample_name=sample, conn=conn)
        meas = Measurement(exp=exp, name=name)
        meas.register_custom_parameter("frequency", label="Frequency", unit="Hz",
                                       paramtype="array")
        for sparam in data:     # "array", never "complex": complex arrays fail on read-back
            meas.register_custom_parameter(sparam.lower(), label=sparam, unit="",
                                           setpoints=("frequency",), paramtype="array")
        with meas.run() as saver:
            saver.add_result(("frequency", np.asarray(freq)),
                             *[(s.lower(), np.asarray(v)) for s, v in data.items()])
            dataset = saver.dataset
            dataset.add_metadata("n_ports", 2 if len(data) == 4 else 1)
            for key, value in metadata.items():
                value = _clean_value(value)
                if value is not None:
                    dataset.add_metadata(key, value)
        run_id = dataset.run_id
    finally:
        conn.close()
    _checkpoint(db_path)
    return run_id


def files_folder(root, kind, ident, serial, temp_str):
    """<root>/Sweeps/<kind>/<id>_<serial>_<temp>/: root is the database's folder;
    kind is takes, stuck_check or thru_check."""
    return Path(root).resolve() / FILES_DIR / kind / f"{ident}_{serial}_{temp_str}"


class Saver:
    """
    What vna.take / stuck_check / thru_check call after each position:
    save(name, freq, data, metadata) -> run id (or None).

    db_path: the database (None = don't save to a database).
    experiment: "take", "stuck_check" or "thru_check" (the QCoDeS
    experiment; sample = the switch serial).
    files: also write a Touchstone file per position into
    files_folder(...), named <step>_<position>[_run<id>].s2p; an existing
    file is never overwritten (skipped with a warning). `files_root`: the
    folder Sweeps/ goes in (default: the database's folder; needed when
    db_path is None). After a sequence, .folder is where the files went.
    """

    def __init__(self, db_path, experiment, files=False, files_root=None):
        if db_path is None and files and files_root is None:
            raise ValueError("files without a database need files_root (a folder)")
        self.db_path = Path(db_path) if db_path is not None else None
        self.experiment = experiment
        self.files = files
        self.files_root = Path(files_root) if files_root is not None else (
            self.db_path.parent if self.db_path is not None else None)
        self.folder = None
        self.paths = []

    def __call__(self, name, freq, data, metadata):
        run_id = None
        if self.db_path is not None:
            run_id = save_run(self.db_path, self.experiment, metadata.get("switch_serial"),
                              name, freq, data, metadata)
        if self.files:
            ident = metadata.get("take_id") or metadata.get("check_id")
            kind = {"take": "takes"}.get(self.experiment, self.experiment)
            self.folder = files_folder(self.files_root, kind, ident, metadata.get("switch_serial"),
                                       metadata.get("temp_str"))
            stem = f"{metadata.get('step', 0):02d}_{name}" + (f"_run{run_id}" if run_id else "")
            path = self.folder / (stem + (".s2p" if len(data) == 4 else ".s1p"))
            if save_touchstone(freq, data, path):
                self.paths.append(path)
        return run_id


def save_corrected(db_path, result, take, files=False):
    """
    Save ecal.correct_take's result as runs of their own: one per channel
    per repeat, named RF<n>_corrected, a 1-port reflection at RF n's
    connector (stored as S11), kind "corrected", linked to the raw runs it
    came from (source_run_ids: the channel run, then that repeat's
    standards), plus which definitions and which raw trace were used.
    Refuses (saves nothing) if this take already has corrected runs with
    the same definitions. Returns the new run ids.
    """
    existing = [c for c in take["corrected"] if c["definitions"] == result["definitions"]]
    if existing:
        print(f"Take {take['take_id']} already has corrected runs with {result['definitions']} "
              f"(runs {', '.join(str(c['run_id']) for c in existing)}); nothing saved.")
        return []
    cfg = take["cfg"]
    ids = []
    for k, (corr, src) in enumerate(zip(result["corrected"], result["sources"]), 1):
        for ch, gamma in corr.items():
            raw_meta = take["repeats"][k - 1]["ports"][ch]["meta"]
            meta = {"take_id": take["take_id"], "kind": "corrected", "repeat": k,
                    "position": f"RF{ch}", "channel": ch, "port": cfg["ports"].get(f"RF{ch}"),
                    "cooldown": cfg["cooldown"], "switch_serial": cfg["switch_serial"],
                    "start_temp_k": take["temp_k"], "temp_str": take["temp_str"],
                    "mxc_temp_k": raw_meta.get("mxc_temp_k"), "note": raw_meta.get("note"),
                    "cooldown_json": cfg["_text"], "definitions": result["definitions"],
                    "definitions_chosen": result.get("chosen"),
                    "definitions_dir": Path(result["definitions_dir"]).name,
                    "corrected_from": result["sparam"], "fmin_hz": result["fmin_hz"],
                    "source_run_ids": json.dumps(src[ch])}
            run_id = save_run(db_path, "take", cfg["switch_serial"], f"RF{ch}_corrected",
                              result["freq"], {"S11": gamma}, meta)
            ids.append(run_id)
            if files:
                folder = files_folder(Path(db_path).parent, "takes", take["take_id"],
                                      cfg["switch_serial"], take["temp_str"])
                save_touchstone(result["freq"], gamma,
                                folder / f"RF{ch}_corrected_rep{k}_run{run_id}.s1p")
    print(f"Saved {len(ids)} corrected run(s) ({result['definitions']}, from "
          f"{result['sparam']}): {ids}")
    return ids


def write_check_csv(result, folder, pass_db=None):
    """positions.csv for a stuck/thru check: one row per position."""
    folder = Path(folder)
    path = folder / "positions.csv"
    if path.exists():
        print(f"  [warning] {path} exists; not overwritten")
        return None
    folder.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "position", "run_id", "mxc_temp_K",
                    "median_abs_S21_minus_first_open", "contrast_db", "level_db", "flag"])
        for r in result["rows"]:
            w.writerow([r["step"], r["label"], r["run_id"], r["temp_k"], r["diff"],
                        r.get("contrast_db"), r.get("level_db"), r.get("flag", "")])
    print(f"  wrote {path}")
    return path


# ---------------------------------------------------------------------------
# Touchstone
# ---------------------------------------------------------------------------

def save_touchstone(freq, data, path):
    """
    Write a .s1p (a bare array or {"S11": ...}) or .s2p (all four, in
    Touchstone's S11 S21 S12 S22 order). Never overwrites: an existing
    file is left alone with a warning and False returned.
    """
    data = dict(data) if hasattr(data, "keys") else {"S11": data}
    path = Path(path)
    if path.exists():
        print(f"  [warning] {path} exists; not overwritten")
        return False
    if len(data) == 1:
        columns = [next(iter(data.values()))]
    elif len(data) == 4:
        columns = [data[s] for s in TOUCHSTONE_2PORT_ORDER]
    else:
        raise ValueError(f"Touchstone needs 1 or 4 S-parameters, got {sorted(data)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x") as f:                    # "x": fails rather than overwrite
        f.write("! MM4250 lab code (measurements/lab/db.py)\n# HZ S RI R 50\n")
        for i in range(len(freq)):
            f.write(f"{freq[i]:.1f} " + " ".join(f"{c[i].real:.10e} {c[i].imag:.10e}"
                                                for c in columns) + "\n")
    return True


def read_touchstone(path):
    """A .s1p/.s2p written by save_touchstone (RI, Hz) -> (freq, {"S11": ...})."""
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.split("!")[0].strip()
        if line and not line.startswith("#"):
            rows.append([float(x) for x in line.split()])
    a = np.array(rows)
    z = a[:, 1::2] + 1j * a[:, 2::2]
    names = ("S11",) if z.shape[1] == 1 else TOUCHSTONE_2PORT_ORDER
    return a[:, 0], {n: z[:, i] for i, n in enumerate(names)}


def export_touchstone(run_ids, db_path, out_dir=None):
    """
    Write runs from the database as Touchstone files, <name>_run<id>.s2p
    (.s1p for corrected runs), into `out_dir` (default
    <database folder>/Sweeps/export/). Never overwrites. Returns the paths
    of the files that exist afterwards.
    """
    if isinstance(run_ids, int):
        run_ids = [run_ids]
    out_dir = Path(out_dir) if out_dir is not None else Path(db_path).resolve().parent / FILES_DIR / "export"
    names = {r["run_id"]: r["name"] for r in _run_rows(db_path, "", list(run_ids))}
    paths = []
    for run_id in run_ids:
        if run_id not in names:
            raise KeyError(f"No run {run_id} in {Path(db_path).name}")
        freq, data = load_run(run_id, db_path)
        path = out_dir / f"{names[run_id]}_run{run_id}{'.s2p' if len(data) == 4 else '.s1p'}"
        if save_touchstone(freq, data, path):
            print(f"  wrote {path}")
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Reading (sqlite3 + numpy)
# ---------------------------------------------------------------------------

def _connect(db_path):
    """Open for reading. mode=rw (not ro: ro leaves -wal/-shm it can't clean up); never creates."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"No database at {db_path}")
    return sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=rw", uri=True)


def _columns(con):
    return {row[1] for row in con.execute('PRAGMA table_info("runs")')}


def _array(blob, where):
    if not isinstance(blob, bytes) or not blob.startswith(_NUMPY_MAGIC):
        raise RuntimeError(f"{where}: not a numpy array blob; QCoDeS's storage format may have "
                           "changed. Load it with qcodes.dataset.load_by_id instead.")
    return np.load(io.BytesIO(blob), allow_pickle=False)


def load_run(run_id, db_path):
    """One run's data: (freq_hz, {"S11": complex array, ...})."""
    con = _connect(db_path)
    try:
        row = con.execute("SELECT result_table_name, parameters FROM runs WHERE run_id = ?",
                          (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"No run {run_id} in {Path(db_path).name}")
        table, params = row
        cols = params.split(",")
        rows = con.execute(f'SELECT {", ".join(cols)} FROM "{table}"').fetchall()
    finally:
        con.close()
    freq, data = None, {}
    for values in rows:
        for col, blob in zip(cols[1:], values[1:]):
            if blob is None:
                continue
            f = _array(values[0], f"run {run_id} frequency")
            if freq is None:
                freq = f
            data[col.upper()] = _array(blob, f"run {run_id} {col}")
    if freq is None or len(data) != len(cols) - 1:
        raise RuntimeError(f"run {run_id}: incomplete data")
    return freq, data


_META = ("take_id", "check_id", "check_type", "kind", "standards_when", "repeat", "step",
         "position", "channel", "port", "mxc_temp_k", "start_temp_k", "temp_str", "note",
         "definitions", "definitions_chosen", "corrected_from", "source_run_ids", "cooldown_json", "switch_serial")


def _run_rows(db_path, where, params):
    """Runs (with the metadata columns above) matching a WHERE clause, oldest first."""
    con = _connect(db_path)
    try:
        have = _columns(con)
        cols = ", ".join(f'"{c}"' if c in have else f'NULL AS "{c}"' for c in _META)
        sql = f"SELECT run_id, name, run_timestamp, {cols} FROM runs"
        if where:
            sql += " WHERE " + where
        elif params:                                       # a list of run ids
            sql += f" WHERE run_id IN ({', '.join('?' * len(params))})"
        rows = con.execute(sql + " ORDER BY run_id", params).fetchall()
    finally:
        con.close()
    return [dict(zip(("run_id", "name", "run_timestamp") + _META, r)) for r in rows]


def list_takes(db_path, verbose=True):
    """Every take in the database, oldest first: id, when, channels, temperature, what was saved."""
    if not Path(db_path).is_file():
        print(f"No database at {db_path}")
        return []
    have = set()
    con = _connect(db_path)
    try:
        have = _columns(con)
    finally:
        con.close()
    if "take_id" not in have:
        if verbose:
            print("No takes in this database yet.")
        return []
    takes = {}
    for r in _run_rows(db_path, '"take_id" IS NOT NULL', ()):
        t = takes.setdefault(r["take_id"], {"take_id": r["take_id"], "first_run": r["run_id"],
                                            "temp_str": r["temp_str"], "note": r["note"],
                                            "channels": [], "raw": 0, "standard": 0,
                                            "corrected": 0, "definitions": set()})
        t[r["kind"]] = t.get(r["kind"], 0) + 1
        if r["kind"] == "raw" and r["channel"] not in t["channels"]:
            t["channels"].append(r["channel"])
        if r["kind"] == "corrected":
            t["definitions"].add(r["definitions"])
    out = list(takes.values())
    for t in out:
        t["definitions"] = sorted(t["definitions"])
        if verbose:
            print(f"{t['take_id']}  {t['temp_str']:>6}  "
                  f"{','.join(f'RF{c}' for c in sorted(t['channels'])):<24} runs from {t['first_run']}: "
                  f"{t['standard']} standards, {t['raw']} raw, {t['corrected']} corrected"
                  + (f" ({', '.join(t['definitions'])})" if t["definitions"] else "")
                  + (f"  note: {t['note']}" if t["note"] else ""))
    return out


def load_take(take_id, db_path):
    """
    A take back from the database (take_id None = the newest):

        {"take_id", "cfg" (its cooldown file, with "_text"), "temp_k" (reading at
         the start), "temp_str", "channels", "freq",
         "repeats": [{"before": {state: run}, "ports": {ch: run}, "after": {state: run}}],
         "corrected": [run, ...]}

    where each run is {"run_id", "data": {"S11": ...}, "meta": {...}}.
    """
    if take_id is None:
        takes = list_takes(db_path, verbose=False)
        if not takes:
            raise LookupError(f"No takes in {db_path}")
        take_id = takes[-1]["take_id"]
    rows = _run_rows(db_path, '"take_id" = ?', (take_id,))
    if not rows:
        raise LookupError(f"No take {take_id!r} in {Path(db_path).name}")
    cfg = json.loads(rows[0]["cooldown_json"])
    cfg["_text"] = rows[0]["cooldown_json"]
    repeats, corrected, freq, channels = {}, [], None, []
    for r in rows:
        f, data = load_run(r["run_id"], db_path)
        run = {"run_id": r["run_id"], "data": data, "meta": r}
        if r["kind"] == "corrected":
            corrected.append(dict(run, channel=r["channel"], repeat=r["repeat"],
                                  definitions=r["definitions"], freq=f))
            continue
        if freq is None:
            freq = f
        elif not np.array_equal(f, freq):
            raise RuntimeError(f"run {r['run_id']} is on a different frequency axis from the "
                               "rest of the take")
        block = repeats.setdefault(int(r["repeat"]), {"before": {}, "ports": {}, "after": {}})
        if r["kind"] == "raw":
            block["ports"][int(r["channel"])] = run
            if r["channel"] not in channels:
                channels.append(int(r["channel"]))
        else:
            block[r["standards_when"]][r["position"]] = run
    return {"take_id": take_id, "cfg": cfg, "temp_k": rows[0]["start_temp_k"],
            "temp_str": rows[0]["temp_str"], "channels": sorted(channels), "freq": freq,
            "repeats": [repeats[k] for k in sorted(repeats)], "corrected": corrected}


def load_runs(run_ids, db_path):
    """{run_id: {"name", "freq", "data", "meta"}} for a list of run ids (Recall)."""
    out = {}
    for r in _run_rows(db_path, "", list(run_ids)):
        f, data = load_run(r["run_id"], db_path)
        out[r["run_id"]] = {"name": r["name"], "freq": f, "data": data, "meta": r}
    missing = sorted(set(run_ids) - set(out))
    if missing:
        raise KeyError(f"No run(s) {missing} in {Path(db_path).name}")
    return out


# ---------------------------------------------------------------------------
# Write-ahead log and backups
# ---------------------------------------------------------------------------

def _checkpoint(db_path):
    """
    Fold the -wal into the .db (PASSIVE: never blocks a reader), so the
    .db is complete on its own while a kernel still holds it open.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        _, log_pages, done = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    finally:
        conn.close()
    if log_pages > 0 and done < log_pages:
        print(f"  [note] {Path(db_path).name}: {log_pages - done} page(s) still in the -wal "
              "(another reader has it open); copy the -wal too")


def _fingerprint(con):
    h = hashlib.sha256()
    for table in ("experiments", "runs"):
        for row in con.execute(f"SELECT * FROM {table} ORDER BY 1"):
            h.update(repr(row).encode())
    return h.hexdigest()


def _existing_backups(backup_dir, stem):
    pattern = re.compile(re.escape(stem) + r"_\d{8}-\d{6}")
    return sorted(p for p in Path(backup_dir).glob(f"{stem}_*.db") if pattern.fullmatch(p.stem))


def backup_db(db_path, keep=BACKUPS_KEPT):
    """
    A checked snapshot of the database in db_backups/ beside it
    (<stem>_YYYYMMDD-HHMMSS.db), through SQLite's backup API (consistent
    while a kernel has it open). Skipped if nothing changed since the
    last backup; the newest `keep` are kept. Never raises: a backup
    problem is printed, so it can't hide the measurement's own result.
    Returns the new path or None.
    """
    try:
        return _backup(Path(db_path), keep)
    except Exception as e:
        print(f"  [warning] database backup failed ({type(e).__name__}: {e}); "
              "the database itself is fine -- run db.backup_db(DB) by hand")
        return None


def _backup(db_path, keep):
    if not db_path.is_file():
        raise FileNotFoundError(f"no database at {db_path}")
    backup_dir = db_path.parent / BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    stem = db_path.stem
    existing = _existing_backups(backup_dir, stem)
    src = sqlite3.connect(str(db_path), timeout=30)
    try:
        fingerprint = _fingerprint(src)
        if existing:
            last = sqlite3.connect(f"file:{existing[-1].as_posix()}?mode=ro", uri=True)
            try:
                same = _fingerprint(last) == fingerprint
            except sqlite3.Error:
                same = False
            finally:
                last.close()
            if same:
                print(f"  backup: no new runs since {existing[-1].name}, nothing written")
                return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final = backup_dir / f"{stem}_{stamp}.db"
        while final.exists():                    # two in one second: never overwrite
            time.sleep(1.05)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            final = backup_dir / f"{stem}_{stamp}.db"
        partial = backup_dir / f"{stem}_{stamp}.db.partial"
        dst = sqlite3.connect(str(partial))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
            check = dst.execute("PRAGMA quick_check").fetchone()[0]
            n_src = src.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            n_dst = dst.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if check != "ok" or n_src != n_dst:
        partial.unlink()
        raise RuntimeError(f"backup failed its check (quick_check={check!r}, runs {n_dst} vs {n_src})")
    os.replace(partial, final)
    removed = _existing_backups(backup_dir, stem)[:-keep] if keep else []
    for old in removed:
        old.unlink()
    print(f"  backup: {n_dst} runs -> {final}" + (f", removed {len(removed)} older" if removed else ""))
    return final
