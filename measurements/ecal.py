"""
Calibrated S11 at the MM4250's output connectors, from its built-in
standards -- the "e-cal" of Menlo's app note and the NIST/Menlo paper
(Spietz et al., IEEE J. Microwaves 2025).

    cal    = run_ecal_set("20261015", "3K", "SN0077", repeats=2)   # sweep_db
    ideals = find_ideals("3K")
    result = correct_set(cal, ideals)        # calibrated S11 per channel
    drift(cal)                               # did anything move during the set?
    plot_ecal(result)
    export_corrected(result)                 # .s1p files, if something else needs them

How it works. The VNA sees the switch through everything in between --
cables, attenuators, couplers, amplifiers -- which a one-port error box
describes completely:

    Gm = e00 + e01e10 * Ga / (1 - e11 * Ga)

Gm is what the VNA reads, Ga what's actually at the reference plane. The
three unknowns come from measuring three things whose Ga is known: the
switch's internal open (ALL_OPEN), short and load. NIST measured what
those three look like *from each RF port's SMA connector* (their tier-2
"ideals", one set per port), so solving with port n's ideals puts the
reference plane at RF n's connector, and correcting the RF n measurement
gives the device on that connector with the whole fridge removed.

Written out, the model is linear in (e00, e11, delta = e00*e11 - e01e10):

    Gm = e00 + (Ga * Gm) * e11 - Ga * delta

so each frequency is one 3x3 solve, and the correction is

    Ga = (Gm - e00) / (Gm * e11 - delta)

That's the whole algorithm, and it's the same one scikit-rf's OnePort
uses (tests/test_ecal.py checks the two agree on NIST's own
dilution-fridge data). numpy and the standard library only, so it runs on
the DAQ machine right at the fridge.

Things this can't fix, so they're worth knowing:

  * The ideals are for NIST's switch, SN0031, not yours. Menlo lists
    device-to-device variation as "data pending". Correcting SN0077 with
    SN0031's definitions is an approximation -- checking how good it is,
    with a known standard on a port, is part of the 3 K test.
  * Use ideals for the temperature you're at. The 295 K and 3 K
    definitions differ by far more than the uncertainty. NIST found the
    3 K ones hold down to 25 mK.
  * Below ~100 MHz the NIST definitions are ill-conditioned (the port-1
    open has |G| ~ 3 at 1 MHz), so results are cropped there by default.
"""

from pathlib import Path

import numpy as np

from read_db import (
    DEFAULT_OUT_DIR_NAME,
    _columns,
    _connect,
    load_run,
    save_touchstone,
    sweep_folder,
)

# Switch state measured for each standard (see sweep_db.ECAL_STANDARDS).
STANDARD_STATES = {"open": "ALL_OPEN", "short": "INTERNAL_SHORT", "load": "INTERNAL_LOAD"}

# Below this the NIST definitions stop being three distinct standards.
DEFAULT_FMIN_HZ = 100e6

# Where find_ideals looks, relative to any folder at or above the
# starting point. First match wins. {t} is "3k" or "295k".
IDEALS_CANDIDATES = (
    "ideals_{T}",                                                   # a copy you made, e.g. ideals_3K/
    "nist_MM4250_calibration_data_2025/tier2_scikitrf_caldata/tier2_{t}1",
    "mm4250-ecal/ideals",                                           # 3 K only
)

_FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


# ---------------------------------------------------------------------------
# Standard definitions ("ideals")
# ---------------------------------------------------------------------------

def read_s1p(path):
    """
    Read a 1-port Touchstone file -> (freq_hz, gamma). Handles RI, MA and
    DB formats and any frequency unit. Kept here, rather than borrowed
    from plots.py, so this module doesn't pull in matplotlib.
    """
    scale, fmt, rows = 1e9, "MA", []   # Touchstone defaults: GHz, MA
    with open(path) as f:
        for line in f:
            line = line.split("!")[0].strip()
            if not line:
                continue
            if line.startswith("#"):
                for field in line[1:].upper().split():
                    if field in _FREQ_UNITS:
                        scale = _FREQ_UNITS[field]
                    elif field in ("RI", "MA", "DB"):
                        fmt = field
                continue
            rows.append([float(x) for x in line.split()[:3]])
    a = np.array(rows)
    freq, x, y = a[:, 0] * scale, a[:, 1], a[:, 2]
    if fmt == "RI":
        gamma = x + 1j * y
    else:
        mag = x if fmt == "MA" else 10 ** (x / 20)
        gamma = mag * np.exp(1j * np.deg2rad(y))
    return freq, gamma


