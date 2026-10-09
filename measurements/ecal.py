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

    result = correct_and_plot(cal, ideals, sparam="S21")   # drift + correct + plot, one call

Which trace. `sparam` picks which raw S-parameter the correction is
solved on: S11, S12, S21 or S22. Any of them works mathematically -- each
trace the VNA reads is a fixed linear network with the switch as its one
unknown termination, so each one has this same 3-term form -- but it only
means something on a trace the switch's reflection actually reaches. In
the fridge's circulator wiring (VNA port 1 -> circulator -> RFC, the
reflection back up to VNA port 2) that's S21; with a single line to RFC
it's S11. The default, sparam=None, uses S21 if the set's runs have it
(2-port sets) and S11 otherwise (1-port sets), so older sets still
correct the way they always did.

Which definitions. The correction needs to know what the internal
standards actually are, as seen from the plane you want to calibrate to.
Three choices, all passed as correct_set's `ideals_dir`:

    find_ideals("295K")   NIST's, measured on their switch (SN0031). Plane:
                          each RF connector. The only choice when cold.
    make_ideals(kit)      Your own switch's, from an external cal kit's
                          open/short/load on each RF connector (taken
                          with sweep_db.run_kit_set). Plane: each RF
                          connector. Warm only -- a kit can't be swapped
                          on a cold connector.
    "perfect"             Assume open = +1, short = -1, load = 0. No data
                          needed, but the plane is wherever the internal
                          standards sit (inside the switch, near RFC), so
                          the result still includes the RFC -> RF n path,
                          and it's only as good as the internal standards
                          are close to ideal.

plot_compare overlays the results of several of these on one channel.

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

# The traces a 2-port run holds; correct_set/drift take any of them as `sparam`.
SPARAMS = ("S11", "S12", "S21", "S22")

# Below this the NIST definitions stop being three distinct standards.
DEFAULT_FMIN_HZ = 100e6

# Pass as ideals_dir to assume ideal standards (see "Which definitions").
PERFECT = "perfect"
PERFECT_VALUES = {"open": 1 + 0j, "short": -1 + 0j, "load": 0j}

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
    """The file for one port/standard, in any of the naming schemes, or None."""
    for name in (f"port{port}_{standard}_tier2.s1p",      # NIST tier2 folders
                 f"ideal_port{port}_{standard}.s1p",      # mm4250-ecal/ideals
                 f"port{port}_{standard}.s1p"):           # make_ideals
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

    `ideals_dir` "perfect" (PERFECT) gives open +1, short -1, load 0 at
    every frequency, for any port.
    """
    if _is_perfect(ideals_dir):
        return {std: np.full(len(freq_hz), PERFECT_VALUES[std]) for std in STANDARD_STATES}
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
        out[std] = np.interp(freq_hz, f, np.real(g)) + 1j * np.interp(freq_hz, f, np.imag(g))
    return out


def _is_perfect(ideals_dir):
    return isinstance(ideals_dir, str) and ideals_dir.strip().lower() == PERFECT


def _ideals_name(ideals_dir):
    """Short name for a definitions choice, for plot labels: "perfect", "tier2_295k1", ..."""
    return PERFECT if _is_perfect(ideals_dir) else Path(ideals_dir).name


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
        have = _columns(con, "runs")
        if "ecal_set" not in have:
            raise LookupError("No e-cal sets in the database")
        # dut_label only exists once some run has had a label.
        dut = "dut_label" if "dut_label" in have else "NULL"
        rows = con.execute(
            "SELECT run_id, name, ecal_repeat, ecal_role, switch_serials, date_str, temp_str, "
            f"{dut} FROM runs WHERE ecal_set = ? ORDER BY run_id", (set_id,)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise LookupError(f"No e-cal set {set_id!r} in the database")

    repeats, labels = {}, {}
    for run_id, name, k, role, _, _, _, label in rows:
        block = repeats.setdefault(int(k), {"before": {}, "ports": {}, "after": {}})
        if role == "port":
            ch = int(name[2:])                            # "RF3" -> 3
            block["ports"][ch] = run_id
            if label:
                labels.setdefault(ch, label)
        else:
            block[role][name] = run_id
    _, _, _, _, sn, date, temp, _ = rows[0]
    return {"set": set_id, "switch_serials": sn, "date_str": date, "temp_str": temp,
            "labels": {ch: labels[ch] for ch in sorted(labels)},
            "repeats": [repeats[k] for k in sorted(repeats)]}


def _as_set(ecal, db_path):
    if ecal is None or isinstance(ecal, str):
        return find_set(ecal, db_path)
    return ecal


def _vna_corrected(run_id, db_path):
    """Whether the VNA's own correction was on for a run: True, False, or None if not recorded."""
    con, _ = _connect(db_path)
    try:
        if "vna_correction_enabled" not in _columns(con, "runs"):
            return None
        (v,) = con.execute("SELECT vna_correction_enabled FROM runs WHERE run_id = ?",
                           (run_id,)).fetchone()
    finally:
        con.close()
    return None if v is None else bool(v)


