"""
The e-cal: calibrated reflection at each switch channel's connector from
the switch's internal standards (Menlo's e-cal app note; NIST/Menlo,
Spietz et al., IEEE J. Microwaves 2025). numpy and the standard library
only; it works on a take loaded by db.load_take and returns arrays, so it
never touches the instruments or the database.

    take   = db.load_take(take_id, DB)
    result = ecal.correct_take(take)          # NIST 295K or 3K from the take's temperature
    ecal.drift(take)                          # did the chain move during the take?

How it works. Between the VNA and the switch is a fixed linear network
(cables, circulator, attenuators, LNA), which a one-port error box
describes completely:

    Gm = e00 + e01e10 * Ga / (1 - e11 * Ga)

Gm is what the VNA reads on the trace the reflection reaches (S21 in the
fridge's circulator wiring), Ga what is at the reference plane. Measuring
the switch's internal open (ALL_OPEN), short and load, whose Ga NIST
measured from each RF port's SMA connector (their tier-2 definitions, one
set per port), gives the three unknowns; with port n's definitions the
plane is RF n's connector. Linear in (e00, e11, delta = e00*e11 - e01e10):

    Gm = e00 + (Ga * Gm) * e11 - Ga * delta,      Ga = (Gm - e00) / (Gm * e11 - delta)

one 3x3 solve per frequency, the same algorithm as scikit-rf's OnePort.

Limits worth knowing: the definitions are NIST's switch (SN0031), not
ours; use the ones for the temperature you're at (295 K, or 3 K which NIST
found holds to 25 mK; nothing covers 30-80 K); below ~100 MHz they are
ill-conditioned, so results are cropped at FMIN_HZ.

Copied from measurements/ecal.py (2026-11-08) so this folder stands alone.
"""

import re
from pathlib import Path

import numpy as np

# Switch state measured for each standard.
STANDARD_STATES = {"open": "ALL_OPEN", "short": "INTERNAL_SHORT", "load": "INTERNAL_LOAD"}

SPARAMS = ("S11", "S12", "S21", "S22")

# Below this NIST's definitions stop being three distinct standards.
FMIN_HZ = 100e6

# Ideal standards, for drift (and "perfect" definitions).
PERFECT_VALUES = {"open": 1 + 0j, "short": -1 + 0j, "load": 0j}

# Where find_definitions looks, in this folder and every folder above it.
# {T} is "3K"/"295K", {t} is "3k"/"295k". First match wins.
DEFINITIONS_CANDIDATES = (
    "ideals_{T}",                                                   # a copy beside the code (DAQ)
    "nist_MM4250_calibration_data_2025/tier2_scikitrf_caldata/tier2_{t}1",
)

_FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

def read_s1p(path):
    """A 1-port Touchstone file -> (freq_hz, gamma). RI, MA or DB, any frequency unit."""
    scale, fmt, rows = 1e9, "MA", []       # Touchstone defaults: GHz, MA
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
        return freq, x + 1j * y
    mag = x if fmt == "MA" else 10 ** (x / 20)
    return freq, mag * np.exp(1j * np.deg2rad(y))


def nist_definitions_for(temperature):
    """
    "295K" or "3K": which of NIST's definitions fits a temperature, given
    in kelvin (a number) or as a label ("295K", "10mK"). 295K from 250 K
    up, 3K at 4.5 K and below. In between (30-80 K on the way down)
    nothing fits: a loud warning and None, so correct_take saves the raw
    data only (pass definitions= yourself for a rough correction). None
    (no reading) -> None.
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
    The folder of NIST's definitions for `which` ("295K" or "3K"), looking
    for DEFINITIONS_CANDIDATES in `start` (default: this folder) and every
    folder above it. On a laptop that finds SD Code/nist_MM4250_calibration_data_2025;
    on the DAQ, copy tier2_scikitrf_caldata/tier2_295k1 beside the code as ideals_295K/
    (and tier2_3k1 as ideals_3K/).
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


def _definition_file(folder, port, standard):
    """One port/standard's file, in NIST's tier-2 or end-user naming, or None."""
    for name in (f"port{port}_{standard}_tier2.s1p", f"ideal_port{port}_{standard}.s1p"):
        p = Path(folder) / name
        if p.is_file():
            return p
    return None


def load_definitions(folder, port, freq_hz):
    """
    Port `port`'s {"open", "short", "load"} definitions on `freq_hz`, real
    and imaginary parts interpolated separately (as scikit-rf does).
    Outside the file's range is an error, not an extrapolation.
    """
    out = {}
    for std in STANDARD_STATES:
        path = _definition_file(folder, port, std)
        if path is None:
            raise FileNotFoundError(f"No port{port} {std} definition in {folder}")
        f, g = read_s1p(path)
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
    One-port error terms from three standards. `measured` and `ideal`:
    {"open", "short", "load"} complex arrays on one frequency axis.
    Returns e00, e11, e01e10, delta and `cond` (each frequency's 3x3
    condition number: large = the standards are nearly degenerate there).
    """
    stds = list(STANDARD_STATES)
    gm = np.stack([np.asarray(measured[s], complex) for s in stds], axis=1)   # (nf, 3)
    ga = np.stack([np.asarray(ideal[s], complex) for s in stds], axis=1)
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
    """{"open": G, ...}: a repeat's raw standards on `sparam`, averaged over `roles`."""
    return {std: np.mean([block[role][state]["data"][sparam] for role in roles], axis=0)
            for std, state in STANDARD_STATES.items()}


def correct_take(take, definitions=None, sparam=None, fmin=FMIN_HZ, start=None):
    """
    Correct every channel of every repeat of a take (db.load_take), each
    repeat with its own standards: the mean of its before and after sets
    (cancels drift linear in time).

    definitions: "295K" / "3K" (NIST's, found with find_definitions), a
    folder path, or None = pick from the take's temperature
    (nist_definitions_for: 295K from 250 K up, 3K at 4.5 K and below).
    With no temperature, or one in between, and no definitions given,
    nothing is corrected: prints why and returns None (the raw runs are
    already saved; correct later with definitions="295K" or "3K").
    The result's "chosen" says "auto" (from the temperature) or "by hand".
    sparam: the raw trace to solve on; None = the cooldown file's
    correct_sparam (S21 in the circulator wiring).

    Returns {"take_id", "freq", "sparam", "definitions" (label),
    "definitions_dir", "corrected": [{ch: G}, ...] one dict per repeat,
    "raw": [{ch: raw trace}, ...], "terms": [{ch: error terms}, ...],
    "sources": [{ch: [raw run ids used]}, ...], "ports" {ch: description}}.
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
    if str(definitions).strip().upper() in ("295K", "3K"):
        label = f"NIST {str(definitions).strip().upper()}"
        folder = find_definitions(definitions, start=start)
    else:
        folder = Path(definitions)
        label = folder.name
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
            "corrected": corrected,
            "raw": raw, "terms": terms_out, "sources": sources, "fmin_hz": fmin,
            "ports": {ch: take["cfg"]["ports"].get(f"RF{ch}") for ch in take["channels"]}}


def drift(take, sparam=None, fmin=FMIN_HZ, verbose=True):
    """
    How far the standards moved between the before and after sets of each
    repeat, in reflection units at the switch: calibrate with the before
    standards as "perfect" ones and see how far off the after ones read.
    Independent of the chain's gain, so it compares directly with NIST's
    same-temperature repeatability (a few 1e-2); well above that, the
    chain (usually the cold LNA) drifted: take it again, quicker.
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
