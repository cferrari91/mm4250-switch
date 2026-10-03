"""Figures for the 2026-09-25 SN0077 room-temperature calibration sweeps,
plus like-for-like comparisons with the NIST MM4250 data set.

Run from anywhere:  python make_figures.py
Needs only numpy + matplotlib. Writes one 16:9 PDF per figure (and a PNG
preview) beside this script, all_figures.pdf, and summary_values.csv.

Data used
---------
Ours (SN0077, 295 K, 25 Sep 2026, P5004B, 1 MHz-10 GHz, 10000 pts, IFBW 1 kHz, -20 dBm):
  Sweeps/SN0077/20260925/295K/RF<n>_cal     VNA correction ON, cal set
      20260911_1MHz_10GHz_female (2-port, plane at the cable ends that mate with
      the switch), VNA port 1 on RFC, port 2 on RF<n>.
  Sweeps/SN0077/20260925/295K/RF<n>_uncal   same cabling, correction OFF.
  Menlo/Sweeps/09112026_295K/77                 SN0077 on 11 Sep, same cal set.
NIST (nist_MM4250_calibration_data_2025, 295 K only):
  single_switch_0030_data  SN0030, RAW VNA data; insertion loss = S21(port1) - S21(thru),
                           i.e. a thru-normalised (response-cal) number. Only RF1 was
                           measured as a through path.
  tier2_scikitrf_caldata/tier2_295k{1,2}  SN0031 internal-standard definitions ("ideals")
                           referenced to each RF port's SMA plane. Identical to the
                           "room 295K ideals" in mm4250-ecal/assets/nist-mm4250-ideals.zip.
  tier2_3k1               the 3 K ideals. NOTE: mm4250-ecal/ideals/ (the notebook's
                           default folder) holds these 3 K ideals, not the 295 K ones.
"""
from pathlib import Path
import glob

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

HERE = Path(__file__).resolve().parent
USER = HERE.parents[3]                                   # mm4250-switch/measurements
SDCODE = USER.parents[1]
PROJ = SDCODE.parent
CALDIR = USER / "Sweeps" / "SN0077" / "20260925" / "295K"
SEP11 = PROJ / "Menlo" / "Sweeps" / "09112026_295K" / "77"
NIST = SDCODE / "nist_MM4250_calibration_data_2025"

C = {1: "#2a78d6", 2: "#eb6834", 3: "#1baf7a", 4: "#eda100", 5: "#e87ba4", 6: "#008300"}
VIOLET, RED = "#4a3aa7", "#e34948"
INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
CH = range(1, 7)
NOTE = "MM4250 SN0077 · 295 K · 25 Sep 2026"
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


# ----------------------------------------------------------------------------- I/O
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


def ours(ch, cal, state):
    tag = "cal" if cal else "uncal"
    g = glob.glob(str(CALDIR / f"RF{ch}_{tag}" / f"{state}_run*.s2p"))
    assert len(g) == 1, (ch, tag, state, g)
    return read_ts(g[0])


def read_csv(path):
    a = np.array([l.strip().split(",") for l in open(path) if l[:1].isdigit()], float)
    return a[:, 0], a[:, 1]


def db(x):
    return 20 * np.log10(np.abs(x))


def smooth_db(f, x, bw):
    """Power (|S|^2) running mean over `bw` Hz, edge-normalised, in dB."""
    n = max(1, int(round(bw / (f[1] - f[0]))) | 1)
    k = np.ones(n)
    p = np.abs(x) ** 2
    return 10 * np.log10(np.convolve(p, k, "same") / np.convolve(np.ones_like(p), k, "same"))


def smooth_lin(f, y, bw):
    n = max(1, int(round(bw / (f[1] - f[0]))) | 1)
    k = np.ones(n)
    return np.convolve(y, k, "same") / np.convolve(np.ones_like(y), k, "same")


def cinterp(fq, f, z):
    return np.interp(fq, f, z.real) + 1j * np.interp(fq, f, z.imag)


