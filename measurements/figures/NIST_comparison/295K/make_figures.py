"""Compare Charlie's 11 Sep 2026 room-temperature sweeps (SN77, SN78) with a
*calibrated* NIST reference.

NIST reference: SN0031 throw-path S-parameters recovered from NIST's tier-2
calibration files (tier2_scikitrf_caldata/tier2_295k1 and _295k2, averaged).
For each port N, the tier-2 files give the switch's internal short/open/load
(assumed ideal: -1, +1, 0 at the internal plane) as seen from the RF-port-N SMA
plane. Solving that one-port relation gives the 2-port error box between the
internal-standard plane and the SMA plane, i.e. the throw path itself:
    S11_nist = e00  (reflection at the internal plane, looking toward RF port N)
    S21_nist = sqrt(e01*e10)  (throw-path transmission, reciprocal)
This excludes the RFC connector/trace section, which Charlie's measurement includes.

Run from anywhere:  python make_figures.py   (writes PDFs + PNG previews and summary_medians.csv beside this file)
Laptop analysis only: needs the NIST repo (SD Code/nist_MM4250_calibration_data_2025) and the 11 Sep CSVs in
measurements/Sweeps/SN00{77,78}/20260911/295K/vna_csv, neither of which is on the DAQ.
"""
from pathlib import Path
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

HERE = Path(__file__).resolve().parent
USER = HERE.parents[2]                                  # mm4250-switch/measurements
SEP11 = {sn: USER / "Sweeps" / f"SN00{sn}" / "20260911" / "295K" / "vna_csv" for sn in ("77", "78")}
NIST_REPO = USER.parents[1] / "nist_MM4250_calibration_data_2025"
TIER2 = NIST_REPO / "tier2_scikitrf_caldata"
for _what, _p in (("NIST data", NIST_REPO), ("Sep 11 data", SEP11["77"]), ("Sep 11 data", SEP11["78"])):
    if not _p.is_dir():
        sys.exit(f"Laptop analysis script: {_what} not found at {_p}. This doesn't run on the DAQ.")
sys.path.insert(0, str(USER))
from plots import read_vna_csv                          # noqa: E402
RUNS = ["tier2_295k1", "tier2_295k2"]

COL = {"77": "#2a78d6", "78": "#eb6834"}          # validated categorical pair
INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
NOTE = ("Measured: MM4250 SN77/SN78, 295 K, 11 Sep 2026, VNA-calibrated at switch connectors (RFC to RF port).  "
        "NIST: SN0031 throw path from tier-2 cal, 295 K (internal junction to RF port; excludes RFC section).")
plt.rcParams.update({
    "pdf.fonttype": 42, "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 11, "axes.labelsize": 11, "xtick.labelsize": 10, "ytick.labelsize": 10,
    "legend.fontsize": 11, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK3, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "lines.linewidth": 1.6, "figure.facecolor": "white",
})
FMIN, FMAX = 0.1, 10.0


def read_sep11(sn, n, s):
    """11 Sep VNA export for switch `sn`, channel RF`n`, S-parameter `s` -> f (GHz), dB."""
    f, y = read_vna_csv(SEP11[sn] / f"RF{n}_{s}.csv")
    return f / 1e9, y


def read_s1p(p):
    a = np.loadtxt(p, comments=["!", "#"])
    return a[:, 0], a[:, 1] + 1j * a[:, 2]


def nist_throw(port):
    """Return f (GHz), S11 dB, S21 dB for NIST throw path `port`, averaged over runs (linear)."""
    s11s, s21s = [], []
    for run in RUNS:
        f, gs = read_s1p(TIER2 / run / f"port{port}_short_tier2.s1p")
        _, go = read_s1p(TIER2 / run / f"port{port}_open_tier2.s1p")
        _, gl = read_s1p(TIER2 / run / f"port{port}_load_tier2.s1p")
        gi = np.array([-1, 1, 0], complex)
        G = np.stack([gs, go, gl], 1)                       # (nf, 3)
        # Gi = e00 + G*(t - e00*e11) + e11*Gi*G   -> linear in (e00, D, e11)
        A = np.stack([np.ones_like(G), G, gi * G], 2)       # (nf, 3, 3)
        x = np.linalg.solve(A, np.broadcast_to(gi, G.shape)[..., None])[..., 0]
        e00, D, e11 = x[:, 0], x[:, 1], x[:, 2]
        t = D + e00 * e11
        s11s.append(np.abs(e00)); s21s.append(np.sqrt(np.abs(t)))
    k = f >= FMIN                                        # drop <100 MHz, where the cal is ill-conditioned
    return f[k], 20 * np.log10(np.mean(s11s, 0))[k], 20 * np.log10(np.mean(s21s, 0))[k]


NIST = {n: nist_throw(n) for n in range(1, 7)}
MEAS = {(sn, n, s): read_sep11(sn, n, s)
        for sn in COL for n in range(1, 7) for s in ("S11", "S21")}


def nist_on(f, n, s):
    fn, s11, s21 = NIST[n]
    return np.interp(f, fn, s11 if s == "S11" else s21)


