"""
Batch sweeps: measure a set of switch positions and save the results.

The measurement itself comes from vna_measure.py -- this module is the
data layer on top of it. For interactive one-off measurements, use
vna_measure directly.

One core sweep handles both port counts; run_oneport_sweep and
run_twoport_sweep are thin wrappers that differ only in how many
S-parameters they ask for. Each position measured is saved twice:

    Sweeps/<date_str>_<temp_str>/<switch_serials>/raw/<position>.s1p (or .s2p)
    a QCoDeS run named "<position>" in a shared .db file

Database layout: ONE accumulating .db file (mm4250_sweeps.db, beside
these files -- see DEFAULT_DB_NAME) holds every run ever taken, the way
the lab's nanoRFE_data.db / tinySA_plottr.db files are used. Inside it:

    experiment  -- one per run_*_sweep() call, named
                   "<date_str>_<temp_str>_<switch_serials>", with
                   sample_name set to the switch serials
    dataset     -- one per position measured in that call, named "RF<n>"
                   (or the switch state name, e.g. "ALL_OPEN")

So a sweep session's runs stay grouped together and can't be confused
with another date's or another unit's. 1-port and 2-port runs can share
an experiment -- each run records only the S-parameters it actually
measured, and carries n_ports as metadata. Browse the file afterwards
with `plottr-inspectr --db mm4250_sweeps.db`, or load runs in Python with
qcodes.dataset's load_by_id / load_by_run_spec.

Every parameter is registered with paramtype="array": that's the blob
storage type, and it round-trips a complex-valued numpy array correctly.
Do NOT use paramtype="complex" here -- that one is for complex *scalars*,
and a complex array written with it writes without error but then fails
on read-back.

No calibration or de-embedding is applied -- this is raw acquisition.
"""

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
    _resolve_instrument,
    measure_2port,
    measure_s11,
)

DEFAULT_DB_NAME = "mm4250_sweeps.db"
DEFAULT_OUT_DIR_NAME = "Sweeps"

# Touchstone column order for a 2-port file is S11, S21, S12, S22 -- NOT
# the reading order SPARAMS_2PORT uses. Getting these two confused
# silently swaps forward and reverse transmission in every saved file, so
# the writer below goes through this tuple and nothing else.
TOUCHSTONE_2PORT_ORDER = ("S11", "S21", "S12", "S22")


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


def save_touchstone(freq_hz, data, path):
    """
    Write `freq_hz`/`data` to a Touchstone file at `path`.

    `data` is either a bare complex array (written as a 1-port .s1p) or
    the {"S11": ..., "S12": ..., "S21": ..., "S22": ...} dict returned by
    measure_2port (written as a 2-port .s2p). The file extension follows
    from the data, so pass a path whose suffix matches.
    """
    data = _as_dict(data)
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


def save_s1p(freq_hz, s11, path):
    """Write a 1-port Touchstone (.s1p). Thin wrapper on save_touchstone."""
    save_touchstone(freq_hz, s11, path)


def save_s2p(freq_hz, data, path):
    """Write a 2-port Touchstone (.s2p). Thin wrapper on save_touchstone."""
    save_touchstone(freq_hz, data, path)


def record_measurement(name, freq_hz, data, exp, touchstone_path=None, **metadata):
    """
    Save one already-measured position as a QCoDeS dataset named `name`
    (e.g. "RF3") inside the experiment `exp`. Returns the new run's id.

    `data` is a bare complex S11 array or the dict from measure_2port;
    only the S-parameters actually present are registered, so 1-port and
    2-port runs can live side by side in the same experiment.

    `touchstone_path` and any extra keyword arguments are attached to the
    dataset as metadata, so a run in the .db can always be traced back to
    the raw file and the sweep it came from.
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
        datasaver.dataset.add_metadata("n_ports", 2 if len(data) == 4 else 1)
        if touchstone_path is not None:
            datasaver.dataset.add_metadata("touchstone_path", str(touchstone_path))
        for key, value in metadata.items():
            # A position is reached either by channel number or by state
            # name, never both -- skip whichever one doesn't apply rather
            # than writing a null into the run's metadata.
            if value is not None:
                datasaver.dataset.add_metadata(key, value)
        run_id = datasaver.run_id

    return run_id


def record_channel(channel, freq_hz, data, exp, touchstone_path=None, **metadata):
    """
    Save one measured RF channel as a run named "RF<n>", tagging it with
    the channel number. Thin wrapper on record_measurement.
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


