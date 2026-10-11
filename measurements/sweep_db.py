"""
Batch sweeps: measure a set of switch positions and save the results.

The measurement itself comes from vna_measure.py -- this module is the
data layer on top of it. For interactive one-off measurements, use
vna_measure directly.

One core sweep handles both port counts; run_oneport_sweep and
run_twoport_sweep are thin wrappers that differ only in how many
S-parameters they ask for. run_ecal_set builds a fridge e-cal set out of
those sweeps (see its docstring, and ecal.py for the correction), and
run_kit_set measures an external cal kit for ecal.make_ideals.
run_ecal_set(cal_standards=...) records an e-cal set with known calibration
standards on the RF ports into the separate calibration database,
Sweeps/calibration/mm4250_calibration.db -- raw data for building your own
standard definitions later. Each position measured is saved as

    a QCoDeS run named "<position>" in a shared .db file        (always)
    Sweeps/<switch_serials>/<date_str>/<temp_str>/<group>/<position>_run<id>.s1p (or .s2p)
                                                                (only with touchstone=True)

The database is the record: it holds every number a Touchstone file
would, at full precision, plus what the VNA was doing. Files are for
handing data to something outside QCoDeS, so they're written only when
asked for -- touchstone=True during the sweep, or read_db.export_touchstone
afterwards, which writes the identical file to the identical place.

<group> is "<setup>_cal" or "<setup>_uncal" -- `setup` is what you pass
to describe the cabling (e.g. "RF3" for VNA port 2 on RF3), and cal/uncal
is read off the VNA's correction state, never typed. With no setup it's
just "cal" or "uncal". Switch first, because the switch is the thing
being characterized: one unit's whole history -- every date, every
temperature -- sits under one folder.

The run id in a filename is the QCoDeS run number, which is unique
across the whole database -- so a file and a run identify each other
exactly, in both directions, and re-measuring a position never
overwrites the earlier attempt.

Database layout: ONE accumulating .db file (mm4250_sweeps.db, beside
these files -- see DEFAULT_DB_NAME) holds every run ever taken, the way
the lab's nanoRFE_data.db / tinySA_plottr.db files are used. Inside it:

    experiment  -- one per run_*_sweep() call, named
                   "<date_str>_<temp_str>_<switch_serials>_<group>",
                   with sample_name set to the switch serials
    dataset     -- one per position measured in that call, named "RF<n>"
                   (or the switch state name, e.g. "ALL_OPEN")

So a sweep session's runs stay grouped together and can't be confused
with another date's or another unit's. 1-port and 2-port runs can share
an experiment -- each run records only the S-parameters it actually
measured, and carries n_ports as metadata. Browse the file afterwards
with `plottr-inspectr --db mm4250_sweeps.db`, or load runs in Python with
qcodes.dataset's load_by_id / load_by_run_spec.

After every position, run_sweep checkpoints the database (see
_checkpoint), so mm4250_sweeps.db is complete on its own even while the
kernel that wrote it is still running.

Every parameter is registered with paramtype="array": that's the blob
storage type, and it round-trips a complex-valued numpy array correctly.
Do NOT use paramtype="complex" here -- that one is for complex *scalars*,
and a complex array written with it writes without error but then fails
on read-back.

No calibration or de-embedding is applied here -- the data is whatever
the VNA hands back, corrected only if a cal set is active on it. What
the VNA itself was doing at the time is recorded though: every run
carries instrument_state()'s snapshot as "vna_*" (power, IF bandwidth, whether a cal was on, port extensions, electrical
delay per trace, and so on), so a run can be audited later instead of
taken on trust.
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from qcodes.dataset import (
    Measurement,
    initialise_or_create_database_at,
    load_or_create_experiment,
)

from vna_measure import (
    DEFAULT_SWITCH_NAME,
    DEFAULT_VNA_NAME,
    SPARAMS_2PORT,
    VNA_STATE_QUERIES,
    _resolve_instrument,
    _select,
    _try_ask,
    instrument_state,
    measure_2port,
    measure_s11,
)

# The Touchstone writer, the Sweeps/ layout and the names of the default
# files live in read_db, which has no qcodes dependency. Imported here so
# a file written during a sweep and one exported later come from the
# same code, and re-exported so `from sweep_db import save_touchstone`
# keeps working.
from read_db import (  # noqa: F401
    CAL_DIR_NAME,
    DEFAULT_DB_NAME,
    DEFAULT_OUT_DIR_NAME,
    default_cal_db_path,
    files_root,
    load_standards,
    TOUCHSTONE_2PORT_ORDER,
    backup_db,
    save_touchstone,
    sweep_folder,
)


def _default_out_root():
    """
    Where sweeps are saved by default: <this folder>/Sweeps.

    Resolved from this file's location, NOT the working directory --
    otherwise running from a notebook that lives somewhere else (the
    lab's QCodesMeasurmentFramework.ipynb, say) would scatter sweep data
    into whatever folder that notebook happens to sit in.

    So copying these files into a folder of your own -- e.g. the lab
    machine's users/<name>/ -- puts the sweeps in that folder too.
    """
    return Path(__file__).resolve().parent / DEFAULT_OUT_DIR_NAME


def _default_cal_db_path():
    """
    Sweeps/calibration/mm4250_calibration.db beside these files: where
    run_ecal_set(cal_standards=...) records by default (read_db.default_cal_db_path).
    Resolved here, like _default_db_path, so tests can point it elsewhere.
    """
    return _default_out_root() / CAL_DIR_NAME / Path(default_cal_db_path()).name


def _default_db_path():
    """
    Path to the shared database: mm4250_sweeps.db, in the same folder as
    these files.

    Resolved from this file's location rather than the working directory,
    so every notebook that calls this writes to the same one file.
    """
    return Path(__file__).resolve().parent / DEFAULT_DB_NAME


def _as_dict(data):
    """
    Accept either a bare complex array (1-port S11) or a dict of
    S-parameter name -> complex array, and return the dict form.
    """
    if hasattr(data, "keys"):
        return dict(data)
    return {"S11": data}


def save_s1p(freq_hz, s11, path):
    """Write a 1-port Touchstone (.s1p). Thin wrapper on save_touchstone."""
    save_touchstone(freq_hz, s11, path)


def save_s2p(freq_hz, data, path):
    """Write a 2-port Touchstone (.s2p). Thin wrapper on save_touchstone."""
    save_touchstone(freq_hz, data, path)


def record_measurement(name, freq_hz, data, exp, touchstone_path=None, **metadata):
    """
    Save one already-measured position as a QCoDeS dataset named `name`
    (e.g. "RF3") inside the experiment `exp`. Returns the DataSet -- its
    `.run_id` is the run number.

    `data` is a bare complex S11 array or the dict from measure_2port;
    only the S-parameters actually present are registered, so 1-port and
    2-port runs can live side by side in the same experiment.

    `touchstone_path` and any extra keyword arguments are attached to the
    dataset as metadata, so a run in the .db can always be traced back to
    the raw file and the sweep it came from.

    The DataSet rather than the bare id is returned because a dataset
    stays writable for metadata after its run closes, and run_sweep needs
    that: the Touchstone file is named after the run id, so it can't
    exist until the run does, and its path can only be attached
    afterwards.
    """
    data = _as_dict(data)

    meas = Measurement(exp=exp, name=name)
    meas.register_custom_parameter(
        "frequency", label="Frequency", unit="Hz", paramtype="array"
    )
    for sparam in data:
        meas.register_custom_parameter(
            sparam.lower(),
            label=sparam,
            unit="",
            setpoints=("frequency",),
            paramtype="array",
        )

    with meas.run() as datasaver:
        datasaver.add_result(
            ("frequency", freq_hz),
            *[(sparam.lower(), values) for sparam, values in data.items()],
        )
        dataset = datasaver.dataset
        dataset.add_metadata("n_ports", 2 if len(data) == 4 else 1)
        if touchstone_path is not None:
            dataset.add_metadata("touchstone_path", str(touchstone_path))
        for key, value in metadata.items():
            # A position is reached either by channel number or by state
            # name, never both -- skip whichever one doesn't apply rather
            # than writing a null into the run's metadata.
            if value is not None:
                dataset.add_metadata(key, value)

    return dataset


def record_channel(channel, freq_hz, data, exp, touchstone_path=None, **metadata):
    """
    Save one measured RF channel as a run named "RF<n>", tagging it with
    the channel number. Returns the DataSet, like record_measurement.
    """
    return record_measurement(
        f"RF{channel}",
        freq_hz,
        data,
        exp,
        touchstone_path=touchstone_path,
        channel=channel,
        **metadata,
    )


def _position(entry):
    """
    Normalize one entry of a sweep list into (label, channel, state).

    An int 1-6 is an RF channel; a string is a switch state name
    ("ALL_OPEN", "INTERNAL_LOAD", "RFC_RF4", ...). Both are measured the
    same way -- the only difference is how the switch gets there and what
    the run and file end up called.
    """
    if isinstance(entry, str):
        return entry, None, entry
    return f"RF{entry}", entry, None


def _cal_state(vna):
    """
    "cal" if the VNA's error correction is on, "uncal" if it's off.

    Read off the instrument rather than passed in, so a folder can't say
    cal when the correction was actually off. Raises if the VNA won't
    answer, rather than guess a label that then sticks to the data.
    """
    corr = _try_ask(vna, VNA_STATE_QUERIES["correction_enabled"])
    if corr not in (0, 1):
        raise RuntimeError(
            f"Couldn't read the VNA's correction state (SENS:CORR:STAT? -> "
            f"{corr!r}), so this sweep can't be filed as cal or uncal."
        )
    return "cal" if corr == 1 else "uncal"


def _open_experiment(switch_serials, db_path, exp_name):
    """Point QCoDeS at the shared database and open this call's experiment."""
    db_path = Path(db_path) if db_path is not None else _default_db_path()
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initialise_or_create_database_at(db_path)

    exp = load_or_create_experiment(exp_name, sample_name=switch_serials)
    print(f"Recording to {db_path}")
    print(f"  experiment {exp_name!r} (sample {switch_serials!r})")
    return exp, db_path


def _relative_to_db(path, db_path):
    """
    `path` relative to the database's folder, with forward slashes, for
    the run's touchstone_path metadata. An absolute path would name one
    machine's folder (C:\\Users\\QTSF_DAQ\\...) and be wrong everywhere
    the folder is copied to. Falls back to absolute only when there's no
    relative path at all (a different Windows drive).
    """
    try:
        rel = os.path.relpath(Path(path).resolve(), db_path.parent)
    except ValueError:
        return str(Path(path).resolve())
    return Path(rel).as_posix()


def _checkpoint(db_path):
    """
    Fold the database's write-ahead log back into the .db file itself.

    QCoDeS opens the database in WAL mode: new runs go into
    mm4250_sweeps.db-wal first and only reach mm4250_sweeps.db at a
    checkpoint -- which SQLite does on its own only every ~4 MB of writes,
    or when the last connection closes. A kernel left running holds its
    connection open, so without this the newest runs live only in the
    -wal, and copying just the .db off the machine silently drops them.

    PASSIVE never blocks or waits on anyone else reading the file
    (plottr-inspectr, another kernel); if a reader is holding pages back,
    it copies what it can and the rest goes at the next checkpoint. It
    moves pages, not rows -- run ids, experiments and metadata are
    unchanged.
    """
    conn = sqlite3.connect(db_path)
    try:
        busy, log_pages, done_pages = conn.execute(
            "PRAGMA wal_checkpoint(PASSIVE)"
        ).fetchone()
    finally:
        conn.close()
    if log_pages > 0 and done_pages < log_pages:
        print(f"  [note] {db_path.name}: {log_pages - done_pages} page(s) still "
              "in the -wal (another reader has it open); copy the -wal too")


def _safe_backup(db_path=None):
    """
    read_db.backup_db, for the end of a sweep: never raises, so a backup
    problem can't hide the sweep's own result or error. Writes nothing if
    no runs changed since the last backup.
    """
    try:
        backup_db(db_path if db_path is not None else _default_db_path())
    except Exception as e:
        print(f"  [warning] database backup failed ({type(e).__name__}: {e}); "
              "the database itself is fine -- run backup_db() by hand")


def _read_temperature(temp_reader, strict):
    """
    One reading from `temp_reader`, as a float in kelvin. strict=True
    (the check before a sweep starts) raises on any problem; strict=False
    (mid-sweep) prints a warning and returns None instead, so a flaky
    thermometer read can't throw away a measurement that already happened.
    """
    try:
        value = temp_reader()
        if isinstance(value, bool):
            raise TypeError("got a bool")
        value = float(value)
        if not value > 0:
            raise ValueError(f"got {value!r} K")
        return value
    except Exception as e:
        if strict:
            raise RuntimeError(f"temp_reader failed before the sweep started "
                               f"({type(e).__name__}: {e}); nothing was measured") from e
        print(f"  [warning] temp_reader failed ({type(e).__name__}: {e}); "
              "saving this run without mxc_temp_k")
        return None


def _checked(temp_reader):
    """`temp_reader`, marked as already checked so run_sweep doesn't check it again."""
    def reader():
        return temp_reader()
    reader._checked = True
    return reader


def _check_labels(labels, positions):
    """
    Validate `labels`, a {position: text} dict saying what's on each
    position (e.g. {1: "resonator", 4: "50 ohm load"}), against the
    positions about to be measured. Returns a clean copy: text stripped,
    blank entries dropped. Raises before anything is measured, so a typo
    can't leave half a sweep labelled.
    """
    if not labels:
        return {}
    if not hasattr(labels, "items"):
        raise TypeError("labels must be a dict like {1: 'resonator'}, "
                        f"got {type(labels).__name__}")
    positions = list(positions)
    out = {}
    for pos, text in labels.items():
        if pos not in positions:
            raise ValueError(f"labels has {pos!r}, which isn't one of the positions "
                             f"being measured ({positions})")
        if not isinstance(text, str):
            raise TypeError(f"label for {pos!r} must be text, got {text!r}")
        if text.strip():
            out[pos] = text.strip()
    return out


def run_sweep(positions, date_str, temp_str, switch_serials, n_ports=2,
              setup=None, touchstone=False, out_root=None, db_path=None,
              exp_name=None, prompt_between=False, metadata=None, labels=None,
              position_metadata=None, temp_reader=None, backup=True, vna=None,
              switch=None):
    """
    Measure each entry in `positions` and save it as a QCoDeS run -- and,
    with touchstone=True, as a Touchstone file too. Returns the run ids.

    `positions` is a list of RF channel numbers (any subset of 1-6, e.g.
    [1, 3, 5] or list(range(1, 7))), and/or switch state names as strings:

        run_sweep([1, 3, 5], ...)                     # three channels
        run_sweep(list(range(1, 7)), ...)             # all six
        run_sweep([3, "ALL_OPEN", 1], ...)            # mixed

    Mixing the two is how you measure isolation without touching a cable:
    hold the cabling fixed, measure the channel that's connected, then
    measure again on a state that disconnects it.

    `n_ports` is 2 for a full S11/S12/S21/S22 measurement (.s2p when
    files are written), or 1 for S11 only (.s1p).

    `setup` names the cabling, e.g. "RF3" for VNA port 2 on RF3. Whether
    the VNA's correction is on is read off the instrument, and the two
    together make the group folder: setup="RF3" gives "RF3_cal" or
    "RF3_uncal"; no setup gives plain "cal" or "uncal". The correction
    state is checked again at every position, and the sweep stops if it
    changed mid-sweep rather than file a run under the wrong label.

    `touchstone=False` (the default) saves to the database only. Every
    number is there; get files later with read_db.export_touchstone(runs)
    if something outside QCoDeS needs them. `touchstone=True` also writes
    each position to
    <out_root>/<switch_serials>/<date_str>/<temp_str>/<group>/<position>_run<id>.s2p,
    where <id> is the QCoDeS run number -- so measuring the same position
    twice leaves you with both files rather than silently overwriting the
    first. `out_root` defaults to a Sweeps/ folder beside this file -- not
    beside whatever notebook called it -- so sweeps land in the same place
    no matter where you run from. Pass it explicitly (absolute, or
    relative to the working directory) to put them somewhere else.

    When a file is written, the run's `touchstone_path` metadata records
    it relative to the database's folder (e.g.
    "Sweeps/SN0077/20260925/295K/RF3_cal/RF3_run43.s2p"), so the link
    survives copying the folder between machines. No file, no
    touchstone_path.

    All of this call's runs go into one experiment named
    "<date_str>_<temp_str>_<switch_serials>_<group>" (override with
    `exp_name`), with sample_name=switch_serials. Calling this again with
    the same date/temp/serials/group adds runs to that same experiment
    rather than making a duplicate one.

    `prompt_between=True` pauses before each position and waits for you to
    press Enter -- for setups where something has to be re-cabled by hand
    between measurements.

    `metadata` is an optional dict attached to every run in this call, on
    top of what's recorded anyway -- e.g. {"mxc_temp_k": 3.1}. It's how
    run_ecal_set tags which runs belong to which calibration set. Keys
    become columns in the database's runs table, so keep them short,
    lowercase and consistent. None values are skipped.

    `position_metadata` is like `metadata`, but for one position only: a
    dict keyed by entries of `positions` (2, "ALL_OPEN", ...), each mapping
    to a dict merged into that position's run on top of `metadata`. It's
    how run_ecal_set tags the channel a short or a calibration standard is
    on. A key that isn't in `positions` is an error, raised before anything
    is measured.

    `temp_reader` is a function with no arguments that returns the fridge
    temperature in kelvin (e.g. a thin wrapper around the fridge's QCoDeS
    driver: `temp_reader=lambda: fridge.mxc_temperature()` -- whatever the
    driver calls it). It's called once before the sweep starts (so a broken
    reader fails before the switch moves) and again right before each
    position, and the reading is saved on that run as `mxc_temp_k`,
    replacing any mxc_temp_k in `metadata`. If a reading fails partway, the
    run is saved without one and a warning is printed, rather than losing
    the measurement.

    `labels` says what's on individual positions, e.g.
    {1: "resonator", 2: "SNTJ", "ALL_OPEN": "isolation"}. Each label is
    saved only on that position's run, as `dut_label` (read_db.list_runs
    shows it). Positions you leave out, or give "", get no label. A key
    that isn't in `positions` is an error, raised before anything is
    measured.

    Returns the list of run ids, in the order measured -- what
    plots.plot_sweep, read_db.load_run and read_db.export_touchstone take:

        runs = run_twoport_sweep([3, "ALL_OPEN"], ..., setup="RF3")
        plot_sweep(runs)

    `backup=True` (the default) writes a snapshot of the database to
    db_backups/ beside it once the sweep finishes (read_db.backup_db;
    skipped if nothing changed). A sweep that dies partway isn't backed
    up here -- the notebook's Close cell catches those runs.

    vna/switch default to the already-instantiated instruments named
    "ksvna"/"switch" if not passed explicitly.
    """
    if n_ports not in (1, 2):
        raise ValueError(f"n_ports must be 1 or 2, got {n_ports!r}")
    positions = list(positions)
    labels = _check_labels(labels, positions)
    position_metadata = dict(position_metadata or {})
    stray = [k for k in position_metadata if k not in positions]
    if stray:
        # A typo'd key would otherwise tag nothing, silently.
        raise ValueError(f"position_metadata has keys that aren't in positions: {stray!r}")
    if temp_reader is not None and not getattr(temp_reader, "_checked", False):
        _read_temperature(temp_reader, strict=True)     # fail before anything moves

    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")

    cal = _cal_state(vna)
    group = f"{setup}_{cal}" if setup else cal
    if exp_name is None:
        exp_name = f"{date_str}_{temp_str}_{switch_serials}_{group}"
    exp, db_path = _open_experiment(switch_serials, db_path, exp_name)

    out_root = Path(out_root) if out_root is not None else _default_out_root()
    sweep_dir = sweep_folder(out_root, switch_serials, date_str, temp_str, group)
    print(f"  VNA correction is {'ON' if cal == 'cal' else 'OFF'} -> group {group!r}"
          + (f", files in {sweep_dir}" if touchstone else " (database only)"))
    suffix = ".s2p" if n_ports == 2 else ".s1p"
    sparams = SPARAMS_2PORT if n_ports == 2 else ("S11",)

    run_ids = []
    for entry in positions:
        label, channel, state = _position(entry)
        dut_label = labels.get(entry)
        shown = f"{label} ({dut_label})" if dut_label else label

        if prompt_between:
            input(f"Ready to measure {shown}? Set up the connections, then press Enter...")

        run_meta = dict(metadata or {})
        run_meta.update(position_metadata.get(entry, {}))
        if temp_reader is not None:
            run_meta["mxc_temp_k"] = _read_temperature(temp_reader, strict=False)

        print(f"Measuring {shown}...")
        if n_ports == 2:
            freq_hz, data = measure_2port(channel, state=state, vna=vna, switch=switch)
        else:
            freq_hz, data = measure_s11(channel, state=state, vna=vna, switch=switch)

        # Snapshot the VNA while it's still set the way this measurement
        # was taken, so the run records what the numbers mean and not
        # just what they are.
        vna_state = instrument_state(sparams, vna=vna)
        now = vna_state.get("vna_correction_enabled")
        if now != (1 if cal == "cal" else 0):
            raise RuntimeError(
                f"VNA correction changed mid-sweep (was {cal!r}, now "
                f"SENS:CORR:STAT? -> {now!r}) -- {label} not saved. Start a "
                "new run_sweep() so it's filed under the right folder."
            )

        # Record before writing any file: a file is named after the run
        # id, so the run has to exist first. Its path is attached to the
        # run once it does.
        dataset = record_measurement(
            label,
            freq_hz,
            data,
            exp,
            channel=channel,
            state=state,
            switch_serials=switch_serials,
            date_str=date_str,
            temp_str=temp_str,
            dut_label=dut_label,
            **vna_state,
            **run_meta,
        )
        print(f"  recorded run #{dataset.run_id} in {db_path.name}")
        run_ids.append(dataset.run_id)

        if touchstone:
            path = sweep_dir / f"{label}_run{dataset.run_id}{suffix}"
            save_touchstone(freq_hz, data, path)
            dataset.add_metadata("touchstone_path", _relative_to_db(path, db_path))
            print(f"  saved {path}")

        # Per position rather than once at the end, so a sweep that dies
        # halfway still leaves every finished run in the .db proper.
        _checkpoint(db_path)

    if backup and run_ids:
        _safe_backup(db_path)
    return run_ids


def run_twoport_sweep(positions, date_str, temp_str, switch_serials, **kwargs):
    """
    Full 2-port sweep: S11, S12, S21 and S22 at every position, saved as
    QCoDeS runs (and .s2p files with touchstone=True). Returns the run
    ids. See run_sweep for the full argument list.
    """
    return run_sweep(positions, date_str, temp_str, switch_serials, n_ports=2, **kwargs)


def run_oneport_sweep(positions, date_str, temp_str, switch_serials, **kwargs):
    """
    1-port sweep: S11 only, saved as QCoDeS runs (and .s1p files with
    touchstone=True). Returns the run ids. See run_sweep for the full
    argument list.
    """
    return run_sweep(positions, date_str, temp_str, switch_serials, n_ports=1, **kwargs)



# ---------------------------------------------------------------------------
# Electronic calibration (e-cal) sets
# ---------------------------------------------------------------------------

# The switch positions that act as calibration standards, in the order an
# e-cal set measures them. ALL_OPEN is the open standard: with every
# channel open, RFC sees the open end of the internal switch tree. This is
# the same convention as Menlo's e-cal app note ("select ALL OFF") and
# NIST's ecal_open files.
ECAL_STANDARDS = ("ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD")


def _leave_switch_open(switch, vna):
    """
    Cleanup for a sweep that may have died: get the switch to ALL_OPEN
    whatever state the VNA is in. Never raises, so the error that ended
    the sweep is the one you see.

    The normal path is _select, which drops the source for the move. If
    the VNA is what failed (a dropped VISA connection, say), _select
    can't ask it whether the source is on and never reaches the switch --
    which left the switch closed on RF2 on 2026-10-01. So fall back to
    the switch's own USB link, which doesn't need the VNA. At -20 dBm
    that move is far inside the hot-switching limit (see _select).
    """
    try:
        _select(state="ALL_OPEN", switch=switch, vna=vna)   # normal path: source off for the move
        return
    except Exception as e:
        print(f"  [warning] couldn't open the switch the normal way ({type(e).__name__}: {e})")
    try:
        vna.output(False)           # best effort; fails if the VNA link is what died
    except Exception:
        pass
    try:
        switch.open_all()           # the switch's own USB link, no VNA needed
        print("  [warning] switch opened directly: ALL_OPEN")
    except Exception as e:
        print(f"  [WARNING] SWITCH MAY STILL BE CLOSED -- run switch.open_all() by hand "
              f"({type(e).__name__}: {e})")


def _check_correction(vna, vna_cal, what):
    """
    For run_ecal_set/run_kit_set: the VNA's correction has to be what the
    caller said it would be -- off by default, on only with vna_cal=True --
    so a cal set left active by another section can't slip in unnoticed,
    and vna_cal=True can't quietly measure raw.
    """
    state = _cal_state(vna)
    if state == "cal" and not vna_cal:
        raise RuntimeError(
            f"VNA correction is ON. {what} is measured raw by default -- turn it off "
            "with ksvna.write('SENS:CORR:CSET:DEAC'), or pass vna_cal=True to keep "
            "the VNA's cal on underneath on purpose."
        )
    if state == "uncal" and vna_cal:
        raise RuntimeError(
            "vna_cal=True but VNA correction is OFF -- activate the cal set first "
            "(ksvna.write(f'SENS:CORR:CSET:ACT \"{CAL_SET}\",0')), or pass vna_cal=False."
        )


def _check_channel_key(ch, what):
    if isinstance(ch, bool) or not isinstance(ch, int) or ch not in range(1, 7):
        raise ValueError(f"{what} keys must be RF channel numbers 1-6, got {ch!r}")


def _check_terminations(terminations):
    """
    Validate run_ecal_set's `terminations` and return it as a fresh
    {channel: description} dict sorted by channel (None stays None).
    """
    if terminations is None:
        return None
    if not hasattr(terminations, "items"):
        raise ValueError("terminations must be a dict like {2: 'short'}, "
                         f"got {type(terminations).__name__}")
    out = {}
    for ch, what in terminations.items():
        _check_channel_key(ch, "terminations")
        if not isinstance(what, str) or not what.strip():
            raise ValueError(f"terminations[{ch}] must be a non-empty description, got {what!r}")
        out[ch] = what.strip()
    return dict(sorted(out.items()))


def _check_cal_standards(cal_standards, channels, registry):
    """
    Validate run_ecal_set's `cal_standards` ({channel: standard name})
    against the channels being measured and the standards file. Returns
    {channel: name} sorted by channel.
    """
    if not hasattr(cal_standards, "items") or not cal_standards:
        raise ValueError("cal_standards must be a non-empty dict like {2: 'flat_short'}")
    out = {}
    for ch, name in cal_standards.items():
        _check_channel_key(ch, "cal_standards")
        if ch not in channels:
            raise ValueError(f"cal_standards has RF{ch}, which this set doesn't measure "
                             f"(channels={channels})")
        if not isinstance(name, str) or name.strip() not in registry:
            raise ValueError(f"cal_standards[{ch}] = {name!r} isn't in the standards file; "
                             f"known: {', '.join(sorted(registry))}")
        out[ch] = name.strip()
    return dict(sorted(out.items()))


# What each cal standard's run is tagged with, from its standards-file entry.
_STD_FIELDS = (("cal_std_type", "type"), ("cal_std_description", "description"),
               ("cal_std_part", "part"), ("cal_std_serial", "serial"),
               ("cal_std_offset_mm", "offset_mm"))


def run_ecal_set(date_str, temp_str, switch_serials, channels=(1, 2, 3, 4, 5, 6),
                 repeats=1, mxc_temp_k=None, note=None, labels=None, terminations=None,
                 cal_standards=None, cooldown=None, standards_file=None, temp_reader=None,
                 n_ports=2, vna_cal=False, setup="ecal", touchstone=False, out_root=None,
                 db_path=None, vna=None, switch=None):
    """
    Measure one complete e-cal set: the raw switch's internal standards,
    then each RF channel, then the standards again. ecal.correct_set()
    turns a set into calibrated S11 at each channel's SMA connector.

        cal = run_ecal_set("20261015", "3K", "SN0077", repeats=2, mxc_temp_k=3.2,
                           channels=[1, 4], labels={1: "resonator", 4: "50 ohm load"})
        result = ecal.correct_set(cal, ideals_dir, sparam="S21")

    `labels` says what's on each channel: {channel: text}. Each label is
    saved on that channel's runs only (as `dut_label`), comes back with
    ecal.find_set, and shows in ecal.plot_ecal's legend as
    "RF1 (resonator)". Channels you leave out, or give "", are unlabelled.
    Every key has to be one of `channels`; that's checked before anything
    is measured. `note` is for what applies to the whole set (fridge
    state, wiring, why you took it).

    `terminations` says what's screwed onto each RF connector, as
    {channel: description}, e.g. {2: "short", 5: "load"}. With it, each
    measured channel's run is tagged termination="short" (etc.), and a
    channel left out of the dict is tagged termination="none", meaning
    nothing is attached -- so list every port that has something on it.
    The whole dict is also stored on every run in the set, standards
    included, as ecal_terminations (JSON text, e.g. '{"2": "short",
    "5": "load"}'), so the port setup can be read off any run. A port can
    be listed even if this set doesn't measure it. Leave it as None and
    nothing about terminations is recorded.

    `cal_standards` is for building your own standard definitions, NIST
    style: {channel: name} of known calibration standards on the RF
    connectors, e.g. {1: "flat_short", 3: "open_1", 5: "offset_short_60mm"}.
    Names come from the standards file (`standards_file`, default
    Sweeps/calibration/standards.json; see read_db.load_standards), and an
    unknown name is an error before anything moves. With it:

      * the set goes into the calibration database,
        Sweeps/calibration/mm4250_calibration.db (unless you pass db_path),
        with its own db_backups/ and, with touchstone=True, its Touchstone
        files under Sweeps/calibration/<serials>/<date>/<temp>/...;
      * `cooldown` is required (e.g. "CD1", or "bench" for a warm session),
        since rotating standards between ports only works if you know which
        cooldown each set came from;
      * each standard's channel run is tagged cal_std (the name) plus
        cal_std_type, cal_std_description, cal_std_part, cal_std_serial and
        cal_std_offset_mm from the file, and labelled "<name> (<type>)"
        unless `labels` says otherwise; the full entries are also stored on
        every run of the set as cal_standards (JSON), so the database stays
        self-describing even if the file changes later;
      * the standards count as terminations too (other entries in
        `terminations`, like {2: "resonator"}, still work alongside).

    ecal.list_standard_runs() lists every such run: which standard, which
    port, which switch, cooldown, temperature, and when.

    `temp_reader` reads the fridge temperature for you: a function with no
    arguments returning kelvin (see run_sweep), called before each
    position and saved on each run as mxc_temp_k. Pass it or `mxc_temp_k`,
    not both. `cooldown` can be given for any set, not just standards sets.

    One call per temperature. `n_ports=2` (the default) saves all four
    S-parameters at every position, for the fridge's circulator wiring:
    VNA port 1 -> circulator -> RFC, the reflection back up to VNA port 2,
    so the switch's reflection is S21. `n_ports=1` saves S11 only, for a
    single line from VNA port 1 to RFC. Which trace gets corrected is
    chosen afterwards, in ecal.correct_set(sparam=...).

    Order, for each repeat:

        ALL_OPEN, INTERNAL_SHORT, INTERNAL_LOAD     role "before"
        RF1 ... RF6 (or `channels`)                 role "port"
        ALL_OPEN, INTERNAL_SHORT, INTERNAL_LOAD     role "after"

    Measuring the standards on both sides of the channels is the drift
    check: if the cold amplifier's gain or the cables moved during the
    set, "before" and "after" disagree, and ecal.drift() shows by how
    much. It also lets the correction use their average, which cancels
    drift that's linear in time. `repeats` measures the whole block again
    -- NIST found repeatability dominated their uncertainty, so 2 is worth
    the extra minute.

    VNA correction is OFF by default, and this refuses to run with a cal
    set active: the e-cal replaces the VNA's own cal (which only reaches
    wherever its kit was attached -- the cable ends), so it isn't needed.
    `vna_cal=True` measures with the VNA's cal on underneath instead (and
    refuses if it's off). The e-cal result should come out the same either
    way -- the VNA-corrected trace has the same 3-term form, just with the
    cables already removed -- but it lets you compare "VNA cal only" with
    "VNA cal + e-cal". The cal set's frequency settings have to match
    setup_sweep, or the VNA interpolates it or turns it off. The runs are
    filed under "<setup>_cal" and record vna_correction_enabled = 1, which
    ecal.correct_set reads back. Don't change setup_sweep settings during
    a set; every run in it has to share one frequency axis.

    Every run is tagged in the database with:

        ecal_set      one id per call, e.g. "20261015T141502.318"
        ecal_repeat   1, 2, ...
        ecal_role     "before", "port" or "after"
        mxc_temp_k    if given -- the mixing-chamber reading at the time
        ecal_note     if given
        dut_label     channel runs only, if that channel has a label
        ecal_terminations, termination     if `terminations` or `cal_standards` given
        cooldown      if given
        cal_standards, cal_std, cal_std_*  if `cal_standards` given (see above)

    and filed like any sweep, under the group "<setup>_uncal" (default
    "ecal_uncal"). Runs from several sets on the same date/temp share an
    experiment; ecal_set is what keeps them apart.

    Leaves the switch ALL_OPEN, so it's never left sitting on a closed
    channel if the fridge warms or cools next -- the cryogenic app note
    wants every channel open through any temperature change.

    Returns the set as a dict of run ids, which ecal.correct_set and
    ecal.drift take directly (or look it up later with ecal.find_set):

        {"set": "20261015T141502.318", "switch_serials": ..., "date_str": ...,
         "temp_str": ..., "labels": {1: "resonator", ...},
         "terminations": {2: "short", ...}, "cal_standards": {1: "flat_short", ...},
         "cooldown": "CD1" or None, "repeats": [
             {"before": {"ALL_OPEN": 101, "INTERNAL_SHORT": 102, ...},
              "ports":  {1: 104, 2: 105, ...},
              "after":  {"ALL_OPEN": 110, ...}},
             ...]}
    """
    channels = list(channels)
    if not channels or any(c not in range(1, 7) for c in channels):
        raise ValueError(f"channels must be a non-empty subset of 1-6, got {channels!r}")
    if len(set(channels)) != len(channels):
        raise ValueError(f"channels has duplicates: {channels!r}")
    if int(repeats) != repeats or repeats < 1:
        raise ValueError(f"repeats must be a positive integer, got {repeats!r}")
    if n_ports not in (1, 2):
        raise ValueError(f"n_ports must be 1 or 2, got {n_ports!r}")
    if mxc_temp_k is not None and temp_reader is not None:
        raise ValueError("pass mxc_temp_k or temp_reader, not both")
    if cooldown is not None and (not isinstance(cooldown, str) or not cooldown.strip()):
        raise ValueError(f"cooldown must be a non-empty label like 'CD1', got {cooldown!r}")
    cooldown = cooldown.strip() if cooldown else None
    terminations = _check_terminations(terminations)

    std_names, std_records = {}, {}
    if cal_standards is not None:
        if cooldown is None:
            raise ValueError("cal_standards needs cooldown= (e.g. 'CD1', or 'bench' warm), "
                             "so each set can be matched to the cooldown it came from")
        registry = load_standards(standards_file)
        std_names = _check_cal_standards(cal_standards, channels, registry)
        std_records = {ch: registry[name] for ch, name in std_names.items()}
        terminations = dict(terminations or {})
        for ch, name in std_names.items():
            if terminations.get(ch, name) != name:
                raise ValueError(f"RF{ch} is {terminations[ch]!r} in terminations but "
                                 f"{name!r} in cal_standards")
            terminations[ch] = name
        terminations = dict(sorted(terminations.items()))
        if labels is None or hasattr(labels, "items"):     # anything else: _check_labels says why
            labels = dict(labels or {})
            for ch, name in std_names.items():
                labels.setdefault(ch, f"{name} ({std_records[ch]['type']})")
        if db_path is None:
            db_path = _default_cal_db_path()
        if out_root is None:
            out_root = files_root(db_path)
    labels = _check_labels(labels, channels)

    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")

    _check_correction(vna, vna_cal, "An e-cal set")
    if temp_reader is not None:
        # Check once for the whole set, so a reader that breaks partway
        # through only costs readings (warnings), never half a set.
        _read_temperature(temp_reader, strict=True)
        temp_reader = _checked(temp_reader)

    # To the millisecond: two sets started within the same second (a quick
    # re-run) must not share an id, or find_set would merge them.
    set_id = datetime.now().strftime("%Y%m%dT%H%M%S.%f")[:-3]
    tags = {"ecal_set": set_id, "mxc_temp_k": mxc_temp_k, "ecal_note": note,
            "cooldown": cooldown,
            "ecal_terminations": (json.dumps({str(ch): t for ch, t in terminations.items()})
                                  if terminations is not None else None),
            "cal_standards": (json.dumps({str(ch): dict(std_records[ch], name=std_names[ch])
                                          for ch in std_names})
                              if std_names else None)}
    port_tags = {}
    for ch in channels:
        t = {}
        if terminations is not None:
            t["termination"] = terminations.get(ch, "none")
        if ch in std_names:
            t["cal_std"] = std_names[ch]
            for key, field in _STD_FIELDS:
                t[key] = std_records[ch].get(field)
        if t:
            port_tags[ch] = t
    print(f"E-cal set {set_id}: {repeats} repeat(s), channels {channels}, {n_ports}-port"
          + (", VNA cal ON underneath" if vna_cal else "")
          + (f", cooldown {cooldown}" if cooldown else ""))
    for ch in channels:
        bits = [x for x in (labels.get(ch), (terminations or {}).get(ch)) if x]
        if bits:
            print(f"  RF{ch}: {' / '.join(dict.fromkeys(bits))}")
    if std_names:
        print(f"  calibration standards -> {Path(db_path).resolve()}")

    repeats_out = []
    try:
        for k in range(1, repeats + 1):
            block = {}
            for role, positions in (("before", list(ECAL_STANDARDS)),
                                    ("ports", channels),
                                    ("after", list(ECAL_STANDARDS))):
                meta = dict(tags, ecal_repeat=k,
                            ecal_role="port" if role == "ports" else role)
                print(f"-- repeat {k}/{repeats}: {role}")
                # backup=False: one backup for the whole set (below), not one per block.
                ids = run_sweep(positions, date_str, temp_str, switch_serials,
                                n_ports=n_ports, setup=setup, touchstone=touchstone,
                                out_root=out_root, db_path=db_path, metadata=meta,
                                labels=labels if role == "ports" else None,
                                position_metadata=port_tags if role == "ports" else None,
                                temp_reader=temp_reader, backup=False, vna=vna, switch=switch)
                block[role] = dict(zip(positions, ids))
            repeats_out.append(block)
    finally:
        # Even if the set dies partway -- never leave a channel closed.
        _leave_switch_open(switch, vna)
        _safe_backup(db_path)       # also backs up a set that died partway

    print(f"E-cal set {set_id} complete.")
    return {"set": set_id, "switch_serials": switch_serials, "date_str": date_str,
            "temp_str": temp_str, "labels": dict(labels),
            "terminations": dict(terminations or {}), "cal_standards": dict(std_names),
            "cooldown": cooldown, "repeats": repeats_out}


# The external cal kit's standards, in the order run_kit_set asks for them.
KIT_STANDARDS = ("open", "short", "load")


def run_kit_set(date_str, temp_str, switch_serials, channels=(1,), note=None, n_ports=2,
                vna_cal=False, setup="kit", touchstone=False, out_root=None, db_path=None,
                vna=None, switch=None):
    """
    Measure what ecal.make_ideals needs to build your own switch's
    definitions of its internal standards (instead of NIST's, which are
    for their switch): the internal standards, then an external cal kit's
    open, short and load on each channel's RF connector, then the internal
    standards again. Warm only -- it needs hands on the connectors.

        kit = run_kit_set(date_str, "295K", "SN0077", channels=[1])
        my_ideals = ecal.make_ideals(kit)
        result = ecal.correct_set(cal, my_ideals, sparam="S21")

    Before each kit standard it opens every channel and waits for you to
    put that standard on the channel's connector and press Enter. Whatever
    is normally on the channel (the resonator, say) comes off for this and
    goes back on afterwards; the definitions belong to the switch alone,
    so the e-cal set can be taken before or after, with any wiring outside
    the switch.

    Same rules as run_ecal_set: VNA correction OFF unless vna_cal=True
    (refuses otherwise; the definitions come out the same either way),
    keep setup_sweep fixed for the whole set, and the switch is left
    ALL_OPEN even if it dies partway. `n_ports` as in run_ecal_set (2 for
    the circulator wiring). Runs are tagged with
    kit_set (one id per call), kit_role ("before", "kit" or "after"),
    kit_std on the kit runs, kit_note if given, and dut_label "kit open"
    etc. on the kit runs, and filed under the group "<setup>_uncal"
    ("<setup>_cal" with vna_cal=True).

    Returns the set (ecal.find_kit_set rebuilds it later):

        {"set": "20261015T141502.318", "switch_serials": ..., "date_str": ...,
         "temp_str": ...,
         "before": {"ALL_OPEN": 101, "INTERNAL_SHORT": 102, "INTERNAL_LOAD": 103},
         "kit":    {1: {"open": 104, "short": 105, "load": 106}, ...},
         "after":  {"ALL_OPEN": 107, ...}}
    """
    channels = list(channels)
    if not channels or any(c not in range(1, 7) for c in channels):
        raise ValueError(f"channels must be a non-empty subset of 1-6, got {channels!r}")
    if len(set(channels)) != len(channels):
        raise ValueError(f"channels has duplicates: {channels!r}")
    if n_ports not in (1, 2):
        raise ValueError(f"n_ports must be 1 or 2, got {n_ports!r}")

    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")
    _check_correction(vna, vna_cal, "A kit set")

    set_id = datetime.now().strftime("%Y%m%dT%H%M%S.%f")[:-3]
    tags = {"kit_set": set_id, "kit_note": note}
    print(f"Kit set {set_id}: channels {channels}, {n_ports}-port")
    out = {"set": set_id, "switch_serials": switch_serials, "date_str": date_str,
           "temp_str": temp_str, "before": {}, "kit": {}, "after": {}}

    def sweep(positions, role, std=None):
        # backup=False: one backup for the whole set (below).
        return run_sweep(positions, date_str, temp_str, switch_serials, n_ports=n_ports,
                         setup=setup, touchstone=touchstone, out_root=out_root,
                         db_path=db_path, metadata=dict(tags, kit_role=role, kit_std=std),
                         labels={positions[0]: f"kit {std}"} if std else None,
                         backup=False, vna=vna, switch=switch)

    try:
        print("-- internal standards (before)")
        out["before"] = dict(zip(ECAL_STANDARDS, sweep(list(ECAL_STANDARDS), "before")))
        for ch in channels:
            out["kit"][ch] = {}
            for std in KIT_STANDARDS:
                _select(state="ALL_OPEN", switch=switch, vna=vna)   # swap with every channel open
                input(f"Put the kit {std} on RF{ch}'s connector, then press Enter...")
                (run_id,) = sweep([ch], "kit", std)
                out["kit"][ch][std] = run_id
        print("-- internal standards (after)")
        out["after"] = dict(zip(ECAL_STANDARDS, sweep(list(ECAL_STANDARDS), "after")))
    finally:
        _leave_switch_open(switch, vna)
        _safe_backup(db_path)

    print(f"Kit set {set_id} complete. Put back whatever goes on "
          f"RF{', RF'.join(str(c) for c in channels)}.")
    return out