# ---------------------------------------------------------------- one-port algebra
def deembed(gin, S):
    """Load reflection at port 2 of 2-port S that gives input reflection `gin` at port 1."""
    d = gin - S["S11"]
    return d / (S["S12"] * S["S21"] + S["S22"] * d)


def bilinear_fit(x, y):
    """Per-frequency (a,b,c) with y = (a x + b)/(c x + 1) through 3 point pairs (list of arrays)."""
    A = np.stack([np.stack([xk, np.ones_like(xk), -xk * yk], -1) for xk, yk in zip(x, y)], 1)
    rhs = np.stack(y, 1)[..., None]
    return np.linalg.solve(A, rhs)[..., 0].T               # a, b, c


def bilinear_apply(abc, x):
    a, b, c = abc
    return (a * x + b) / (c * x + 1)


def ecal_correct(raw_std, ideals, raw_dut):
    """MM4250 e-cal (same math as skrf OnePort with 3 standards): raw -> ideal plane."""
    # forward model raw = (a*G + b)/(c*G + 1); invert for the DUT
    a, b, c = bilinear_fit(ideals, raw_std)
    return (raw_dut - b) / (a - c * raw_dut)


# ---------------------------------------------------------------------- load data
f, _ = ours(1, True, "RF1")
fg = f / 1e9
CAL = {ch: ours(ch, True, f"RF{ch}")[1] for ch in CH}
UNC = {ch: ours(ch, False, f"RF{ch}")[1] for ch in CH}
OPEN_C = {ch: ours(ch, True, "ALL_OPEN")[1] for ch in CH}
OPEN_U = {ch: ours(ch, False, "ALL_OPEN")[1] for ch in CH}
STD_C = {ch: {s: ours(ch, True, s)[1]["S11"] for s in ("ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD")} for ch in (1, 6)}
STD_U = {ch: {s: ours(ch, False, s)[1]["S11"] for s in ("ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD")} for ch in (1, 6)}

nf, n_port1 = read_ts(NIST / "single_switch_0030_data" / "port1_295k.s2p")
_, n_thru = read_ts(NIST / "single_switch_0030_data" / "thru_295k.s2p")
thru_reps = [read_ts(NIST / "single_switch_0030_data" / f"{n}.s2p")[1]["S21"]
             for n in ("thru_295k", "thru_repeat1_295k", "thru_repeat2_295k")]


def nist_ideals(folder, port):
    out = {}
    for s in ("open", "short", "load"):
        ff, d = read_ts(NIST / "tier2_scikitrf_caldata" / folder / f"port{port}_{s}_tier2.s1p")
        out[s] = cinterp(f, ff, d["S11"])
    return out


NI = {(run, p): nist_ideals(run, p) for run in ("tier2_295k1", "tier2_295k2", "tier2_3k1") for p in (1, 6)}

# SN0077's own internal-standard definitions at the RF<n> SMA plane, from the SOLT data:
#   Gamma_def = throw path RFC->RF<n> de-embedded from the calibrated standard reflection at RFC
OURS_DEF = {ch: {"open": deembed(STD_C[ch]["ALL_OPEN"], CAL[ch]),
                 "short": deembed(STD_C[ch]["INTERNAL_SHORT"], CAL[ch]),
                 "load": deembed(STD_C[ch]["INTERNAL_LOAD"], CAL[ch])} for ch in (1, 6)}

FIGS, SUMMARY = [], []


def save(fig, name):
    fig.savefig(HERE / f"{name}.pdf")
    fig.savefig(HERE / f"{name}.png", dpi=110)
    FIGS.append(fig)


def foot(fig, text):
    fig.text(0.99, 0.012, text, ha="right", va="bottom", fontsize=10, color=INK3)


def ch_legend(ax, loc="lower left", ncol=6, extra=()):
    h = [plt.Line2D([], [], color=C[c], lw=2.2, label=f"RF{c}") for c in CH] + list(extra)
    ax.legend(handles=h, ncol=ncol, loc=loc, handlelength=1.4, columnspacing=1.1)


# ============================================================ 1. insertion loss
fig, ax = plt.subplots(figsize=SIZE)
for ch in CH:
    ax.plot(fg, db(CAL[ch]["S21"]), color=C[ch], lw=1.4, alpha=0.95)