def _open_experiment(date_str, temp_str, switch_serials, db_path, exp_name):
    """Point QCoDeS at the shared database and open this call's experiment."""
    db_path = Path(db_path) if db_path is not None else _default_db_path()
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initialise_or_create_database_at(db_path)

    if exp_name is None:
        exp_name = f"{date_str}_{temp_str}_{switch_serials}"
    exp = load_or_create_experiment(exp_name, sample_name=switch_serials)
    print(f"Recording to {db_path}")
    print(f"  experiment {exp_name!r} (sample {switch_serials!r})")
    return exp, db_path


def run_sweep(positions, date_str, temp_str, switch_serials, n_ports=2,
              out_root=None, db_path=None, exp_name=None, prompt_between=False,
              vna=None, switch=None):
    """
    Measure each entry in `positions` and save it twice -- as a Touchstone
    file and as a QCoDeS run.

    `positions` is a list of RF channel numbers (any subset of 1-6, e.g.
    [1, 3, 5] or list(range(1, 7))), and/or switch state names as strings:

        run_sweep([1, 3, 5], ...)                     # three channels
        run_sweep(list(range(1, 7)), ...)             # all six
        run_sweep([3, "ALL_OPEN", 1], ...)            # mixed

    Mixing the two is how you measure isolation without touching a cable:
    hold the cabling fixed, measure the channel that's connected, then
    measure again on a state that disconnects it.

    `n_ports` is 2 for a full S11/S12/S21/S22 measurement saved as .s2p,
    or 1 for S11 only saved as .s1p.

    Files land in
    <out_root>/<date_str>_<temp_str>/<switch_serials>/raw/<position>.s2p,
    with `out_root` defaulting to a Sweeps/ folder beside this file -- not
    beside whatever notebook called it -- so sweeps land in the same place
    no matter where you run from. Pass it explicitly (absolute, or
    relative to the working directory) to put them somewhere else.

    All of this call's runs go into one experiment named
    "<date_str>_<temp_str>_<switch_serials>" (override with `exp_name`),
    with sample_name=switch_serials. Calling this again with the same
    date/temp/serials adds runs to that same experiment rather than
    making a duplicate one.

    `prompt_between=True` pauses before each position and waits for you to
    press Enter -- for setups where something has to be re-cabled by hand
    between measurements.

    Returns the sweep's output directory.

    vna/switch default to the already-instantiated instruments named
    "ksvna"/"switch" if not passed explicitly.
    """
    if n_ports not in (1, 2):
        raise ValueError(f"n_ports must be 1 or 2, got {n_ports!r}")

    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")

    exp, db_path = _open_experiment(
        date_str, temp_str, switch_serials, db_path, exp_name
    )

    out_root = Path(out_root) if out_root is not None else _default_out_root()
    sweep_dir = out_root / f"{date_str}_{temp_str}" / switch_serials
    raw_dir = sweep_dir / "raw"
    suffix = ".s2p" if n_ports == 2 else ".s1p"

    for entry in positions:
        label, channel, state = _position(entry)

        if prompt_between:
            input(f"Ready to measure {label}? Set up the connections, then press Enter...")

        print(f"Measuring {label}...")
        if n_ports == 2:
            freq_hz, data = measure_2port(channel, state=state, vna=vna, switch=switch)
        else:
            freq_hz, data = measure_s11(channel, state=state, vna=vna, switch=switch)

        path = raw_dir / f"{label}{suffix}"
        save_touchstone(freq_hz, data, path)
        print(f"  saved {path}")

        run_id = record_measurement(
            label,
            freq_hz,
            data,
            exp,
            touchstone_path=path,
            channel=channel,
            state=state,
            switch_serials=switch_serials,
            date_str=date_str,
            temp_str=temp_str,
        )
        print(f"  recorded run #{run_id} in {db_path.name}")

    return sweep_dir


def run_twoport_sweep(positions, date_str, temp_str, switch_serials, **kwargs):
    """
    Full 2-port sweep: S11, S12, S21 and S22 at every position, saved as
    .s2p files and QCoDeS runs. See run_sweep for the full argument list.
    """
    return run_sweep(positions, date_str, temp_str, switch_serials, n_ports=2, **kwargs)


def run_oneport_sweep(positions, date_str, temp_str, switch_serials, **kwargs):
    """
    1-port sweep: S11 only, saved as .s1p files and QCoDeS runs.
    See run_sweep for the full argument list.
    """
    return run_sweep(positions, date_str, temp_str, switch_serials, n_ports=1, **kwargs)
