"""Figures for the 2026-10-08 SN0077 room-temperature resonator check.

Run from anywhere:  python make_figures.py
Needs numpy + matplotlib, and the measurements/ modules (ecal.py, read_db.py).
Writes one 16:9 PDF per figure (and a PNG preview) beside this script,
all_figures.pdf, and summary_values.csv.

Data used
---------
Reference (resonator alone):
    measurements/Sweeps/zoomed_in_resonator_S11_VNA_20261008.s1p
    S11 of the resonator on one VNA cable, VNA calibrated, 520-580 MHz,
    1000 pts, saved from the VNA front panel 2026-10-08 16:15. Its header
    says "Correction: S11(C* 2-Port)". The full-span file
    (resonator_S11_VNA_2026108.s1p, 1 MHz-10 GHz) says "C": on Keysight
    VNAs C* means the correction is on but interpolated, i.e. the cal was
    done on a different sweep than this one.

Full setup (resonator through the switch):
    E-cal set 20261008T160234.969 in measurements/mm4250_sweeps.db
    (runs 29-42), note "Zoomed in: warm setup run w/ circ., switch,
    resonator, but no amp.". SN0077 at 295 K, VNA port 1 -> circulator ->
    RFC -> RF1 -> short cable -> resonator, reflection back through the
    circulator to VNA port 2. VNA correction off, 520-580 MHz, 2001 pts,
    IFBW 1 kHz, -20 dBm, 2 repeats of
    [open, short, load] -> RF1 -> [open, short, load].
    De-embedded with ecal.correct_set on the raw S21 (the circulator
    path), both repeats averaged, mean of before/after standards, with
    two sets of standard definitions:
      NIST     tier2_295k1: NIST's 295 K definitions for THEIR unit
               (SN0031). Reference plane at the RF1 connector.
      perfect  open +1, short -1, load 0. Reference plane at the internal
               standards (inside the switch), so the RFC -> RF1 path stays in.
    Either way the short cable from RF1 to the resonator stays in, so the
    de-embedded trace is the resonator seen through that cable.

The 2-way delay and loss of everything left between the two reference
planes are estimated from the ratio (de-embedded / VNA reference) away
from the resonance (|f - f_dip| > 6 MHz); they go in summary_values.csv.
"""
from pathlib import Path
import csv
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

HERE = Path(__file__).resolve().parent
USER = HERE.parents[3]                                   # mm4250-switch/measurements
sys.path.insert(0, str(USER))
import ecal                                              # noqa: E402

SET = "20261008T160234.969"
DB = USER / "mm4250_sweeps.db"
VNA_FILE = USER / "Sweeps" / "zoomed_in_resonator_S11_VNA_20261008.s1p"
XLIM_MHZ = (535, 562)                                    # zoom on the dip
OFFRES_HZ = 6e6                                          # "away from the dip" for the cable fit

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
NOTE = ("MM4250 SN0077 · 295 K · 8 Oct 2026 · e-cal set " + SET +
        " · short cable RF1 -> resonator stays in")
plt.rcParams.update({
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 13, "axes.labelsize": 14, "xtick.labelsize": 12, "ytick.labelsize": 12,
    "legend.fontsize": 12, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK3, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "lines.linewidth": 1.8, "lines.solid_capstyle": "round",
    "figure.facecolor": "white", "axes.facecolor": "white",
})
SIZE = (11, 6.1875)                                      # 16:9

DEFS = {
    "NIST": {"dir": None, "long": "NIST 295 K definitions (SN0031), plane at RF1 connector"},
    "perfect": {"dir": ecal.PERFECT, "long": "perfect standards, plane inside the switch"},
}


def db(x):
    return 20 * np.log10(np.abs(x))


def cinterp(fq, f, z):
    return np.interp(fq, f, z.real) + 1j * np.interp(fq, f, z.imag)


