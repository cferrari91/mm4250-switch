"""
Offline analysis (no instruments): calibrate raw data from the database or from Touchstone
files, gather traces to plot, and take a quick, fit-free look at a resonance.
Used by analysis.ipynb.

    files  = analysis.load_folder(folder)               # position from each file name
    cal    = analysis.calibrate_files(files, "3K")      # e-cal from the files' own standards
    traces = analysis.traces_from_calibration(cal)      # or traces_from_take / _runs / _files
    plots.plot_mag_smith(traces)
    analysis.print_resonance(analysis.resonance(traces[0]))

A "trace" is {"label", "freq" (Hz), "gamma" (complex), "kind" ("corrected" or "raw"),
"channel" (or None), "meta"}.
"""

import re
from pathlib import Path

import numpy as np

import db
import ecal

# Positions as they appear in our file names: 06_RF1.s2p, 03_ALL_OPEN_run12.s2p, RF4_corrected_rep1_run20.s1p
_POSITION_RE = re.compile(r"(?<![A-Za-z])(ALL_OPEN|INTERNAL_SHORT|INTERNAL_LOAD|RF[1-6])(?![0-9A-Za-z])")
_SUBFOLDERS = ("takes", "stuck_check", "thru_check", "export")
MIN_DEPTH_DB = 3.0       # resonance(): shallower than this isn't called a dip


# ---------------------------------------------------------------------------
# What's there
# ---------------------------------------------------------------------------

def list_runs(db_path, last=20):
    """Print and return the newest `last` runs: id, name, kind, take/check, temperature."""
    if not Path(db_path).is_file():
        print(f"No database at {db_path}")
        return []
    rows = db._run_rows(db_path, "", ())[-last:] if last else db._run_rows(db_path, "", ())
    for r in rows:
        group = r["take_id"] or r["check_id"] or ""
        kind = r["kind"] or r["check_type"] or ""
        print(f"run {r['run_id']:>5}  {r['name']:<20} {kind:<9} {group:<22} {r['temp_str'] or ''}")
    return rows


def list_folders(files_root):
    """The folders of saved sweeps under a database's Sweeps/ folder (takes, checks, export),
    printed; returned newest last."""
    out = []
    for sub in _SUBFOLDERS:
        base = Path(files_root) / sub
        if not base.is_dir():
            continue
        if sub == "export":
            folders = [base] if any(base.glob("*.s?p")) else []
        else:
            folders = sorted(p for p in base.iterdir() if p.is_dir())
        for p in folders:
            n = len(list(p.glob("*.s?p")))
            print(f"{sub:<12} {p.name:<45} {n} file(s)")
            out.append(p)
    if not out:
        print(f"No saved sweeps under {files_root}")
    return sorted(out, key=lambda p: p.stat().st_mtime)


def newest_folder(files_root):
    """The most recently changed folder of sweeps (a take or a check), or None."""
    cands = []
    for sub in ("takes", "stuck_check", "thru_check"):
        base = Path(files_root) / sub
        if base.is_dir():
            cands += [p for p in base.iterdir() if p.is_dir() and any(p.glob("*.s?p"))]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def position_of(path):
    """The switch position a file name refers to ("ALL_OPEN", "RF4", ...), or None."""
    m = _POSITION_RE.search(Path(path).stem)
    return m.group(1) if m else None


def load_folder(folder, pattern="*.s?p", verbose=True):
    """
    Every Touchstone file in `folder` (by name), read, with its position and kind
    ("corrected" if "_corrected"/"_calibrated" is in the name, else "raw").
    Returns [{"path", "name", "position", "kind", "freq", "data"}, ...].
    """
    folder = Path(folder)
    paths = sorted(folder.glob(pattern))
    if not paths:
        raise FileNotFoundError(f"No {pattern} files in {folder}")
    out = []
    for p in paths:
        freq, data = ecal.read_touchstone(p)
        kind = "corrected" if re.search(r"_(corrected|calibrated)", p.stem) else "raw"
        out.append({"path": p, "name": p.name, "position": position_of(p), "kind": kind,
                    "freq": freq, "data": data})
    if verbose:
        by = {}
        for f in out:
            by.setdefault((f["position"] or "?", f["kind"]), []).append(f["name"])
        print(f"{len(out)} file(s) in {folder}:")
        for (pos, kind), names in by.items():
            print(f"  {pos:<15} {kind:<9} {len(names)}: {', '.join(names[:4])}"
                  + (" ..." if len(names) > 4 else ""))
    return out


