"""
Resonator Q fits with fit uncertainties (python q_fit_uncertainty.py [--out | --mag | --phase | --circle]), 2026-10-08 SN0077 295 K check.

Same procedure as q_fit_resonator_20261008.ipynb (skrf Qfactor, reflection,
NLQFIT7, rough fit -> crop to 2 f_L/Q_L -> refit, A = None), then:

  * refits the same weighted model with scipy least_squares, starting from
    skrf's answer, to get a parameter covariance (J^T W J)^-1 * s^2 with
    s^2 = weighted residual variance (curve_fit-style, absolute_sigma=False);
  * propagates it to f0, Q_L, Q_i, Q_e, beta by Monte Carlo (20k draws);
  * reports the spread of the two e-cal repeats, and window / model
    sensitivity, as a check on whether the statistical number is realistic.

Model (MAT 58 eq. 26, as skrf NLQFIT7 fits it):
    S(f) = (c2 + c3 / (1 + 2j Q_L (f - f_L)/f_L)) * exp(j m7a (f - f_L))
    d = |c3| / |c2|, beta = d / (2 - d), Q_i = Q_L (1 + beta), Q_e = Q_i / beta
"""
import sys
import warnings
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
import skrf as rf
from skrf.qfactor import Qfactor

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ecal  # noqa: E402

warnings.filterwarnings("ignore")
SET = "20261008T160234.969"
DB = HERE / "mm4250_sweeps.db"
METHOD, SPAN_MULT = "NLQFIT7", 2.0
Q_L0, F_L0 = 40, 549e6
RNG = np.random.default_rng(0)


def net(f, g):
    return rf.Network(frequency=rf.Frequency.from_f(f, unit="hz"),
                      s=np.asarray(g).reshape(-1, 1, 1))


def remove_delay(f, g, f_dip=F_L0, keep_out=15e6):
    off = np.abs(f - f_dip) > keep_out
    slope = np.polyfit(f[off], np.unwrap(np.angle(g))[off], 1)[0]
    tau = -slope / (2 * np.pi)
    return g * np.exp(2j * np.pi * f * tau), tau


def model(p, f):
    m1, m2, m3, m4, ql, fl, m7 = p
    y = 1 / (1 + 2j * ql * (f - fl) / fl)
    return ((m1 + 1j * m2) + (m3 + 1j * m4) * y) * np.exp(1j * m7 * (f - fl))


def derived(p):
    m1, m2, m3, m4, ql, fl, _ = np.moveaxis(np.atleast_2d(p), -1, 0)
    d = np.abs(m3 + 1j * m4) / np.abs(m1 + 1j * m2)
    beta = d / (2 - d)
    qi = ql * (1 + beta)
    return {"f0_MHz": fl / 1e6, "Q_L": ql, "Q_i": qi, "Q_e": qi / beta, "beta": beta}


def skrf_fit(f, g, span_mult=SPAN_MULT, method=METHOD, window=True):
    n = net(f, g)
    qf = Qfactor(n.s11, res_type="reflection", Q_L0=Q_L0, f_L0=F_L0)
    qf.fit(method=method)
    if window:
        span = span_mult * qf.f_L / qf.Q_L
        n = n[f"{qf.f_L - span / 2}-{qf.f_L + span / 2}Hz"].copy()
    qf2 = Qfactor(n.s11, res_type="reflection")
    res = qf2.fit(method=method)
    q0 = qf2.Q_unloaded(A=None)
    ql = qf2.Q_L
    out = {"f0_MHz": qf2.f_L / 1e6, "Q_L": ql, "Q_i": q0, "Q_e": q0 * ql / (q0 - ql),
           "beta": q0 / ql - 1, "rms": float(np.ravel(res.RMS_Error)[0])}
    return out, res, n.f, n.s[:, 0, 0], qf2


