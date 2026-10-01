"""Figures for the 2026-10-01 SN0077 room-temperature bench e-cal.

Run from anywhere:  python make_figures.py
Needs numpy + matplotlib, and the measurements/ modules (ecal.py, read_db.py).
Writes one 16:9 PDF per figure (and a PNG preview) beside this script,
all_figures.pdf, and summary_values.csv.

Data used
---------
E-cal set 20261001T134108.938 in measurements/mm4250_sweeps.db (runs 67-90),
taken on the DAQ machine: SN0077 on the bench at 295 K, VNA port 1 cabled
straight to RFC, RF1-RF6 left UNTERMINATED (open SMA connectors), VNA
correction off, 1 MHz-10 GHz, 10000 pts, IFBW 1 kHz, -20 dBm, 2 repeats of
[open, short, load] -> RF1..RF6 -> [open, short, load].
(Set 20261001T133418.033, runs 63-66, died on a VISA I/O error and is ignored.)

Two sets of standard definitions are used to correct it:
  NIST    tier2_295k1: NIST's 295 K definitions for THEIR unit, SN0031
          (what ecal.correct_set / the notebook used).
  SN0077  this unit's own definitions, derived from the 25 Sep SOLT data
          exactly as in ../../20260925/295K/make_figures.py: the calibrated
          reflection of each internal state at RFC, with the calibrated
          RFC->RF<n> path de-embedded. The short and load at RFC were measured
          with the RF1 and RF6 cabling (the cabling of the unused RF port does
          not matter in those states); their mean is used for every channel.
          The open uses each channel's own ALL_OPEN measurement.

Truth for every channel is an open SMA connector: |Gamma| = 1 (0 dB), phase
near 0 deg with a small negative slope from fringing capacitance.
"""
from pathlib import Path
import glob
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
from read_db import load_run                             # noqa: E402

SET = "20261001T134108.938"
DB = USER / "mm4250_sweeps.db"
SEP25 = USER / "Sweeps" / "SN0077" / "20260925" / "295K"

C = {1: "#2a78d6", 2: "#eb6834", 3: "#1baf7a", 4: "#eda100", 5: "#e87ba4", 6: "#008300"}
STDCOL = {"open": "#101513", "short": "#eb6834", "load": "#4a3aa7"}
INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
CH = range(1, 7)
NOTE = "MM4250 SN0077 · 295 K · bench e-cal, 1 Oct 2026 · RF1-RF6 unterminated"
plt.rcParams.update({
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 13, "axes.labelsize": 14, "xtick.labelsize": 12, "ytick.labelsize": 12,
    "legend.fontsize": 12, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK3, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "lines.linewidth": 1.6, "lines.solid_capstyle": "round",
    "figure.facecolor": "white", "axes.facecolor": "white",
})
SIZE = (11, 6.1875)                                      # 16:9


