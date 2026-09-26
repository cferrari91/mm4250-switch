"""Slide-ready PDF figures for the 2026-09-24 SN0078 room-temperature sweeps.

Run from anywhere:  python make_figures.py
Reads the Touchstone files in measurements/Sweeps/SN0078/20260924/295K and the 11 Sep comparison CSVs in
Menlo/Sweeps, writes one 16:9 PDF per figure beside this script plus
all_figures.pdf.
"""
import glob
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np

HERE = Path(__file__).resolve().parent
USER = HERE.parents[3]                                  # mm4250-switch/measurements
SWEEPS = USER / "Sweeps" / "SN0078" / "20260924" / "295K"
SEP11 = USER.parents[2] / "Menlo" / "Sweeps" / "09112026_295K"   # project folder
sys.path.insert(0, str(USER))
from plots import read_touchstone                       # noqa: E402

# Categorical palette (validated for CVD); colour follows the channel.
C = {"RF1": "#2a78d6", "RF2": "#eb6834", "RF3": "#1baf7a",
     "RF4": "#eda100", "RF5": "#e87ba4", "RF6": "#008300"}
INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
CH = list(C)
NOTE = "MM4250 SN0078 · 295 K · 24 Sep 2026"

plt.rcParams.update({
    "pdf.fonttype": 42, "ps.fonttype": 42,              # editable text in PowerPoint/Keynote
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 14, "axes.labelsize": 15, "xtick.labelsize": 13, "ytick.labelsize": 13,
    "legend.fontsize": 13, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK3, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
    "lines.linewidth": 2.0, "lines.solid_capstyle": "round",
    "figure.facecolor": "white", "axes.facecolor": "white",
})
SIZE = (10, 5.625)                                      # 16:9


def run_file(cabling, position):
    return glob.glob(str(SWEEPS / f"{cabling}_uncal" / f"{position}_run*.s2p"))[0]


def load(path):
    freq, data = read_touchstone(path)[:2]
    return freq / 1e9, data


def smooth_db(x, n):
    """Sliding power average (|S|^2), edge-normalised, returned in dB."""
    k = np.ones(n)
    p = np.abs(x) ** 2
    return 10 * np.log10(np.convolve(p, k, "same") / np.convolve(np.ones_like(p), k, "same"))


def read_csv(path):
    rows = [l.strip().split(",") for l in open(path) if l[:1].isdigit()]
    a = np.array(rows, float)
    return a[:, 0] / 1e9, a[:, 1]


def finish(fig, ax, name, note=NOTE, xlim=(0, 10)):
    ax.set_xlim(*xlim)
    ax.set_xlabel("Frequency (GHz)")
    fig.text(0.99, 0.015, note, ha="right", va="bottom", fontsize=10.5, color=INK3)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(HERE / f"{name}.pdf")
    FIGS.append(fig)


FIGS = []

# 1. Raw through S21
fig, ax = plt.subplots(figsize=SIZE)
for ch in CH:
    f, d = load(run_file(ch, ch))
    ax.plot(f, smooth_db(d["S21"], 61), color=C[ch], label=ch, lw=2.4 if ch == "RF6" else 1.8,
            zorder=3 if ch == "RF6" else 2)
ax.set_ylabel("S21 (dB)")
ax.set_ylim(-19, 0.5)
ax.annotate("RF6 dip", xy=(8.17, -17.3), xytext=(6.2, -15.5), color=INK, fontsize=13,
            arrowprops=dict(arrowstyle="-", color=INK3, lw=1))
ax.legend(ncol=6, loc="lower left", handlelength=1.4, columnspacing=1.2)
finish(fig, ax, "01_thru_s21_raw", NOTE + " · raw, cables included (no VNA cal)")

