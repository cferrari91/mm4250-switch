"""
Plots, from what db.py and vna.py return (no instruments, no e-cal math):

    plot_take(db.load_take(None, DB))     # a take: raw and corrected, |S| and phase per channel
    plot_check(result)                     # a stuck or thru check: raw |S21| + per-position bars
    plot_runs(db.load_runs([12, 13], DB))  # any runs

Every function returns the Figure; pass save="path.png" (or .pdf) to
write it too (never overwrites an existing file).
"""

from pathlib import Path

import numpy as np


def _plt():
    import matplotlib.pyplot as plt
    return plt


def _db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-30))


def _deg(x):
    return np.rad2deg(np.angle(x))


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
    A take (db.load_take): left, the raw trace the correction is solved on
    (the cooldown file's correct_sparam, mean over repeats); right, the
    corrected reflection at each channel's connector (mean over repeats),
    |S| in dB on top and phase below. `definitions` picks which corrected
    runs if a take has more than one kind (None = the newest saved).
    """
    plt = _plt()
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
        axes[1, 0].plot(f, _deg(raw), color=line.get_color())
        mine = [c for c in corr if c["channel"] == ch]
        if mine:
            g = np.mean([c["data"]["S11"] for c in mine], axis=0)
            fc = mine[0]["freq"] / 1e6
            axes[0, 1].plot(fc, _db(g), color=line.get_color(), label=label)
            axes[1, 1].plot(fc, _deg(g), color=line.get_color())
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
    A stuck or thru check (vna.stuck_check / thru_check). Top: raw |S21| of
    every position, ALL_OPEN in grey. Bottom: stuck check, each position's
    median |S21 - first ALL_OPEN| (grey ALL_OPEN bars should all be small,
    coloured ones clearly bigger); thru check, each position's median |S21| in dB.
    """
    plt = _plt()
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
        ax2.set_ylabel("median |S21 - first ALL_OPEN|")
        ax2.set_title("Grey (ALL_OPEN) should all be small, blue all clearly bigger", fontsize=10)
    ax2.bar(range(len(rows)), heights, color=colours)
    ax2.set_xticks(range(len(rows)))
    ax2.set_xticklabels([f"{r['step']}: {r['label']}" for r in rows], rotation=45, ha="right",
                        fontsize=8)
    fig.tight_layout()
    _save(fig, save)
    return fig


def plot_runs(runs, sparam=None, save=None):
    """
    Runs from db.load_runs: |S| in dB and phase. sparam None = S21 for a
    2-port run, S11 (the corrected reflection) for a corrected run.
    """
    plt = _plt()
    fig, (ax_m, ax_p) = plt.subplots(2, 1, sharex=True, figsize=(9, 6))
    for run_id, run in runs.items():
        s = sparam or ("S21" if "S21" in run["data"] else "S11")
        if s not in run["data"]:
            print(f"  run {run_id} has no {s}; skipped")
            continue
        meta = run["meta"]
        what = meta.get("port") or ""
        label = f"run {run_id} {run['name']} {s}" + (f" ({what})" if what else "") + \
            (f" {meta['temp_str']}" if meta.get("temp_str") else "")
        (line,) = ax_m.plot(run["freq"] / 1e6, _db(run["data"][s]), label=label)
        ax_p.plot(run["freq"] / 1e6, _deg(run["data"][s]), color=line.get_color())
    ax_m.set_ylabel("|S| (dB)")
    ax_p.set_ylabel("phase (deg)")
    ax_p.set_xlabel("Frequency (MHz)")
    ax_m.legend(fontsize=8, frameon=False)
    for ax in (ax_m, ax_p):
        ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, save)
    return fig
