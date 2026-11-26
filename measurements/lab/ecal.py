"""
The e-cal: calibrated reflection at each switch channel's connector, from the switch's
internal standards (Menlo's e-cal app note; Spietz et al., IEEE J. Microwaves 2025).
numpy only; works on a take from db.load_take and never touches instruments or databases.

    take   = db.load_take(take_id, DB)
    result = ecal.correct_take(take)          # NIST 295K or 3K, picked from the take's temperature
    ecal.drift(take)                          # did the chain move during the take?

How it works: everything between the VNA and the switch (cables, circulator, attenuators,
LNA) is one fixed one-port error box:

    Gm = e00 + e01e10 * Ga / (1 - e11 * Ga)

Gm is the raw reading (S21 in the fridge's circulator wiring), Ga the reflection at RF n's
connector. The switch's open (ALL_OPEN), short and load, with NIST's definitions for port n,
give the three unknowns: one 3x3 solve per frequency (same as scikit-rf's OnePort).

Limits: the definitions are NIST's switch (SN0031), not ours; they exist for 295 K and
3 K (good to 25 mK), nothing in between; below ~100 MHz they're ill-conditioned, so
results stop at FMIN_HZ.
"""

import re
from pathlib import Path

import numpy as np

STANDARD_STATES = {"open": "ALL_OPEN", "short": "INTERNAL_SHORT", "load": "INTERNAL_LOAD"}
SPARAMS = ("S11", "S12", "S21", "S22")
FMIN_HZ = 100e6                              # below this the three standards aren't distinct
PERFECT_VALUES = {"open": 1 + 0j, "short": -1 + 0j, "load": 0j}   # ideal standards (drift)

# Where find_definitions looks, here and in every folder above ({T}: "3K"/"295K", {t}: lower case).
DEFINITIONS_CANDIDATES = (
    "ideals_{T}",                                                   # a copy beside the code (DAQ)
    "nist_MM4250_calibration_data_2025/tier2_scikitrf_caldata/tier2_{t}1",
)

_FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
_TOUCHSTONE_ORDER = {1: ("S11",), 2: ("S11", "S21", "S12", "S22")}   # column order


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def read_touchstone(path):
    """
    Any .s1p or .s2p -> (freq_hz, {"S11": ..., "S21": ..., ...}). RI, MA or DB, any frequency
    unit, numbers may wrap across lines. All-NaN columns (a forward sweep's S12/S22) are
    left out.
    """
    path = Path(path)
    m = re.search(r"\.s([12])p$", path.name.lower())
    if m is None:
        raise ValueError(f"{path.name}: only .s1p and .s2p files")
    names = _TOUCHSTONE_ORDER[int(m.group(1))]
    scale, fmt, numbers = 1e9, "MA", []          # Touchstone defaults: GHz, MA
    for line in path.read_text().splitlines():
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
        numbers += [float(x) for x in line.split()]
    width = 1 + 2 * len(names)
    if not numbers or len(numbers) % width:
        raise ValueError(f"{path.name}: {len(numbers)} numbers isn't a whole number of "
                         f"{width}-number rows")
    a = np.array(numbers).reshape(-1, width)
    data = {}
    for i, s in enumerate(names):
        x, y = a[:, 1 + 2 * i], a[:, 2 + 2 * i]
        if fmt == "RI":
            data[s] = x + 1j * y
        else:
            mag = x if fmt == "MA" else 10 ** (x / 20)
            data[s] = mag * np.exp(1j * np.deg2rad(y))
    data = {s: v for s, v in data.items() if not np.all(np.isnan(v))}
    return a[:, 0] * scale, data


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

def nist_definitions_for(temperature):
    """
    "295K" or "3K" for a temperature (kelvin, or a label like "295K" / "10mK"): 295K from
    250 K up, 3K at 4.5 K and below. In between nothing fits: a warning and None (so
    correct_take saves raw only). No reading (None) -> None.
    """
    if temperature is None:
        return None
    if isinstance(temperature, str):
        m = re.fullmatch(r"\s*(\d+(?:\.\d*)?)\s*(mK|K|mk|k)\s*", temperature)
        if m is None:
            raise ValueError(f"can't read a temperature from {temperature!r} (want e.g. '295K', '10mK')")
        kelvin = float(m.group(1)) * (1e-3 if m.group(2).lower() == "mk" else 1.0)
    else:
        kelvin = float(temperature)
    if kelvin >= 250:
        return "295K"
    if kelvin <= 4.5:
        return "3K"
    print(f"[WARNING] {kelvin:.3g} K: NIST's definitions exist for 295 K and 3 K (3 K holds "
          "to 25 mK) only, so none are picked automatically here.")
    return None