def with_uncertainty(f, g, **kw):
    out, res, fw, gw, qf2 = skrf_fit(f, g, **kw)
    if kw.get("method", METHOD) != "NLQFIT7":
        return out, None
    # same angular weights skrf ends its loop plan with
    w = Qfactor.angular_weights(fw, res["f_L"], res["Q_L"])
    sw = np.sqrt(np.concatenate([w, w]))
    p0 = [res["m1"], res["m2"], res["m3"], res["m4"], res["Q_L"], res["f_L"], res["m7a"]]
    scale = np.array([1, 1, 1, 1, 10, 1e6, 1e-8])

    def resid(x):
        e = gw - model(x * scale, fw)
        return np.concatenate([e.real, e.imag]) * sw

    sol = least_squares(resid, np.array(p0) / scale, method="lm", xtol=1e-14, ftol=1e-14)
    J = sol.jac
    dof = 2 * len(fw) - 7
    s2 = np.sum(sol.fun ** 2) / dof
    cov_x = np.linalg.inv(J.T @ J) * s2
    p = sol.x * scale
    cov = cov_x * np.outer(scale, scale)
    draws = RNG.multivariate_normal(p, cov, size=20000)
    dd = derived(draws)
    centre = {k: float(v[0]) for k, v in derived(p).items()}
    sig = {k: float(np.std(v)) for k, v in dd.items()}
    # residual autocorrelation: how many points are effectively independent
    e = gw - model(p, fw)
    r = np.concatenate([e.real, e.imag])
    ac1 = np.corrcoef(e.real[:-1], e.real[1:])[0, 1]
    n_eff_factor = (1 + ac1) / (1 - ac1) if ac1 > 0 else 1.0
    return out, {"centre": centre, "sigma": sig, "npts": len(fw),
                 "span_MHz": (fw[0] / 1e6, fw[-1] / 1e6), "ac1": ac1,
                 "inflate": np.sqrt(n_eff_factor), "rms": float(np.sqrt(np.mean(np.abs(e) ** 2)))}


def main():
    nist = ecal.correct_set(SET, ecal.find_ideals("295K", start=HERE), sparam="S21", db_path=DB)
    perf = ecal.correct_set(SET, ecal.PERFECT, sparam="S21", db_path=DB)
    f = nist["freq"]
    raw_g, tau = remove_delay(f, nist["raw"][1])
    vna = {}
    for name in ("zoomed_in_resonator_S11_VNA_20261008.s1p", "Trace_00000_297.85K.s1p"):
        vna[name] = ecal.read_s1p(HERE / "Sweeps" / name)

    traces = {
        "VNA cal, resonator alone (zoomed .s1p, 8 Oct)": vna["zoomed_in_resonator_S11_VNA_20261008.s1p"],
        "Switch, e-cal NIST 295K": (f, nist["ports"][1]),
        "Switch, e-cal perfect": (perf["freq"], perf["ports"][1]),
        f"Switch, raw S21 ({tau*1e9:.2f} ns removed)": (f, raw_g),
    }
    print(f"skrf {rf.__version__}  {METHOD}  window {SPAN_MULT} f_L/Q_L\n")
    hdr = f"{'':48s} {'f0 MHz':>18s} {'Q_L':>12s} {'Q_i':>12s} {'Q_e':>12s} {'beta':>14s}  pts  ac1  infl"
    print(hdr)
    for name, (ff, gg) in traces.items():
        out, u = with_uncertainty(ff, gg)
        c, s = u["centre"], u["sigma"]
        print(f"{name:48s} {c['f0_MHz']:9.4f}+/-{s['f0_MHz']:.4f} "
              f"{c['Q_L']:6.2f}+/-{s['Q_L']:.2f} {c['Q_i']:6.2f}+/-{s['Q_i']:.2f} "
              f"{c['Q_e']:6.2f}+/-{s['Q_e']:.2f} {c['beta']:6.4f}+/-{s['beta']:.4f} "
              f"{u['npts']:4d} {u['ac1']:.2f} {u['inflate']:.1f}")
        print(f"{'   skrf itself':48s} {out['f0_MHz']:9.4f}         {out['Q_L']:6.2f}       "
              f"{out['Q_i']:6.2f}       {out['Q_e']:6.2f}       {out['beta']:6.4f}   "
              f"span {u['span_MHz'][0]:.2f}-{u['span_MHz'][1]:.2f}  rms {u['rms']:.1e}")

    print("\nRaw S21, full span (as fit_resonator_q.py / report footnote):")
    out, *_ = skrf_fit(f, raw_g, window=False)
    print("  ", {k: round(v, 3) for k, v in out.items()})

    print("\nRepeat to repeat:")
    for lab, s in (("NIST", nist), ("perfect", perf)):
        for i, rep in enumerate(s["per_repeat"], 1):
            o, *_ = skrf_fit(s["freq"], rep[1])
            print(f"  {lab} rep {i}: " + ", ".join(f"{k} {o[k]:.3f}" for k in ("f0_MHz", "Q_L", "Q_i", "Q_e", "beta")))
    # raw per repeat
    for i in (1, 2):
        sr = ecal.correct_set(SET, ecal.PERFECT, sparam="S21", repeat=i, db_path=DB)
        g, _ = remove_delay(sr["freq"], sr["raw"][1])
        o, *_ = skrf_fit(sr["freq"], g)
        print(f"  raw rep {i}: " + ", ".join(f"{k} {o[k]:.3f}" for k in ("f0_MHz", "Q_L", "Q_i", "Q_e", "beta")))

    print("\nWindow / model sensitivity:")
    for name, (ff, gg) in traces.items():
        if name.startswith("VNA cal, Trace"):
            continue
        row = []
        for sm in (1.0, 2.0, 3.0):
            o, *_ = skrf_fit(ff, gg, span_mult=sm)
            row.append(f"win{sm:g}: Qi {o['Q_i']:.2f} Qe {o['Q_e']:.2f} f0 {o['f0_MHz']:.3f}")
        o, *_ = skrf_fit(ff, gg, method="NLQFIT8")
        row.append(f"FIT8: Qi {o['Q_i']:.2f} Qe {o['Q_e']:.2f} f0 {o['f0_MHz']:.3f}")
        print(f"  {name[:30]:30s} " + " | ".join(row))