def _trace_name(result):
    """ "VNA-corrected" if the set was taken with the VNA's cal on underneath, else "raw". """
    return "VNA-corrected" if result.get("vna_cal") else "raw"


def _raw(run_id, db_path, sparam, freq_ref=None):
    """(freq, raw `sparam` trace) of one run in a set."""
    f, data = load_run(run_id, db_path)
    if sparam not in data:
        raise RuntimeError(f"run {run_id} has no {sparam} (it has {', '.join(data)})")
    if freq_ref is not None and not np.array_equal(f, freq_ref):
        raise RuntimeError(
            f"run {run_id} is on a different frequency axis from the rest of "
            "the set -- setup_sweep changed partway through. Take a new set."
        )
    return f, data[sparam]


def _pick_sparam(ecal, sparam, db_path):
    """
    Check `sparam`, or choose one: S21 if the set's runs have it (2-port),
    else S11. Returns (sparam, freq) from the set's first channel run.
    """
    return _pick_sparam_of_run(next(iter(ecal["repeats"][0]["ports"].values())),
                               sparam, db_path)


def _pick_sparam_of_run(run_id, sparam, db_path):
    freq, data = load_run(run_id, db_path)
    if sparam is None:
        sparam = "S21" if "S21" in data else "S11"
    if sparam not in SPARAMS:
        raise ValueError(f"sparam must be one of {SPARAMS}, got {sparam!r}")
    if sparam not in data:
        raise ValueError(f"this set's runs have no {sparam} (they have {', '.join(data)})")
    return sparam, freq


def _raw_standards(block, which, db_path, freq_ref, sparam):
    """{"open": G, ...} from a block's before/after standards (or their mean)."""
    roles = ("before", "after") if which == "mean" else (which,)
    roles = [r for r in roles if block.get(r)]
    if not roles:
        raise LookupError(f"this repeat has no {which!r} standards")
    out = {}
    for std, state in STANDARD_STATES.items():
        out[std] = np.mean([_raw(block[r][state], db_path, sparam, freq_ref)[1] for r in roles],
                           axis=0)
    return out