ax.plot([10], [-4], marker="D", color=INK2, ms=9, ls="none", clip_on=False, zorder=5)
ax.annotate("datasheet: 4 dB typ. at 10 GHz", xy=(10, -4), xytext=(6.9, -4.25), color=INK2, fontsize=12,
            va="center", arrowprops=dict(arrowstyle="-", color=INK3, lw=1))
ax.set(xlim=(0, 10), ylim=(-4.6, 0.2), xlabel="Frequency (GHz)", ylabel="|S21| through path (dB)")
ch_legend(ax)
ax.set_title("Insertion loss, RFC to each RF port (VNA-calibrated at the switch connectors)", loc="left", fontsize=14)
foot(fig, NOTE + " · 2-port cal set 20260911_1MHz_10GHz_female · 10000 pts, 1 kHz IFBW, −20 dBm")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "01_insertion_loss")

# ============================================================ 2. return loss
fig, axs = plt.subplots(1, 2, figsize=SIZE, sharey=True)
for ax, sp, title in ((axs[0], "S11", "S11 at RFC"), (axs[1], "S22", "S22 at the RF port")):
    for ch in CH:
        ax.plot(fg, smooth_db(f, CAL[ch][sp], 20e6), color=C[ch], lw=1.3)
    ax.axhline(-7, color=INK2, ls=(0, (5, 4)), lw=1.2)
    ax.set(xlim=(0, 10), ylim=(-50, 0), xlabel="Frequency (GHz)", title=title)
    ax.title.set_ha("left"); ax.title.set_x(0)
axs[0].text(0.15, -6.3, "datasheet RFC return loss: 7 dB typ. at 10 GHz", color=INK2, fontsize=11)
axs[0].set_ylabel("Return (dB, 20 MHz power average)")
ch_legend(axs[1], loc="lower right", ncol=3)
foot(fig, NOTE + " · calibrated · each curve is one channel closed, other VNA port on that channel")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "02_return_loss")

# ============================================================ 3. off-state isolation
fig, ax = plt.subplots(figsize=SIZE)
for ch in CH:
    ax.plot(fg, -smooth_db(f, OPEN_C[ch]["S21"], 20e6), color=C[ch], lw=1.4)
ax.axhline(40, color=INK2, ls=(0, (5, 4)), lw=1.3)
ax.text(0.15, 41.2, "datasheet off-state isolation: 40 dB typ. at 10 GHz", color=INK2, fontsize=12)
floor = np.median([-smooth_db(f, OPEN_C[c]["S21"], 20e6) for c in CH], 0)
ax.set(xlim=(0, 10), ylim=(30, 110), xlabel="Frequency (GHz)", ylabel="Isolation = −|S21| (dB)")
ax.axhspan(85, 110, color=INK3, alpha=0.10, lw=0)
ax.text(9.9, 104, r"$\gtrsim$85 dB: at or near the VNA noise floor (−20 dBm source, 1 kHz IFBW)", ha="right", color=INK2, fontsize=11)
ch_legend(ax, loc="lower left")
ax.set_title("Off-state isolation: ALL_OPEN, VNA on RFC and on the named RF port", loc="left", fontsize=14)
foot(fig, NOTE + " · calibrated · 20 MHz power average")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "03_isolation_all_open")

# ============================================================ 4. what the cal removes
fig, axs = plt.subplots(2, 1, figsize=SIZE, sharex=True, gridspec_kw=dict(height_ratios=[1.5, 1]))
ax = axs[0]
ax.plot(fg, db(UNC[1]["S21"]), color=INK3, lw=1.3, label="uncalibrated (VNA correction off)")
ax.plot(fg, db(CAL[1]["S21"]), color=C[1], lw=1.6, label="calibrated")
ax.set(ylim=(-11, 0.3), ylabel="RF1 |S21| (dB)")
ax.legend(loc="lower left")
ax = axs[1]
diffs = []
for ch in CH:
    d = smooth_db(f, UNC[ch]["S21"], 20e6) - smooth_db(f, CAL[ch]["S21"], 20e6)
    diffs.append(d)
    ax.plot(fg, d, color=C[ch], lw=1.2)