def dip(f, g):
    """(f_min Hz, depth dB, off-resonance level dB, half-depth width Hz) of an |S11| dip."""
    d = db(g)
    i = int(np.argmin(d))
    base = np.median(np.r_[d[: len(d) // 20], d[-len(d) // 20:]])
    below = np.where(d < base + (d[i] - base) / 2)[0]
    return f[i], d[i], base, f[below[-1]] - f[below[0]]


def leftover(f_ref, g_ref, f, g, f_dip):
    """2-way delay (ns) and loss (dB) of whatever sits between the two planes."""
    ratio = cinterp(f_ref, f, g) / g_ref
    off = np.abs(f_ref - f_dip) > OFFRES_HZ
    slope = np.polyfit(f_ref[off], np.unwrap(np.angle(ratio))[off], 1)[0]
    return -slope / (2 * np.pi) * 1e9, float(np.median(db(ratio)[off]))


def finish(fig, ax, title, sub, legend_loc="lower left"):
    ax.set_xlim(*XLIM_MHZ)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Magnitude (dB)")
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo - 0.22 * (hi - lo), hi)
    ax.legend(loc=legend_loc, fontsize=11.5)
    fig.suptitle(title, x=0.06, ha="left", fontsize=17, color=INK, y=0.975)
    fig.text(0.06, 0.935, sub, fontsize=11.5, color=INK2, va="top", linespacing=1.4)
    fig.text(0.06, 0.02, NOTE, fontsize=9.5, color=INK3)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.84, bottom=0.15)


def mark_dip(ax, f, g, color):
    """Dot on the dip; returns ", dip <f> MHz, <depth> dB" for the legend label."""
    fd, depth, _, _ = dip(f, g)
    ax.plot(fd / 1e6, depth, "o", ms=8, color=color, mec="white", mew=2, zorder=5)
    return f" (dip {fd / 1e6:.2f} MHz, {depth:.1f} dB)"


def main():
    if not DB.is_file() or not VNA_FILE.is_file():
        sys.exit(f"Missing data: need {DB} and {VNA_FILE} (laptop copy of the DAQ data).")
    DEFS["NIST"]["dir"] = ecal.find_ideals("295K", start=USER)

    f_ref, g_ref = ecal.read_s1p(VNA_FILE)
    ref_dip = dip(f_ref, g_ref)
    results = {k: ecal.correct_set(SET, v["dir"], sparam="S21", db_path=DB) for k, v in DEFS.items()}

    figs, rows = [], []
    rows.append(["VNA cal (resonator alone)", *[f"{x:.6g}" for x in
                 (ref_dip[0] / 1e6, ref_dip[1], ref_dip[2], ref_dip[3] / 1e6)], "", ""])
    raw_f, raw_g = None, None
    for k, r in results.items():
        f, g = r["freq"], r["ports"][1]
        raw_f, raw_g = f, r["raw"][1]
        d = dip(f, g)
        tau, loss = leftover(f_ref, g_ref, f, g, ref_dip[0])
        rows.append([f"de-embedded, {k}", f"{d[0] / 1e6:.6g}", f"{d[1]:.4g}", f"{d[2]:.4g}",
                     f"{d[3] / 1e6:.4g}", f"{tau:.3g}", f"{loss:.3g}"])

        # VNA-calibrated resonator vs de-embedded
        fig, ax = plt.subplots(figsize=SIZE)
        a = ax.plot(f_ref / 1e6, db(g_ref), color=INK, lw=2.2)[0]
        b = ax.plot(f / 1e6, db(g), color=BLUE)[0]
        a.set_label("Resonator alone, VNA cal" + mark_dip(ax, f_ref, g_ref, INK))
        b.set_label(f"Via switch, de-embedded ({k})" + mark_dip(ax, f, g, BLUE))
        finish(fig, ax, f"Resonator |S11|: VNA calibration vs. switch e-cal ({k})",
               f"E-cal: {DEFS[k]['long']}. Both repeats averaged.")
        figs.append((f"0{len(figs) + 1}_vna_vs_deembedded_{k}", fig))

    for k, r in results.items():
        f, g = r["freq"], r["ports"][1]
        fig, ax = plt.subplots(figsize=SIZE)
        a = ax.plot(raw_f / 1e6, db(raw_g), color=ORANGE)[0]
        b = ax.plot(f / 1e6, db(g), color=BLUE)[0]
        a.set_label("Raw S21" + mark_dip(ax, raw_f, raw_g, ORANGE))
        b.set_label(f"De-embedded S11 ({k})" + mark_dip(ax, f, g, BLUE))
        finish(fig, ax, f"Full setup: raw vs. de-embedded ({k})",
               "Raw = VNA port 1 -> circulator -> switch RF1 -> cable -> resonator -> back to port 2, VNA cal off.\n"
               f"De-embedded: {DEFS[k]['long']}. Both repeats averaged.")
        figs.append((f"0{len(figs) + 1}_raw_vs_deembedded_{k}", fig))

    with PdfPages(HERE / "all_figures.pdf") as pdf:
        for name, fig in figs:
            fig.savefig(HERE / f"{name}.pdf")
            fig.savefig(HERE / f"{name}.png", dpi=150)
            pdf.savefig(fig)
            plt.close(fig)

    with open(HERE / "summary_values.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["trace", "dip_freq_MHz", "dip_depth_dB", "offres_level_dB",
                    "half_depth_width_MHz", "leftover_2way_delay_ns", "leftover_2way_loss_dB"])
        w.writerow(["raw S21", *[f"{x:.6g}" for x in
                    (dip(raw_f, raw_g)[0] / 1e6, dip(raw_f, raw_g)[1], dip(raw_f, raw_g)[2],
                     dip(raw_f, raw_g)[3] / 1e6)], "", ""])
        w.writerows(rows)
    for name, _ in figs:
        print("wrote", name)
    print(open(HERE / "summary_values.csv").read())


if __name__ == "__main__":
    main()
