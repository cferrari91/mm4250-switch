"""
What's on the switch's RF channels, after section A has measured and
corrected it. The resonator for now; an SNTJ part can go beside it.

    import duts
    set_id = duts.copy_set(cal)               # A4's set: raw standards + resonator runs -> resonator.db
    res = duts.correct_resonator(set_id, ideals)   # the e-cal, from resonator.db's own copy
    fit = duts.fit_resonator(res)
    duts.plot_resonator_fit(fit, save=True)
    duts.save_resonator(fit)                  # the fit -> Sweeps/duts/resonator.db too
    duts.list_resonator()
    duts.plot_resonator_vs_temp(save=True)    # every saved fit: f0, Q_0, Q_e, beta vs T

Only the fit needs scikit-rf, and it's imported there (_skrf), so `import duts`
works without it.

Where things live
-----------------
Section A records every e-cal set in mm4250_sweeps.db (it measures all
the DUT channels and the switch's internal standards together, and the
correction needs both). The resonator gets its own database,
resonator_db_path(): the lab's resonator project for this cooldown on the
DAQ (sweep_db.use_lab_databases), else Sweeps/duts/resonator.db beside
these files (QCoDeS like the others, so plottr,
read_db.list_runs and ecal.list_sets/find_set work on it), holding:

  * the raw runs: copy_set copies one set's internal-standard runs and the
    resonator channel's runs (all repeats) over from the main database,
    with all their metadata (ecal_set, roles, labels, VNA state, fridge
    reading), plus source_db / source_run_id / source_guid /
    source_run_timestamp to trace each back. Other channels (the SNTJ, ...)
    aren't copied. The copies get their own run ids, so always correct them
    through correct_resonator (it reads this database). QCoDeS's snapshot
    (full instrument state) isn't copied; the original run still has it.
  * the fits: one run per saved fit (save_resonator), holding the
    corrected S11 over the whole sweep, with the fit's numbers and where
    they came from as metadata. It's tagged fit_set, not ecal_set, so
    ecal.find_set never mistakes it for part of a set. A fit can be redone
    from that trace alone (load_resonator).

So everything about the resonator can be redone from this one file,
without the main database. Backups go to Sweeps/duts/db_backups/.

The fit
-------
Same as q_fit_resonator_20261008.ipynb (adapted from ____'s
q_measurements.ipynb; scikit-rf Qfactor, NPL report MAT 58):

  1. rough fit to find f_L and Q_L
  2. crop to SPAN_MULT * f_L / Q_L around f_L
  3. fit again on that window
  4. Q_0 from the fitted circle, then Q_e = Q_0 Q_L / (Q_0 - Q_L) and
     beta = Q_0 / Q_L - 1

with that notebook's settings: reflection, NLQFIT7, window 2 f_L/Q_L, no
edge normalisation, A=None (its first cell says why each differs from
____'s). Two changes, both so it works cold too:
  - The starting guesses come from the data (the dip and its half-depth
    width) instead of a fixed 549 MHz / Q_L 40. Pass f0_guess / q_guess
    to override.
  - Step 1 fits FIRST_SPAN_MULT * f0/Q around the guess instead of the
    whole sweep, so a narrow cold dip isn't swamped by the rest of A's
    140 MHz sweep. Warm (Q_L ~ 40) that's wider than the whole
    2026-10-08 sweep, so those numbers don't change.

Resolution: the fit window holds about SPAN_MULT * f/Q_L / (frequency
step) points. A's default sweep steps ~70 kHz: plenty warm, too coarse if
Q_L reaches the thousands cold. Below MIN_WINDOW_POINTS the fit warns;
take another A4 set with a narrow DUT_SPAN around the resonance then (the
NIST definitions interpolate to any span).
"""

import re
import warnings
from pathlib import Path

import numpy as np

import ecal
import read_db

HERE = Path(__file__).resolve().parent

# Fit settings (q_fit_resonator_20261008.ipynb)
RES_TYPE = "reflection"      # ____: "reflection_method2"
A_SCALE = None               # None = scale to the fitted off-resonance |S_V|
METHOD = "NLQFIT7"           # adds a line-length term; every trace has some cable in it
SPAN_MULT = 2.0              # fit window = SPAN_MULT * f_L / Q_L
FIRST_SPAN_MULT = 8.0        # step-1 window around the data's guess
NORMALIZE_EDGES = False      # ____'s baseline step; wrong for a broad dip (see the notebook)
MIN_WINDOW_POINTS = 40       # fewer than this in the fit window -> warning

DUT_DIR_NAME = "duts"
RESONATOR_DB_NAME = "resonator.db"
RESONATOR_EXPERIMENT = "resonator"

# Plot style (the Q notebook's)
INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
SERIES = (INK, "#2a78d6", "#1baf7a", "#c2572b", "#7a52c7")
STYLE = {
    "pdf.fonttype": 42, "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 12, "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK3, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "legend.frameon": False,
}