# ---------------------------------------------------------------------------
# Table (CSV) + data-vs-fit figure. Run:  python q_fit_uncertainty.py --out
# ---------------------------------------------------------------------------
def write_outputs(out_dir):
    import csv
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    nist = ecal.correct_set(SET, ecal.find_ideals("295K", start=HERE), sparam="S21", db_path=DB)
    perf = ecal.correct_set(SET, ecal.PERFECT, sparam="S21", db_path=DB)
    f = nist["freq"]
    raw_g, tau = remove_delay(f, nist["raw"][1])
    f_v, g_v = ecal.read_s1p(HERE / "Sweeps" / "zoomed_in_resonator_S11_VNA_20261008.s1p")
    traces = [
        ("raw", f"Before correction: raw S21 via switch ({tau * 1e9:.1f} ns delay removed)",
         f, raw_g, "#c8641e", nist["per_repeat"]),
        ("NIST", "After e-cal, NIST 295 K definitions", f, nist["ports"][1], "#2a78d6", nist["per_repeat"]),
        ("perfect", "After e-cal, perfect standards", perf["freq"], perf["ports"][1], "#1baf7a", perf["per_repeat"]),
        ("vna", "Resonator alone, VNA cal (reference)", f_v, g_v, "#101513", None),
    ]
    rows = []
    fig, axes = plt.subplots(2, 4, figsize=(16, 7.6), gridspec_kw={"height_ratios": [1.5, 1]})
    for j, (key, label, ff, gg, col, reps) in enumerate(traces):
        out, res, fw, gw, _ = skrf_fit(ff, gg)
        _, u = with_uncertainty(ff, gg)
        c, s, k = u["centre"], u["sigma"], u["inflate"]
        sys_ = {}
        alts = [skrf_fit(ff, gg, span_mult=m)[0] for m in (1.0, 3.0)] + [skrf_fit(ff, gg, method="NLQFIT8")[0]]
        for q in ("f0_MHz", "Q_i", "Q_e", "beta"):
            sys_[q] = max(abs(a[q] - out[q]) for a in alts)
        if key == "raw":
            rep_vals = []
            for i in (1, 2):
                sr = ecal.correct_set(SET, ecal.PERFECT, sparam="S21", repeat=i, db_path=DB)
                rep_vals.append(skrf_fit(sr["freq"], remove_delay(sr["freq"], sr["raw"][1])[0])[0])
        elif reps:
            rep_vals = [skrf_fit(ff, r[1])[0] for r in reps]
        else:
            rep_vals = None
        row = {"measurement": label, "f0_MHz": c["f0_MHz"], "Q_L": c["Q_L"], "Q_i": c["Q_i"],
               "Q_e": c["Q_e"], "beta": c["beta"]}
        for q in ("f0_MHz", "Q_L", "Q_i", "Q_e", "beta"):
            row[f"{q}_fit_sigma"] = s[q]
            row[f"{q}_fit_sigma_x_corr"] = s[q] * k
        for q in ("f0_MHz", "Q_i", "Q_e", "beta"):
            row[f"{q}_win_model_spread"] = sys_[q]
            row[f"{q}_repeat_diff"] = abs(rep_vals[0][q] - rep_vals[1][q]) if rep_vals else ""
        row.update({"fit_rms": u["rms"], "resid_lag1_corr": u["ac1"], "npts": u["npts"],
                    "window_MHz": f"{u['span_MHz'][0]:.2f}-{u['span_MHz'][1]:.2f}"})
        rows.append(row)

        p = [res["m1"], res["m2"], res["m3"], res["m4"], res["Q_L"], res["f_L"], res["m7a"]]
        fine = np.linspace(fw[0], fw[-1], 600)
        fit = model(p, fine)
        a1, a2 = axes[0, j], axes[1, j]
        st = max(1, len(fw) // 120)
        a1.plot(fw[::st] / 1e6, 20 * np.log10(np.abs(gw[::st])), "o", ms=3, color=col, alpha=0.5, mew=0, label="data")
        a1.plot(fine / 1e6, 20 * np.log10(np.abs(fit)), color=col, lw=1.6, label="fit")
        a1.set_title(label, fontsize=10)
        a1.set_xlabel("Frequency (MHz)")
        if j == 0:
            a1.set_ylabel("|S| (dB)")
        txt = (f"$f_0$ = {c['f0_MHz']:.3f} MHz\n$Q_i$ = {c['Q_i']:.2f}\n$Q_e$ = {c['Q_e']:.2f}\n"
               f"$\\beta$ = {c['beta']:.3f}")
        a1.text(0.03, 0.04, txt, transform=a1.transAxes, fontsize=9.5, va="bottom")
        a1.legend(loc="upper right", fontsize=8.5, frameon=False)
        th = np.linspace(0, 2 * np.pi, 300)
        a2.plot(gw[::st].real, gw[::st].imag, "o", ms=3, color=col, alpha=0.5, mew=0)
        a2.plot(fit.real, fit.imag, color=col, lw=1.6)
        a2.set_aspect("equal", adjustable="datalim")
        a2.set_xlabel("Re")
        if j == 0:
            a2.set_ylabel("Im")
        for a in (a1, a2):
            a.grid(True, color="#e3e6e4")
            for sp in ("top", "right"):
                a.spines[sp].set_visible(False)
    fig.suptitle("Resonator fit before and after the switch e-cal (MM4250 SN0077, RF1, 295 K, 8 Oct 2026)",
                 fontsize=13, x=0.01, ha="left")
    fig.text(0.01, 0.005, f"e-cal set {SET} · skrf Qfactor reflection, NLQFIT7 (incl. line-length term), "
             "window 2 f_L/Q_L · fitted curve drawn with the line-length term · dots data, lines fit",
             fontsize=8.5, color="#48504c")
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "09_qfit_before_after_ecal.pdf")
    fig.savefig(out_dir / "09_qfit_before_after_ecal.png", dpi=150)
    with open(out_dir / "q_fit_uncertainty.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()})
    for r in rows:
        print(r)


def write_mag_overlays(out_dir, kind="mag"):
    """Three |S| (kind="mag"), phase (kind="phase") or complex-plane (kind="circle") plots: before cal, NIST e-cal, perfect e-cal,
    each over the VNA-cal resonator alone. Phase: unwrapped, in degrees, each curve shifted by a
    whole number of turns so it sits near 0 deg; the raw trace has its fitted cable delay removed."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    INK, INK2, INK3, GRID = "#101513", "#48504c", "#6e7672", "#e3e6e4"
    plt.rcParams.update({
        "pdf.fonttype": 42, "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 12, "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": INK3, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "legend.frameon": False,
    })
    nist = ecal.correct_set(SET, ecal.find_ideals("295K", start=HERE), sparam="S21", db_path=DB)
    perf = ecal.correct_set(SET, ecal.PERFECT, sparam="S21", db_path=DB)
    f_v, g_v = ecal.read_s1p(HERE / "Sweeps" / "zoomed_in_resonator_S11_VNA_20261008.s1p")
    xlim = (535, 562)

    def total(ff, gg):
        """Centre values and a combined 1-sigma: fit sigma (x residual-correlation factor)
        in quadrature with the window (1x/3x) and model (NLQFIT8) spread."""
        out = skrf_fit(ff, gg)[0]
        _, u = with_uncertainty(ff, gg)
        alts = [skrf_fit(ff, gg, span_mult=m)[0] for m in (1.0, 3.0)] + [skrf_fit(ff, gg, method="NLQFIT8")[0]]
        sig = {q: float(np.hypot(u["sigma"][q] * u["inflate"], max(abs(a[q] - out[q]) for a in alts)))
               for q in ("f0_MHz", "Q_i", "Q_e")}
        return out, sig

    def lab(name, rs):
        r, e = rs
        return (f"{name}:  $f_0$ = {r['f0_MHz']:.3f} $\\pm$ {e['f0_MHz']:.3f} MHz,  "
                f"$Q_i$ = {r['Q_i']:.1f} $\\pm$ {e['Q_i']:.1f},  $Q_e$ = {r['Q_e']:.1f} $\\pm$ {e['Q_e']:.1f}")

    raw_nodelay, tau = remove_delay(nist["freq"], nist["raw"][1])
    if kind == "mag":
        figs = [
            ("10_mag_before_cal_vs_vna", "before calibration", "Raw $S_{21}$ through circulator, switch and cable; no correction.",
             nist["freq"], nist["raw"][1], raw_nodelay, "#c8641e", "Via switch, raw"),
            ("11_mag_NIST_cal_vs_vna", "e-cal (NIST definitions)", "NIST 295 K definitions (SN0031), plane at RF1 connector.",
             nist["freq"], nist["ports"][1], nist["ports"][1], "#2a78d6", "Via switch, e-cal (NIST)"),
            ("12_mag_perfect_cal_vs_vna", "e-cal (perfect standards)", "Perfect standards, plane inside the switch.",
             perf["freq"], perf["ports"][1], perf["ports"][1], "#1baf7a", "Via switch, e-cal (perfect)"),
        ]
    else:
        pre = "13_phase" if kind == "phase" else "13_circle"
        figs = [
            (f"{pre}_before_cal_vs_vna", "before calibration",
             f"Raw $S_{{21}}$ through circulator, switch and cable; no correction except its {tau * 1e9:.1f} ns cable delay removed.",
             nist["freq"], raw_nodelay, raw_nodelay, "#c8641e", "Via switch, raw"),
            (pre.replace("13", "14") + "_NIST_cal_vs_vna", "e-cal (NIST definitions)", "NIST 295 K definitions (SN0031), plane at RF1 connector.",
             nist["freq"], nist["ports"][1], nist["ports"][1], "#2a78d6", "Via switch, e-cal (NIST)"),
            (pre.replace("13", "15") + "_perfect_cal_vs_vna", "e-cal (perfect standards)", "Perfect standards, plane inside the switch.",
             perf["freq"], perf["ports"][1], perf["ports"][1], "#1baf7a", "Via switch, e-cal (perfect)"),
        ]

    def yvals(g, f=None, ref=None):
        """dB, or unwrapped phase in deg. The reference is put near 0 deg (whole turns);
        another trace is shifted by whole turns to sit closest to the reference."""
        if kind == "mag":
            return 20 * np.log10(np.abs(g))
        ph = np.degrees(np.unwrap(np.angle(g)))
        if ref is None:
            return ph - 360 * np.round(np.mean(ph) / 360)
        fr, pr = ref
        return ph - 360 * np.round(np.mean(ph - np.interp(f, fr, pr)) / 360)

    ref = total(f_v, g_v)
    for stem, title, sub, ff, gg, gfit, col, name in figs:
        r = total(ff, gfit)
        fig = plt.figure(figsize=(11, 7.4))
        ax_t = fig.add_axes([0.08, 0.735, 0.89, 0.12])
        if kind == "circle":
            ax = fig.add_axes([0.08, 0.06, 0.89, 0.645])
            th = np.linspace(0, 2 * np.pi, 400)
            ax.plot(np.cos(th), np.sin(th), color=INK3, lw=1)
            ax.axhline(0, color=GRID, lw=0.8, zorder=0)
            ax.axvline(0, color=GRID, lw=0.8, zorder=0)
            k = (f_v / 1e6 >= xlim[0]) & (f_v / 1e6 <= xlim[1])
            ax.plot(g_v[k].real, g_v[k].imag, color=INK, lw=2)
            k = (ff / 1e6 >= xlim[0]) & (ff / 1e6 <= xlim[1])
            ax.plot(gg[k].real, gg[k].imag, color=col, lw=2)
            ax.set_aspect("equal")
            ax.set_xlim(-1.08, 1.08)
            ax.set_ylim(-1.08, 1.08)
            ax.set_xticks([-1, -0.5, 0, 0.5, 1])
            ax.set_yticks([-1, -0.5, 0, 0.5, 1])
            ax.grid(False)
            ax.set_xlabel("Re S11")
            ax.set_ylabel("Im S11")
        else:
            ax = fig.add_axes([0.08, 0.09, 0.89, 0.615])
            k = (f_v / 1e6 >= xlim[0]) & (f_v / 1e6 <= xlim[1])
            y_ref = yvals(g_v[k])
            ax.plot(f_v[k] / 1e6, y_ref, color=INK, lw=2)
            f_ref = f_v[k]
            k = (ff / 1e6 >= xlim[0]) & (ff / 1e6 <= xlim[1])
            ax.plot(ff[k] / 1e6, yvals(gg[k], ff[k], (f_ref, y_ref)), color=col, lw=2)
            ax.set_xlim(*xlim)
            if kind == "mag":
                ax.set_ylim(-31, 1)
                ax.set_ylabel("Magnitude (dB)")
            else:
                ax.set_ylabel("Phase (deg)")
            ax.set_xlabel("Frequency (MHz)")

        ax_t.axis("off")
        rows = [("Resonator alone, VNA cal", ref, INK), (name, r, col)]
        cells = [[f"\u2014\u2014  {n}", f"{v['f0_MHz']:.2f}",
                  f"{v['Q_i']:.1f} \u00b1 {e['Q_i']:.1f}", f"{v['Q_e']:.1f} \u00b1 {e['Q_e']:.1f}",
                  f"{v['beta']:.2f}"] for n, (v, e), _ in rows]
        tab = ax_t.table(cellText=cells, colLabels=["", "$f_0$ (MHz)", "$Q_i$", "$Q_e$", r"$\beta$"],
                         colWidths=[0.40, 0.18, 0.14, 0.15, 0.08], cellLoc="center", loc="center",
                         bbox=[0, 0, 1, 1])
        tab.auto_set_font_size(False)
        tab.set_fontsize(11.5)
        for (rr, cc), cell in tab.get_celld().items():
            cell.set_edgecolor(GRID)
            cell.visible_edges = "B" if rr == 0 else ("B" if rr == 1 else "")
            if rr == 0:
                cell.set_text_props(color=INK2)
            else:
                cell.set_text_props(color=rows[rr - 1][2])
                if cc == 0:
                    cell.set_text_props(ha="left", color=rows[rr - 1][2])
                    cell._loc = "left"
        fig.text(0.08, 0.955, f"Resonator {dict(mag='|S11|', phase='S11 phase', circle='S11, complex plane')[kind]}: VNA calibration vs. switch, {title}", fontsize=16, color=INK)
        fig.text(0.08, 0.915, sub + (f"  {xlim[0]}-{xlim[1]} MHz shown." if kind == "circle" else ""),
                 fontsize=11, color=INK2)
        fig.savefig(out_dir / f"{stem}.pdf")
        fig.savefig(out_dir / f"{stem}.png", dpi=150)
        plt.close(fig)
        print("wrote", stem, r)


if __name__ == "__main__":
    if "--mag" in sys.argv:
        write_mag_overlays(HERE / "figures" / "SN0077" / "20261008" / "295K")
    elif "--circle" in sys.argv:
        write_mag_overlays(HERE / "figures" / "SN0077" / "20261008" / "295K", kind="circle")
    elif "--phase" in sys.argv:
        write_mag_overlays(HERE / "figures" / "SN0077" / "20261008" / "295K", kind="phase")
    elif "--out" in sys.argv:
        write_outputs(HERE / "figures" / "SN0077" / "20261008" / "295K")
    else:
        main()