# ---------------------------------------------------------------------------
# Calibrating raw data
# ---------------------------------------------------------------------------

def calibrate(freq, standards, raw, definitions, fmin=ecal.FMIN_HZ, start=None):
    """
    The e-cal on bare arrays. standards: raw {"open", "short", "load"} readings; raw:
    {channel: raw reading}, same trace and frequencies (S21 in the fridge, S11 on the
    bench). definitions: "3K" / "295K" or a folder. Returns {"freq", "corrected": {ch: G},
    "raw", "terms", "definitions", "definitions_dir"}, cropped to >= fmin.
    """
    freq = np.asarray(freq, float)
    label, folder = ecal.definitions_folder(definitions, start)
    keep = freq >= fmin
    corrected, raws, terms_out = {}, {}, {}
    for ch, gm in sorted(raw.items()):
        terms = ecal.solve_error_terms(standards, ecal.load_definitions(folder, ch, freq))
        corrected[ch] = ecal.apply_correction(terms, gm)[keep]
        raws[ch] = np.asarray(gm)[keep]
        terms_out[ch] = {k: v[keep] for k, v in terms.items()}
    return {"freq": freq[keep], "corrected": corrected, "raw": raws, "terms": terms_out,
            "definitions": label, "definitions_dir": str(folder)}


def calibrate_files(files, definitions, sparam="S21", channels=None, standards="mean",
                    fmin=ecal.FMIN_HZ, start=None, verbose=True):
    """
    Calibrate raw Touchstone files (load_folder's list) with the standards among them: the
    ALL_OPEN, INTERNAL_SHORT and INTERNAL_LOAD files are the open/short/load, and each RF<n>
    file is calibrated with port n's definitions.

    standards: how several files of one standard combine, "mean", "first" or "last"
    (channels always use the mean). After a stuck-closed channel the later ALL_OPENs
    aren't opens: use "first" there.
    sparam: "S21" in the fridge, "S11" on the bench. channels: None = every RF<n> file.
    The files must be one session (same chain, same temperature).
    """
    if standards not in ("mean", "first", "last"):
        raise ValueError(f"use must be 'mean', 'first' or 'last', not {standards!r}")
    raw_files = [f for f in files if f["kind"] == "raw" and f["position"]]
    freq = raw_files[0]["freq"] if raw_files else None
    for f in raw_files:
        if sparam not in f["data"]:
            raise ValueError(f"{f['name']} has no {sparam} ({', '.join(f['data'])}); "
                             "pass sparam='S11' for 1-port files")
        if len(f["freq"]) != len(freq) or not np.allclose(f["freq"], freq, rtol=1e-12, atol=0.5):
            raise ValueError(f"{f['name']} is on a different frequency axis from {raw_files[0]['name']}")
    std, missing = {}, []
    for name, state in ecal.STANDARD_STATES.items():
        group = [f["data"][sparam] for f in raw_files if f["position"] == state]
        if not group:
            missing.append(state)
            continue
        if standards == "mean":
            std[name] = np.mean(group, axis=0)
        else:
            std[name] = group[0] if standards == "first" else group[-1]
        if verbose:
            names = [f["name"] for f in raw_files if f["position"] == state]
            print(f"  {name:<5} = {standards} of {len(group)}: "
                  f"{', '.join(names[:6])}" + (" ..." if len(names) > 6 else ""))
    if missing:
        raise LookupError(f"No raw {', '.join(missing)} file(s): the e-cal needs the switch's "
                          "own open, short and load measured in the same session")
    chans = {}
    for f in raw_files:
        if f["position"].startswith("RF"):
            ch = int(f["position"][2:])
            if channels is None or ch in channels:
                chans.setdefault(ch, []).append(f)
    if not chans:
        raise LookupError("No RF<n> files to calibrate" + (f" among channels {channels}" if channels else ""))
    raw = {ch: np.mean([f["data"][sparam] for f in group], axis=0) for ch, group in chans.items()}
    if verbose:
        for ch, group in sorted(chans.items()):
            print(f"  RF{ch:<3} = mean of {len(group)}: {', '.join(f['name'] for f in group)}")
    cal = calibrate(freq, std, raw, definitions, fmin=fmin, start=start)
    cal.update(sparam=sparam, source=str(Path(raw_files[0]["path"]).parent),
               files={ch: [str(f["path"]) for f in g] for ch, g in chans.items()})
    if verbose:
        print(f"Calibrated RF{', RF'.join(str(c) for c in sorted(raw))} on {sparam} "
              f"with {cal['definitions']}")
    return cal