# 2. Isolation
fig, ax = plt.subplots(figsize=SIZE)
off_mean = []
for ch in CH:
    f, on = load(run_file(ch, ch))
    _, off = load(run_file(ch, "ALL_OPEN"))
    off_mean.append(smooth_db(off["S21"], 101))
    ax.plot(f, smooth_db(on["S21"], 101) - smooth_db(off["S21"], 101), color=C[ch], label=ch, lw=1.8)
noise_to = f[np.argmax(np.mean(off_mean, axis=0) > -88)]
ax.axvspan(0, noise_to, color=INK3, alpha=0.10, lw=0)
ax.text(0.1, 33, "at VNA noise floor", color=INK2, fontsize=12)
ax.axhline(40, color=INK2, ls=(0, (5, 4)), lw=1.3)
ax.text(1.9, 41, "datasheet: 40 dB typ. at 10 GHz", ha="left", color=INK2, fontsize=12)
ax.set_ylabel("Isolation (dB)")
ax.set_ylim(30, 100)
ax.legend(ncol=6, loc="upper right", handlelength=1.4, columnspacing=1.2)
finish(fig, ax, "02_isolation", NOTE + " · S21(on) − S21(ALL_OPEN), same cabling")

# 3. Estimated switch insertion loss
fig, ax = plt.subplots(figsize=SIZE)
for ch in CH:
    f, on = load(run_file(ch, ch))
    _, op = load(run_file(ch, "ALL_OPEN"))
    il = smooth_db(on["S21"], 201) - (smooth_db(op["S11"], 201) + smooth_db(op["S22"], 201)) / 2
    ax.plot(f, il, color=C[ch], label=ch, lw=2.4 if ch == "RF6" else 1.8, zorder=3 if ch == "RF6" else 2)
ax.axhline(-4, color=INK2, ls=(0, (5, 4)), lw=1.3)
ax.text(0.15, -3.75, "datasheet: 4 dB typ. at 10 GHz", ha="left", color=INK2, fontsize=12)
ax.set_ylabel("Estimated switch S21 (dB)")
ax.set_ylim(-11.5, 0.5)
ax.legend(ncol=6, loc="lower left", handlelength=1.4, columnspacing=1.2)
finish(fig, ax, "03_est_insertion_loss", NOTE + " · rough estimate: cables removed using ALL_OPEN reflections")

# 4. RF6 dip, today's runs
fig, ax = plt.subplots(figsize=SIZE)
f, d = load(run_file("RF1", "RF1"))
ax.plot(f, smooth_db(d["S21"], 61), color=C["RF1"], label="RF1 (reference)", lw=1.8)
runs = [(SWEEPS / "uncal/RF6_run1.s2p", "RF6 run 1", (0, (1.5, 2.5))),
        (SWEEPS / "uncal/RF6_run6.s2p", "RF6 run 6", (0, (6, 3.5))),
        (Path(run_file("RF6", "RF6")), "RF6 run 27", "-")]
for path, label, ls in runs:
    f, d = load(path)
    y = smooth_db(d["S21"], 61 if len(f) > 2000 else 7)
    m = (f >= 6) & (f <= 10)
    i = np.argmin(np.where(m, y, 0))
    ax.plot(f, y, color=C["RF6"], ls=ls, lw=2.2, label=f"{label}  (min {f[i]:.2f} GHz)")
ax.set_ylabel("Raw S21 (dB)")
ax.set_ylim(-19, -4)
ax.legend(loc="lower left")
finish(fig, ax, "04_rf6_dip_runs", NOTE + " · raw", xlim=(6, 10))

# 5. 11 Sep calibrated comparison
fig, ax = plt.subplots(figsize=SIZE)
for ch in CH:
    f, y = read_csv(SEP11 / "78" / f"{ch}_S21.csv")
    ax.plot(f, y, color=C[ch], label=f"SN0078 {ch}", lw=2.4 if ch == "RF6" else 1.6,
            zorder=3 if ch == "RF6" else 2)
