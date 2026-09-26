"""
Plots and printed summaries for MM4250 sweeps.

Two views, one for each moment you want to look at something:

    plot_measurement(freq, data)    what you just measured, one position
    plot_sweep(runs)                every position of a finished sweep,
                                    overlaid on one axes -- from the
                                    database by run id, or from a folder
                                    of .s1p/.s2p files

and summarize(freq, data), which prints the same measurement as numbers
-- min, max, and the value at a few marker frequencies.

Both plots take the same optional styling and saving arguments --
xlim, ylim, colors, labels, title, save -- and return the Axes so
anything else matplotlib can do still applies afterwards. save= puts
the figure in figures/<serials>/<date>/<temp>/ beside these files,
the same switch -> date -> temperature layout Sweeps/ uses.

matplotlib and numpy only (plus read_db, which is numpy and sqlite3).
Nothing is imported from vna_measure or sweep_db, so this module loads
with no instruments connected and no qcodes installed: you can plot a
sweep taken last week from the database or the files alone.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import read_db

# Column order inside a Touchstone file. NOT reading order -- a .s2p
# holds S11, S21, S12, S22. This is the reader's copy, covering 1- and
# 2-port; read_db.TOUCHSTONE_2PORT_ORDER is the writer's. The two must
# agree -- the 2-port entry here is checked against it below.
TOUCHSTONE_ORDER = {
    1: ("S11",),
    2: ("S11", "S21", "S12", "S22"),
}

assert TOUCHSTONE_ORDER[2] == read_db.TOUCHSTONE_2PORT_ORDER, "reader/writer column order disagree"

# Multiplier from the frequency unit named on a Touchstone option line.
FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}

FIGURES_DIR_NAME = "figures"
SAVE_DPI = 200


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


def plot_measurement(freq_hz=None, data=None, title=None, phase=False, ax=None,
                     run=None, db_path=None, xlim=None, ylim=None,
                     colors=None, labels=None, save=None):
    """
    Plot one measurement: magnitude in dB against frequency, one line per
    S-parameter. Takes what measure_s11 or measure_2port returned, or a
    run id from the database:

        freq, data = measure_2port(3)
        ax = plot_measurement(freq, data, title="RF3")
        ax = plot_measurement(run=43)                  # any saved run

    `phase=True` adds a second panel underneath with the unwrapped phase,
    which is what you want when checking electrical length or looking for
    a cable problem. Returns the magnitude Axes (or both, with phase).

    Styling -- all optional, all defaulting to what it did before:
      xlim=(0, 6)         x range in GHz (shared by the phase panel)
      ylim=(-40, 0)       magnitude range in dB
      colors=[...]        one per S-parameter, in plotting order, or a
                          dict like {"S21": "black"}
      labels=[...]        legend text, same forms as colors
      title="..."         defaults to the run's name with run=

    save="name.png" writes the figure (PNG, PDF or SVG by the extension;
    .png if there's none) into figures/<serials>/<date>/<temp>/ for a
    run=, or figures/ for a measurement not yet saved to the database.
    save=True picks the name for you; a path with folders in it is used
    exactly as given.
    """
    where, default_name = (None, None, None), "measurement"
    if run is not None:
        info = _run_info([run], db_path)[run]
        freq_hz, data = read_db.load_run(run, db_path)
        group = read_db._group(info)
        title = title or f"{info['name']} ({group}, run {run})"
        where = _where(info)
        default_name = f"{group}_{info['name']}_run{run}"
    elif freq_hz is None or data is None:
        raise TypeError("plot_measurement needs (freq, data) or run=<run id>")
    elif title:
        default_name = title

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

    for i, (sparam, values) in enumerate(data.items()):
        color = _pick(colors, i, sparam)
        label = _pick(labels, i, sparam) or sparam
        ax.plot(freq_ghz, _db(values), label=label, color=color, linewidth=1.3)
        if ax_phase is not None:
            ax_phase.plot(
                freq_ghz,
                np.rad2deg(np.unwrap(np.angle(values))),
                label=label,
                color=color,
                linewidth=1.3,
            )

    ax.set_ylabel("Magnitude (dB)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    if title:
        ax.set_title(title)
    _limits(ax, xlim, ylim)

    if ax_phase is not None:
        ax_phase.set_ylabel("Phase (deg)")
        ax_phase.set_xlabel("Frequency (GHz)")
        ax_phase.grid(True, alpha=0.3)
        _save(ax.figure, save, default_name, where)
        return ax, ax_phase

    ax.set_xlabel("Frequency (GHz)")
    _save(ax.figure, save, default_name, where)
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


def _pick(spec, index, key):
    """
    One entry of a colors=/labels= argument: a list is taken in plotting
    order, a dict is looked up by `key` (run id or S-parameter name).
    Anything missing is None, which means "use the default".
    """
    if spec is None:
        return None
    if isinstance(spec, dict):
        return spec.get(key)
    return spec[index] if index < len(spec) else None


def _limits(ax, xlim, ylim):
    if xlim is not None:
        ax.set_xlim(*xlim)
    if ylim is not None:
        ax.set_ylim(*ylim)


def _run_info(run_ids, db_path):
    """list_runs() rows for these run ids, or KeyError naming the missing ones."""
    info = {r["run_id"]: r for r in read_db.list_runs(db_path)}
    missing = [r for r in run_ids if r not in info]
    if missing:
        raise KeyError(f"No run(s) {missing} in the database")
    return info


def _where(run):
    """(serials, date, temp) for a list_runs() row -- a figure's folder."""
    return (run["switch_serials"], run["date_str"], run["temp_str"])


def _figure_folder(wheres):
    """
    figures/<serials>/<date>/<temp>/, going only as deep as every trace
    agrees: runs from two dates of one switch land in figures/<serials>/,
    two switches in figures/ itself. Same layout as Sweeps/, minus the
    group -- one figure often spans cal and uncal.
    """
    folder = Path(__file__).resolve().parent / FIGURES_DIR_NAME
    for level in range(3):
        values = {w[level] for w in wheres}
        if len(values) != 1 or None in values:
            break
        folder = folder / values.pop()
    return folder


def _save(fig, save, default_name, wheres):
    """
    Write `fig` if save= asks for it; returns the path, or None.

    save=True        figures/<...>/<default_name>.png
    save="x.pdf"     figures/<...>/x.pdf -- a bare filename gets the folder
    save="a/b/x.png" exactly that path (relative to where you're running)
    """
    if not save:
        return None
    wheres = wheres if isinstance(wheres, list) else [wheres]
    if save is True:
        path = _figure_folder(wheres) / _safe_name(default_name)
    else:
        path = Path(save)
        if path.parent == Path("."):
            path = _figure_folder(wheres) / path
    if not path.suffix:
        path = path.with_suffix(".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=SAVE_DPI, bbox_inches="tight")
    print(f"saved {path}")
    return path


def _safe_name(name):
    """A title or label turned into something every OS accepts as a filename."""
    keep = "".join(c if c.isalnum() or c in "-_.+" else "_" for c in str(name))
    return keep.strip("._") or "figure"


def _is_run_ids(sweep):
    """True for a run id or a list of them; False for a folder path."""
    if isinstance(sweep, (str, Path)):
        return False
    if isinstance(sweep, (int, np.integer)):
        return True
    return all(isinstance(r, (int, np.integer)) for r in sweep)


def _traces_from_db(run_ids, db_path):
    """One dict per run -- label, data, and where it came from."""
    if isinstance(run_ids, (int, np.integer)):
        run_ids = [run_ids]
    run_ids = [int(r) for r in run_ids]
    info = _run_info(run_ids, db_path)
    # Across several experiments (a cal and an uncal sweep, say) the
    # position alone is ambiguous, so the group goes in the label too.
    several = len({info[r]["experiment"] for r in run_ids}) > 1
    traces = []
    for run_id in run_ids:
        run = info[run_id]
        freq_hz, data = read_db.load_run(run_id, db_path)
        group = read_db._group(run)
        where = f"{group}, " if several else ""
        traces.append({
            "label": f"{run['name']} ({where}run {run_id})",
            "n_ports": 2 if len(data) == 4 else 1,
            "freq": freq_hz, "data": data, "run": run_id,
            "group": group, "where": _where(run),
        })
    title = (info[run_ids[0]]["experiment"] if not several
             else f"runs {min(run_ids)}-{max(run_ids)}")
    return traces, title


def _traces_from_folder(sweep_dir):
    """
    One dict per Touchstone file in a folder. A folder inside a Sweeps/
    tree (Sweeps/<serials>/<date>/<temp>/<group>/) says where it came
    from, so a saved figure still lands in the right figures/ folder.
    """
    sweep_dir = Path(sweep_dir)
    raw_dir = sweep_dir / "raw" if (sweep_dir / "raw").is_dir() else sweep_dir
    files = sorted(
        list(raw_dir.glob("*.s1p")) + list(raw_dir.glob("*.s2p")),
        key=lambda p: (_position_and_run(p)[1] is None, _position_and_run(p)[1]),
    )
    if not files:
        raise FileNotFoundError(f"No .s1p or .s2p files in {raw_dir}")

    parts = sweep_dir.resolve().parts
    where, group = (None, None, None), sweep_dir.name
    if read_db.DEFAULT_OUT_DIR_NAME in parts:
        after = parts[parts.index(read_db.DEFAULT_OUT_DIR_NAME) + 1:]
        if len(after) >= 3:
            where = tuple(after[:3])
        if len(after) >= 4:
            group = after[3]

    traces = []
    for path in files:
        freq_hz, data = read_touchstone(path)
        position, run = _position_and_run(path)
        label = position if run is None else f"{position} (run {run})"
        traces.append({
            "label": label, "n_ports": int(path.suffix[2:-1]),
            "freq": freq_hz, "data": data, "run": run,
            "group": group, "where": where,
        })
    return traces, f"{sweep_dir.parent.name} {sweep_dir.name}"


def _sweep_file_name(sparam, traces):
    """Default save=True name, e.g. S21_RF1_cal_runs31-34 or S21_RF1_cal+RF1_uncal_runs31-36."""
    groups = list(dict.fromkeys(t["group"] for t in traces if t["group"]))
    runs = [t["run"] for t in traces if t["run"] is not None]
    name = sparam
    if groups:
        name += "_" + ("+".join(groups) if len(groups) <= 2 else "mixed")
    if runs:
        name += (f"_run{runs[0]}" if len(runs) == 1
                 else f"_runs{min(runs)}-{max(runs)}")
    return name


def plot_sweep(sweep, sparam=None, ax=None, title=None, db_path=None,
               xlim=None, ylim=None, colors=None, labels=None, save=None):
    """
    Overlay every position of a finished sweep on one axes -- the view
    that actually tells you something about a switch.

    `sweep` is either the run ids run_twoport_sweep returned (read from
    the database -- no files needed), or a folder of .s1p/.s2p files:

        runs = run_twoport_sweep([1, 3, 5], ...)
        ax = plot_sweep(runs)
        ax = plot_sweep([43, 44, 45, 46])                    # any runs, any session
        ax = plot_sweep("Sweeps/SN0077/20260925/295K/RF3_cal")

    `db_path` defaults to mm4250_sweeps.db beside these files. `sparam`
    defaults to S21 where the data is 2-port (insertion loss, one line
    per channel) and S11 where it's 1-port. Runs from more than one
    experiment -- a cal and an uncal sweep together, say -- get their
    group in the legend so the two can be told apart.

    Styling -- all optional, all defaulting to what it did before:
      xlim=(0, 6)          x range in GHz
      ylim=(-100, 5)       y range in dB
      colors=[...]         one per line, in run order, or a dict keyed by
                           run id: {31: "black", 32: "tab:red"}
      labels=[...]         legend text, same forms as colors
      title="..."          replaces the automatic "S21 - <experiment>"
      ax=...               draw into your own axes, e.g. one of a row of
                           subplots

    save="name.png" writes the figure (PNG, PDF or SVG by the extension;
    .png if there's none) into figures/<serials>/<date>/<temp>/ -- as
    deep as all the runs agree, so two switches together go in figures/.
    save=True picks the name too, e.g. S21_RF1_cal+RF1_uncal_runs31-36.png.
    A path with folders in it is used exactly as given. With ax= the whole
    figure it belongs to is saved, so side-by-side subplots save as one.

    Plotting S21 with an isolated state in the set is how you read
    isolation off the plot: the connected channels sit near the top, the
    open state drops to the floor, and the gap between them is the
    number you want.

    Lines are ordered by run id, so the legend follows the order you
    measured in. Returns the Axes.
    """
    if _is_run_ids(sweep):
        traces, where = _traces_from_db(sweep, db_path)
    else:
        traces, where = _traces_from_folder(sweep)

    if sparam is None:
        sparam = "S21" if any(t["n_ports"] == 2 for t in traces) else "S11"

    if ax is None:
        _, ax = plt.subplots(figsize=(9, 5))

    plotted = [t for t in traces if sparam in t["data"]]
    for i, t in enumerate(plotted):
        key = t["run"] if t["run"] is not None else t["label"]
        ax.plot(t["freq"] / 1e9, _db(t["data"][sparam]),
                label=_pick(labels, i, key) or t["label"],
                color=_pick(colors, i, key), linewidth=1.3)

    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel(f"|{sparam}| (dB)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    ax.set_title(title or f"{sparam} - {where}")
    _limits(ax, xlim, ylim)
    _save(ax.figure, save, _sweep_file_name(sparam, plotted),
          [t["where"] for t in plotted])
    return ax
