"""
Read sweeps back out of mm4250_sweeps.db, and write Touchstone files
from them on demand.

    list_runs()                     what's in the database, one dict per run
    load_run(43)                    (freq_hz, {"S11": ..., "S21": ...}) for one run
    export_touchstone([43, 44])     write .s2p files for runs, in the Sweeps/ layout
    backup_db()                     consistent, timestamped copy of the database

numpy and the standard library only -- no qcodes. So this runs anywhere
the .db file is, including a laptop with no QCoDeS installed and no
instruments connected. sweep_db.py (which does need qcodes) writes the
database; this reads it.

It reads the file with sqlite3 directly rather than through qcodes, which
means it depends on how QCoDeS stores an "array" parameter: one numpy
array per cell, serialized with np.save. That has been stable, but it is
QCoDeS's internal format rather than a promise -- so every blob is
checked for the numpy header and anything else fails loudly rather than
decoding into wrong numbers.

The Touchstone writer and the folder layout live here too, and sweep_db
uses them, so a file written during a sweep and one exported later are
byte-for-byte the same and land in the same place.
"""

import hashlib
import io
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np

DEFAULT_DB_NAME = "mm4250_sweeps.db"
DEFAULT_OUT_DIR_NAME = "Sweeps"
DEFAULT_BACKUP_DIR_NAME = "db_backups"
DEFAULT_BACKUPS_KEPT = 10

# Touchstone column order for a 2-port file is S11, S21, S12, S22 -- NOT
# the reading order measure_2port uses. Getting these two confused
# silently swaps forward and reverse transmission in every saved file, so
# the writer below goes through this tuple and nothing else.
TOUCHSTONE_2PORT_ORDER = ("S11", "S21", "S12", "S22")

_NUMPY_MAGIC = b"\x93NUMPY"


def default_db_path():
    """mm4250_sweeps.db beside these files -- the one sweep_db writes to."""
    return Path(__file__).resolve().parent / DEFAULT_DB_NAME


def sweep_folder(out_root, switch_serials, date_str, temp_str, group):
    """
    Where a sweep's files go:
    <out_root>/<switch_serials>/<date_str>/<temp_str>/<group>/

    The one definition of the layout -- run_sweep and export_touchstone
    both call this, so they can't drift apart.
    """
    return Path(out_root) / switch_serials / date_str / temp_str / group


def save_touchstone(freq_hz, data, path):
    """
    Write `freq_hz`/`data` to a Touchstone file at `path`.

    `data` is either a bare complex array (written as a 1-port .s1p) or
    the {"S11": ..., "S12": ..., "S21": ..., "S22": ...} dict returned by
    measure_2port or load_run (written as a 2-port .s2p). The file
    extension follows from the data, so pass a path whose suffix matches.
    """
    data = dict(data) if hasattr(data, "keys") else {"S11": data}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if len(data) == 1:
        columns = [data["S11"]]
    elif len(data) == 4:
        columns = [data[sparam] for sparam in TOUCHSTONE_2PORT_ORDER]
    else:
        raise ValueError(
            f"Touchstone needs 1 or 4 S-parameters, got {sorted(data)}"
        )

    with open(path, "w") as f:
        f.write("!Created by sweep_db.py\n")
        f.write("# HZ S RI R 50\n")
        for i in range(len(freq_hz)):
            values = " ".join(
                f"{col[i].real:.6e} {col[i].imag:.6e}" for col in columns
            )
            f.write(f"{freq_hz[i]:.1f} {values}\n")


def _connect(db_path):
    """
    Open the database for reading.

    mode=rw rather than ro: a read-only connection to a WAL-mode database
    leaves -wal/-shm files behind that it isn't allowed to clean up, and
    stray -wal files are exactly what LAB_SETUP tells you to worry about.
    Nothing here writes. mode=rw also refuses to create the file, so a
    wrong path is an error instead of a new empty database.
    """
    db_path = Path(db_path) if db_path is not None else default_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(f"No database at {db_path}")
    return sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True), db_path


def _array(blob, where):
    """Decode one QCoDeS array cell, or refuse if it isn't np.save output."""
    if not isinstance(blob, bytes) or not blob.startswith(_NUMPY_MAGIC):
        raise RuntimeError(
            f"{where}: not a numpy array blob -- QCoDeS's storage format may "
            "have changed. Load it with qcodes.dataset.load_by_id instead."
        )
    return np.load(io.BytesIO(blob), allow_pickle=False)


def _columns(con, table):
    return {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}