def find_definitions(which, start=None):
    """
    The folder of NIST's definitions for "295K" or "3K": the first DEFINITIONS_CANDIDATES
    match in `start` (default: here) or any folder above. On the DAQ that's ideals_295K/
    and ideals_3K/ beside the code (copies of NIST's tier2_295k1 / tier2_3k1).
    """
    t = str(which).strip().lower()
    if t not in ("3k", "295k"):
        raise ValueError(f"NIST definitions exist for 3K and 295K only, not {which!r}")
    start = Path(start) if start is not None else Path(__file__).resolve().parent
    for folder in [start, *start.parents]:
        for pattern in DEFINITIONS_CANDIDATES:
            cand = folder / pattern.format(t=t, T=t.upper())
            if cand.is_dir() and _definition_file(cand, 1, "open") is not None:
                return cand
    raise FileNotFoundError(f"No {which} definitions at or above {start}. Copy NIST's "
                            f"tier2_scikitrf_caldata/tier2_{t}1 beside the code as ideals_{t.upper()}/.")


def definitions_folder(definitions, start=None):
    """("NIST 3K", its folder) for "3K"/"295K"; (folder name, folder) for a folder path."""
    if str(definitions).strip().upper() in ("3K", "295K"):
        return f"NIST {str(definitions).strip().upper()}", find_definitions(definitions, start=start)
    folder = Path(definitions)
    return folder.name, folder


def _definition_file(folder, port, standard):
    """One port/standard's file (NIST's tier-2 or end-user naming), or None."""
    for name in (f"port{port}_{standard}_tier2.s1p", f"ideal_port{port}_{standard}.s1p"):
        p = Path(folder) / name
        if p.is_file():
            return p
    return None


def load_definitions(folder, port, freq_hz):
    """Port `port`'s {"open", "short", "load"} definitions on freq_hz (real and imaginary
    parts interpolated separately, like scikit-rf). Outside the file's range: an error."""
    out = {}
    for std in STANDARD_STATES:
        path = _definition_file(folder, port, std)
        if path is None:
            raise FileNotFoundError(f"No port{port} {std} definition in {folder}")
        f, data = read_touchstone(path)
        g = data["S11"]
        if freq_hz.min() < f.min() * (1 - 1e-9) or freq_hz.max() > f.max() * (1 + 1e-9):
            raise ValueError(f"{path.name} covers {f.min() / 1e9:g}-{f.max() / 1e9:g} GHz; "
                             f"data spans {freq_hz.min() / 1e9:g}-{freq_hz.max() / 1e9:g} GHz")
        out[std] = np.interp(freq_hz, f, g.real) + 1j * np.interp(freq_hz, f, g.imag)
    return out


# ---------------------------------------------------------------------------
# The calibration
# ---------------------------------------------------------------------------

def solve_error_terms(measured, ideal):
    """
    One-port error terms from three standards. measured, ideal: {"open", "short", "load"}
    complex arrays on one frequency axis. Returns e00, e11, e01e10, delta and cond (each
    frequency's condition number: large = the standards nearly coincide there).
    """
    stds = list(STANDARD_STATES)
    gm = np.stack([np.asarray(measured[s], complex) for s in stds], axis=1)   # (nf, 3)
    ga = np.stack([np.asarray(ideal[s], complex) for s in stds], axis=1)
    # linear in (e00, e11, delta = e00*e11 - e01e10):  Gm = e00 + Ga*Gm*e11 - Ga*delta
    A = np.stack([np.ones_like(gm), ga * gm, -ga], axis=2)                   # (nf, 3, 3)
    x = np.linalg.solve(A, gm[..., None])[..., 0]
    e00, e11, delta = x[:, 0], x[:, 1], x[:, 2]
    return {"e00": e00, "e11": e11, "delta": delta, "e01e10": e00 * e11 - delta,
            "cond": np.linalg.cond(A)}


def apply_correction(terms, gamma_measured):
    """Raw reflection -> reflection at the reference plane."""
    gm = np.asarray(gamma_measured, complex)
    return (gm - terms["e00"]) / (gm * terms["e11"] - terms["delta"])


def _standards(block, roles, sparam):
    """{"open": G, ...}: one repeat's raw standards on `sparam`, averaged over `roles` (before/after)."""
    return {std: np.mean([block[role][state]["data"][sparam] for role in roles], axis=0)
            for std, state in STANDARD_STATES.items()}


