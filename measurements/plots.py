"""
Plots and printed summaries for MM4250 sweeps.

Two views, one for each moment you want to look at something:

    plot_measurement(freq, data)    what you just measured, one position
    plot_sweep(sweep_dir)           every position of a finished sweep,
                                    overlaid on one axes

and summarize(freq, data), which prints the same measurement as numbers
-- min, max, and the value at a few marker frequencies.

matplotlib and numpy only. Nothing is imported from vna_measure or
sweep_db, so this module loads with no instruments connected: you can
plot a sweep taken last week from the files alone.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# Column order inside a Touchstone file. NOT reading order -- a .s2p
# holds S11, S21, S12, S22. Kept here rather than imported from
# sweep_db so this module doesn't drag in qcodes; the two must agree,
# and sweep_db.TOUCHSTONE_2PORT_ORDER is the other half of the pair.
TOUCHSTONE_ORDER = {
    1: ("S11",),
    2: ("S11", "S21", "S12", "S22"),
}

# Multiplier from the frequency unit named on a Touchstone option line.
FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


def _as_dict(data):
    """Accept a bare complex array (1-port S11) or a dict, return a dict."""
    if hasattr(data, "keys"):
        return dict(data)
    return {"S11": np.asarray(data)}


def _db(values):
    """
    Magnitude in dB.

    Zeros are floored before the log so a perfectly isolated point plots
    as a very small number instead of -inf, which would blank the axes
    and take the rest of the trace with it.
    """
    mag = np.abs(np.asarray(values))
    return 20 * np.log10(np.maximum(mag, 1e-15))


def read_touchstone(path):
    """
    Read a .s1p or .s2p back. Returns (freq_hz, {sparam: complex array}).

    Understands the three Touchstone data formats -- RI (real/imaginary,
    which is what sweep_db writes), MA (magnitude/angle) and DB
    (dB/angle) -- and any of the four frequency units, so this also opens
    files the VNA or another tool wrote, not just ours.

    Numbers are collected across line breaks rather than per line: our
    writer puts a whole frequency point on one line, but the format
    allows a 2-port point to be split across several, and files from
    elsewhere do that.
    """
    path = Path(path)
    n_ports = int(path.suffix[2:-1])  # ".s2p" -> 2
    if n_ports not in TOUCHSTONE_ORDER:
        raise ValueError(f"{path.name}: only .s1p and .s2p are supported")

    freq_scale, fmt = 1.0, "RI"
    numbers = []
    with open(path) as handle:
        for line in handle:
            line = line.split("!")[0].strip()  # drop comments
            if not line:
                continue
            if line.startswith("#"):
                fields = line[1:].upper().split()
                for field in fields:
                    if field in FREQ_UNITS:
                        freq_scale = FREQ_UNITS[field]
                    elif field in ("RI", "MA", "DB"):
                        fmt = field
                continue
            numbers.extend(float(token) for token in line.split())

    per_point = 1 + 2 * n_ports**2
    if not numbers or len(numbers) % per_point:
        raise ValueError(
            f"{path.name}: got {len(numbers)} numbers, which isn't a whole "
            f"number of {per_point}-value points for a {n_ports}-port file."
        )

    rows = np.asarray(numbers, dtype=np.float64).reshape((-1, per_point))
    freq_hz = rows[:, 0] * freq_scale

    data = {}
    for index, sparam in enumerate(TOUCHSTONE_ORDER[n_ports]):
        first, second = rows[:, 1 + 2 * index], rows[:, 2 + 2 * index]
        if fmt == "RI":
            data[sparam] = first + 1j * second
        elif fmt == "MA":
            data[sparam] = first * np.exp(1j * np.deg2rad(second))
        else:  # DB
            data[sparam] = 10 ** (first / 20) * np.exp(1j * np.deg2rad(second))
    return freq_hz, data


def plot_measurement(freq_hz, data, title=None, phase=False, ax=None):
    """
    Plot one measurement: magnitude in dB against frequency, one line per
    S-parameter. Takes what measure_s11 or measure_2port returned.

        freq, data = measure_2port(3)
        ax = plot_measurement(freq, data, title="RF3")

    `phase=True` adds a second panel underneath with the unwrapped phase,
    which is what you want when checking electrical length or looking for
    a cable problem. Returns the magnitude Axes (or both, with phase).
    """
    data = _as_dict(data)
    freq_ghz = np.asarray(freq_hz) / 1e9

    if ax is None:
        if phase:
            _, (ax, ax_phase) = plt.subplots(
                2, 1, figsize=(9, 6.5), sharex=True,
                gridspec_kw={"height_ratios": [2, 1]},
            )
        else:
            _, ax = plt.subplots(figsize=(9, 4.5))
            ax_phase = None
    else:
        ax_phase = None

    for sparam, values in data.items():
        ax.plot(freq_ghz, _db(values), label=sparam, linewidth=1.3)
        if ax_phase is not None:
            ax_phase.plot(
                freq_ghz,
                np.rad2deg(np.unwrap(np.angle(values))),
                label=sparam,
                linewidth=1.3,
            )

    ax.set_ylabel("Magnitude (dB)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    if title:
        ax.set_title(title)

    if ax_phase is not None:
        ax_phase.set_ylabel("Phase (deg)")
        ax_phase.set_xlabel("Frequency (GHz)")
        ax_phase.grid(True, alpha=0.3)
        return ax, ax_phase

    ax.set_xlabel("Frequency (GHz)")
    return ax


def summarize(freq_hz, data, markers=None, title=None):
    """
    Print one measurement as numbers: min and max magnitude across the
    band, and the value at each marker frequency.

        summarize(freq, data)
        summarize(freq, data, markers=[2e9, 6e9, 10e9])

    `markers` are in Hz and default to the bottom, middle and top of the
    sweep. Each is reported at the nearest measured point, so a marker
    between samples doesn't interpolate anything that wasn't measured.

    Returns the marker frequencies actually used.
    """
    data = _as_dict(data)
    freq_hz = np.asarray(freq_hz)

    if markers is None:
        markers = [freq_hz[0], freq_hz[len(freq_hz) // 2], freq_hz[-1]]
    indices = [int(np.argmin(np.abs(freq_hz - m))) for m in markers]
    used = freq_hz[indices]

    if title:
        print(title)
    print(f"{len(freq_hz)} points, {freq_hz[0] / 1e9:.6g} - "
          f"{freq_hz[-1] / 1e9:.6g} GHz")

    header = f"{'':<6}{'min dB':>10}{'max dB':>10}"
    header += "".join(f"{f / 1e9:>9.3g} GHz" for f in used)
    print(header)
    for sparam, values in data.items():
        db = _db(values)
        row = f"{sparam:<6}{db.min():>10.2f}{db.max():>10.2f}"
        row += "".join(f"{db[i]:>13.2f}" for i in indices)
        print(row)

    return used


def _position_and_run(path):
    """
    Split "RF3_run12.s2p" into ("RF3", 12). Returns (stem, None) for a
    filename that doesn't carry a run id, so files from elsewhere still
    plot -- they just sort last.
    """
    position, _, run = path.stem.rpartition("_run")
    if position and run.isdigit():
        return position, int(run)
    return path.stem, None


def plot_sweep(sweep_dir, sparam=None, ax=None, title=None):
    """
    Overlay every position of a finished sweep on one axes -- the view
    that actually tells you something about a switch.

        sweep_dir = run_twoport_sweep([1, 3, 5], ...)
        ax = plot_sweep(sweep_dir)

    Pass either the directory run_sweep returned or the raw/ folder
    inside it. `sparam` defaults to S21 where the files are 2-port
    (insertion loss, one line per channel) and S11 where they're 1-port.

    Plotting S21 with an isolated state in the set is how you read
    isolation off the plot: the connected channels sit near the top, the
    open state drops to the floor, and the gap between them is the
    number you want.

    Lines are ordered by run id, so the legend follows the order you
    measured in. Returns the Axes.
    """
    sweep_dir = Path(sweep_dir)
    raw_dir = sweep_dir / "raw" if (sweep_dir / "raw").is_dir() else sweep_dir

    files = sorted(
        list(raw_dir.glob("*.s1p")) + list(raw_dir.glob("*.s2p")),
        key=lambda p: (_position_and_run(p)[1] is None, _position_and_run(p)[1]),
    )
    if not files:
        raise FileNotFoundError(f"No .s1p or .s2p files in {raw_dir}")

    if sparam is None:
        sparam = "S21" if files[0].suffix == ".s2p" else "S11"

    if ax is None:
        _, ax = plt.subplots(figsize=(9, 5))

    for path in files:
        freq_hz, data = read_touchstone(path)
        if sparam not in data:
            continue
        position, run = _position_and_run(path)
        label = position if run is None else f"{position} (run {run})"
        ax.plot(freq_hz / 1e9, _db(data[sparam]), label=label, linewidth=1.3)

    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel(f"|{sparam}| (dB)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    ax.set_title(title or f"{sparam} - {sweep_dir.parent.name} {sweep_dir.name}")
    return ax