def grid_fig(title, sub=None):
    fig, axs = plt.subplots(2, 3, figsize=(13, 7.6), sharex=True, sharey=True)
    fig.text(0.01, 0.985, title, ha="left", va="top", fontsize=14, color=INK)
    if sub:
        fig.text(0.01, 0.948, sub, ha="left", va="top", fontsize=10.5, color=INK2)
    return fig, axs.ravel()


def finish(fig, axs, ylabel, handles):
    for ax in axs[3:]:
        ax.set_xlabel("Frequency (GHz)")
    for ax in axs[::3]:
        ax.set_ylabel(ylabel)
    axs[0].set_xlim(0, FMAX)
    fig.legend(handles=handles, loc="upper right", ncol=len(handles), bbox_to_anchor=(0.995, 0.995))
    fig.text(0.01, 0.01, NOTE, fontsize=8, color=INK3, ha="left", va="bottom", wrap=True)
    fig.tight_layout(rect=(0, 0.04, 1, 0.90))


FIGS, SUMMARY = [], []

# --- 1/2: overlays -----------------------------------------------------------
for s, ylim in (("S21", (-5, 0.2)), ("S11", (-45, -5))):
    fig, axs = grid_fig(f"{s}: measured switches vs calibrated NIST reference (295 K)",
                        "NIST curve is SN0031's throw path only; measured curves also include the RFC connector section")
    for i, ax in enumerate(axs):
        n = i + 1
        fn, s11, s21 = NIST[n]
        m = fn <= FMAX
        h_n, = ax.plot(fn[m], (s21 if s == "S21" else s11)[m], color=INK, ls="--", lw=1.6, label="NIST SN0031 (calibrated)")
        hs = []
        for sn in COL:
            f, y = MEAS[(sn, n, s)]
            hs.append(ax.plot(f, y, color=COL[sn], label=f"SN{sn} (measured)")[0])
        ax.set_title(f"RF{n}", loc="left", color=INK, fontsize=12)
        ax.set_ylim(*ylim)
        if s == "S21":
            for sn in COL:
                f, y = MEAS[(sn, n, s)]
                if y.min() < ylim[0]:
                    i = np.argmin(y)
                    ax.annotate(f"SN{sn} dip: {y[i]:.1f} dB at {f[i]:.2f} GHz (off scale)",
                                xy=(f[i], ylim[0]), xytext=(0.3, ylim[0] + 0.5),
                                fontsize=9, color=INK2, arrowprops=dict(arrowstyle="->", color=INK3))
    finish(fig, axs, f"{s} (dB)", hs + [h_n])
    FIGS.append((f"{s}_vs_NIST_calibrated", fig))

# --- 3/4: differences -------------------------------------------------------
for s, ylim in (("S21", (-2.5, 2.5)), ("S11", (-20, 20))):
    sense = ("Above 0 = closer to 0 dB (more reflection) than NIST.\nSharp spikes are where nulls fall at different "
             "frequencies (different reference planes); read the overall level, not individual spikes."
             if s == "S11" else "Below 0 = more loss than NIST. Part of the offset is the RFC connector section NIST's curve excludes.")
    fig, axs = grid_fig(f"{s} difference: measured minus calibrated NIST (295 K)", sense)
    for i, ax in enumerate(axs):
        n = i + 1
        ax.axhline(0, color=INK3, lw=1)
        txt = []
        hs = []
        for sn in COL:
            f, y = MEAS[(sn, n, s)]
            k = (f >= FMIN) & (f <= FMAX)
            d = y[k] - nist_on(f[k], n, s)
            hs.append(ax.plot(f[k], d, color=COL[sn], label=f"SN{sn} minus NIST")[0])
            bands = {b: np.median(d[(f[k] >= lo) & (f[k] < hi)]) for b, (lo, hi) in
                     {"0-2": (0, 2), "2-6": (2, 6), "6-10": (6, 10.01)}.items()}
            SUMMARY.append((s, sn, n, np.median(d), bands))
            txt.append(f"SN{sn} median {np.median(d):+.2f} dB")
        ax.text(0.02, 0.04, "\n".join(txt), transform=ax.transAxes, fontsize=9, color=INK2, va="bottom")
        ax.set_title(f"RF{n}", loc="left", color=INK, fontsize=12)
        ax.set_ylim(*ylim)
    finish(fig, axs, f"Δ{s} (dB)", hs)
    FIGS.append((f"{s}_difference_vs_NIST_calibrated", fig))

with PdfPages(HERE / "Combined_vs_NIST_calibrated.pdf") as pdf:
    for name, fig in FIGS:
        fig.savefig(HERE / f"{name}.pdf")
        fig.savefig(HERE / f"{name}.png", dpi=110)
        pdf.savefig(fig)

with open(HERE / "summary_medians.csv", "w") as fh:
    fh.write("param,switch,channel,median_diff_dB,median_0-2GHz,median_2-6GHz,median_6-10GHz\n")
    for s, sn, n, med, b in SUMMARY:
        fh.write(f"{s},SN{sn},RF{n},{med:.2f},{b['0-2']:.2f},{b['2-6']:.2f},{b['6-10']:.2f}\n")
print(open(HERE / "summary_medians.csv").read())