spread = np.ptp(np.array(diffs)[:, (f > 0.1e9)], 0)
SUMMARY.append(("uncal-cal spread across 6 channels (dB), median / max above 0.1 GHz", "all", np.median(spread)))
ax.set(xlim=(0, 10), ylim=(-6, 0), xlabel="Frequency (GHz)", ylabel="uncal − cal (dB)")
ax.text(0.15, -5.5, f"six channels overlap: typical spread {np.median(spread):.3f} dB (max {spread.max():.2f} dB)\n"
        r"$\rightarrow$ same test-cable response each time, so re-mating the port-2 cable was repeatable", color=INK2, fontsize=11)
axs[0].set_title("What the VNA calibration removes (test cables + VNA test-set response)", loc="left", fontsize=14)
foot(fig, NOTE + " · correction toggled with cabling untouched")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "04_cal_vs_uncal")

# ============================================================ 5. internal standards at RFC
fig, axs = plt.subplots(1, 2, figsize=SIZE)
cols = {"ALL_OPEN": INK, "INTERNAL_SHORT": INK3, "INTERNAL_LOAD": VIOLET}
lab = {"ALL_OPEN": "open (ALL_OPEN)", "INTERNAL_SHORT": "internal short", "INTERNAL_LOAD": "internal load"}
for s in cols:
    for ch, ls in ((1, "-"), (6, (0, (5, 3)))):
        y = STD_C[ch][s]
        axs[0].plot(fg, smooth_db(f, y, 10e6), color=cols[s], ls=ls, lw=1.6,
                    label=f"{lab[s]}, RF{ch} setup")
    d = np.angle(STD_C[6][s] / STD_C[1][s], deg=True)
    dm = db(STD_C[6][s]) - db(STD_C[1][s])
    if s != "INTERNAL_LOAD":
        axs[1].plot(fg, smooth_lin(f, dm, 20e6), color=cols[s], lw=1.6, label=f"{lab[s]}: Δ|Γ|")
axs[0].set(xlim=(0, 10), ylim=(-45, 2), xlabel="Frequency (GHz)", ylabel="|Γ| at RFC (dB)",
           title="Internal standards seen at RFC")
axs[0].legend(loc="lower left", fontsize=10.5)
axs[1].axhline(0, color=INK3, lw=1)
axs[1].set(xlim=(0, 10), ylim=(-0.1, 0.1), xlabel="Frequency (GHz)", ylabel="RF6 setup − RF1 setup (dB)",
           title="Repeatability, ~22 min apart")
axs[1].legend(loc="lower left", fontsize=10.5)
for ax in axs:
    ax.title.set_ha("left"); ax.title.set_x(0)
foot(fig, NOTE + " · calibrated at RFC · the RF6-setup measurement was ~22 min after the RF1 one")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "05_internal_standards")

# ============================================================ 6. ALL_OPEN repeatability across 6 setups
fig, axs = plt.subplots(1, 2, figsize=SIZE)
ref = np.mean([OPEN_C[c]["S11"] for c in CH], 0)
for ch in CH:
    z = OPEN_C[ch]["S11"] / ref
    axs[0].plot(fg, smooth_lin(f, 20 * np.log10(np.abs(z)), 20e6), color=C[ch], lw=1.3)
    axs[1].plot(fg, smooth_lin(f, np.angle(z, deg=True), 20e6), color=C[ch], lw=1.3)
axs[0].set(xlim=(0, 10), ylim=(-0.08, 0.08), xlabel="Frequency (GHz)", ylabel="Δ|S11| vs mean (dB)",
           title="ALL_OPEN, S11 at RFC, six setups")
axs[1].set(xlim=(0, 10), ylim=(-2, 2), xlabel="Frequency (GHz)", ylabel="Δ phase vs mean (deg)",
           title="same, phase")
for ax in axs:
    ax.axhline(0, color=INK3, lw=1)
    ax.title.set_ha("left"); ax.title.set_x(0)