def _skrf():
    """scikit-rf, imported only when a fit runs, so `import duts` works without it."""
    try:
        import skrf as rf
        from skrf.qfactor import Qfactor
    except ImportError as e:
        raise ImportError("the resonator fit needs scikit-rf: pip install scikit-rf") from e
    return rf, Qfactor


def resonator_db_path():
    """
    The resonator's database: the lab's resonator project for this cooldown
    once sweep_db.use_lab_databases() has run, otherwise
    Sweeps/duts/resonator.db beside these files.
    """
    if read_db.DB_OVERRIDES["resonator"] is not None:
        return read_db.DB_OVERRIDES["resonator"]
    return HERE / read_db.DEFAULT_OUT_DIR_NAME / DUT_DIR_NAME / RESONATOR_DB_NAME


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

def _pick_channel(result, channel):
    """The resonator's channel in an e-cal result: `channel`, else the one labelled resonator."""
    ports = result["ports"]
    labels = result.get("labels") or {}
    if channel is not None:
        if channel not in ports:
            raise KeyError(f"RF{channel} isn't in this set (it has {sorted(ports)})")
        return channel
    hits = [ch for ch, text in labels.items() if "resonator" in str(text).lower() and ch in ports]
    if len(hits) == 1:
        return hits[0]
    if len(ports) == 1:
        return next(iter(ports))
    have = ", ".join(f"RF{ch}" + (f" ({labels[ch]})" if ch in labels else "") for ch in sorted(ports))
    raise ValueError(f"Which channel is the resonator? Pass channel=...; this set has {have}")


def _set_channel(set_, channel):
    """The resonator's channel in an e-cal set (run_ecal_set / ecal.find_set shape)."""
    return _pick_channel({"ports": set_["repeats"][0]["ports"], "labels": set_.get("labels")},
                         channel)


def _load(source, channel, label):
    """(freq_hz, gamma, metadata, label) from an e-cal result, a .s1p path or (freq, gamma)."""
    if isinstance(source, dict) and "ports" in source:
        ch = _pick_channel(source, channel)
        dut = (source.get("labels") or {}).get(ch)
        ideals = ecal._ideals_name(source["ideals_dir"]) if source.get("ideals_dir") else None
        vna_cal = source.get("vna_cal")
        meta = {"set": source.get("set"), "channel": ch, "dut_label": dut,
                "switch_serials": source.get("switch_serials"), "date_str": source.get("date_str"),
                "temp_str": source.get("temp_str"), "cooldown": source.get("cooldown"),
                "ideals": ideals, "sparam": source.get("sparam"),
                "vna_cal": None if vna_cal is None else bool(vna_cal)}
        default = f"RF{ch}" + (f" ({dut})" if dut else "") + f", e-cal ({ideals})"
        return (np.asarray(source["freq"], float), np.asarray(source["ports"][ch], complex),
                meta, label or default)
    if isinstance(source, (str, Path)):
        f, g = ecal.read_s1p(source)
        return f, g, {"source_file": str(source)}, label or Path(source).name
    f, g = source
    return np.asarray(f, float), np.asarray(g, complex), {}, label or "trace"


# QCoDeS's own columns in the runs table; everything after them is run metadata.
_QCODES_RUN_COLUMNS = frozenset((
    "run_id", "exp_id", "name", "result_table_name", "result_counter", "run_timestamp",
    "completed_timestamp", "is_completed", "parameters", "guid", "run_description", "snapshot",
    "captured_run_id", "captured_counter", "parent_datasets"))


def _copied_guids(db_path):
    """source_guid of every run already copied into db_path (empty if none or no file)."""
    if not Path(db_path).is_file():
        return set()
    con, _ = read_db._connect(db_path)
    try:
        if "source_guid" not in read_db._columns(con, "runs"):
            return set()
        return {g for (g,) in con.execute("SELECT source_guid FROM runs WHERE source_guid IS NOT NULL")}
    finally:
        con.close()


def _has_raw(set_id, channel, db_path):
    """Whether db_path already holds this set's runs for `channel`."""
    try:
        return channel in ecal.find_set(set_id, db_path)["repeats"][0]["ports"]
    except (FileNotFoundError, LookupError):
        return False