def find_ideals(temperature="3K", start=None):
    """
    Find a folder of NIST standard definitions for `temperature` ("3K" or
    "295K"), searching `start` (default: this file's folder) and every
    folder above it for the names in IDEALS_CANDIDATES.

    On a laptop this finds SD Code/nist_MM4250_calibration_data_2025. On
    the DAQ machine, copy that repo's tier2_scikitrf_caldata/tier2_3k1
    folder next to the notebook as ideals_3K/ (and tier2_295k1 as
    ideals_295K/ for room temperature).
    """
    t = temperature.strip().lower()
    if t not in ("3k", "295k"):
        raise ValueError(f"NIST definitions exist for 3K and 295K only, not {temperature!r}")
    start = Path(start) if start is not None else Path(__file__).resolve().parent
    for folder in [start, *start.parents]:
        for pattern in IDEALS_CANDIDATES:
            if pattern.startswith("mm4250-ecal") and t != "3k":
                continue
            cand = folder / pattern.format(t=t, T=t.upper())
            if cand.is_dir() and _ideal_file(cand, 1, "open") is not None:
                return cand
    raise FileNotFoundError(
        f"No {temperature} definitions found at or above {start}. Pass the "
        "folder yourself, or copy tier2_scikitrf_caldata/tier2_"
        f"{t}1 next to the notebook as ideals_{t.upper()}/."
    )


def _ideal_file(ideals_dir, port, standard):
    """The file for one port/standard, in either naming scheme, or None."""
    for name in (f"port{port}_{standard}_tier2.s1p",      # NIST tier2 folders
                 f"ideal_port{port}_{standard}.s1p"):     # mm4250-ecal/ideals
        p = Path(ideals_dir) / name
        if p.is_file():
            return p
    return None


def load_ideals(ideals_dir, port, freq_hz):
    """
    Port `port`'s open/short/load definitions, interpolated onto
    `freq_hz`: {"open": array, "short": array, "load": array}.

    Real and imaginary parts are interpolated separately (what scikit-rf
    does too). NIST's files have 10001 points from 1 MHz to 20 GHz, so this
    is fine-grained; asking for anything outside that range is an error
    rather than an extrapolation.
    """
    out = {}
    for std in STANDARD_STATES:
        path = _ideal_file(ideals_dir, port, std)
        if path is None:
            raise FileNotFoundError(f"No port{port} {std} definition in {ideals_dir}")
        f, g = read_s1p(path)
        if freq_hz.min() < f.min() * (1 - 1e-9) or freq_hz.max() > f.max() * (1 + 1e-9):
            raise ValueError(
                f"{path.name} covers {f.min() / 1e9:g}-{f.max() / 1e9:g} GHz; "
                f"data spans {freq_hz.min() / 1e9:g}-{freq_hz.max() / 1e9:g} GHz"
            )
        out[std] = np.interp(freq_hz, f, g.real) + 1j * np.interp(freq_hz, f, g.imag)
    return out


# ---------------------------------------------------------------------------
# The calibration itself
# ---------------------------------------------------------------------------