def correct_take(take, definitions=None, sparam=None, fmin=FMIN_HZ, start=None):
    """
    Correct every channel of every repeat of a take, each repeat with the mean of its
    before and after standards (cancels drift that's linear in time).

    definitions: "295K" / "3K", a folder, or None = from the take's temperature
    (nist_definitions_for). If none fits, nothing is corrected: prints why, returns None
    (the raw runs are already saved). The result's "chosen" is "auto" or "by hand".
    sparam: the raw trace; None = the cooldown file's correct_sparam.

    Returns {"take_id", "freq", "sparam", "definitions", "chosen", "definitions_dir",
    "corrected": [{ch: G}, ...] per repeat, "raw", "terms", "sources" (raw run ids used),
    "fmin_hz", "ports"}.
    """
    chosen = "by hand" if definitions is not None else "auto"
    if definitions is None:
        definitions = nist_definitions_for(take["temp_k"])
        if definitions is None:
            why = ("No fridge temperature for this take" if take["temp_k"] is None else
                   f"The take started at {take['temp_k']:.3g} K, where neither NIST set fits")
            print(f"[WARNING] {why}, so no definitions were picked and NOTHING WAS CORRECTED. "
                  "The raw runs are saved. To correct anyway, set CORRECT_WITH = \"295K\" "
                  "or \"3K\" in Recall (saved as chosen by hand).")
            return None
    label, folder = definitions_folder(definitions, start)
    sparam = sparam or take["cfg"]["correct_sparam"]
    if sparam not in SPARAMS:
        raise ValueError(f"sparam must be one of {SPARAMS}, got {sparam!r}")

    freq = take["freq"]
    keep = freq >= fmin
    defs = {}
    corrected, raw, terms_out, sources = [], [], [], []
    for block in take["repeats"]:
        roles = [r for r in ("before", "after") if len(block[r]) == len(STANDARD_STATES)]
        if not roles:
            raise LookupError("a repeat of this take has no complete set of standards")
        measured = _standards(block, roles, sparam)
        std_ids = [block[r][s]["run_id"] for r in roles for s in STANDARD_STATES.values()]
        c, rw, tm, src = {}, {}, {}, {}
        for ch, run in sorted(block["ports"].items()):
            if ch not in defs:
                defs[ch] = load_definitions(folder, ch, freq)
            terms = solve_error_terms(measured, defs[ch])
            gm = run["data"][sparam]
            c[ch] = apply_correction(terms, gm)[keep]
            rw[ch] = gm[keep]
            tm[ch] = {k: v[keep] for k, v in terms.items()}
            src[ch] = [run["run_id"]] + std_ids
        corrected.append(c)
        raw.append(rw)
        terms_out.append(tm)
        sources.append(src)
    return {"take_id": take["take_id"], "freq": freq[keep], "sparam": sparam,
            "definitions": label, "chosen": chosen, "definitions_dir": str(folder),
            "corrected": corrected, "raw": raw, "terms": terms_out, "sources": sources,
            "fmin_hz": fmin,
            "ports": {ch: take["cfg"]["ports"].get(f"RF{ch}") for ch in take["channels"]}}


def drift(take, sparam=None, fmin=FMIN_HZ, verbose=True):
    """
    How far the standards moved between the before and after sets of each repeat, in
    reflection units at the switch (calibrate with the before set as "perfect" standards,
    see how far off the after set reads). Compare with NIST's repeatability, a few 1e-2;
    well above that the chain (usually the LNA) drifted: take it again, quicker.
    Returns {"freq", "repeats": [{"open": |dG|, ...}, ...]}.
    """
    sparam = sparam or take["cfg"]["correct_sparam"]
    freq = take["freq"]
    keep = freq >= fmin
    perfect = {s: np.full(len(freq), v) for s, v in PERFECT_VALUES.items()}
    out = []
    for k, block in enumerate(take["repeats"], 1):
        if len(block["after"]) < len(STANDARD_STATES) or len(block["before"]) < len(STANDARD_STATES):
            continue
        before = _standards(block, ["before"], sparam)
        after = _standards(block, ["after"], sparam)
        terms = solve_error_terms(before, perfect)
        d = {s: np.abs(apply_correction(terms, after[s]) - perfect[s])[keep] for s in STANDARD_STATES}
        out.append(d)
        if verbose:
            parts = ", ".join(f"{s} median {np.median(v):.2g} / max {np.max(v):.2g}"
                              for s, v in d.items())
            print(f"repeat {k} drift ({sparam}): {parts}  <- compare with NIST's few 1e-2")
    return {"freq": freq[keep], "repeats": out}