def correct_set(ecal=None, ideals_dir=None, sparam=None, repeat=None, standards="mean",
                fmin=DEFAULT_FMIN_HZ, db_path=None):
    """
    Calibrated S11 at each measured channel's connector, solved on the raw
    `sparam` trace ("S11", "S12", "S21" or "S22"; None = S21 for a 2-port
    set, S11 for a 1-port one -- see "Which trace" at the top).

    `ecal`: what run_ecal_set returned, a set id string, or None for the
    most recent set. `ideals_dir`: the standards' definitions, for the
    temperature the set was taken at -- a NIST folder (find_ideals("3K")),
    your own from make_ideals, or "perfect". See "Which definitions" at
    the top for what each one means.

    `repeat`: which repeat to correct (1, 2, ...); None corrects every
    repeat and averages the calibrated results, which is what NIST
    recommends for beating down switch repeatability. `standards`:
    "before", "after", or "mean" (default) -- the raw standards measured
    on either side of the channels, or their average.

    Returns a dict:
        freq     Hz, cropped to >= fmin
        ports    {channel: calibrated S11}   (averaged over repeats)
        raw      {channel: raw `sparam`}     (repeat 1, for comparison)
        terms    {channel: error terms}      (repeat 1)
        labels   {channel: label}            (labelled channels only; see run_ecal_set)
        per_repeat [{channel: calibrated S11}, ...]
        sparam   the raw trace it was solved on
        vna_cal  True if the VNA's own cal was on underneath (run_ecal_set's
                 vna_cal=True; then "raw" above means VNA-corrected),
                 False if raw, None if the runs don't say
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

    sparam, freq = _pick_sparam(ecal, sparam, db_path)
    keep = freq >= fmin
    ideals = {}
    per_repeat, raw, terms_out = [], {}, {}

    for i, block in enumerate(blocks):
        measured = _raw_standards(block, standards, db_path, freq, sparam)
        corrected = {}
        for ch, run_id in sorted(block["ports"].items()):
            if ch not in ideals:
                ideals[ch] = load_ideals(ideals_dir, ch, freq)
            terms = solve_error_terms(measured, ideals[ch])
            _, gm = _raw(run_id, db_path, sparam, freq)
            corrected[ch] = apply_correction(terms, gm)[keep]
            if i == 0:
                raw[ch] = gm[keep]
                terms_out[ch] = {k: v[keep] for k, v in terms.items()}
        per_repeat.append(corrected)

    channels = sorted(per_repeat[0])
    ports = {ch: np.mean([r[ch] for r in per_repeat], axis=0) for ch in channels}
    # Sets taken before labels existed have no "labels" key.
    labels = {ch: text for ch, text in (ecal.get("labels") or {}).items() if ch in ports}
    return {"set": ecal["set"], "switch_serials": ecal["switch_serials"],
            "date_str": ecal["date_str"], "temp_str": ecal["temp_str"], "sparam": sparam,
            "ideals_dir": str(ideals_dir), "freq": freq[keep], "ports": ports,
            "labels": labels, "raw": raw, "terms": terms_out, "per_repeat": per_repeat,
            "vna_cal": _vna_corrected(next(iter(blocks[0]["ports"].values())), db_path)}


def drift(ecal=None, db_path=None, fmin=DEFAULT_FMIN_HZ, sparam=None, verbose=True):
    """
    How much the raw standards moved during each repeat: |G_after - G_before|
    per standard. Returns {"freq": f, "repeats": [{"open": arr, ...}, ...]}.

    For scale: the corrected result can't be better than this, and NIST's
    same-temperature repeatability was a few 1e-2. Values well above that
    mean the chain (usually a cold amplifier) drifted -- take the set
    again, and make it quicker (fewer points or channels) if it keeps
    happening. `sparam` is the trace to check, as in correct_set.
    """
    ecal = _as_set(ecal, db_path)
    sparam, freq = _pick_sparam(ecal, sparam, db_path)
    keep = freq >= fmin
    out = []
    for k, block in enumerate(ecal["repeats"], start=1):
        if not block.get("after"):
            continue
        before = _raw_standards(block, "before", db_path, freq, sparam)
        after = _raw_standards(block, "after", db_path, freq, sparam)
        d = {std: np.abs(after[std] - before[std])[keep] for std in STANDARD_STATES}
        out.append(d)
        if verbose:
            parts = ", ".join(f"{std} median {np.median(v):.2g} / max {np.max(v):.2g}"
                              for std, v in d.items())
            print(f"repeat {k} drift |after - before| ({sparam}): {parts}")
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
    plot for the fridge. Channels labelled in run_ecal_set show as
    "RF1 (resonator)" in the legend. `show_raw=True` overlays the uncorrected traces
    (dashed) on the magnitude panel. `save` is a path for the figure.
    Returns the two Axes.
    """
    import matplotlib.pyplot as plt

    channels = channels or sorted(result["ports"])
    labels = result.get("labels") or {}
    f = result["freq"] / 1e9
    fig, (ax_m, ax_p) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))
    for ch in channels:
        g = result["ports"][ch]
        name = f"RF{ch} ({labels[ch]})" if labels.get(ch) else f"RF{ch}"
        (line,) = ax_m.plot(f, 20 * np.log10(np.abs(g)), label=name)
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
    ax_m.legend(ncol=2 if labels else 3, frameon=False)   # labels make entries wider
    for ax in (ax_m, ax_p):
        ax.grid(True, alpha=0.3)
    ax_m.set_title(f"{result['switch_serials']}  {result['temp_str']}  e-cal set {result['set']}"
                   f"  (from {_trace_name(result)} {result.get('sparam', 'S11')},"
                   f" ideals {_ideals_name(result['ideals_dir'])})", fontsize=10)
    fig.tight_layout()
    if save:
        fig.savefig(save, dpi=150)
    return ax_m, ax_p


