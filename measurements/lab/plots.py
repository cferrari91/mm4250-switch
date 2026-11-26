"""
Plots from what db.py, vna.py and analysis.py return (no instruments, no e-cal math):

    plot_take(db.load_take(None, DB))     # a take: raw and corrected, |S| and phase per channel
    plot_check(result)                     # a stuck or thru check: raw |S21| + one bar per position
    plot_runs(db.load_runs([12, 13], DB))  # any runs
    plot_mag_smith(traces)                 # |S| beside a Smith chart (analysis.traces_from_*)

Every function returns the figure(s); save="path.png" also writes it (never overwrites).
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def _db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-30))


def _save(fig, save):
    if save:
        path = Path(save)
        if path.exists():
            print(f"  [warning] {path} exists; figure not saved")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=150)
        print(f"  saved {path}")


def plot_take(take, definitions=None, save=None):
    """
    A take: left, the raw trace the correction uses (mean over repeats); right, the corrected
    reflection at each channel's connector. |S| in dB on top, phase below. `definitions`
    picks which corrected runs if there are several (None = the newest).
    """
    sparam = take["cfg"]["correct_sparam"]
    ports = take["cfg"]["ports"]
    corr = take["corrected"]
    if corr:
        definitions = definitions or corr[-1]["definitions"]
        corr = [c for c in corr if c["definitions"] == definitions]
    fig, axes = plt.subplots(2, 2, sharex=True, figsize=(12, 7), squeeze=False)
    f = take["freq"] / 1e6
    for ch in take["channels"]:
        label = f"RF{ch} ({ports.get(f'RF{ch}', '?')})"
        raw = np.mean([b["ports"][ch]["data"][sparam] for b in take["repeats"] if ch in b["ports"]],
                      axis=0)
        (line,) = axes[0, 0].plot(f, _db(raw), label=label)
        axes[1, 0].plot(f, np.rad2deg(np.angle(raw)), color=line.get_color())
        mine = [c for c in corr if c["channel"] == ch]
        if mine:
            g = np.mean([c["data"]["S11"] for c in mine], axis=0)
            fc = mine[0]["freq"] / 1e6
            axes[0, 1].plot(fc, _db(g), color=line.get_color(), label=label)
            axes[1, 1].plot(fc, np.rad2deg(np.angle(g)), color=line.get_color())
    axes[0, 0].set_title(f"raw {sparam} (standards not shown)", fontsize=10)
    axes[0, 0].set_ylabel(f"|{sparam}| raw (dB)")
    axes[1, 0].set_ylabel("phase (deg)")
    if corr:
        axes[0, 1].set_title(f"corrected at each connector ({definitions})", fontsize=10)
    else:
        axes[0, 1].set_title("not corrected (no definitions; see Take's printout)", fontsize=10)
    axes[0, 1].set_ylabel("|reflection| corrected (dB)")
    axes[1, 1].set_ylabel("phase (deg)")
    for ax in axes.ravel():
        ax.grid(True, alpha=0.3)
        ax.ticklabel_format(axis="y", useOffset=False)
    for ax in axes[1]:
        ax.set_xlabel("Frequency (MHz)")
    axes[0, 0].legend(fontsize=8, frameon=False)
    n_rep = len(take["repeats"])
    fig.suptitle(f"{take['cfg']['switch_serial']}  cooldown {take['cfg']['cooldown']}  "
                 f"{take['temp_str']}  take {take['take_id']}"
                 + (f"  (mean of {n_rep} repeats)" if n_rep > 1 else ""), fontsize=11)
    fig.tight_layout()
    _save(fig, save)
    return fig


def plot_check(result, serial="", save=None):
    """
    A stuck or thru check. Top: raw |S21| (dB) of every position, ALL_OPEN in grey.
    Bottom, stuck check: each position's median |S21 - first ALL_OPEN| (linear, no unit;
    grey ALL_OPEN bars should be small, blue ones clearly bigger). Thru check: median |S21| in dB.
    """
    rows = result["rows"]
    f = result["freq"] / 1e6
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    for r in rows:
        if r["position"] == "ALL_OPEN":
            ax1.plot(f, _db(r["s21"]), color="0.7", lw=1)
        else:
            ax1.plot(f, _db(r["s21"]), lw=1.3, label=r["label"])
    ax1.plot([], [], color="0.7", lw=1, label="ALL_OPEN (every one)")
    ax1.set_xlabel("Frequency (MHz)")
    ax1.set_ylabel("|S21| raw (dB)")
    t = result.get("temp_k")
    temp = "no temperature" if t is None else (f"{t:.1f} K" if t >= 1 else f"{t * 1e3:.0f} mK")
    ax1.set_title(f"{result['check']} check {result['check_id']}, {serial}, {temp}: raw", fontsize=10)
    ax1.legend(fontsize=8, ncol=2)
    colours = ["0.7" if r["position"] == "ALL_OPEN" else "C0" for r in rows]
    if result["check"] == "thru":
        heights = [r["level_db"] for r in rows]
        ax2.set_ylabel("median |S21| (dB)")
        ax2.set_title(f"closed vs ALL_OPEN gap {result['gap_db']:.1f} dB", fontsize=10)
    else:
        heights = [r["diff"] for r in rows]
        ax2.set_ylabel("median |S21 - first ALL_OPEN| (linear)")
        ax2.set_title("Grey (ALL_OPEN) should all be small, blue all clearly bigger", fontsize=10)
    ax2.bar(range(len(rows)), heights, color=colours)
    ax2.set_xticks(range(len(rows)))
    ax2.set_xticklabels([f"{r['step']}: {r['label']}" for r in rows], rotation=45, ha="right",
                        fontsize=8)
    fig.tight_layout()
    _save(fig, save)
    return fig


def plot_runs(runs, sparam=None, save=None):
    """Runs from db.load_runs: |S| in dB and phase. sparam None: S21 if there, else S11."""
    fig, (ax_m, ax_p) = plt.subplots(2, 1, sharex=True, figsize=(9, 6))
    for run_id, run in runs.items():
        s = sparam or ("S21" if "S21" in run["data"] else "S11")
        if s not in run["data"]:
            print(f"  run {run_id} has no {s}; skipped")
            continue
        meta = run["meta"]
        what = meta.get("port") or ""
        label = (f"run {run_id} {run['name']} {s}" + (f" ({what})" if what else "")
                 + (f" {meta['temp_str']}" if meta.get("temp_str") else ""))
        (line,) = ax_m.plot(run["freq"] / 1e6, _db(run["data"][s]), label=label)
        ax_p.plot(run["freq"] / 1e6, np.rad2deg(np.angle(run["data"][s])), color=line.get_color())
    ax_m.set_ylabel("|S| (dB)")
    ax_p.set_ylabel("phase (deg)")
    ax_p.set_xlabel("Frequency (MHz)")
    ax_m.legend(fontsize=8, frameon=False)
    for ax in (ax_m, ax_p):
        ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, save)
    return fig


def smith_grid(ax):
    """A light Smith-chart grid: unit circle, a few r and x circles, open/short labels."""
    t = np.linspace(0, 2 * np.pi, 400)
    ax.plot(np.cos(t), np.sin(t), color="0.6", lw=1.0, zorder=1)
    for r in (0.2, 0.5, 1, 2, 5):
        c, rad = r / (1 + r), 1 / (1 + r)
        ax.plot(c + rad * np.cos(t), rad * np.sin(t), color="0.88", lw=0.7, zorder=0)
    for x in (0.2, 0.5, 1, 2, 5):
        for sign in (1, -1):
            z = 1j * sign * x + np.concatenate([np.linspace(0, 1, 200), np.linspace(1, 50, 400)])
            g = (z - 1) / (z + 1)
            ax.plot(g.real, g.imag, color="0.88", lw=0.7, zorder=0)
    ax.plot([-1, 1], [0, 0], color="0.88", lw=0.7, zorder=0)
    ax.plot(0, 0, "+", color="0.6", ms=7, zorder=1)
    ax.text(1.04, 0, "open", fontsize=8, color="0.4", va="center")
    ax.text(-1.04, 0, "short", fontsize=8, color="0.4", va="center", ha="right")
    ax.set_xlim(-1.12, 1.12)
    ax.set_ylim(-1.12, 1.12)
    ax.set_aspect("equal")
    ax.axis("off")


def plot_mag_smith(traces, together=False, title=None, mark_hz=None, save=None):
    """
    Each trace (analysis.traces_from_*) as |S| in dB beside its Smith chart; together=True
    puts them all on one figure. mark_hz marks a frequency. save: a folder, one
    <label>.png per figure (never overwrites). Returns the figures.
    The Smith chart only means something for a corrected reflection (raw |S21| through the
    fridge is a dot near the centre).
    """
    if not traces:
        print("No traces to plot")
        return []
    figs = []
    for group in ([traces] if together else [[t] for t in traces]):
        name = title or (group[0]["label"] if len(group) == 1 else f"{len(group)} traces")
        fig, (am, asm) = plt.subplots(1, 2, figsize=(12, 4.8), gridspec_kw={"width_ratios": [1.5, 1]})
        smith_grid(asm)
        for tr in group:
            f = tr["freq"] / 1e6
            (line,) = am.plot(f, _db(tr["gamma"]), lw=1.0, label=tr["label"])
            asm.plot(tr["gamma"].real, tr["gamma"].imag, lw=1.0, color=line.get_color())
            asm.plot(tr["gamma"].real[0], tr["gamma"].imag[0], "o", ms=5, color=line.get_color())
            if mark_hz is not None and f[0] <= mark_hz / 1e6 <= f[-1]:
                j = int(np.argmin(np.abs(tr["freq"] - mark_hz)))
                asm.plot(tr["gamma"].real[j], tr["gamma"].imag[j], "x", ms=9, mew=2, color="k")
        if mark_hz is not None:
            am.axvline(mark_hz / 1e6, color="k", ls="--", lw=0.8)
        raw = all(tr["kind"] == "raw" for tr in group)
        am.set_ylabel("|S| raw (dB)" if raw else "|reflection| (dB)")
        am.set_xlabel("Frequency (MHz)")
        am.grid(True, alpha=0.3)
        am.ticklabel_format(axis="y", useOffset=False)
        am.legend(fontsize=8, frameon=False)
        asm.set_title("Smith chart (dot = first frequency)" + (" - raw, not a reflection" if raw else ""),
                      fontsize=9)
        fig.suptitle(name, fontsize=11)
        fig.tight_layout()
        if save:
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip("_")[:80]
            _save(fig, Path(save) / f"{safe}.png")
        figs.append(fig)
    return figs


def plot_temperatures(rows, save=None):
    """MXP, MXP2 and STP just before (dot) and after (x) every position of a take or check
    (analysis.temperature_log), log scale. Sensors with no readings are left out."""
    fig, ax = plt.subplots(figsize=(10, 4.5))
    steps = np.arange(1, len(rows) + 1)
    plotted = False
    for sensor in ("mxp", "mxp2", "stp"):
        before = np.array([r.get(f"{sensor}_k_before") for r in rows], dtype=float)
        after = np.array([r.get(f"{sensor}_k_after") for r in rows], dtype=float)
        if np.all(np.isnan(before)) and np.all(np.isnan(after)):
            continue
        (line,) = ax.plot(steps, before, "o-", ms=4, lw=1, label=f"{sensor.upper()} before")
        ax.plot(steps, after, "x", ms=6, color=line.get_color(), label=f"{sensor.upper()} after")
        plotted = True
    if not plotted:
        ax.text(0.5, 0.5, "no fridge readings saved for these runs", transform=ax.transAxes,
                ha="center")
    else:
        ax.set_yscale("log")
        ax.legend(fontsize=8, ncol=3, frameon=False)
    ax.set_xticks(steps)
    ax.set_xticklabels([r["name"] for r in rows], rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("temperature (K)")
    ax.grid(True, alpha=0.3, which="both")
    ax.set_title(f"Fridge temperatures, {rows[0].get('what', '')}", fontsize=10)
    fig.tight_layout()
    _save(fig, save)
    return fig


def plot_hv_test(result, positions, save=None):
    """
    A gate test (vna.hv_test) after ecal.hv_positions. Top: unwrapped phase of each closed
    position, corrected to the internal standards (slope = how far out it reflects; flat =
    at the internal standards, like ALL_OPEN). Bottom: |G| (dB). One colour per position.
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    for step, v in positions.items():
        f = v["freq"] / 1e6
        ph = np.degrees(np.unwrap(np.angle(v["inside"])))
        ph -= 360 * np.round(ph[0] / 360)
        lab = f"{step}: {v['label']}  ({v['delay_in_ns'] / 2:+.3f} ns one way)"
        ax1.plot(f, ph, lw=1.5, label=lab)
        ax2.plot(f, _db(v["inside"]), lw=1.2)
    ax1.set_ylabel("phase, corrected to the internal standards (deg)")
    ax1.set_title(f"Gate test {result['check_id']}: flat = reflects at the internal standards; "
                  "steeper down = further out", fontsize=10)
    ax1.legend(fontsize=7, ncol=1)
    ax2.set_ylabel("|G| (dB)")
    ax2.set_xlabel("Frequency (MHz)")
    fig.tight_layout()
    _save(fig, save)
    return fig