def save_calibrated(cal, out_dir):
    """Each channel of a calibration as RF<n>_calibrated_<definitions>.s1p (never overwrites)."""
    out_dir = Path(out_dir)
    tag = re.sub(r"[^A-Za-z0-9]+", "_", cal["definitions"]).strip("_")
    paths = []
    for ch, g in sorted(cal["corrected"].items()):
        p = out_dir / f"RF{ch}_calibrated_{tag}.s1p"
        if db.save_touchstone(cal["freq"], g, p):
            print(f"  wrote {p}")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Traces to plot
# ---------------------------------------------------------------------------

def _trace(label, freq, gamma, kind, channel=None, meta=None):
    return {"label": label, "freq": np.asarray(freq), "gamma": np.asarray(gamma), "kind": kind,
            "channel": channel, "meta": meta or {}}


def traces_from_calibration(cal):
    """One trace per channel of calibrate/calibrate_files, or of ecal.correct_take (mean over repeats)."""
    corrected = cal["corrected"]
    if isinstance(corrected, list):               # correct_take: one dict per repeat
        chans = sorted(corrected[0])
        corrected = {ch: np.mean([rep[ch] for rep in corrected if ch in rep], axis=0) for ch in chans}
    ports = cal.get("ports") or {}
    out = []
    for ch, g in sorted(corrected.items()):
        what = ports.get(ch)
        out.append(_trace(f"RF{ch}" + (f" ({what})" if what else "") + f", {cal['definitions']}",
                          cal["freq"], g, "corrected", ch, {"definitions": cal["definitions"]}))
    return out


def traces_from_take(take, which="corrected", definitions=None):
    """A take's corrected runs (mean over repeats; `definitions` picks a set, None = the
    newest), or which="raw": the raw trace the correction uses."""
    ports = take["cfg"]["ports"]
    tag = f"{take['temp_str']}, take {take['take_id']}"
    out = []
    if which == "raw":
        s = take["cfg"]["correct_sparam"]
        for ch in take["channels"]:
            g = np.mean([b["ports"][ch]["data"][s] for b in take["repeats"] if ch in b["ports"]], axis=0)
            out.append(_trace(f"RF{ch} ({ports.get(f'RF{ch}', '?')}) raw {s}, {tag}", take["freq"], g,
                              "raw", ch))
        return out
    corr = take["corrected"]
    if not corr:
        print(f"Take {take['take_id']} has no corrected runs (calibrate it in section 2a)")
        return []
    definitions = definitions or corr[-1]["definitions"]
    corr = [c for c in corr if c["definitions"] == definitions]
    for ch in sorted({c["channel"] for c in corr}):
        mine = [c for c in corr if c["channel"] == ch]
        g = np.mean([c["data"]["S11"] for c in mine], axis=0)
        out.append(_trace(f"RF{ch} ({ports.get(f'RF{ch}', '?')}), {definitions}, {tag}",
                          mine[0]["freq"], g, "corrected", ch, mine[0]["meta"]))
    return out