def copy_set(cal=None, channel=None, main_db_path=None, db_path=None):
    """
    Copy one e-cal set's raw runs for the resonator into its own database:
    every internal-standard run (before and after, all repeats) and the
    resonator channel's runs, with all their metadata. Runs already copied
    are skipped, so calling it twice is harmless. Returns the set id, for
    correct_resonator.

    `cal`: A4's `cal`, a set id string, or None for the most recent set in
    the main database. `channel`: the resonator's channel; None = the one
    labelled "resonator" in DUTS. `main_db_path` / `db_path`: the main
    database (mm4250_sweeps.db beside this file) and the resonator's
    (Sweeps/duts/resonator.db). The main database is only read.
    """
    import qcodes as qc
    import sweep_db

    main_db_path = Path(main_db_path) if main_db_path is not None else read_db.default_db_path()
    db_path = Path(db_path) if db_path is not None else resonator_db_path()
    set_id = cal["set"] if isinstance(cal, dict) else cal
    set_ = ecal.find_set(set_id, main_db_path)       # always by id, with the main db's run ids
    ch = _set_channel(set_, channel)
    ids = []
    for block in set_["repeats"]:
        ids += list(block["before"].values()) + list(block["after"].values())
        ids.append(block["ports"][ch])
    ids.sort()

    con, _ = read_db._connect(main_db_path)
    try:
        cols = [r[1] for r in con.execute('PRAGMA table_info("runs")')]
        rows = {}
        for rid in ids:
            row = dict(zip(cols, con.execute("SELECT * FROM runs WHERE run_id = ?", (rid,)).fetchone()))
            row["_exp"], row["_sample"] = con.execute(
                "SELECT name, sample_name FROM experiments WHERE exp_id = ?", (row["exp_id"],)).fetchone()
            rows[rid] = row
    finally:
        con.close()

    done = _copied_guids(db_path)
    todo = [rid for rid in ids if rows[rid]["guid"] not in done]
    where = f"set {set_['set']}, RF{ch}"
    if not todo:
        print(f"{where}: all {len(ids)} raw runs already in {db_path.name}")
        return set_["set"]

    previous = qc.config.core.db_location
    try:
        exps = {}
        for rid in todo:
            row = rows[rid]
            key = (row["_exp"], row["_sample"])
            if key not in exps:
                exps[key], db_path = sweep_db._open_experiment(row["_sample"], db_path, row["_exp"])
            freq, data = read_db.load_run(rid, main_db_path)
            meta = {k: v for k, v in row.items()
                    if k not in _QCODES_RUN_COLUMNS and not k.startswith("_")}
            meta.update(source_db=main_db_path.name, source_run_id=rid, source_guid=row["guid"],
                        source_run_timestamp=row["run_timestamp"])
            sweep_db.record_measurement(row["name"], freq, data, exps[key], **meta)
    finally:
        qc.config.core.db_location = previous     # don't leave QCoDeS pointed at this db
    sweep_db._checkpoint(db_path)
    sweep_db._safe_backup(db_path)
    print(f"{where}: copied {len(todo)} raw run(s) into {db_path.name}"
          + (f" ({len(ids) - len(todo)} were already there)" if len(todo) < len(ids) else ""))
    return set_["set"]


def correct_resonator(set_id, ideals_dir, sparam="S21", db_path=None, **kwargs):
    """
    ecal.correct_set on the resonator database's copy of a set (copy_set
    first). Same arguments and result as correct_set; `set_id` may also be
    A4's `cal` (only its id is used, since the copies have their own run ids).
    """
    set_id = set_id["set"] if isinstance(set_id, dict) else set_id
    return ecal.correct_set(set_id, ideals_dir, sparam=sparam,
                            db_path=db_path if db_path is not None else resonator_db_path(),
                            **kwargs)


def guess_resonance(freq_hz, gamma):
    """
    Rough (f0, Q_L) from |S11| alone: f0 at the deepest point, Q_L from the
    width where the dip is half as deep (relative to the median level).
    Only a starting point for the fit. Q_L is None if there's no dip.
    """
    mag = np.abs(gamma)
    i0 = int(np.argmin(mag))
    base = float(np.median(mag))
    if base <= mag[i0]:
        return float(freq_hz[i0]), None
    half = (base + mag[i0]) / 2
    lo = hi = i0
    while lo > 0 and mag[lo - 1] < half:
        lo -= 1
    while hi < len(mag) - 1 and mag[hi + 1] < half:
        hi += 1
    step = float(np.median(np.diff(freq_hz)))
    width = max(float(freq_hz[hi] - freq_hz[lo]), 2 * step)
    return float(freq_hz[i0]), float(freq_hz[i0] / width)


def _network(rf, freq_hz, gamma, name):
    return rf.Network(frequency=rf.Frequency.from_f(freq_hz, unit="hz"),
                      s=np.asarray(gamma).reshape(-1, 1, 1), name=name)


def _fitted_s(res, freq_hz, qf):
    # The fitted S11 curve, for plotting. skrf 2.1's fitted_s / fitted_network
    # leave out NLQFIT7's line-length term, so for a trace with cable in it they
    # draw a curve rotated away from the data even when the fit is good.
    # MAT 58 eq. (26), as skrf fits it:
    #   S = (m1 + j m2 + (m3 + j m4) / (1 + 2j Q_L (f - f_L) / f_L)) * exp(j m7a (f - f_L))
    if "m7a" not in res:
        return qf.fitted_s(res)
    y = 1 / (1 + 2j * res["Q_L"] * (freq_hz - res["f_L"]) / res["f_L"])
    return ((res["m1"] + 1j * res["m2"] + (res["m3"] + 1j * res["m4"]) * y)
            * np.exp(1j * res["m7a"] * (freq_hz - res["f_L"])))


