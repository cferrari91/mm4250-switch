"""
Which uncorrected error term moves which Q (2026-10-08 SN0077 295 K, RF1).
Rebuilds the raw S21 from the NIST-corrected trace with the e-cal's error terms
(repeat 1), switches the terms on one at a time, refits (skrf Qfactor, NLQFIT7,
window 2 f_L/Q_L, A=None), and writes 16_error_term_breakdown.pdf/png.
    python error_term_breakdown.py
"""
import warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ecal
import q_fit_uncertainty as q

warnings.filterwarnings("ignore")
OUT = q.HERE / "figures" / "SN0077" / "20261008" / "295K"
INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
UP, DOWN = "#c8641e", "#2a78d6"

nist = ecal.correct_set(q.SET, ecal.find_ideals("295K", start=q.HERE), sparam="S21", repeat=1, db_path=q.DB)
f, t, G = nist["freq"], nist["terms"][1], nist["ports"][1]
z = np.zeros_like(G)
k = np.argmin(abs(f - 549e6))


def fwd(e00, e11, tr):
    return e00 + tr * G / (1 - e11 * G)


cases = [
    ("Nothing (NIST e-cal)", G, False),
    (f"Loss + delay only (tracking, {20 * np.log10(abs(t['e01e10'][k])):.1f} dB)", fwd(z, z, t["e01e10"]), True),
    (f"Loss + delay + directivity (|e00| = {abs(t['e00'][k]):.2f})", fwd(t["e00"], z, t["e01e10"]), True),
    (f"Loss + delay + source match (|e11| = {abs(t['e11'][k]):.2f})", fwd(z, t["e11"], t["e01e10"]), True),
    ("All three (= raw data)", fwd(t["e00"], t["e11"], t["e01e10"]), True),
]
assert np.max(np.abs(cases[-1][1] - nist["raw"][1])) < 1e-12
rows = []
for name, g, strip in cases:
    o = q.skrf_fit(f, q.remove_delay(f, g)[0] if strip else g)[0]
    rows.append((name, o))
base = rows[0][1]

plt.rcParams.update({"pdf.fonttype": 42, "font.family": "sans-serif",
                     "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]})
fig = plt.figure(figsize=(10, 4.1))
ax = fig.add_axes([0.04, 0.04, 0.92, 0.66])
ax.axis("off")
cols = ["What is left uncorrected", "$Q_L$", "$Q_i$", "$Q_e$", r"$\beta$"]
cells, colors = [], []
for i, (name, o) in enumerate(rows):
    row, crow = [name], [INK]
    for key, fmt in (("Q_L", "{:.1f}"), ("Q_i", "{:.1f}"), ("Q_e", "{:.1f}"), ("beta", "{:.2f}")):
        v = o[key]
        txt = fmt.format(v)
        if i > 0 and key != "beta":
            d = v - base[key]
            txt += f"  ({d:+.0f})"
        row.append(txt)
        if i == 0 or abs(v - base[key]) < (0.03 if key == "beta" else 2.0):
            crow.append(INK)
        else:
            crow.append(UP if v > base[key] else DOWN)
    cells.append(row)
    colors.append(crow)
tab = ax.table(cellText=cells, colLabels=cols, colWidths=[0.47, 0.13, 0.13, 0.16, 0.08],
               cellLoc="center", loc="center", bbox=[0, 0, 1, 1])
tab.auto_set_font_size(False)
tab.set_fontsize(12)
for (r, c), cell in tab.get_celld().items():
    cell.set_edgecolor(GRID)
    cell.visible_edges = "B" if r in (0, 1, len(rows) - 1) else ""
    if r == 0:
        cell.set_text_props(color=INK2)
    else:
        cell.set_text_props(color=colors[r - 1][c], weight="bold" if r == len(rows) else "normal")
    if c == 0:
        cell.set_text_props(ha="left")
        cell._loc = "left"
fig.text(0.04, 0.91, "Which uncorrected error term moves which Q", fontsize=16, color=INK)
fig.text(0.04, 0.80, "Raw trace rebuilt from the NIST e-cal result using the e-cal's own error terms,\n"
         "switched on one at a time and refit. Brackets: change vs. the e-cal. Orange = higher, blue = lower.",
         fontsize=10.5, color=INK2, linespacing=1.5)
fig.savefig(OUT / "16_error_term_breakdown.pdf")
fig.savefig(OUT / "16_error_term_breakdown.png", dpi=200)
for name, o in rows:
    print(f"{name:45s} {o['Q_L']:.1f} {o['Q_i']:.1f} {o['Q_e']:.1f} {o['beta']:.2f}")