ch_legend(axs[1], loc="lower left", ncol=3)
foot(fig, NOTE + " · calibrated · port-1 cable never moved, so this is switch actuation + drift over ~25 min")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "06_open_repeatability")

# ============================================================ 7. day-to-day vs 11 Sep
fig, axs = plt.subplots(1, 2, figsize=SIZE)
for ch in CH:
    fs, y11 = read_csv(SEP11 / f"RF{ch}_S21.csv")
    today = np.interp(fs, f, smooth_db(f, CAL[ch]["S21"], 20e6))
    k = fs >= 0.1e9
    axs[0].plot(fs[k] / 1e9, today[k] - y11[k], color=C[ch], lw=1.3)
    fs, s11 = read_csv(SEP11 / f"RF{ch}_S11.csv")
    t11 = np.interp(fs, f, smooth_db(f, CAL[ch]["S11"], 20e6))
    axs[1].plot(fs[k] / 1e9, t11[k] - s11[k], color=C[ch], lw=1.1)
    SUMMARY.append(("S21 25Sep - 11Sep (dB), median 0.1-10 GHz", f"RF{ch}", np.median(today[k] - y11[k])))
axs[0].set(xlim=(0, 10), ylim=(-0.5, 0.5), xlabel="Frequency (GHz)", ylabel="25 Sep − 11 Sep (dB)",
           title="|S21| through")
axs[1].set(xlim=(0, 10), ylim=(-3, 3), xlabel="Frequency (GHz)", ylabel="25 Sep − 11 Sep (dB)",
           title="|S11| at RFC")
axs[1].text(0.15, -2.75, "spikes = return-loss nulls (dB of a tiny number), not real changes", color=INK2, fontsize=10.5)
for ax in axs:
    ax.axhline(0, color=INK3, lw=1)
    ax.title.set_ha("left"); ax.title.set_x(0)
ch_legend(axs[0], loc="lower left", ncol=3)
foot(fig, "MM4250 SN0077 · 295 K · same cal set both days (made 11 Sep) · 11 Sep traces are 1000-pt CSV exports")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "07_day_to_day_vs_sep11")

# ============================================================ 8. NIST insertion-loss comparison (RF1)
nist_il = db(n_port1["S21"]) - db(n_thru["S21"])
nk = nf <= 10.001e9
nist_il_s = smooth_lin(nf[nk], nist_il[nk], 100e6)
ours_s = {ch: smooth_db(f, CAL[ch]["S21"], 100e6) for ch in CH}
thru_rep = max(np.max(np.abs(db(t) - db(thru_reps[0]))[nk & (nf > 0.1e9)]) for t in thru_reps[1:])
fig, axs = plt.subplots(2, 1, figsize=SIZE, sharex=True, gridspec_kw=dict(height_ratios=[1.5, 1]))
ax = axs[0]
band = np.array([ours_s[c] for c in CH])
ax.fill_between(fg, band.min(0), band.max(0), color=C[1], alpha=0.15, lw=0, label="SN0077 RF1–RF6 range")
ax.plot(fg, ours_s[1], color=C[1], lw=2.0, label="SN0077 RF1 (SOLT-calibrated), 25 Sep 2026")
ax.plot(nf[nk] / 1e9, nist_il_s, color=INK, ls=(0, (6, 3)), lw=2.0, label="NIST SN0030 RF1 (raw, thru-normalised), 2025")
ax.set(ylim=(-4.2, 0.2), ylabel="RF1 insertion loss (dB)")
ax.legend(loc="lower left")
ax = axs[1]
d = ours_s[1] - np.interp(f, nf[nk], nist_il_s)
k = f >= 0.1e9
ax.plot(fg[k], d[k], color=C[1], lw=1.8)
ax.axhline(0, color=INK3, lw=1)
ax.set(xlim=(0, 10), ylim=(-1, 1), xlabel="Frequency (GHz)", ylabel="SN0077 − NIST (dB)")
ax.text(0.15, 0.62, "above 0 = our switch has less loss", color=INK2, fontsize=11)
axs[0].set_title("Insertion loss vs NIST at 295 K: both referenced to the switch SMA connectors", loc="left", fontsize=14)
foot(fig, "Both curves 100 MHz running mean (NIST's thru-normalisation leaves mismatch ripple a SOLT cal removes) · "
     f"NIST thru repeats agree to {thru_rep:.2f} dB")