def fit_resonator(source, channel=None, label=None, *, f0_guess=None, q_guess=None,
                  res_type=RES_TYPE, A=A_SCALE, method=METHOD, span_mult=SPAN_MULT,
                  first_span_mult=FIRST_SPAN_MULT, normalize_edges=NORMALIZE_EDGES,
                  verbose=True):
    """
    Fit a reflection resonator's Q. `source` is one of:

        an e-cal result     A5's `result` (ecal.correct_set / correct_and_plot).
                            `channel` picks the port; None = the channel
                            labelled "resonator" in DUTS (or the only one).
        a .s1p path         e.g. the resonator alone on the VNA's own cal,
                            to overlay as a reference.
        (freq_hz, gamma)    anything else, e.g. from load_resonator.

    Returns a dict: f0_MHz, Q_L, Q_0, Q_e, beta, rms, n_window, window_MHz,
    the windowed data and fit curve (freq, data, fit), the whole trace
    (trace_freq, trace), the settings, and for an e-cal result where it
    came from (set, channel, dut_label, temp_str, ideals, ...).
    """
    rf, Qfactor = _skrf()
    freq, gamma, meta, label = _load(source, channel, label)
    order = np.argsort(freq)
    freq, gamma = freq[order], gamma[order]

    g_f0, g_q = guess_resonance(freq, gamma)
    f0 = float(f0_guess) if f0_guess else g_f0
    q = float(q_guess) if q_guess else (g_q or 40.0)
    ntwk = _network(rf, freq, gamma, label)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        # 1. rough fit near the guess, to find f_L and Q_L
        half = first_span_mult * f0 / q / 2
        if f0 - half <= freq[0] and f0 + half >= freq[-1]:
            rough = ntwk
        else:
            rough = ntwk[f"{max(f0 - half, freq[0])}-{min(f0 + half, freq[-1])}Hz"]
        qf = Qfactor(rough.s11, res_type=res_type, Q_L0=q, f_L0=f0)
        qf.fit(method=method)

        # 2. crop to a window set by the loaded Q
        span = span_mult * qf.f_L / qf.Q_L
        lo, hi = qf.f_L - span / 2, qf.f_L + span / 2
        win = ntwk[f"{lo}-{hi}Hz"].copy()
        if len(win) < 5:
            raise RuntimeError(
                f"{label}: only {len(win)} points between {lo / 1e6:.3f} and {hi / 1e6:.3f} MHz, "
                "too few to fit. Sweep a narrower span around the resonance (more points per MHz).")

        if normalize_edges:      # ____'s baseline step
            mags = np.abs(win.s[:, 0, 0])
            edge = max(1, int(len(mags) * 0.05))
            win.s = win.s / np.mean(np.concatenate((mags[:edge], mags[-edge:])))

        # 3. fit again on the window
        qf2 = Qfactor(win.s11, res_type=res_type)
        res = qf2.fit(method=method)

        # 4. unloaded Q, then external Q and coupling
        q0 = float(qf2.Q_unloaded(A=A))
    ql = float(qf2.Q_L)
    qe = q0 * ql / (q0 - ql) if q0 > ql else float("inf")
    n = len(win)

    fit = {"label": label, **meta,
           "f0_MHz": float(qf2.f_L) / 1e6, "Q_L": ql, "Q_0": q0, "Q_e": qe, "beta": q0 / ql - 1,
           "rms": float(np.ravel(res.RMS_Error)[0]), "n_window": n,
           "window_MHz": (float(win.f[0]) / 1e6, float(win.f[-1]) / 1e6),
           "freq": win.f, "data": win.s[:, 0, 0], "fit": _fitted_s(res, win.f, qf2),
           "trace_freq": freq, "trace": gamma,
           "settings": {"res_type": res_type, "A": A, "method": method,
                        "span_mult": span_mult, "normalize_edges": bool(normalize_edges)}}

    if verbose:
        print(f"{label}: f0 {fit['f0_MHz']:.3f} MHz  Q_L {ql:.1f}  Q_0 {q0:.1f}  "
              f"Q_e {qe:.1f}  beta {fit['beta']:.2f}  (fit rms {fit['rms']:.1e}, {n} points)")
        step = float(np.median(np.diff(freq)))
        if n < MIN_WINDOW_POINTS:
            print(f"  [warning] only {n} points in the fit window ({span / 1e6:.3g} MHz at "
                  f"{step / 1e3:.0f} kHz per point); the Q isn't trustworthy. Take another A4 set "
                  f"with a narrow DUT_SPAN around {fit['f0_MHz']:.2f} MHz, enough for "
                  f">= {MIN_WINDOW_POINTS} points across {span / 1e6:.3g} MHz.")
        if lo < freq[0] or hi > freq[-1]:
            print(f"  [warning] the fit window ({lo / 1e6:.2f}-{hi / 1e6:.2f} MHz) runs past the "
                  "sweep, so the fit only sees part of the resonance; widen the span.")
        if q0 <= ql:
            print("  [warning] Q_0 <= Q_L: the fit found no coupling (beta <= 0), so Q_e "
                  "means nothing here. Look at the plot.")
    return fit


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def figure_dir(fit):
    """figures/<serials>/<date>/<temp>/ for an e-cal fit (the slide-figure layout)."""
    if not fit.get("switch_serials"):
        raise ValueError("this fit isn't from an e-cal result; pass save=<path> instead of True")
    return HERE / "figures" / fit["switch_serials"] / fit["date_str"] / fit["temp_str"]