def traces_from_runs(runs, sparam=None):
    """Runs from db.load_runs. sparam None: S21 if the run has it, else S11 (a corrected run)."""
    out = []
    for run_id, run in runs.items():
        s = sparam or ("S21" if "S21" in run["data"] else "S11")
        if s not in run["data"]:
            print(f"  run {run_id} has no {s}; skipped")
            continue
        meta = run["meta"]
        kind = "corrected" if meta.get("kind") == "corrected" else "raw"
        what = meta.get("port")
        out.append(_trace(f"run {run_id} {run['name']} {s}" + (f" ({what})" if what else "")
                          + (f", {meta['temp_str']}" if meta.get("temp_str") else ""),
                          run["freq"], run["data"][s], kind, meta.get("channel"), meta))
    return out


def traces_from_files(paths, sparam=None):
    """Touchstone files (paths, or load_folder's list). sparam None: S21 if there, else S11."""
    out = []
    for p in paths:
        f = p if isinstance(p, dict) else None
        path = Path(f["path"] if f else p)
        freq, data = (f["freq"], f["data"]) if f else ecal.read_touchstone(path)
        s = sparam or ("S21" if "S21" in data else "S11")
        if s not in data:
            print(f"  {path.name} has no {s}; skipped")
            continue
        pos = position_of(path)
        kind = "corrected" if re.search(r"_(corrected|calibrated)", path.stem) else "raw"
        out.append(_trace(f"{path.name} {s}", freq, data[s], kind,
                          int(pos[2:]) if pos and pos.startswith("RF") else None))
    return out


def zoom(trace, start_mhz=None, stop_mhz=None):
    """The part of a trace between start_mhz and stop_mhz."""
    f = trace["freq"] / 1e6
    keep = np.ones(len(f), bool)
    if start_mhz is not None:
        keep &= f >= start_mhz
    if stop_mhz is not None:
        keep &= f <= stop_mhz
    if keep.sum() < 3:
        raise ValueError(f"fewer than 3 points between {start_mhz} and {stop_mhz} MHz")
    return dict(trace, freq=trace["freq"][keep], gamma=trace["gamma"][keep])


# ---------------------------------------------------------------------------
# Resonance: a fit-free first look
# ---------------------------------------------------------------------------

def resonance(trace, edge_fraction=0.1):
    """
    Rough numbers for a reflection resonance in a corrected trace (no fit; use a proper fit
    for Q). edge_fraction: how much of each end counts as off resonance. Returns:

      f_min_hz, min_db     where |G| is smallest, and how small
      off_db, depth_db     |G| off resonance (the edges' median), and off_db - min_db
      clear_dip            depth_db >= MIN_DEPTH_DB (else no width or Q: noise has no width)
      width_hz             full width at half depth (linear); None if the dip runs off the trace
      q_loaded_rough       f_min / width
      phase_span_deg       phase swing after removing a straight-line (cable delay) trend
      coupling_guess       clear dip: "over" if G at the dip is > 90 deg from off resonance,
                           else "under"; no dip: "over" if the phase swings > 180 deg, else None
    """
    f, g = trace["freq"], trace["gamma"]
    n = len(f)
    k = max(3, int(n * edge_fraction))
    mag = np.abs(g)
    i = int(np.argmin(mag))
    edges = np.r_[0:k, n - k:n]
    off = float(np.median(mag[edges]))
    # cable delay from the two edges, fitted separately so a 360 deg step across the resonance doesn't bend it
    ph = np.unwrap(np.angle(g))
    slope = np.mean([np.polyfit(f[:k], ph[:k], 1)[0], np.polyfit(f[-k:], ph[-k:], 1)[0]])
    resid = ph - slope * f
    resid = resid - np.median(resid[:k])
    depth = 20 * np.log10(off / max(mag[i], 1e-30))
    clear = bool(depth >= MIN_DEPTH_DB)
    half = (mag[i] + off) / 2
    lo = next((j for j in range(i, -1, -1) if mag[j] >= half), None)
    hi = next((j for j in range(i, n) if mag[j] >= half), None)
    width = (float(f[hi] - f[lo]) if clear and lo is not None and hi is not None and hi > lo
             else None)
    g_off = np.median(g[:k] * np.exp(-1j * slope * f[:k]))
    g_min = g[i] * np.exp(-1j * slope * f[i])
    angle = abs(np.rad2deg(np.angle(g_min / g_off))) if abs(g_off) > 0 else None
    span = float(np.rad2deg(resid.max() - resid.min()))
    coupling = None
    if clear and angle is not None:
        coupling = "over" if angle > 90 else "under"
    elif span > 180:
        coupling = "over"
    return {"label": trace["label"], "f_min_hz": float(f[i]), "min_db": float(20 * np.log10(max(mag[i], 1e-30))),
            "off_db": float(20 * np.log10(max(off, 1e-30))), "depth_db": float(depth),
            "width_hz": width, "q_loaded_rough": float(f[i] / width) if width else None,
            "phase_span_deg": span, "clear_dip": clear,
            "angle_from_off_deg": None if angle is None else float(angle),
            "coupling_guess": coupling}