fig.tight_layout(rect=(0, 0.03, 1, 1))
save(fig, "08_nist_insertion_loss_RF1")
for lo, hi in ((0.1, 2), (2, 6), (6, 10)):
    m = (f >= lo * 1e9) & (f < hi * 1e9)
    SUMMARY.append((f"IL SN0077 RF1 - NIST SN0030 RF1 (dB), median {lo}-{hi} GHz", "RF1", np.median(d[m])))

# ============================================================ 9. ideals vs NIST
fig, axs = plt.subplots(1, 2, figsize=SIZE, sharey=True)
scol = {"open": INK, "short": C[2], "load": VIOLET}
k = f >= 0.1e9
for ax, p in zip(axs, (1, 6)):
    for s in ("open", "short", "load"):
        dv = np.abs(OURS_DEF[p][s] - NI[("tier2_295k1", p)][s])
        rv = np.abs(NI[("tier2_295k2", p)][s] - NI[("tier2_295k1", p)][s])
        ax.plot(fg[k], smooth_lin(f, dv, 50e6)[k], color=scol[s], lw=1.9, label=f"{s}: SN0077 vs NIST SN0031")
        ax.plot(fg[k], smooth_lin(f, rv, 50e6)[k], color=scol[s], lw=1.2, ls=(0, (2, 2)),
                label=f"{s}: NIST cal 1 vs cal 2 (same switch)")
        for lo, hi in ((0.1, 2), (2, 6), (6, 10)):
            m = (f >= lo * 1e9) & (f < hi * 1e9)
            SUMMARY.append((f"|dGamma| {s} def SN0077-NIST, median {lo}-{hi} GHz", f"port{p}", np.median(dv[m])))
    ax.set_yscale("log")
    ax.set(xlim=(0, 10), ylim=(1e-3, 1), xlabel="Frequency (GHz)", title=f"RF{p} ideals (295 K)")
    ax.title.set_ha("left"); ax.title.set_x(0)
axs[0].set_ylabel("|Γ_ours − Γ_NIST|  (vector difference)")
hs = [plt.Line2D([], [], color=scol[s_], lw=2.2, label=s_) for s_ in scol] + [
    plt.Line2D([], [], color=INK2, lw=1.9, label="SN0077 vs NIST SN0031"),
    plt.Line2D([], [], color=INK2, lw=1.2, ls=(0, (2, 2)), label="NIST cal 1 vs cal 2, same switch (NIST's own repeatability)")]
fig.legend(handles=hs, loc="upper left", bbox_to_anchor=(0.01, 0.935), ncol=5, fontsize=10.5, handlelength=1.8, columnspacing=1.2)
fig.suptitle("Internal-standard definitions at the RF-port SMA plane: our SOLT-derived vs NIST's 295 K ideals",
             x=0.01, ha="left", fontsize=14)
foot(fig, r"Ours: calibrated standard at RFC with the calibrated RFC$\rightarrow$RFn path de-embedded · 50 MHz running mean · "
     "0.01 ≈ −40 dB error vector")
fig.tight_layout(rect=(0, 0.03, 1, 0.89))
save(fig, "09_ideals_vs_nist")

# ============================================================ 10. what an e-cal with NIST ideals would report
fig, axs = plt.subplots(1, 2, figsize=SIZE)
p = 1
truth = [OURS_DEF[p][s] for s in ("open", "short", "load")]
rows = []
for run, lab_, ls in (("tier2_295k1", "NIST 295 K ideals", "-"), ("tier2_3k1", "NIST 3 K ideals (mm4250-ecal default folder)", (0, (5, 3)))):
    nist = [NI[(run, p)][s] for s in ("open", "short", "load")]
    H = bilinear_fit(truth, nist)                       # maps true Gamma -> what the e-cal reports
    for g, nm, col in ((-1 + 0j, "flush short", C[2]), (1 + 0j, "ideal open", INK)):
        rep = bilinear_apply(H, g * np.ones_like(f))
        axs[0].plot(fg[k], smooth_lin(f, db(rep), 50e6)[k], color=col, ls=ls, lw=1.6, label=f"{nm}, {lab_}")
    rep0 = bilinear_apply(H, np.zeros_like(f))
    axs[1].plot(fg[k], smooth_db(f, rep0, 50e6)[k], color=VIOLET, ls=ls, lw=1.6, label=f"matched load, {lab_}")
