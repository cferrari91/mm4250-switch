"""
Batch 1-port sweep: measure a set of RF channels and save the results.

Measurement itself comes from vna_measure.py -- this module is
the data layer on top of it. For interactive one-off measurements, use
vna_measure directly.

Each channel is saved twice:
    Sweeps/<date_str>_<temp_str>/<switch_serials>/raw/RF<n>.s1p
    a QCoDeS run named "RF<n>" in a shared .db file

Database layout: ONE accumulating .db file (mm4250_oneport.db, beside
these files -- see DEFAULT_DB_NAME) holds every run ever taken, the way
the lab's nanoRFE_data.db / tinySA_plottr.db files are used. Inside it:

    experiment  -- one per run_oneport_sweep() call, named
                   "<date_str>_<temp_str>_<switch_serials>", with
                   sample_name set to the switch serials
    dataset     -- one per channel measured in that call, named "RF<n>"

So a sweep session's runs stay grouped together and can't be confused
with another date's or another unit's. Browse the file afterwards with
`plottr-inspectr --db mm4250_oneport.db`, or load runs in Python with
qcodes.dataset's load_by_id / load_by_run_spec.

Both frequency and S11 are registered with paramtype="array": that's the
blob storage type, and it round-trips a complex-valued numpy array
correctly. Do NOT use paramtype="complex" here -- that one is for complex
*scalars*, and a complex array written with it writes without error but
then fails on read-back.

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
    _resolve_instrument,
    measure_s11,
)

DEFAULT_DB_NAME = "mm4250_oneport.db"
DEFAULT_OUT_DIR_NAME = "Sweeps"


def _default_out_root():
    """
    Where sweeps are saved by default: <repo root>/Sweeps.

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
    Path to the shared database: mm4250_oneport.db, in the same folder
    as these files.

    Resolved from this file's location rather than the working directory,
    so every notebook that calls this writes to the same one file.
    """
    return Path(__file__).resolve().parent / DEFAULT_DB_NAME


def save_s1p(freq_hz, s11, path):
    """
    Write `freq_hz`/`s11` (as returned by measure_s11) to a standard
    1-port Touchstone (.s1p) file at `path`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("!Created by oneport_db_sweep.py\n")
        f.write("# HZ S RI R 50\n")
        for i in range(len(freq_hz)):
            f.write(f"{freq_hz[i]:.1f} {s11[i].real:.6e} {s11[i].imag:.6e}\n")


def record_channel(channel, freq_hz, s11, exp, s1p_path=None, **metadata):
    """
    Save one already-measured channel as a QCoDeS dataset named "RF<n>"
    inside the experiment `exp`. Returns the new run's id.

    `s1p_path` and any extra keyword arguments are attached to the
    dataset as metadata, so a run in the .db can always be traced back to
    the raw file and the sweep it came from.
    """
    meas = Measurement(exp=exp, name=f"RF{channel}")
    meas.register_custom_parameter("frequency", label="Frequency", unit="Hz", paramtype="array")
    meas.register_custom_parameter(
        "s11", label="S11", unit="", setpoints=("frequency",), paramtype="array"
    )

    with meas.run() as datasaver:
        datasaver.add_result(("frequency", freq_hz), ("s11", s11))
        datasaver.dataset.add_metadata("channel", channel)
        if s1p_path is not None:
            datasaver.dataset.add_metadata("s1p_path", str(s1p_path))
        for key, value in metadata.items():
            datasaver.dataset.add_metadata(key, value)
        run_id = datasaver.run_id

    return run_id


def run_oneport_sweep(channels, date_str, temp_str, switch_serials, out_root=None,
                      db_path=None, exp_name=None, vna=None, switch=None):
    """
    Sweep the RF channels in `channels` (any subset of 1-6, e.g. [1, 3, 5]
    or list(range(1, 7))), saving each measurement twice:

      - <out_root>/<date_str>_<temp_str>/<switch_serials>/raw/RF<n>.s1p
      - a QCoDeS run named "RF<n>" in the shared database

    `out_root` defaults to a Sweeps/ folder beside this file -- not
    beside whatever notebook called it -- so sweeps land in the same
    place no matter where you run from. Pass it explicitly (absolute, or
    relative to the working directory) to put them somewhere else.

    All of this call's runs go into one experiment named
    "<date_str>_<temp_str>_<switch_serials>" (override with `exp_name`),
    with sample_name=switch_serials. Calling this again with the same
    date/temp/serials adds runs to that same experiment rather than
    making a duplicate one.

    Returns the sweep's output directory.

    vna/switch default to the already-instantiated instruments named
    "ksvna"/"switch" if not passed explicitly.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")

    db_path = Path(db_path) if db_path is not None else _default_db_path()
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initialise_or_create_database_at(db_path)

    if exp_name is None:
        exp_name = f"{date_str}_{temp_str}_{switch_serials}"
    exp = load_or_create_experiment(exp_name, sample_name=switch_serials)
    print(f"Recording to {db_path}")
    print(f"  experiment {exp_name!r} (sample {switch_serials!r})")

    out_root = Path(out_root) if out_root is not None else _default_out_root()
    sweep_dir = out_root / f"{date_str}_{temp_str}" / switch_serials
    raw_dir = sweep_dir / "raw"

    for channel in channels:
        print(f"Measuring RF{channel}...")
        freq_hz, s11 = measure_s11(channel, vna=vna, switch=switch)

        path = raw_dir / f"RF{channel}.s1p"
        save_s1p(freq_hz, s11, path)
        print(f"  saved {path}")

        run_id = record_channel(
            channel,
            freq_hz,
            s11,
            exp,
            s1p_path=path,
            switch_serials=switch_serials,
            date_str=date_str,
            temp_str=temp_str,
        )
        print(f"  recorded run #{run_id} in {db_path.name}")

    return sweep_dir