# ----------------------------------------------------------------------------- helpers
def read_ts(path):
    """Minimal Touchstone reader -> (f_Hz, dict of complex arrays)."""
    unit, fmt, rows = 1.0, "RI", []
    for line in open(path):
        s = line.strip()
        if not s or s.startswith("!"):
            continue
        if s.startswith("#"):
            t = s.upper().split()
            unit = {"HZ": 1, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[t[1]]
            fmt = t[3]
            continue
        rows.append(s.split())
    a = np.array(rows, float)
    v = a[:, 1:]
    if fmt == "RI":
        c = v[:, 0::2] + 1j * v[:, 1::2]
    elif fmt == "MA":
        c = v[:, 0::2] * np.exp(1j * np.deg2rad(v[:, 1::2]))
    else:
        c = 10 ** (v[:, 0::2] / 20) * np.exp(1j * np.deg2rad(v[:, 1::2]))
    f = a[:, 0] * unit
    if c.shape[1] == 4:                                  # Touchstone 2-port order: 11 21 12 22
        return f, {"S11": c[:, 0], "S21": c[:, 1], "S12": c[:, 2], "S22": c[:, 3]}
    return f, {"S11": c[:, 0]}


def sep25(ch, state):
    g = glob.glob(str(SEP25 / f"RF{ch}_cal" / f"{state}_run*.s2p"))
    assert len(g) == 1, (ch, state, g)
    return read_ts(g[0])


def cinterp(fq, f, z):
    return np.interp(fq, f, z.real) + 1j * np.interp(fq, f, z.imag)


def db(x):
    return 20 * np.log10(np.abs(x))


def deg(x):
    return np.rad2deg(np.angle(x))


def deembed(gin, S):
    """Load reflection at port 2 of 2-port S that gives input reflection `gin` at port 1."""
    d = gin - S["S11"]
    return d / (S["S12"] * S["S21"] + S["S22"] * d)


# ----------------------------------------------------------------------------- the set
cal = ecal.find_set(SET, DB)
f_full, _ = load_run(cal["repeats"][0]["ports"][1], DB)
keep = f_full >= ecal.DEFAULT_FMIN_HZ
f = f_full[keep]
fg = f / 1e9

# NIST (SN0031) 295 K definitions -- what the notebook used
NIST_DIR = ecal.find_ideals("295K", start=USER)
NIST_DEF = {ch: ecal.load_ideals(NIST_DIR, ch, f_full) for ch in CH}

# SN0077's own definitions from the 25 Sep SOLT data
f25, _ = sep25(1, "RF1")
std_rfc = {}
for state, key in (("INTERNAL_SHORT", "short"), ("INTERNAL_LOAD", "load")):
    a, b = sep25(1, state)[1]["S11"], sep25(6, state)[1]["S11"]
    std_rfc[key] = (a + b) / 2
    std_rfc[key + "_spread"] = np.abs(a - b)
OURS_DEF = {}
for ch in CH:
    path = sep25(ch, f"RF{ch}")[1]
    d = {"open": deembed(sep25(ch, "ALL_OPEN")[1]["S11"], path),
         "short": deembed(std_rfc["short"], path),
         "load": deembed(std_rfc["load"], path)}
    OURS_DEF[ch] = {k: cinterp(f_full, f25, v) for k, v in d.items()}


def correct_with(defs):
    """Same algorithm as ecal.correct_set (mean of before/after standards,
    average of the calibrated result over repeats), with any definitions."""
    per = []
    for block in cal["repeats"]:
        meas = ecal._raw_standards(block, "mean", DB, f_full)
        out = {}
        for ch, rid in sorted(block["ports"].items()):
            terms = ecal.solve_error_terms(meas, defs[ch])
            out[ch] = ecal.apply_correction(terms, load_run(rid, DB)[1]["S11"])[keep]
        per.append(out)
    return {ch: np.mean([p[ch] for p in per], axis=0) for ch in per[0]}, per


RES = ecal.correct_set(SET, NIST_DIR, db_path=DB)        # the notebook's own call
G_NIST, PER_NIST = correct_with(NIST_DEF)
G_OURS, PER_OURS = correct_with(OURS_DEF)
check = max(np.max(np.abs(G_NIST[ch] - RES["ports"][ch])) for ch in CH)
assert check < 1e-9, f"re-implementation disagrees with ecal.correct_set by {check}"

DRIFT = ecal.drift(SET, db_path=DB, verbose=True)
REP = ecal.repeatability(RES, verbose=True)

FIGS, SUMMARY = [], []


def save(fig, name):
    fig.savefig(HERE / f"{name}.pdf")
    fig.savefig(HERE / f"{name}.png", dpi=110)
    FIGS.append(fig)


def foot(fig, text):
    fig.text(0.99, 0.012, text, ha="right", va="bottom", fontsize=10, color=INK3)


def ch_legend(ax, loc="lower left", ncol=6):
    h = [plt.Line2D([], [], color=C[c], lw=2.2, label=f"RF{c}") for c in CH]
    ax.legend(handles=h, ncol=ncol, loc=loc, handlelength=1.4, columnspacing=1.1)


# ============================================================ 1. the notebook's result
fig, (am, ap) = plt.subplots(2, 1, sharex=True, figsize=SIZE)
for ch in CH:
    am.plot(fg, db(G_NIST[ch]), color=C[ch], lw=1.2)
    ap.plot(fg, deg(G_NIST[ch]), color=C[ch], lw=1.2)
am.axhline(0, color=INK3, lw=1)
am.set(ylabel="|S11| calibrated (dB)", ylim=(-1.2, 0.6))
ap.set(ylabel="phase (deg)", xlabel="Frequency (GHz)", xlim=(0, 10))
ch_legend(am)
am.set_title("Bench e-cal with NIST's SN0031 295 K definitions: open SMA connector on every port",
             loc="left", fontsize=14)
foot(fig, NOTE + " · truth = 0 dB · mean of 2 repeats")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "01_ecal_nist_definitions")