def correct_and_plot(ecal=None, ideals_dir=None, sparam=None, show_raw=False, save=None,
                     db_path=None):
    """
    Everything you'd look at right after taking a set, in one call:
    drift (did the chain move during the set?), the correction,
    repeatability (if there's more than one repeat) and the plot.
    Returns correct_set's result dict, for export_corrected etc.

        result = correct_and_plot(cal, find_ideals("3K"), sparam="S21")
    """
    ecal = _as_set(ecal, db_path)
    drift(ecal, db_path=db_path, sparam=sparam)
    result = correct_set(ecal, ideals_dir, sparam=sparam, db_path=db_path)
    if len(result["per_repeat"]) > 1:
        repeatability(result)
    plot_ecal(result, show_raw=show_raw, save=save)
    return result


def plot_compare(results, labels=None, channel=None, show_raw=False, xlim=None, ylim_db=None,
                 save=None):
    """
    Several correct_set results overlaid for one channel -- e.g. the same
    set corrected with NIST's definitions, your own and "perfect":

        plot_compare([correct_set(cal, find_ideals("295K")),
                      correct_set(cal, my_ideals),
                      correct_set(cal, "perfect")],
                     labels=["NIST (SN0031)", "kit (SN0077)", "perfect"])

    `labels` defaults to each result's definitions folder name. `channel`
    defaults to the first channel of the first result; every result needs
    it. `show_raw=True` adds the first result's raw trace (grey, dashed) to
    the magnitude panel -- the uncalibrated reading, for scale (or the
    VNA-corrected one, for a set taken with vna_cal=True). `save` is
    a path for the figure. Returns the two Axes.
    """
    import matplotlib.pyplot as plt

    results = list(results)
    if not results:
        raise ValueError("pass at least one result")
    labels = list(labels) if labels is not None else [_ideals_name(r["ideals_dir"]) for r in results]
    if len(labels) != len(results):
        raise ValueError(f"{len(results)} results but {len(labels)} labels")
    ch = channel if channel is not None else sorted(results[0]["ports"])[0]
    missing = [lab for r, lab in zip(results, labels) if ch not in r["ports"]]
    if missing:
        raise ValueError(f"RF{ch} isn't in: {', '.join(missing)}")

    fig, (ax_m, ax_p) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))
    first = results[0]
    if show_raw:
        ax_m.plot(first["freq"] / 1e9, 20 * np.log10(np.abs(first["raw"][ch])), "--",
                  color="0.5",
                  label=(f"{first.get('sparam', 'S11')}, VNA cal only" if first.get("vna_cal")
                         else f"raw {first.get('sparam', 'S11')} (uncalibrated)"))
    for r, lab in zip(results, labels):
        f, g = r["freq"] / 1e9, r["ports"][ch]
        (line,) = ax_m.plot(f, 20 * np.log10(np.abs(g)), label=lab)
        ax_p.plot(f, np.rad2deg(np.angle(g)), color=line.get_color())
    ax_m.set_ylabel("|S11| calibrated (dB)")
    ax_p.set_ylabel("phase (deg)")
    ax_p.set_xlabel("Frequency (GHz)")
    if ylim_db is not None:
        ax_m.set_ylim(*ylim_db)
    if xlim is not None:
        ax_m.set_xlim(*xlim)
    ax_m.legend(frameon=False)
    for ax in (ax_m, ax_p):
        ax.grid(True, alpha=0.3)
    dut = (first.get("labels") or {}).get(ch)
    ax_m.set_title(f"{first['switch_serials']}  {first['temp_str']}  RF{ch}"
                   + (f" ({dut})" if dut else "") + f"  e-cal set {first['set']}", fontsize=10)
    fig.tight_layout()
    if save:
        fig.savefig(save, dpi=150)
    return ax_m, ax_p