def print_resonance(r):
    """resonance()'s numbers, in words."""
    print(r["label"])
    print(f"  minimum |G| {r['min_db']:.2f} dB at {r['f_min_hz'] / 1e6:.4f} MHz; "
          f"off resonance {r['off_db']:.2f} dB; depth {r['depth_db']:.2f} dB")
    if not r["clear_dip"]:
        print(f"  no clear dip (under {MIN_DEPTH_DB:g} dB deep): no width or Q_L from this")
    elif r["width_hz"]:
        print(f"  half-depth width {r['width_hz'] / 1e3:.1f} kHz -> Q_L about {r['q_loaded_rough']:.0f} "
              "(rough, no fit)")
    else:
        print("  half-depth width: the dip runs off the trace (widen the sweep or zoom out)")
    print(f"  phase swing after removing the cable delay: {r['phase_span_deg']:.1f} deg")
    if r["coupling_guess"] and r["clear_dip"]:
        print(f"  coupling guess: {r['coupling_guess']}coupled (G at the dip is "
              f"{r['angle_from_off_deg']:.0f} deg from off resonance)")
    elif r["coupling_guess"]:
        print("  coupling guess: overcoupled (no dip, but the phase swings more than 180 deg)")
    else:
        print("  coupling guess: none (no clear dip; an overcoupled high-Q resonance shows as a "
              "phase swing of ~360 deg with |G| near 0 dB)")


# ---------------------------------------------------------------------------
# Temperatures and repeatability
# ---------------------------------------------------------------------------

def temperature_log(db_path, take_id=None, check_id=None):
    """Every run of a take (None and no check_id: the newest take) or a check, oldest first,
    with its fridge readings and run_timestamp."""
    if check_id is not None:
        rows = db._run_rows(db_path, '"check_id" = ?', (check_id,))
        what = f"check {check_id}"
    else:
        if take_id is None:
            takes = db.list_takes(db_path, verbose=False)
            if not takes:
                raise LookupError(f"No takes in {db_path}")
            take_id = takes[-1]["take_id"]
        rows = db._run_rows(db_path, '"take_id" = ? AND "kind" != ?', (take_id, "corrected"))
        what = f"take {take_id}"
    if not rows:
        raise LookupError(f"No runs for {what} in {Path(db_path).name}")
    for r in rows:
        r["what"] = what
    return rows


def repeat_spread(take, definitions=None):
    """For a take with several repeats: per channel, the max and median |G_repeat - G_mean|
    from its corrected runs (repeatability plus noise). Prints and returns {ch: (max, median)}."""
    corr = take["corrected"]
    if not corr:
        print("No corrected runs in this take")
        return {}
    definitions = definitions or corr[-1]["definitions"]
    corr = [c for c in corr if c["definitions"] == definitions]
    out = {}
    for ch in sorted({c["channel"] for c in corr}):
        reps = np.array([c["data"]["S11"] for c in corr if c["channel"] == ch])
        if len(reps) < 2:
            continue
        d = np.abs(reps - reps.mean(axis=0))
        out[ch] = (float(d.max()), float(np.median(d)))
        print(f"RF{ch}: {len(reps)} repeats, |G - mean| max {out[ch][0]:.2g}, median {out[ch][1]:.2g}")
    if not out:
        print("Only one repeat per channel: nothing to compare (set repeats > 1 in the cooldown file)")
    return out
