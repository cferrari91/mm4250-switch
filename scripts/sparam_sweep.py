from pathlib import Path

from qcodes.instrument import Instrument

DEFAULT_VNA_NAME = "ksvna"
DEFAULT_SWITCH_NAME = "switch"

# Touchstone .s2p column order is S11, S21, S12, S22 -- NOT the same
# order as SPARAMS_IN_ORDER below. Keep these two straight.
SPARAMS_IN_ORDER = ("S11", "S12", "S21", "S22")

# Which RF channel (1-6) is wired as a straight thru cable between the
# two switches, used as the thru standard for a full 2-port cal through
# the switch matrix, instead of holding a DUT. measure_2port() treats it
# identically to every other channel -- this constant only exists so
# sweep scripts/logs can label it correctly. Set this before sweeping.
THRU_CHANNEL = None


def _resolve_instrument(instrument, default_name, label):
    """Return `instrument` if given, else look up the already-registered
    QCoDeS instrument named `default_name` (e.g. the `ksvna`/`switch`
    instances from QCodesMeasurmentFramework.ipynb)."""
    if instrument is not None:
        return instrument
    try:
        return Instrument.find_instrument(default_name)
    except KeyError:
        raise RuntimeError(
            f"No {label} instance given and none named {default_name!r} is "
            f"registered. Pass it explicitly, e.g. measure_2port(3, vna=ksvna, switch=switch)."
        )


def ensure_full_sparam_traces(vna):
    """
    Make sure S11, S12, S21, S22 traces exist on `vna`, reusing whichever
    of them are already configured rather than adding duplicates.
    Returns the 4 trace objects as (tr_s11, tr_s12, tr_s21, tr_s22).
    """
    existing = {tr.trace(): tr for tr in vna.traces}
    traces = []
    for sparam in SPARAMS_IN_ORDER:
        tr = existing.get(sparam)
        if tr is None:
            tr = vna.add_trace()
            tr.trace(sparam)
            existing[sparam] = tr
        traces.append(tr)
    return tuple(traces)


def measure_2port(channel, vna=None, switch=None):
    """
    Set the switch to `channel` (1-6) and take a full 2-port S-parameter
    measurement. Returns (freq_hz, s_data), where s_data is
    {"S11": ..., "S12": ..., "S21": ..., "S22": ...} of complex numpy
    arrays (magnitude+phase, from the VNA's polar trace data).

    vna/switch default to the already-instantiated instruments named
    "ksvna"/"switch" if not passed explicitly.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")

    switch.channel(channel)

    tr_s11, tr_s12, tr_s21, tr_s22 = ensure_full_sparam_traces(vna)

    # One sweep covers every trace on this channel -- callable from any
    # trace object, not just the first one.
    tr_s11.run_sweep()

    # Reading .polar() on each trace would otherwise re-trigger its own
    # sweep (auto_sweep defaults to True); we already swept above, so
    # turn that off for the 4 reads below.
    prev_auto_sweep = vna.auto_sweep()
    vna.auto_sweep(False)
    try:
        s_data = {
            "S11": tr_s11.polar(),
            "S12": tr_s12.polar(),
            "S21": tr_s21.polar(),
            "S22": tr_s22.polar(),
        }
    finally:
        vna.auto_sweep(prev_auto_sweep)

    freq_hz = vna.frequency_axis()
    return freq_hz, s_data


def save_s2p(freq_hz, s_data, path):
    """
    Write `freq_hz`/`s_data` (as returned by measure_2port) to a standard
    2-port Touchstone (.s2p) file at `path`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Touchstone 2-port column order: S11, S21, S12, S22.
    s11, s21, s12, s22 = s_data["S11"], s_data["S21"], s_data["S12"], s_data["S22"]

    with open(path, "w") as f:
        f.write("!Created by scripts/sparam_sweep.py measure_2port\n")
        f.write("# HZ S RI R 50\n")
        for i in range(len(freq_hz)):
            f.write(
                f"{freq_hz[i]:.1f} "
                f"{s11[i].real:.6e} {s11[i].imag:.6e} "
                f"{s21[i].real:.6e} {s21[i].imag:.6e} "
                f"{s12[i].real:.6e} {s12[i].imag:.6e} "
                f"{s22[i].real:.6e} {s22[i].imag:.6e}\n"
            )


def sweep_all_channels(date_str, temp_str, switch_serials, out_root="Sweeps", vna=None, switch=None):
    """
    Sweep every RF channel (1-6), saving each as
    <out_root>/<date_str>_<temp_str>/<switch_serials>/RF<n>/RF<n>.s2p.

    THRU_CHANNEL (module-level, set it above before calling this) is
    measured identically to every other channel -- it's only called out
    in the printed log, so it isn't mistaken for a DUT measurement later.
    Returns the sweep's output directory.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")

    if THRU_CHANNEL is None:
        print("[WARNING] THRU_CHANNEL is not set in scripts/sparam_sweep.py -- "
              "no channel will be labeled as the thru standard in this sweep.")

    sweep_dir = Path(out_root) / f"{date_str}_{temp_str}" / switch_serials

    for channel in range(1, 7):
        label = " (THRU)" if channel == THRU_CHANNEL else ""
        print(f"Measuring RF{channel}{label}...")
        freq_hz, s_data = measure_2port(channel, vna=vna, switch=switch)
        path = sweep_dir / f"RF{channel}" / f"RF{channel}.s2p"
        save_s2p(freq_hz, s_data, path)
        print(f"  saved {path}")

    return sweep_dir