def solve_error_terms(measured, ideal):
    """
    One-port error terms from three standards.

    `measured` and `ideal` are dicts {"open": G, "short": G, "load": G} of
    complex arrays on the same frequency axis. Returns a dict of arrays:
    e00 (directivity), e11 (source match), e01e10 (reflection tracking)
    and delta = e00*e11 - e01e10, plus `cond`, the condition number of
    each frequency's 3x3 system -- large means the three standards are
    close to degenerate there and the correction is untrustworthy.
    """
    stds = list(STANDARD_STATES)
    gm = np.stack([np.asarray(measured[s], complex) for s in stds], axis=1)  # (nf, 3)
    ga = np.stack([np.asarray(ideal[s], complex) for s in stds], axis=1)
    A = np.stack([np.ones_like(gm), ga * gm, -ga], axis=2)                 # (nf, 3, 3)
    x = np.linalg.solve(A, gm[..., None])[..., 0]
    e00, e11, delta = x[:, 0], x[:, 1], x[:, 2]
    return {"e00": e00, "e11": e11, "delta": delta,
            "e01e10": e00 * e11 - delta, "cond": np.linalg.cond(A)}


def apply_correction(terms, gamma_measured):
    """Raw S11 -> S11 at the reference plane, using solve_error_terms' output."""
    gm = np.asarray(gamma_measured, complex)
    return (gm - terms["e00"]) / (gm * terms["e11"] - terms["delta"])


# ---------------------------------------------------------------------------
# E-cal sets in the database
# ---------------------------------------------------------------------------

def list_sets(db_path=None):
    """
    Every e-cal set in the database, oldest first:
    [{"set": ..., "switch_serials": ..., "date_str": ..., "temp_str": ...,
      "mxc_temp_k": ..., "n_runs": ...}, ...]
    """
    con, _ = _connect(db_path)
    try:
        have = _columns(con, "runs")
        if "ecal_set" not in have:
            return []
        mxc = "r.mxc_temp_k" if "mxc_temp_k" in have else "NULL"
        rows = con.execute(
            f"SELECT r.ecal_set, r.switch_serials, r.date_str, r.temp_str, "
            f"MIN({mxc}), COUNT(*) FROM runs r WHERE r.ecal_set IS NOT NULL "
            "GROUP BY r.ecal_set ORDER BY MIN(r.run_id)"
        ).fetchall()
    finally:
        con.close()
    return [{"set": s, "switch_serials": sn, "date_str": d, "temp_str": t,
             "mxc_temp_k": m, "n_runs": n} for s, sn, d, t, m, n in rows]


