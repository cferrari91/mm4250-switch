"""
Batch sweeps: measure a set of switch positions and save the results.

The measurement itself comes from vna_measure.py -- this module is the
data layer on top of it. For interactive one-off measurements, use
vna_measure directly.

One core sweep handles both port counts; run_oneport_sweep and
run_twoport_sweep are thin wrappers that differ only in how many
S-parameters they ask for. Each position measured is saved as

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

import os
import sqlite3
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
    DEFAULT_DB_NAME,
    DEFAULT_OUT_DIR_NAME,
    TOUCHSTONE_2PORT_ORDER,
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


def run_sweep(positions, date_str, temp_str, switch_serials, n_ports=2,
              setup=None, touchstone=False, out_root=None, db_path=None,
              exp_name=None, prompt_between=False, vna=None, switch=None):
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

    Returns the list of run ids, in the order measured -- what
    plots.plot_sweep, read_db.load_run and read_db.export_touchstone take:

        runs = run_twoport_sweep([3, "ALL_OPEN"], ..., setup="RF3")
        plot_sweep(runs)

    vna/switch default to the already-instantiated instruments named
    "ksvna"/"switch" if not passed explicitly.
    """
    if n_ports not in (1, 2):
        raise ValueError(f"n_ports must be 1 or 2, got {n_ports!r}")

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

        if prompt_between:
            input(f"Ready to measure {label}? Set up the connections, then press Enter...")

        print(f"Measuring {label}...")
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
            **vna_state,
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