# ---------------------------------------------------------------------------
# Gate test (vna.hv_test): where each gate combination reflects
# ---------------------------------------------------------------------------

def _delay_ns(freq, g):
    """Round-trip group delay of a reflection from its phase slope (ns): positive = beyond
    the reference plane, negative = before it."""
    return float(-np.polyfit(freq, np.unwrap(np.angle(g)), 1)[0] / (2 * np.pi) * 1e9)


def hv_positions(result, definitions=None, fmin=FMIN_HZ, start=None, verbose=True):
    """
    Correct every closed position of a gate test two ways, with the test's own standards
    (every ALL_OPEN averaged; short and load before and after averaged):

      "inside":  perfect open/short/load at the switch's internal standards. Needs no NIST
                 definitions. The delay is measured from the internal standards (~the
                 contacts); /2 is one way. Warm SN0077 (2026-09-25): internal open 0.154 ns
                 from RFC, each RF connector 0.401 ns from RFC, so a connector is ~0.25 ns
                 one way past the internal standards.
      "connector": NIST's definitions for that channel's port (definitions "3K"/"295K",
                 None = from the temperature; skipped if none fits). Delay from the RF
                 connector: ~0 = at it, negative = inside the switch.

    Returns {step: {"label", "freq", "inside", "connector" (or None), "mag_db", "delay_in_ns",
    "delay_conn_ns"}}; prints a table if verbose.
    """
    rows = result["rows"]
    freq = np.asarray(result["freq"], float)
    keep = freq >= fmin
    f = freq[keep]
    pick = lambda pos: [r["s21"] for r in rows if r["position"] == pos]
    meas = {"open": np.mean(pick("ALL_OPEN"), axis=0), "short": np.mean(pick("INTERNAL_SHORT"), axis=0),
            "load": np.mean(pick("INTERNAL_LOAD"), axis=0)}
    meas = {k: v[keep] for k, v in meas.items()}
    perfect = {k: np.full(f.size, v, complex) for k, v in PERFECT_VALUES.items()}
    t_in = solve_error_terms(meas, perfect)
    if definitions is None:
        definitions = nist_definitions_for(result.get("temp_k"))
    folder = None
    if definitions is not None:
        try:
            folder = definitions_folder(definitions, start)[1]
        except FileNotFoundError as e:             # the measurement is already saved: just skip NIST
            print(f"[warning] no 'from connector' column: {e}")
    out = {}
    for r in rows:
        if r["position"] in STANDARD_STATES.values():
            continue
        ch = r.get("hv_channel")
        g_in = apply_correction(t_in, np.asarray(r["s21"])[keep])
        g_conn = None
        if folder is not None and ch is not None:
            g_conn = apply_correction(solve_error_terms(meas, load_definitions(folder, ch, f)),
                                      np.asarray(r["s21"])[keep])
        out[r["step"]] = {"label": r["label"], "role": r.get("hv_role"), "freq": f, "inside": g_in,
                          "connector": g_conn, "mag_db": float(np.median(20 * np.log10(np.abs(g_in)))),
                          "delay_in_ns": _delay_ns(f, g_in),
                          "delay_conn_ns": None if g_conn is None else _delay_ns(f, g_conn)}
    if verbose:
        print(f"Correction: perfect standards at the internal standards"
              + (f"; NIST {definitions} at each port's connector" if folder else "; no NIST set fits"))
        print(f"{'step':>4} {'position':>28} {'|G| dB':>7} {'from internal stds (ns)':>24} "
              f"{'from connector (ns)':>22}")
        print(f"{'':>4} {'':>28} {'':>7} {'round trip':>12}{'one way':>12} {'round trip':>11}{'one way':>11}")
        for step, v in out.items():
            c = ("" if v["delay_conn_ns"] is None else
                 f"{v['delay_conn_ns']:+11.3f}{v['delay_conn_ns'] / 2:+11.3f}")
            print(f"{step:>4} {v['label']:>28} {v['mag_db']:7.2f} {v['delay_in_ns']:+12.3f}"
                  f"{v['delay_in_ns'] / 2:+12.3f} {c}")
        print("ALL_OPEN rows sit at ~0 from the internal standards. Only meaningful for |G| near 0 dB "
              "(a load's phase is mostly noise).")
    return out