# ---------------------------------------------------------------------------
# Your own definitions, from an external cal kit (sweep_db.run_kit_set)
# ---------------------------------------------------------------------------
#
# A kit set has, all raw: the internal standards (before), the kit's
# open/short/load on each channel's RF connector, then the internal
# standards again (after). The kit readings give that channel's error
# terms with the plane at its RF connector; correcting the internal
# standards' readings with them gives what the internal standards look
# like from that connector -- the same thing NIST's per-port files are,
# but for your switch. Everything outside the switch (cables, circulator)
# cancels, so the definitions belong to the switch alone and work with
# any e-cal set at the same temperature, whatever the wiring.

def list_kit_sets(db_path=None):
    """Every kit set in the database, oldest first: [{"set", "switch_serials", "date_str", "temp_str", "n_runs"}, ...]."""
    con, _ = _connect(db_path)
    try:
        if "kit_set" not in _columns(con, "runs"):
            return []
        rows = con.execute(
            "SELECT kit_set, switch_serials, date_str, temp_str, COUNT(*) FROM runs "
            "WHERE kit_set IS NOT NULL GROUP BY kit_set ORDER BY MIN(run_id)"
        ).fetchall()
    finally:
        con.close()
    return [{"set": s, "switch_serials": sn, "date_str": d, "temp_str": t, "n_runs": n}
            for s, sn, d, t, n in rows]