# ============================================================ 2. NIST vs SN0077 definitions
fig, axs = plt.subplots(2, 2, sharex=True, sharey="row", figsize=SIZE)
for j, (G, lab) in enumerate(((G_NIST, "NIST SN0031 definitions"), (G_OURS, "SN0077's own definitions (25 Sep SOLT)"))):
    for ch in CH:
        axs[0, j].plot(fg, db(G[ch]), color=C[ch], lw=1.1)
        axs[1, j].plot(fg, deg(G[ch]), color=C[ch], lw=1.1)
    axs[0, j].axhline(0, color=INK3, lw=1)
    axs[0, j].set_title(lab, loc="left", fontsize=13)
    axs[1, j].set_xlabel("Frequency (GHz)")
axs[0, 0].set(ylabel="|S11| (dB)", ylim=(-1.2, 0.6))
axs[1, 0].set(ylabel="phase (deg)", xlim=(0, 10))
ch_legend(axs[0, 0], ncol=3)
fig.suptitle("Same raw e-cal data, corrected with two sets of standard definitions",
             x=0.01, ha="left", fontsize=14)
foot(fig, NOTE + " · truth = open SMA connector")
fig.tight_layout(rect=(0, 0.03, 1, 0.95))
save(fig, "02_nist_vs_sn0077_definitions")

# ============================================================ 3. drift and repeatability
fig, (ad, ar) = plt.subplots(1, 2, sharey=True, figsize=SIZE)
fd = DRIFT["freq"] / 1e9
for k, rep in enumerate(DRIFT["repeats"], start=1):
    for s, v in rep.items():
        ad.semilogy(fd, v, color=STDCOL[s], lw=0.8, alpha=0.9 if k == 1 else 0.45,
                    label=f"{s}, repeat {k}")
for ch in CH:
    ar.semilogy(fg, REP[ch], color=C[ch], lw=0.8, label=f"RF{ch}")
for ax in (ad, ar):
    ax.axhline(1e-2, color=INK3, lw=1, ls=(0, (5, 3)))
    ax.set(xlabel="Frequency (GHz)", xlim=(0, 10), ylim=(1e-6, 1e-1))
ad.set(ylabel="|dGamma|")
ad.set_title("Drift: raw standards, |after - before|", loc="left", fontsize=13)
ar.set_title("Repeatability: calibrated, max |G_repeat - G_mean|", loc="left", fontsize=13)
ad.legend(ncol=2, fontsize=10, loc="upper left")
ar.legend(ncol=3, fontsize=10, loc="upper left")
foot(fig, NOTE + " · dashed: 1e-2")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "03_drift_repeatability")