def list_runs(db_path=None):
    """
    Every run in the database, oldest first, as a list of dicts:

        {"run_id": 43, "experiment": "20260925_295K_SN0077_RF3_cal",
         "name": "RF3", "switch_serials": "SN0077", "date_str": "20260925",
         "temp_str": "295K", "cal": "cal", "n_ports": 2,
         "touchstone_path": "Sweeps/SN0077/.../RF3_run43.s2p" or None}

    "cal" comes from the VNA's recorded correction state (None if the
    run predates that being recorded).
    """
    con, _ = _connect(db_path)
    try:
        have = _columns(con, "runs")
        wanted = ["switch_serials", "date_str", "temp_str", "n_ports",
                  "vna_correction_enabled", "touchstone_path"]
        extra = [f"r.{c}" if c in have else "NULL" for c in wanted]
        rows = con.execute(
            f"SELECT r.run_id, e.name, r.name, {', '.join(extra)} "
            "FROM runs r JOIN experiments e USING (exp_id) ORDER BY r.run_id"
        ).fetchall()
    finally:
        con.close()
    out = []
    for run_id, exp, name, serials, date, temp, n_ports, corr, tpath in rows:
        out.append({
            "run_id": run_id, "experiment": exp, "name": name,
            "switch_serials": serials, "date_str": date, "temp_str": temp,
            "cal": {1: "cal", 0: "uncal"}.get(corr), "n_ports": n_ports,
            "touchstone_path": tpath,
        })
    return out