# real data: the e-cal workflow on today's uncalibrated RF1 data (DUT = what's plugged into RF1: port-2 cable)
raw_std = [STD_U[1][s] for s in ("ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD")]
g_true = ecal_correct(raw_std, truth, UNC[1]["S11"])
g_nist = ecal_correct(raw_std, [NI[("tier2_295k1", 1)][s] for s in ("open", "short", "load")], UNC[1]["S11"])
axs[1].plot(fg[k], smooth_db(f, g_true, 50e6)[k], color=C[1], lw=1.8, label="real DUT at RF1 (port-2 cable): SN0077 ideals")
axs[1].plot(fg[k], smooth_db(f, g_nist, 50e6)[k], color=C[1], lw=1.4, ls=(0, (1.5, 2)), label="same raw data, NIST 295 K ideals")
axs[0].axhline(0, color=INK3, lw=1)
axs[0].set(xlim=(0, 10), ylim=(-3, 3), xlabel="Frequency (GHz)", ylabel="reported |Γ| (dB), truth = 0 dB",
           title="Full reflectors")
axs[1].set(xlim=(0, 10), ylim=(-60, 0), xlabel="Frequency (GHz)", ylabel="reported |Γ| (dB)",
           title="Low reflection")
axs[0].legend(loc="lower left", fontsize=9.5)
axs[1].legend(loc="lower right", fontsize=9.5)
for ax in axs:
    ax.title.set_ha("left"); ax.title.set_x(0)
fig.suptitle("E-cal on SN0077 RF1 with NIST's generic ideals: the error you would get", x=0.01, ha="left", fontsize=14)
foot(fig, "Truth = SN0077 ideals derived from the SOLT data · 50 MHz running mean")
fig.tight_layout(rect=(0, 0.03, 1, 0.95))
save(fig, "10_ecal_error_nist_ideals")
for lo, hi in ((0.1, 2), (2, 6), (6, 10)):
    m = (f >= lo * 1e9) & (f < hi * 1e9)
    SUMMARY.append((f"|g_nist-g_true| real RF1 DUT, median {lo}-{hi} GHz", "RF1", np.median(np.abs(g_nist - g_true)[m])))

# ============================================================ summary table
marks = [1, 2, 4, 6, 8, 10]
with open(HERE / "summary_values.csv", "w") as fh:
    fh.write("quantity,channel," + ",".join(f"{m} GHz" for m in marks) + "\n")
    for ch in CH:
        idx = [np.argmin(abs(f - m * 1e9)) for m in marks]
        il = smooth_db(f, CAL[ch]["S21"], 20e6)
        s11 = smooth_db(f, CAL[ch]["S11"], 20e6)
        s22 = smooth_db(f, CAL[ch]["S22"], 20e6)
        iso = -smooth_db(f, OPEN_C[ch]["S21"], 20e6)
        for nm, arr in (("|S21| dB", il), ("|S11| RFC dB", s11), ("|S22| RF port dB", s22), ("isolation ALL_OPEN dB", iso)):
            fh.write(f"{nm},RF{ch}," + ",".join(f"{arr[i]:.2f}" for i in idx) + "\n")
    fh.write("\nquantity,channel,value\n")
    for q, chn, v in SUMMARY:
        fh.write(f"{q},{chn},{v:.4f}\n")

with PdfPages(HERE / "all_figures.pdf") as pdf:
    for fig in FIGS:
        pdf.savefig(fig)
print(open(HERE / "summary_values.csv").read())