# ============================================================ 4. how far apart the definitions are
fig, axs = plt.subplots(1, 2, sharey=True, figsize=SIZE)
for ax, ch in zip(axs, (1, 6)):
    for s in ("open", "short", "load"):
        dv = np.abs(OURS_DEF[ch][s] - NIST_DEF[ch][s])[keep]
        ax.semilogy(fg, dv, color=STDCOL[s], lw=1.0, label=s)
    ax.set(xlabel="Frequency (GHz)", xlim=(0, 10), ylim=(1e-3, 1), title=f"RF{ch}")
    ax.title.set_ha("left"); ax.title.set_x(0)
axs[0].set_ylabel("|Gamma_SN0077 - Gamma_NIST|")
axs[0].legend(loc="upper left")
fig.suptitle("Definition mismatch that the e-cal inherits (same quantity as the 25 Sep figure 09)",
             x=0.01, ha="left", fontsize=14)
foot(fig, "SN0077 definitions from the 25 Sep SOLT data · NIST: tier2_295k1 (SN0031)")
fig.tight_layout(rect=(0, 0.03, 1, 0.95))
save(fig, "04_definition_mismatch")

# ============================================================ summary table
BANDS = ((0.1, 2), (2, 6), (6, 10))
for lab, G in (("NIST defs", G_NIST), ("SN0077 defs", G_OURS)):
    for lo, hi in BANDS:
        m = (fg >= lo) & (fg < hi)
        allm = np.concatenate([db(G[ch])[m] for ch in CH])
        allp = np.concatenate([deg(G[ch])[m] for ch in CH])
        SUMMARY.append((f"{lab}: median | |S11| dB | (all ch), {lo}-{hi} GHz", "all", np.median(np.abs(allm))))
        SUMMARY.append((f"{lab}: max | |S11| dB | (all ch), {lo}-{hi} GHz", "all", np.max(np.abs(allm))))
        SUMMARY.append((f"{lab}: max |S11| dB (>0 is unphysical), {lo}-{hi} GHz", "all", np.max(allm)))
        SUMMARY.append((f"{lab}: phase min / deg, {lo}-{hi} GHz", "all", np.min(allp)))
        SUMMARY.append((f"{lab}: phase max / deg, {lo}-{hi} GHz", "all", np.max(allp)))
        spread = np.max([db(G[c])[m] for c in CH], 0) - np.min([db(G[c])[m] for c in CH], 0)
        SUMMARY.append((f"{lab}: channel-to-channel |S11| spread dB, median, {lo}-{hi} GHz", "all", np.median(spread)))
for k, rep in enumerate(DRIFT["repeats"], start=1):
    for s, v in rep.items():
        SUMMARY.append((f"drift repeat {k} {s}: median", s, np.median(v)))
        SUMMARY.append((f"drift repeat {k} {s}: max", s, np.max(v)))
for ch in CH:
    SUMMARY.append(("repeatability: median", f"RF{ch}", np.median(REP[ch])))
    SUMMARY.append(("repeatability: max", f"RF{ch}", np.max(REP[ch])))
for key in ("short", "load"):
    SUMMARY.append((f"25 Sep {key} at RFC: |RF1 cabling - RF6 cabling|, median", "RFC", np.median(std_rfc[key + "_spread"])))
for ch in CH:
    for s in ("open", "short", "load"):
        dv = np.abs(OURS_DEF[ch][s] - NIST_DEF[ch][s])[keep]
        for lo, hi in BANDS:
            m = (fg >= lo) & (fg < hi)
            SUMMARY.append((f"|dGamma| {s} def SN0077-NIST, median {lo}-{hi} GHz", f"port{ch}", np.median(dv[m])))

with open(HERE / "summary_values.csv", "w") as fh:
    fh.write("quantity,channel,value\n")
    for q, chn, v in SUMMARY:
        fh.write(f"{q},{chn},{v:.4g}\n")

with PdfPages(HERE / "all_figures.pdf") as pdf:
    for fig in FIGS:
        pdf.savefig(fig)
print(open(HERE / "summary_values.csv").read())