def load_run(run_id, db_path=None):
    """
    One run's data: (freq_hz, {"S11": complex array, ...}) -- the same
    shape measure_2port and read_touchstone return, so it drops straight
    into plot_measurement / summarize / save_touchstone:

        freq, data = load_run(43)
        plot_measurement(freq, data, title="run 43")
    """
    con, db_path = _connect(db_path)
    try:
        row = con.execute(
            "SELECT result_table_name, parameters FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"No run {run_id} in {db_path.name}")
        table, params = row
        cols = params.split(",")
        if cols[0] != "frequency":
            raise RuntimeError(f"run {run_id}: expected frequency first, got {cols}")
        rows = con.execute(
            f'SELECT {", ".join(cols)} FROM "{table}"'
        ).fetchall()
    finally:
        con.close()

    freq_hz, data = None, {}
    for values in rows:
        freq_blob, sparam_blobs = values[0], values[1:]
        for sparam, blob in zip(cols[1:], sparam_blobs):
            if blob is None:
                continue
            f = _array(freq_blob, f"run {run_id} frequency")
            if freq_hz is None:
                freq_hz = f
            elif not np.array_equal(f, freq_hz):
                raise RuntimeError(f"run {run_id}: S-parameters on different frequency axes")
            data[sparam.upper()] = _array(blob, f"run {run_id} {sparam}")

    missing = [c.upper() for c in cols[1:] if c.upper() not in data]
    if freq_hz is None or missing:
        raise RuntimeError(f"run {run_id}: no data for {missing or cols[1:]}")
    return freq_hz, data


def _group(run):
    """
    The <group> folder a run belongs in, recovered from its experiment
    name "<date>_<temp>_<serials>_<group>". An experiment named by hand
    (exp_name=...) that doesn't follow the pattern is used whole.
    """
    prefix = f"{run['date_str']}_{run['temp_str']}_{run['switch_serials']}_"
    exp = run["experiment"]
    return exp[len(prefix):] if exp.startswith(prefix) and len(exp) > len(prefix) else exp


def export_touchstone(run_ids, db_path=None, out_root=None, overwrite=False):
    """
    Write Touchstone files for runs that are only in the database -- for
    scikit-rf, the mm4250-ecal notebook, Keysight or NIST software, or
    sending to someone without QCoDeS.

        export_touchstone([43, 44, 45, 46])
        export_touchstone(runs)            # the list run_twoport_sweep returned

    Files land where the sweep would have put them with touchstone=True:
    <out_root>/<serials>/<date>/<temp>/<group>/<position>_run<id>.s2p,
    with out_root defaulting to Sweeps/ beside the database.

    Exporting a run that already has a file never makes a second one.
    With the default out_root, the run's own file counts wherever it is
    -- the standard place, or wherever touchstone_path says the sweep
    wrote it. It's left alone and its path returned, unless
    overwrite=True, which rewrites that same file in place (same numbers
    either way). Pass out_root yourself to get fresh copies in a folder
    of your choosing, e.g. to send someone; only a file already in that
    folder is skipped.

    The database isn't modified; `touchstone_path` stays as the sweep set
    it. Returns the list of paths, existing or new.
    """
    if isinstance(run_ids, int):
        run_ids = [run_ids]
    con, db_path = _connect(db_path)
    con.close()
    copies = out_root is not None
    out_root = Path(out_root) if copies else db_path.resolve().parent / DEFAULT_OUT_DIR_NAME
    runs = {r["run_id"]: r for r in list_runs(db_path)}

    paths = []
    for run_id in run_ids:
        if run_id not in runs:
            raise KeyError(f"No run {run_id} in {db_path.name}")
        run = runs[run_id]
        freq_hz, data = load_run(run_id, db_path)
        suffix = ".s2p" if len(data) == 4 else ".s1p"
        folder = sweep_folder(out_root, run["switch_serials"], run["date_str"],
                              run["temp_str"], _group(run))
        path = folder / f"{run['name']}_run{run_id}{suffix}"

        # The sweep may already have written this run's file somewhere
        # other than the standard place (touchstone=True with its own
        # out_root). Without an out_root of our own, that file is the
        # run's file: report it, or rewrite it in place with overwrite --
        # never leave a second copy beside it.
        recorded = None if copies else _recorded_file(run, db_path)
        if recorded is not None:
            path = recorded

        if path.exists() and not overwrite:
            print(f"  exists  {path}")
        else:
            save_touchstone(freq_hz, data, path)
            print(f"  wrote   {path}")
        paths.append(path)
    return paths


def _recorded_file(run, db_path):
    """
    The file the run's touchstone_path points at, if it's still there;
    None otherwise. Relative paths are relative to the database's folder,
    and may have been written on Windows with backslashes.
    """
    recorded = run["touchstone_path"]
    if not recorded:
        return None
    candidates = [Path(recorded)]
    if "\\" in recorded:
        candidates.append(Path(recorded.replace("\\", "/")))
    for p in candidates:
        if not p.is_absolute():
            p = db_path.resolve().parent / p
        if p.is_file():
            return p
    return None



# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

def _fingerprint(con):
    """
    A hash of everything that says what's in the database: every row of
    the runs and experiments tables (run ids, names, point counts,
    timestamps, metadata columns). Two databases with the same fingerprint
    hold the same runs. Cheap -- those tables are a few hundred rows; the
    measurement arrays live elsewhere and are covered by result_counter.
    """
    h = hashlib.sha256()
    for table in ("experiments", "runs"):
        for row in con.execute(f"SELECT * FROM {table} ORDER BY 1"):
            h.update(repr(row).encode())
    return h.hexdigest()


def _existing_backups(backup_dir, stem):
    """This database's finished backups, oldest first (names sort by time)."""
    pattern = re.compile(re.escape(stem) + r"_\d{8}-\d{6}")   # <stem>_YYYYMMDD-HHMMSS
    return sorted(p for p in Path(backup_dir).glob(f"{stem}_*.db")
                  if pattern.fullmatch(p.stem))


def backup_db(db_path=None, backup_dir=None, keep=DEFAULT_BACKUPS_KEPT, force=False):
    """
    Write a consistent, timestamped copy of the database:

        <db folder>/db_backups/mm4250_sweeps_20261015-143022.db

    and delete all but the newest `keep` copies. Returns the new backup's
    path, or None if nothing changed since the last one.

    Why not just copy the file: QCoDeS keeps the database in WAL mode, so
    the newest runs can sit in mm4250_sweeps.db-wal until a checkpoint.
    sweep_db._checkpoint folds them in after every position, but a plain
    file copy can still catch the .db mid-write, or miss pages a reader was
    holding back. SQLite's own backup API (used here) reads through the
    WAL and gives a snapshot of one instant, safe to take while a kernel
    still has the database open. The copy is switched out of WAL mode, so
    each backup is one self-contained file -- no -wal/-shm to carry around.

    Each backup is checked (PRAGMA quick_check, and the run count must
    match) before it gets its final name; a half-written or bad copy is
    never left looking like a good one.

    If the newest existing backup already holds exactly the same runs,
    nothing is written (returns None) -- so calling this from every
    sweep and every Close cell doesn't fill the disk with duplicates.
    Pass force=True to write one anyway.

    `backup_dir` defaults to a db_backups/ folder beside the database --
    on the lab machine, Users/Charlie_Ferrari/db_backups/. That protects
    against a bad write or an accidental delete, not against losing the
    machine; copy a backup somewhere else now and then for that.
    """
    db_path = Path(db_path) if db_path is not None else default_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(f"no database at {db_path}")
    backup_dir = Path(backup_dir) if backup_dir is not None else db_path.parent / DEFAULT_BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    stem = db_path.stem
    existing = _existing_backups(backup_dir, stem)

    src = sqlite3.connect(str(db_path), timeout=30)
    try:
        fingerprint = _fingerprint(src)
        if existing and not force:
            last = sqlite3.connect(f"file:{existing[-1].as_posix()}?mode=ro", uri=True)
            try:
                same = _fingerprint(last) == fingerprint
            except sqlite3.Error:
                same = False                       # unreadable last backup: make a new one
            finally:
                last.close()
            if same:
                print(f"  backup: no new runs since {existing[-1].name}, nothing written")
                return None

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final = backup_dir / f"{stem}_{stamp}.db"
        while final.exists():                     # two backups in one second: never overwrite
            time.sleep(1.05)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            final = backup_dir / f"{stem}_{stamp}.db"
        partial = backup_dir / f"{stem}_{stamp}.db.partial"
        if partial.exists():
            partial.unlink()
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
        raise RuntimeError(f"backup failed its check (quick_check={check!r}, "
                           f"runs {n_dst} vs {n_src}); nothing was kept")
    os.replace(partial, final)

    removed = []
    if keep is not None and keep > 0:
        for old in _existing_backups(backup_dir, stem)[:-keep]:
            old.unlink()
            removed.append(old.name)

    size_mb = final.stat().st_size / 1e6
    print(f"  backup: {n_dst} runs -> {final} ({size_mb:.0f} MB)"
          + (f", removed {len(removed)} older" if removed else ""))
    return final