def find_kit_set(set_id=None, db_path=None):
    """
    Rebuild a kit set from the database, in the shape run_kit_set
    returns. `set_id` None means the most recent one.
    """
    if set_id is None:
        sets = list_kit_sets(db_path)
        if not sets:
            raise LookupError("No kit sets in the database")
        set_id = sets[-1]["set"]
    con, _ = _connect(db_path)
    try:
        have = _columns(con, "runs")
        if "kit_set" not in have:
            raise LookupError("No kit sets in the database")
        # kit_std only exists once some kit run has been recorded.
        std = "kit_std" if "kit_std" in have else "NULL"
        rows = con.execute(
            f"SELECT run_id, name, kit_role, {std}, switch_serials, date_str, temp_str "
            "FROM runs WHERE kit_set = ? ORDER BY run_id", (set_id,)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise LookupError(f"No kit set {set_id!r} in the database")
    out = {"set": set_id, "switch_serials": rows[0][4], "date_str": rows[0][5],
           "temp_str": rows[0][6], "before": {}, "kit": {}, "after": {}}
    for run_id, name, role, std, *_ in rows:
        if role == "kit":
            out["kit"].setdefault(int(name[2:]), {})[std] = run_id     # "RF1" -> 1
        else:
            out[role][name] = run_id
    return out


def _kit_values(kit_defs, freq):
    """The kit's open/short/load definitions on `freq`: perfect, a folder, or a dict."""
    if kit_defs is None:
        return {std: np.full(len(freq), PERFECT_VALUES[std]) for std in STANDARD_STATES}
    if isinstance(kit_defs, (str, Path)):
        kit_defs = {std: Path(kit_defs) / f"{std}.s1p" for std in STANDARD_STATES}
    out = {}
    for std in STANDARD_STATES:
        if std not in kit_defs:
            raise ValueError(f"kit_defs has no {std!r}")
        v = kit_defs[std]
        if isinstance(v, (str, Path)):
            f, g = read_s1p(v)
            if freq.min() < f.min() * (1 - 1e-9) or freq.max() > f.max() * (1 + 1e-9):
                raise ValueError(f"{Path(v).name} doesn't cover "
                                 f"{freq.min() / 1e9:g}-{freq.max() / 1e9:g} GHz")
            out[std] = np.interp(freq, f, np.real(g)) + 1j * np.interp(freq, f, np.imag(g))
        else:
            out[std] = np.broadcast_to(np.asarray(v, complex), freq.shape).copy()
    return out


def _write_definition(path, freq, gamma, comment):
    """One definition as a .s1p, at more precision than save_touchstone's 7 digits."""
    rows = "\n".join(f"{f:.1f} {g.real:.12e} {g.imag:.12e}" for f, g in zip(freq, gamma))
    Path(path).write_text(f"! {comment}\n# HZ S RI R 50\n{rows}\n")


def make_ideals(kit_set=None, out_dir=None, kit_defs=None, sparam=None, overwrite=False,
                db_path=None):
    """
    Turn a kit set (sweep_db.run_kit_set) into a folder of your switch's
    own definitions, one port<n>_<std>.s1p per channel and standard, that
    correct_set takes as `ideals_dir` exactly like NIST's:

        kit = run_kit_set(date_str, "295K", "SN0077", channels=[1])
        my_ideals = make_ideals(kit)
        result = correct_set(cal, my_ideals)

    `kit_set`: what run_kit_set returned, a set id, or None for the most
    recent one. `sparam`: the trace to solve on, as in correct_set
    (default S21 for 2-port runs). `out_dir` defaults to
    ideals_<temp>_<serials>_kit<set id>/ beside the database; it refuses
    to write over definition files already there unless overwrite=True.

    `kit_defs`: what the kit's standards are. None (default) assumes
    perfect ones (open +1, short -1, load 0) -- good to a few GHz with
    a decent kit; above that the kit's real open capacitance and short
    inductance start to show. For better, pass a folder with open.s1p,
    short.s1p and load.s1p, or a dict {"open": ..., "short": ..., "load": ...}
    of complex numbers, arrays on the set's frequency axis, or .s1p paths.

    Prints how much the internal standards drifted between the before and
    after readings (the definitions can't be better than that), and
    returns the folder's path. The definitions cover the kit set's
    frequency range, so an e-cal set corrected with them has to sit inside
    it; the frequency points themselves don't have to match.
    """
    kit_set = kit_set if isinstance(kit_set, dict) else find_kit_set(kit_set, db_path)
    if not kit_set["kit"]:
        raise ValueError("this kit set has no kit runs")
    first = next(iter(kit_set["kit"].values()))["open"]
    sparam, freq = _pick_sparam_of_run(first, sparam, db_path)
    kit = _kit_values(kit_defs, freq)

    reads = {std: [_raw(kit_set[r][state], db_path, sparam, freq)[1]
                   for r in ("before", "after") if kit_set.get(r)]
             for std, state in STANDARD_STATES.items()}
    internal = {std: np.mean(v, axis=0) for std, v in reads.items()}
    if all(len(v) == 2 for v in reads.values()):
        keep = freq >= DEFAULT_FMIN_HZ
        parts = ", ".join(f"{std} median {np.median(d):.2g} / max {np.max(d):.2g}"
                          for std, v in reads.items() for d in [np.abs(v[1] - v[0])[keep]])
        print(f"internal standards drift |after - before| ({sparam}): {parts}")

    if out_dir is None:
        con, db = _connect(db_path)
        con.close()
        out_dir = (Path(db).resolve().parent /
                   f"ideals_{kit_set['temp_str']}_{kit_set['switch_serials']}_kit{kit_set['set']}")
    out_dir = Path(out_dir)
    targets = {(ch, std): out_dir / f"port{ch}_{std}.s1p"
               for ch in kit_set["kit"] for std in STANDARD_STATES}
    there = [p.name for p in targets.values() if p.exists()]
    if there and not overwrite:
        raise FileExistsError(f"{out_dir} already has {', '.join(there)}. Pass another "
                              "out_dir, or overwrite=True to replace them.")
    out_dir.mkdir(parents=True, exist_ok=True)

    kit_name = "perfect" if kit_defs is None else str(kit_defs)
    for ch, runs in sorted(kit_set["kit"].items()):
        missing = [std for std in STANDARD_STATES if std not in runs]
        if missing:
            raise ValueError(f"RF{ch} has no kit {', '.join(missing)} run")
        measured = {std: _raw(runs[std], db_path, sparam, freq)[1] for std in STANDARD_STATES}
        terms = solve_error_terms(measured, kit)
        for std in STANDARD_STATES:
            _write_definition(
                targets[ch, std], freq, apply_correction(terms, internal[std]),
                f"{kit_set['switch_serials']} port{ch} internal {std} seen from RF{ch}'s "
                f"connector, {kit_set['temp_str']}, kit set {kit_set['set']}, solved on raw "
                f"{sparam}, kit definitions {kit_name}")
    print(f"wrote definitions for RF{', RF'.join(str(c) for c in sorted(kit_set['kit']))} "
          f"to {out_dir}")
    return out_dir