f, y = read_csv(SEP11 / "77" / "RF6_S21.csv")
ax.plot(f, y, color=C["RF6"], ls=(0, (6, 3.5)), lw=2.0, label="SN0077 RF6")
ax.set_ylabel("Calibrated S21 (dB)")
ax.set_ylim(-14, 0.5)
ax.legend(ncol=4, loc="lower left", handlelength=1.6, columnspacing=1.0)
finish(fig, ax, "05_rf6_dip_sep11", "MM4250 SN0078 and SN0077 · 295 K · 11 Sep 2026 · VNA calibrated")

# 6. S11 at RFC in each switch state
fig, ax = plt.subplots(figsize=SIZE)
states = [("ALL_OPEN", INK, "-"), ("INTERNAL_SHORT", INK3, (0, (6, 3.5))),
          ("INTERNAL_LOAD", "#4a3aa7", "-"), ("RF1", C["RF1"], "-")]
for pos, col, ls in states:
    f, d = load(run_file("RF1", pos))
    ax.plot(f, smooth_db(d["S11"], 61), color=col, ls=ls, lw=1.9,
            label="RF1 connected" if pos == "RF1" else pos)
ax.set_ylabel("S11 at RFC (dB)")
ax.set_ylim(-45, 2)
ax.legend(ncol=2, loc="lower right")
finish(fig, ax, "06_rfc_states_s11", NOTE + " · cabled RF1 · raw, cables included")

# 7. Values table
marks = [1, 2, 4, 6, 8, 9.5]
iso_rows, il_rows = [], []
for ch in CH:
    f, on = load(run_file(ch, ch))
    _, off = load(run_file(ch, "ALL_OPEN"))
    iso = smooth_db(on["S21"], 101) - smooth_db(off["S21"], 101)
    il = smooth_db(on["S21"], 201) - (smooth_db(off["S11"], 201) + smooth_db(off["S22"], 201)) / 2
    idx = [np.argmin(abs(f - m)) for m in marks]
    band = (f > 1) & (f < 9.9)
    w = np.argmin(np.where(band, iso, 999))
    iso_rows.append([f"{iso[i]:.1f}" for i in idx] + [f"{iso[w]:.1f} @ {f[w]:.2f}"])
    il_rows.append([f"{il[i]:.2f}" for i in idx])
fig, axes = plt.subplots(2, 1, figsize=SIZE)
for ax, rows, title, cols in [
        (axes[0], iso_rows, "Isolation (dB)", [f"{m:g} GHz" for m in marks] + ["worst, 1–9.9 GHz"]),
        (axes[1], il_rows, "Estimated switch insertion loss (dB)", [f"{m:g} GHz" for m in marks])]:
    ax.axis("off")
    ax.set_title(title, loc="left", fontsize=14, color=INK, fontweight="bold")
    t = ax.table(cellText=rows, rowLabels=CH, colLabels=cols, loc="center", cellLoc="right")
    t.auto_set_font_size(False)
    t.set_fontsize(12)
    t.scale(1, 1.35)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        if r == 0:
            cell.set_text_props(color=INK3, fontsize=11)
        if c == -1:
            cell.set_text_props(color=C[CH[r - 1]], fontweight="bold")
        txt = cell.get_text().get_text()
        if rows is iso_rows and r > 0 and c == len(marks) and float(txt.split(" @")[0]) < 40:
            cell.set_text_props(color="#d03b3b", fontweight="bold")
        if rows is il_rows and r == 6 and cols[c].startswith("8 "):
            cell.set_text_props(color="#d03b3b", fontweight="bold")
fig.text(0.99, 0.015, NOTE + " · red: below 40 dB isolation, or the RF6 dip", ha="right",
         va="bottom", fontsize=10.5, color=INK3)
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig(HERE / "07_values_table.pdf")
FIGS.append(fig)

with PdfPages(HERE / "all_figures.pdf") as pdf:
    for fig in FIGS:
        pdf.savefig(fig)
print(f"Wrote {len(FIGS)} figures + all_figures.pdf to {HERE}")