def find_set(set_id=None, db_path=None):
    """
    Rebuild an e-cal set from the database, in the same shape
    run_ecal_set returns. `set_id` None means the most recent set -- so
    correct_set(None, ideals) works on the last set you took, even from a
    fresh kernel.
    """
    if set_id is None:
        sets = list_sets(db_path)
        if not sets:
            raise LookupError("No e-cal sets in the database")
        set_id = sets[-1]["set"]

    con, _ = _connect(db_path)
    try:
        if "ecal_set" not in _columns(con, "runs"):
            raise LookupError("No e-cal sets in the database")
        rows = con.execute(
            "SELECT run_id, name, ecal_repeat, ecal_role, switch_serials, date_str, temp_str "
            "FROM runs WHERE ecal_set = ? ORDER BY run_id", (set_id,)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise LookupError(f"No e-cal set {set_id!r} in the database")

    repeats = {}
    for run_id, name, k, role, *_ in rows:
        block = repeats.setdefault(int(k), {"before": {}, "ports": {}, "after": {}})
        if role == "port":
            block["ports"][int(name[2:])] = run_id        # "RF3" -> 3
        else:
            block[role][name] = run_id
    _, _, _, _, sn, date, temp = rows[0]
    return {"set": set_id, "switch_serials": sn, "date_str": date, "temp_str": temp,
            "repeats": [repeats[k] for k in sorted(repeats)]}


def _as_set(ecal, db_path):
    if ecal is None or isinstance(ecal, str):
        return find_set(ecal, db_path)
    return ecal


def _s11(run_id, db_path, freq_ref=None):
    f, data = load_run(run_id, db_path)
    if "S11" not in data:
        raise RuntimeError(f"run {run_id} has no S11")
    if freq_ref is not None and not np.array_equal(f, freq_ref):
        raise RuntimeError(
            f"run {run_id} is on a different frequency axis from the rest of "
            "the set -- setup_sweep changed partway through. Take a new set."
        )
    return f, data["S11"]


def _raw_standards(block, which, db_path, freq_ref):
    """{"open": G, ...} from a block's before/after standards (or their mean)."""
    roles = ("before", "after") if which == "mean" else (which,)
    roles = [r for r in roles if block.get(r)]
    if not roles:
        raise LookupError(f"this repeat has no {which!r} standards")
    out = {}
    for std, state in STANDARD_STATES.items():
        out[std] = np.mean([_s11(block[r][state], db_path, freq_ref)[1] for r in roles], axis=0)
    return out


def correct_set(ecal=None, ideals_dir=None, repeat=None, standards="mean",
                fmin=DEFAULT_FMIN_HZ, db_path=None):
    """
    Calibrated S11 at each measured channel's connector.

    `ecal`: what run_ecal_set returned, a set id string, or None for the
    most recent set. `ideals_dir`: a folder of NIST definitions --
    find_ideals("3K") -- for the temperature the set was taken at.

    `repeat`: which repeat to correct (1, 2, ...); None corrects every
    repeat and averages the calibrated results, which is what NIST
    recommends for beating down switch repeatability. `standards`:
    "before", "after", or "mean" (default) -- the raw standards measured
    on either side of the channels, or their average.

    Returns a dict:
        freq     Hz, cropped to >= fmin
        ports    {channel: calibrated S11}   (averaged over repeats)
        raw      {channel: raw S11}          (repeat 1, for comparison)
        terms    {channel: error terms}      (repeat 1)
        per_repeat [{channel: calibrated S11}, ...]
        plus set/switch_serials/date_str/temp_str/ideals_dir for labelling
    """
    if ideals_dir is None:
        raise ValueError("pass ideals_dir -- e.g. find_ideals('3K')")
    if standards not in ("before", "after", "mean"):
        raise ValueError(f"standards must be 'before', 'after' or 'mean', not {standards!r}")
    ecal = _as_set(ecal, db_path)
    blocks = ecal["repeats"]
    if repeat is not None:
        if not 1 <= repeat <= len(blocks):
            raise ValueError(f"repeat must be 1-{len(blocks)}, got {repeat}")
        blocks = [blocks[repeat - 1]]

    first_run = next(iter(blocks[0]["ports"].values()))
    freq, _ = _s11(first_run, db_path)
    keep = freq >= fmin
    ideals = {}
    per_repeat, raw, terms_out = [], {}, {}

    for i, block in enumerate(blocks):
        measured = _raw_standards(block, standards, db_path, freq)
        corrected = {}
        for ch, run_id in sorted(block["ports"].items()):
            if ch not in ideals:
                ideals[ch] = load_ideals(ideals_dir, ch, freq)
            terms = solve_error_terms(measured, ideals[ch])
            _, gm = _s11(run_id, db_path, freq)
            corrected[ch] = apply_correction(terms, gm)[keep]
            if i == 0:
                raw[ch] = gm[keep]
                terms_out[ch] = {k: v[keep] for k, v in terms.items()}
        per_repeat.append(corrected)

    channels = sorted(per_repeat[0])
    ports = {ch: np.mean([r[ch] for r in per_repeat], axis=0) for ch in channels}
    return {"set": ecal["set"], "switch_serials": ecal["switch_serials"],
            "date_str": ecal["date_str"], "temp_str": ecal["temp_str"],
            "ideals_dir": str(ideals_dir), "freq": freq[keep], "ports": ports,
            "raw": raw, "terms": terms_out, "per_repeat": per_repeat}


def drift(ecal=None, db_path=None, fmin=DEFAULT_FMIN_HZ, verbose=True):
    """
    How much the raw standards moved during each repeat: |G_after - G_before|
    per standard. Returns {"freq": f, "repeats": [{"open": arr, ...}, ...]}.

    For scale: the corrected result can't be better than this, and NIST's
    same-temperature repeatability was a few 1e-2. Values well above that
    mean the chain (usually a cold amplifier) drifted -- take the set
    again, and make it quicker (fewer points or channels) if it keeps
    happening.
    """
    ecal = _as_set(ecal, db_path)
    first_run = next(iter(ecal["repeats"][0]["ports"].values()))
    freq, _ = _s11(first_run, db_path)
    keep = freq >= fmin
    out = []
    for k, block in enumerate(ecal["repeats"], start=1):
        if not block.get("after"):
            continue
        before = _raw_standards(block, "before", db_path, freq)
        after = _raw_standards(block, "after", db_path, freq)
        d = {std: np.abs(after[std] - before[std])[keep] for std in STANDARD_STATES}
        out.append(d)
        if verbose:
            parts = ", ".join(f"{std} median {np.median(v):.2g} / max {np.max(v):.2g}"
                              for std, v in d.items())
            print(f"repeat {k} drift |after - before|: {parts}")
    return {"freq": freq[keep], "repeats": out}


def repeatability(result, verbose=True):
    """
    Spread of the calibrated result between repeats, per channel:
    max over repeats of |G_repeat - G_mean|. Needs a set with repeats >= 2.
    """
    reps = result["per_repeat"]
    if len(reps) < 2:
        raise ValueError("only one repeat -- nothing to compare")
    out = {}
    for ch, mean in result["ports"].items():
        out[ch] = np.max([np.abs(r[ch] - mean) for r in reps], axis=0)
        if verbose:
            print(f"RF{ch}: median {np.median(out[ch]):.2g}, max {np.max(out[ch]):.2g}")
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def export_corrected(result, out_dir=None, db_path=None):
    """
    Write each channel's calibrated S11 as a .s1p. Default folder sits in
    the usual Sweeps layout, beside the database:
    Sweeps/<serials>/<date>/<temp>/ecal_corrected_<set>/RF<n>.s1p
    Returns the paths written.
    """
    if out_dir is None:
        con, db = _connect(db_path)
        con.close()
        root = Path(db).resolve().parent / DEFAULT_OUT_DIR_NAME
        out_dir = sweep_folder(root, result["switch_serials"], result["date_str"],
                               result["temp_str"], f"ecal_corrected_{result['set']}")
    paths = []
    for ch, gamma in result["ports"].items():
        p = Path(out_dir) / f"RF{ch}.s1p"
        save_touchstone(result["freq"], gamma, p)
        paths.append(p)
        print(f"  wrote {p}")
    return paths


def plot_ecal(result, channels=None, show_raw=False, xlim=None, ylim_db=None, save=None):
    """
    Calibrated |S11| (dB) and phase for each channel -- the look-at-it-now
    plot for the fridge. `show_raw=True` overlays the uncorrected traces
    (dashed) on the magnitude panel. `save` is a path for the figure.
    Returns the two Axes.
    """
    import matplotlib.pyplot as plt

    channels = channels or sorted(result["ports"])
    f = result["freq"] / 1e9
    fig, (ax_m, ax_p) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))
    for ch in channels:
        g = result["ports"][ch]
        (line,) = ax_m.plot(f, 20 * np.log10(np.abs(g)), label=f"RF{ch}")
        ax_p.plot(f, np.rad2deg(np.angle(g)), color=line.get_color())
        if show_raw:
            ax_m.plot(f, 20 * np.log10(np.abs(result["raw"][ch])), "--",
                      color=line.get_color(), alpha=0.5)
    ax_m.set_ylabel("|S11| calibrated (dB)")
    ax_p.set_ylabel("phase (deg)")
    ax_p.set_xlabel("Frequency (GHz)")
    if ylim_db is not None and not show_raw:
        ax_m.set_ylim(*ylim_db)
    if xlim is not None:
        ax_m.set_xlim(*xlim)
    ax_m.legend(ncol=3, frameon=False)
    for ax in (ax_m, ax_p):
        ax.grid(True, alpha=0.3)
    ax_m.set_title(f"{result['switch_serials']}  {result['temp_str']}  e-cal set {result['set']}")
    fig.tight_layout()
    if save:
        fig.savefig(save, dpi=150)
    return ax_m, ax_p