def _save(fig, save, default_base):
    """Write <base>.pdf and <base>.png. `save` True = default_base; a path = that (any suffix dropped)."""
    if not save:
        return None
    base = str(default_base() if save is True else save)
    for ext in (".pdf", ".png"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
    Path(base).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(base + ".pdf")
    fig.savefig(base + ".png", dpi=150)
    print("saved", base + ".pdf")
    return Path(base + ".pdf")


def _note(fit):
    s = fit["settings"]
    parts = []
    if fit.get("set"):
        parts.append(f"MM4250 {fit['switch_serials']} · {fit['temp_str']} · {fit['date_str']} · "
                     f"e-cal set {fit['set']} ({fit['ideals']})")
    parts.append(f"skrf Qfactor {s['res_type']}, {s['method']}, window {s['span_mult']:g} f_L/Q_L")
    return " · ".join(parts)


def plot_resonator_fit(fits, save=None, title=None):
    """
    The Q notebook's one-page figure: |S11| data vs fit, the complex plane,
    and a table, for one fit or a list (the first is the main one; add a
    reference fit after it to overlay, e.g. the resonator alone on the
    VNA's cal). Only each fit's window is drawn.

    save: True -> figures/<serials>/<date>/<temp>/resonator_q_fit_RF<n>_<set>.pdf/.png
    (needs the first fit to be from an e-cal result), or a path. Returns the figure.
    """
    import matplotlib.pyplot as plt

    fits = [fits] if isinstance(fits, dict) else list(fits)
    main = fits[0]
    with plt.rc_context(STYLE):
        fig = plt.figure(figsize=(11, 6.1875))
        gs = fig.add_gridspec(2, 2, height_ratios=[2.3, 1], width_ratios=[1.6, 1], left=0.07,
                              right=0.97, top=0.85, bottom=0.04, hspace=0.32, wspace=0.18)
        ax_m, ax_c = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
        ax_t = fig.add_subplot(gs[1, :])
        th = np.linspace(0, 2 * np.pi, 400)
        ax_c.plot(np.cos(th), np.sin(th), color=GRID, lw=1)
        for i, r in enumerate(fits):
            col, f = SERIES[i % len(SERIES)], r["freq"] / 1e6
            step = max(1, len(f) // 100)
            ax_m.plot(f[::step], 20 * np.log10(np.abs(r["data"][::step])), "o", ms=3.2,
                      color=col, alpha=0.55, mew=0)
            ax_m.plot(f, 20 * np.log10(np.abs(r["fit"])), color=col, lw=1.6,
                      label=f"{r['label']}: $Q_0$ {r['Q_0']:.1f}, $Q_e$ {r['Q_e']:.1f}")
            ax_c.plot(r["data"].real[::step], r["data"].imag[::step], "o", ms=3.2, color=col,
                      alpha=0.55, mew=0)
            ax_c.plot(r["fit"].real, r["fit"].imag, color=col, lw=1.6)
        ax_m.set_xlabel("Frequency (MHz)")
        ax_m.set_ylabel("|S11| (dB)")
        ax_m.legend(loc="lower left", fontsize=10)
        lo, hi = ax_m.get_ylim()
        ax_m.set_ylim(lo - 0.35 * (hi - lo), hi)
        ax_c.set_aspect("equal")
        ax_c.set_xlim(-1.1, 1.1)
        ax_c.set_ylim(-1.1, 1.1)
        ax_c.set_xlabel("Re S11")
        ax_c.set_ylabel("Im S11")
        ax_c.set_title("Complex plane (dots data, lines fit)", fontsize=11, color=INK2)
        ax_t.axis("off")
        tab = ax_t.table(
            cellText=[[r["label"], f"{r['f0_MHz']:.2f}", f"{r['Q_L']:.1f}", f"{r['Q_0']:.1f}",
                       f"{r['Q_e']:.1f}", f"{r['beta']:.2f}", f"{r['rms']:.1e}"] for r in fits],
            colLabels=["Measurement", "$f_0$ (MHz)", "$Q_L$", "$Q_0$", "$Q_e$", r"$\beta$", "fit rms"],
            colWidths=[0.38, 0.11, 0.08, 0.08, 0.08, 0.08, 0.1], loc="center", cellLoc="center")
        tab.auto_set_font_size(False)
        tab.set_fontsize(10.5)
        tab.scale(1, 1.4)
        for (i, j), c in tab.get_celld().items():
            c.set_edgecolor(GRID)
            c.get_text().set_color(INK if i else INK2)
            if j == 0 and i:
                c.get_text().set_ha("left")
                c.PAD = 0.02
        temp = f" ({main['temp_str']})" if main.get("temp_str") else ""
        fig.suptitle(title or f"Resonator Q fit{temp}", x=0.07, ha="left", fontsize=16,
                     color=INK, y=0.975)
        fig.text(0.07, 0.915, _note(main), fontsize=9, color=INK3)
        _save(fig, save, lambda: figure_dir(main) / f"resonator_q_fit_RF{main['channel']}_{main['set']}")
    return fig


def plot_resonator_detail(fit, save=None):
    """
    ____'s two-panel data-vs-fit plot for one fit: |S11| (dB) and unwrapped
    phase over the fit window. save: True -> ..._detail beside
    plot_resonator_fit's file, or a path. Returns the figure.
    """
    import matplotlib.pyplot as plt

    f = fit["freq"] / 1e6
    with plt.rc_context(STYLE):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
        ax1.plot(f, 20 * np.log10(np.abs(fit["data"])), color=INK, lw=1.6, label="Data")
        ax1.plot(f, 20 * np.log10(np.abs(fit["fit"])), "--", color=SERIES[1], lw=1.6, label="Fit")
        ax1.set_ylabel("|S11| (dB)")
        ax1.legend(loc="lower left", fontsize=10)
        ax2.plot(f, np.degrees(np.unwrap(np.angle(fit["data"]))), color=INK, lw=1.6)
        ax2.plot(f, np.degrees(np.unwrap(np.angle(fit["fit"]))), "--", color=SERIES[1], lw=1.6)
        ax2.set_ylabel("Phase (deg)")
        ax2.set_xlabel("Frequency (MHz)")
        ax1.set_title(f"{fit['label']}:  $f_0$ {fit['f0_MHz']:.2f} MHz, $Q_L$ {fit['Q_L']:.1f}, "
                      f"$Q_0$ {fit['Q_0']:.1f}, $Q_e$ {fit['Q_e']:.1f}, $\\beta$ {fit['beta']:.2f}",
                      fontsize=11, color=INK2, loc="left")
        fig.tight_layout()
        _save(fig, save, lambda: figure_dir(fit) / f"resonator_q_fit_RF{fit['channel']}_{fit['set']}_detail")
    return fig


# ---------------------------------------------------------------------------
# The resonator's own database
# ---------------------------------------------------------------------------

# Metadata saved on each fit's run (and read back by list_resonator).
COLUMNS = ("fit_set", "channel", "dut_label", "switch_serials", "date_str", "temp_str",
           "mxc_temp_k", "cooldown", "ideals", "sparam", "vna_cal", "f0_MHz", "Q_L", "Q_0",
           "Q_e", "beta", "fit_rms", "n_window", "window_lo_MHz", "window_hi_MHz",
           "res_type", "fit_method", "span_mult", "normalize_edges", "a_scale", "label", "note")


def _mxc_temp(set_id, channel, db_path=None):
    """Mean mxc_temp_k of this channel's runs in the e-cal set (in db_path), or None."""
    try:
        con, _ = read_db._connect(db_path)
    except FileNotFoundError:
        return None
    try:
        have = read_db._columns(con, "runs")
        if not {"mxc_temp_k", "ecal_set"} <= have:
            return None
        vals = [r[0] for r in con.execute(
            "SELECT mxc_temp_k FROM runs WHERE ecal_set = ? AND name = ? AND mxc_temp_k IS NOT NULL",
            (set_id, f"RF{channel}"))]
    finally:
        con.close()
    return float(np.mean(vals)) if vals else None


def _same_fit(row, fit):
    s = fit["settings"]
    return (row["fit_set"] == fit["set"] and row["channel"] == fit["channel"]
            and row["ideals"] == fit["ideals"] and row["res_type"] == s["res_type"]
            and row["fit_method"] == s["method"] and row["span_mult"] == s["span_mult"]
            and bool(row["normalize_edges"]) == s["normalize_edges"])


def save_resonator(fit, note="", db_path=None, main_db_path=None, allow_duplicate=False):
    """
    Save one fit of an e-cal result into the resonator's database
    (Sweeps/duts/resonator.db unless `db_path`): the corrected S11 over the
    whole sweep, plus the fit's numbers and where they came from. If that
    database doesn't have the set's raw runs yet, they're copied over first
    (copy_set), so the raw data always sits beside the fit. The fridge
    reading (mxc_temp_k) comes from that channel's raw runs if A4 recorded
    one. Returns the new run id.

    Refuses to save the same set + channel + definitions + fit settings a
    second time (pass allow_duplicate=True to keep both; the newest wins
    in plot_resonator_vs_temp). Nothing already saved is ever changed.
    """
    import qcodes as qc
    import sweep_db

    if not fit.get("set"):
        raise ValueError(f"{fit['label']!r} isn't a fit of an e-cal result, so it has no set, "
                         "channel or temperature to file it under. Only A5's result is saved here.")
    db_path = Path(db_path) if db_path is not None else resonator_db_path()
    if db_path.is_file() and not allow_duplicate:
        for row in list_resonator(db_path, verbose=False):
            if _same_fit(row, fit):
                raise ValueError(f"Already saved as run {row['run_id']} in {db_path.name} "
                                 "(same set, channel, definitions and fit settings). "
                                 "Pass allow_duplicate=True to save it again.")

    if not _has_raw(fit["set"], fit["channel"], db_path):
        try:
            copy_set(fit["set"], channel=fit["channel"], main_db_path=main_db_path, db_path=db_path)
        except (FileNotFoundError, LookupError) as e:
            print(f"  [warning] couldn't copy the raw runs ({e}); saving the fit without them")

    mxc = _mxc_temp(fit["set"], fit["channel"], db_path)
    if mxc is None:
        mxc = _mxc_temp(fit["set"], fit["channel"], main_db_path)
    s = fit["settings"]
    meta = {"dut": "resonator", "fit_set": fit["set"], "channel": fit["channel"],
            "dut_label": fit.get("dut_label"), "switch_serials": fit["switch_serials"],
            "date_str": fit["date_str"], "temp_str": fit["temp_str"],
            "mxc_temp_k": mxc,
            "cooldown": fit.get("cooldown"), "ideals": fit.get("ideals"),
            "sparam": fit.get("sparam"),
            "vna_cal": None if fit.get("vna_cal") is None else int(fit["vna_cal"]),
            "f0_MHz": fit["f0_MHz"], "Q_L": fit["Q_L"], "Q_0": fit["Q_0"],
            "Q_e": fit["Q_e"] if np.isfinite(fit["Q_e"]) else None, "beta": fit["beta"],
            "fit_rms": fit["rms"], "n_window": fit["n_window"],
            "window_lo_MHz": fit["window_MHz"][0], "window_hi_MHz": fit["window_MHz"][1],
            "res_type": s["res_type"], "fit_method": s["method"], "span_mult": s["span_mult"],
            "normalize_edges": int(s["normalize_edges"]), "a_scale": str(s["A"]),
            "label": fit["label"], "note": note or None}

    previous = qc.config.core.db_location
    try:
        exp, db_path = sweep_db._open_experiment(fit["switch_serials"], db_path, RESONATOR_EXPERIMENT)
        ds = sweep_db.record_measurement(f"RF{fit['channel']}", fit["trace_freq"], fit["trace"],
                                         exp, **meta)
        run_id = ds.run_id
    finally:
        qc.config.core.db_location = previous     # don't leave QCoDeS pointed at this db
    sweep_db._checkpoint(db_path)
    sweep_db._safe_backup(db_path)
    print(f"Saved the fit as run {run_id} in {db_path.name}")
    return run_id


def list_resonator(db_path=None, verbose=True):
    """
    Every fit saved in the resonator's database, oldest first, as a list of
    dicts (run_id, run_timestamp, and COLUMNS). verbose prints a table.
    The raw runs copied in by copy_set aren't listed; ecal.list_sets(
    resonator_db_path()) lists those sets.
    """
    con, db_path = read_db._connect(db_path if db_path is not None else resonator_db_path())
    try:
        have = read_db._columns(con, "runs")
        if "fit_set" in have:
            sel = ", ".join(c if c in have else "NULL" for c in COLUMNS)
            rows = con.execute(f"SELECT run_id, run_timestamp, {sel} FROM runs "
                               "WHERE fit_set IS NOT NULL ORDER BY run_id").fetchall()
        else:
            rows = []
    finally:
        con.close()
    out = [dict(zip(("run_id", "run_timestamp") + COLUMNS, r)) for r in rows]
    if verbose:
        print(f"{'run':>4} {'set':22s} {'ch':>3} {'temp':>6} {'T (K)':>7} {'ideals':14s} "
              f"{'f0 MHz':>9} {'Q_L':>7} {'Q_0':>7} {'Q_e':>7} {'beta':>6}")
        for r in out:
            tk = "" if r["mxc_temp_k"] is None else f"{r['mxc_temp_k']:.3g}"
            qe = "" if r["Q_e"] is None else f"{r['Q_e']:.1f}"
            print(f"{r['run_id']:>4} {str(r['fit_set']):22s} {r['channel']:>3} {str(r['temp_str']):>6} "
                  f"{tk:>7} {str(r['ideals']):14s} {r['f0_MHz']:9.3f} {r['Q_L']:7.1f} "
                  f"{r['Q_0']:7.1f} {qe:>7} {r['beta']:6.2f}")
    return out


def load_resonator(run_id, db_path=None):
    """
    One saved fit's corrected trace and its metadata: (freq_hz, gamma, row).
    Refit with fit_resonator((freq_hz, gamma), label=...).
    """
    db_path = db_path if db_path is not None else resonator_db_path()
    freq, data = read_db.load_run(run_id, db_path)
    row = next(r for r in list_resonator(db_path, verbose=False) if r["run_id"] == run_id)
    return freq, data["S11"], row


def temperature_k(row):
    """A saved fit's temperature in kelvin: the fridge reading if recorded, else from temp_str."""
    if row.get("mxc_temp_k") is not None:
        return float(row["mxc_temp_k"])
    m = re.fullmatch(r"\s*([\d.]+)\s*(mK|K)\s*", str(row.get("temp_str") or ""))
    if not m:
        return None
    value = float(m.group(1))
    return value / 1000 if m.group(2) == "mK" else value


def plot_resonator_vs_temp(db_path=None, save=None, channel=None, ideals=None):
    """
    f0, Q_0, Q_e and beta against temperature, from every fit saved in the
    resonator's database (log temperature axis; the fridge reading where
    recorded, else the set's temp label). One series per definitions
    choice (and channel, if more than one); filter with `ideals` /
    `channel`. A set saved more than once counts once (the newest).

    save: True -> figures/<serials>/resonator_qfit_vs_temp.pdf/.png, or a
    path. Returns (figure, rows plotted).
    """
    import matplotlib.pyplot as plt

    rows = list_resonator(db_path, verbose=False)
    if channel is not None:
        rows = [r for r in rows if r["channel"] == channel]
    if ideals is not None:
        rows = [r for r in rows if r["ideals"] == ideals]
    newest = {}
    for r in rows:                                    # oldest first, so later ones win
        key = (r["fit_set"], r["channel"], r["ideals"], r["res_type"], r["fit_method"],
               r["span_mult"], r["normalize_edges"])
        newest[key] = r
    rows = [r for r in newest.values() if temperature_k(r) is not None]
    skipped = len(newest) - len(rows)
    if not rows:
        raise LookupError("No saved fits with a temperature to plot")
    if skipped:
        print(f"  [note] {skipped} fit(s) left out: no fridge reading and a temp label that isn't 'NK'/'NmK'")

    channels = sorted({r["channel"] for r in rows})
    groups = {}
    for r in rows:
        name = str(r["ideals"]) + (f", RF{r['channel']}" if len(channels) > 1 else "")
        groups.setdefault(name, []).append(r)

    panels = (("f0_MHz", "$f_0$ (MHz)"), ("Q_0", "$Q_0$ (internal)"),
              ("Q_e", "$Q_e$ (external)"), ("beta", r"$\beta$ (coupling)"))
    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True)
        for i, (name, rs) in enumerate(sorted(groups.items())):
            rs = sorted(rs, key=temperature_k)
            t = [temperature_k(r) for r in rs]
            for ax, (key, _) in zip(axes.flat, panels):
                y = [np.nan if r[key] is None else r[key] for r in rs]
                ax.plot(t, y, "o-", ms=5, lw=1.2, color=SERIES[i % len(SERIES)], label=name)
        for ax, (_, ylabel) in zip(axes.flat, panels):
            ax.set_xscale("log")
            ax.set_ylabel(ylabel)
        axes[1, 1].axhline(1, color=INK3, lw=1, ls="--")
        axes[1, 1].text(0.99, 1, " critical coupling", transform=axes[1, 1].get_yaxis_transform(),
                        ha="right", va="bottom", fontsize=9, color=INK3)
        for ax in axes[1]:
            ax.set_xlabel("Temperature (K)")
        axes[0, 0].legend(fontsize=10, title="definitions", title_fontsize=10)
        serials = rows[0]["switch_serials"]
        fig.suptitle(f"Resonator vs temperature ({serials}, through the switch, e-cal)", x=0.07,
                     ha="left", fontsize=15, color=INK)
        fig.tight_layout()
        _save(fig, save, lambda: HERE / "figures" / serials / "resonator_qfit_vs_temp")
    return fig, rows
