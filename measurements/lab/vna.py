"""
The instruments: VNA sweep setup, one 2-port measurement per switch
position, the fridge temperature, and the measurement sequences (a take,
the stuck check, the thru check).

Nothing here writes files. Each sequence hands every position to a
`save` function as soon as it is measured (db.Saver), so a sequence that
dies halfway keeps what it already measured. Every sequence leaves the
switch ALL_OPEN at the end, even when it dies (try/finally).

Safety, kept from the older vna_measure.py / sweep_db.py:

  * No hot switching: the switch only moves through select(), which
    turns the VNA source off for the move and waits SWITCH_SETTLE_S.
  * Power ceiling MAX_POWER_DBM (0 dBm), checked when the sweep is set
    and again before every sweep.
  * Raw data only: a sequence refuses to start if the VNA's own
    correction is on (check_correction_off).
  * The lab driver's switch.state() readback lags (the board's replies
    queue up), so this code only ever sets the switch; to print what was
    last sent, use switch.state.cache.get(get_if_invalid=False).
  * The fridge is optional: a missing fridge, an error or a None reading
    gives no temperature, never a stopped run.

Copied from vna_measure.py and sweep_db.py (2026-11-08) so this folder
stands alone; see those files' history for the longer reasoning.
"""

import time
from datetime import datetime

import numpy as np

# Highest source power this code will set or sweep at, in dBm. 0 dBm is
# the most the P5004B delivers levelled across its whole range, 7 dB under
# receiver compression at 10 GHz, 27 dB under damage, and 4 dB under the
# MM4250's 0.5 V hot-switching limit. power=-20 and power=20 are one
# keystroke apart, so this is enforced, not documented. To go higher on
# purpose, raise it visibly: `vna.MAX_POWER_DBM = 5.0`.
MAX_POWER_DBM = 0.0

# Slack when reading power back (the source has finite resolution).
POWER_READBACK_SLACK_DB = 0.05

# The four S-parameters of a 2-port measurement, in reading order (not
# Touchstone order: that is S11, S21, S12, S22, see db.save_touchstone).
SPARAMS = ("S11", "S12", "S21", "S22")

# Wait after moving the switch, source still off. Menlo's cryogenic app
# note (APN-0021) wants ~25 ms through filtered fridge wiring; 50 ms is twice that.
SWITCH_SETTLE_S = 0.05

# The switch's internal standards, in the order a take measures them.
# ALL_OPEN is the open standard (Menlo's "ALL OFF", NIST's ecal_open).
STANDARDS = ("ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD")


# ---------------------------------------------------------------------------
# Sweep setup and the VNA's state
# ---------------------------------------------------------------------------

def setup_sweep(vna, sweep):
    """
    Set the VNA from the cooldown file's "sweep" block
    ({"start_hz", "stop_hz", "points", "if_bandwidth_hz", "power_dbm"}),
    turn its own correction off (this code measures raw), set the trigger
    to IMM and the source on. Refuses power above MAX_POWER_DBM.
    """
    power = sweep["power_dbm"]
    if power > MAX_POWER_DBM:
        raise ValueError(f"Refusing {power:g} dBm: the ceiling is {MAX_POWER_DBM:g} dBm "
                         "(vna.MAX_POWER_DBM). Fix power_dbm in the cooldown file.")
    vna.start(sweep["start_hz"])
    vna.stop(sweep["stop_hz"])
    vna.points(sweep["points"])
    vna.if_bandwidth(sweep["if_bandwidth_hz"])
    vna.power(power)
    vna.write("SENS:CORR:CSET:DEAC")      # raw data: the VNA's own cal off
    vna.trigger_source("IMM")              # or the sweep waits forever for a trigger
    vna.output(True)                       # or the sweep records the noise floor
    print(f"VNA: {sweep['start_hz'] / 1e6:g}-{sweep['stop_hz'] / 1e6:g} MHz, "
          f"{sweep['points']} points, IF {sweep['if_bandwidth_hz']:g} Hz, {power:g} dBm, "
          "correction off")


# Settings that change what a measurement means but aren't in a
# Touchstone file. Recorded on every run as vna_<key> (best effort).
VNA_STATE_QUERIES = {
    "correction_enabled": "SENS:CORR:STAT?",
    "port_extensions_enabled": "SENS:CORR:EXT:STAT?",
    "fixturing_enabled": "CALC:FSIM:STAT?",
}
TRACE_STATE_QUERIES = {
    "electrical_delay_s": "CALC:CORR:EDEL:TIME?",
    "smoothing_enabled": "CALC:SMO:STAT?",
    "trace_math": "CALC:MATH:FUNC?",
}

# Queries this VNA refused once; not asked again (each costs a VISA timeout).
_UNSUPPORTED_QUERIES = set()


def _try_ask(instrument, query):
    """The reply as a number (or text), or None if the instrument won't answer."""
    if query in _UNSUPPORTED_QUERIES:
        return None
    try:
        reply = instrument.ask(query).strip().strip('"')
    except Exception:
        _UNSUPPORTED_QUERIES.add(query)
        return None
    try:
        value = float(reply)
    except ValueError:
        return reply
    return int(value) if value.is_integer() else value


def instrument_state(vna):
    """The VNA settings that decide what a measurement means, as vna_* metadata."""
    state = {
        "vna_start_hz": vna.start(),
        "vna_stop_hz": vna.stop(),
        "vna_points": vna.points(),
        "vna_if_bandwidth_hz": vna.if_bandwidth(),
        "vna_power_dbm": vna.power(),
        "vna_sweep_type": vna.sweep_type(),
        "vna_averages": vna.averages() if vna.averages_enabled() else 1,
    }
    for key, query in VNA_STATE_QUERIES.items():
        state[f"vna_{key}"] = _try_ask(vna, query)
    existing = {tr.trace(): tr for tr in vna.traces}
    for sparam in SPARAMS:
        if sparam in existing:
            for key, query in TRACE_STATE_QUERIES.items():
                state[f"vna_{key}_{sparam}"] = _try_ask(existing[sparam], query)
    return state


def check_correction_off(vna):
    """Refuse to measure with the VNA's own correction on: this code wants raw data."""
    corr = _try_ask(vna, VNA_STATE_QUERIES["correction_enabled"])
    if corr not in (0, 1):
        raise RuntimeError(f"Couldn't read the VNA's correction state (SENS:CORR:STAT? -> "
                           f"{corr!r}); nothing was measured.")
    if corr == 1:
        raise RuntimeError("VNA correction is ON. This code measures raw data: turn it off "
                           "with ksvna.write('SENS:CORR:CSET:DEAC') (setup_sweep does). "
                           "Nothing was measured.")


def _check_ready(vna):
    """Refuse a sweep that can't finish or would record the wrong thing."""
    power = vna.power()
    if power > MAX_POWER_DBM + POWER_READBACK_SLACK_DB:
        raise RuntimeError(f"VNA source power is {power:g} dBm, above the {MAX_POWER_DBM:g} "
                           "dBm ceiling. Run setup_sweep.")
    if vna.trigger_source() != "IMM":
        raise RuntimeError("VNA trigger source isn't IMM, so the sweep would wait forever. "
                           "Run setup_sweep.")
    if not vna.output():
        raise RuntimeError("VNA RF output is off, so the sweep would record the noise floor. "
                           "Run setup_sweep.")
    _frequency_axis(vna)
    for key in ("fixturing_enabled", "port_extensions_enabled"):
        if _try_ask(vna, VNA_STATE_QUERIES[key]):
            print(f"[WARNING] VNA {key.replace('_enabled', '')} is ON: baked into the data. "
                  f"Recorded as vna_{key}.")


def _frequency_axis(vna):
    """
    The sweep's frequency points, computed from start/stop/points (exact,
    unlike a float32 query). Linear and log sweeps only: for any other
    type the computed axis would be wrong without looking wrong.
    """
    axis = {"LIN": "frequency_axis", "LOG": "frequency_log_axis"}.get(vna.sweep_type())
    if axis is None:
        raise RuntimeError(f"VNA sweep type is {vna.sweep_type()!r}; only LIN/LOG are "
                           f"supported. Set {vna.name}.sweep_type('LIN').")
    return getattr(vna, axis)()


# ---------------------------------------------------------------------------
# One measurement
# ---------------------------------------------------------------------------

def _add_trace(vna):
    """
    Add one trace and return it. The driver's add_trace() can raise or
    return the wrong trace (it zips catalogs of different lengths), so
    the new trace is found by diffing the catalog by name.
    """
    before = {tr.trace_name for tr in vna.traces}
    try:
        vna.add_trace()
    except RuntimeError:
        pass                                    # the trace was made anyway
    added = [tr for tr in vna.traces if tr.trace_name not in before]
    if len(added) != 1:
        raise RuntimeError(f"Expected one new trace on the VNA, found {len(added)}.")
    return added[0]


def _ensure_traces(vna):
    """A trace for each of SPARAMS, reusing existing ones."""
    existing = {tr.trace(): tr for tr in vna.traces}
    traces = []
    for sparam in SPARAMS:
        tr = existing.get(sparam)
        if tr is None:
            tr = _add_trace(vna)
            tr.trace(sparam)
            existing[sparam] = tr
        traces.append(tr)
    return traces


def _read_sdata(trace, vna):
    """
    One trace's complex data, from SDATA (corrected but unformatted), so
    electrical delay, offsets, smoothing and trace math set on the screen
    never reach the saved numbers.
    """
    vna.active_trace(trace.trace_num)
    raw = np.asarray(vna.visa_handle.query_binary_values(
        "CALC:DATA? SDATA", datatype="f", is_big_endian=True), dtype=np.float64)
    points = vna.points()
    if raw.size != 2 * points:
        raise RuntimeError(f"Expected {2 * points} numbers for {points} points, got "
                           f"{raw.size}. The sweep may have been interrupted.")
    return raw.reshape((-1, 2)).view(np.complex128).ravel()


def select(position, vna, switch):
    """
    Move the switch to `position` (a channel 1-6 or a state name like
    "ALL_OPEN") with the VNA source off during the move: the MM4250 is an
    ohmic MEMS switch, and moving it with RF on (hot switching) wears the
    contacts until a channel sticks. The source is restored even if the
    switch refuses. Setting switch.channel()/state() by hand skips this.
    """
    source_was_on = vna.output()
    if source_was_on:
        vna.output(False)
    try:
        if isinstance(position, str):
            switch.state(position)
        else:
            switch.channel(position)
        time.sleep(SWITCH_SETTLE_S)
    finally:
        if source_was_on:
            vna.output(True)
    print(f"Switch set to {position_name(position)}" + (" (source off during the move)"
                                                        if source_was_on else ""))


def measure_2port(position, vna, switch):
    """
    Move the switch to `position` (source off), trigger ONE sweep and read
    all four S-parameters from it. Returns (freq_hz, {"S11": ..., ...}),
    raw at the VNA's ports.
    """
    select(position, vna, switch)
    _check_ready(vna)
    traces = _ensure_traces(vna)
    prev_mode = traces[0].run_sweep()           # leaves the VNA in HOLD
    try:
        data = {sparam: _read_sdata(tr, vna) for sparam, tr in zip(SPARAMS, traces)}
    finally:
        vna.sweep_mode(prev_mode)               # or the screen freezes after every sweep
    return _frequency_axis(vna), data


def leave_switch_open(vna, switch):
    """
    Get the switch to ALL_OPEN whatever state the VNA is in. Never raises,
    so the error that ended a sequence is the one you see. If the VNA is
    what failed, select() can't reach the switch, so fall back to the
    switch's own USB link (at -20 dBm far inside the hot-switching limit).
    """
    try:
        select("ALL_OPEN", vna, switch)
        return
    except Exception as e:
        print(f"  [warning] couldn't open the switch the normal way ({type(e).__name__}: {e})")
    try:
        vna.output(False)
    except Exception:
        pass
    try:
        switch.open_all()
        print("  [warning] switch opened directly: ALL_OPEN")
    except Exception as e:
        print(f"  [WARNING] SWITCH MAY STILL BE CLOSED -- run switch.open_all() by hand "
              f"({type(e).__name__}: {e})")


def position_name(position):
    """4 -> "RF4", "ALL_OPEN" -> "ALL_OPEN"."""
    return position if isinstance(position, str) else f"RF{position}"


# ---------------------------------------------------------------------------
# Fridge temperature (optional)
# ---------------------------------------------------------------------------

def read_temperature(fridge):
    """
    The mixing plate (fridge.mxp, the switch's plate) in kelvin, or None:
    no fridge, an error, or the driver's None (no calibration curve for
    that range). Never raises.
    """
    if fridge is None:
        return None
    try:
        value = fridge.mxp.temperature()
        if value is None or isinstance(value, bool):
            return None
        value = float(value)
        return value if value > 0 else None
    except Exception as e:
        print(f"  [warning] no fridge reading ({type(e).__name__}: {e})")
        return None


def temp_label(kelvin):
    """295.2 -> "295K", 0.012 -> "12mK", None -> "noT" (for names and titles)."""
    if kelvin is None:
        return "noT"
    return f"{kelvin:.0f}K" if kelvin >= 1 else f"{kelvin * 1e3:.0f}mK"


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

def run_positions(positions, vna, switch, fridge=None, on_each=None):
    """
    Measure each position in order (a channel 1-6 or a state name) and
    call on_each(step, position, freq, data, temp_k) right after each one
    (step counts from 1). Returns a list of (position, freq, data, temp_k).
    The switch is left ALL_OPEN at the end, even if this dies partway.
    """
    out = []
    try:
        for step, position in enumerate(positions, 1):
            temp_k = read_temperature(fridge)
            freq, data = measure_2port(position, vna, switch)
            if on_each is not None:
                on_each(step, position, freq, data, temp_k)
            out.append((position, freq, data, temp_k))
    finally:
        leave_switch_open(vna, switch)
    return out


def new_id():
    """A timestamp id to the millisecond, e.g. "20261108T141502.318"."""
    return datetime.now().strftime("%Y%m%dT%H%M%S.%f")[:-3]


def _common(cfg, temp_k):
    """What every run of a take or check carries."""
    return {"cooldown": cfg["cooldown"], "switch_serial": cfg["switch_serial"],
            "start_temp_k": temp_k, "temp_str": temp_label(temp_k),
            "cooldown_json": cfg["_text"]}


def _start(cfg, vna, fridge):
    """Before anything moves: sweep from the file, correction off, first temperature."""
    setup_sweep(vna, cfg["sweep"])
    check_correction_off(vna)
    temp_k = read_temperature(fridge)
    print(f"Fridge (mixing plate): {temp_label(temp_k) if temp_k else 'no reading'}")
    return temp_k


def take(cfg, vna, switch, fridge=None, channels=None, note="", save=None):
    """
    One take: the internal standards, each chosen channel, the standards
    again (the drift check), `cfg["repeats"]` times; every position a
    2-port measurement.

        ALL_OPEN, INTERNAL_SHORT, INTERNAL_LOAD     standards, when="before"
        RF<n> for each channel                      kind "raw"
        ALL_OPEN, INTERNAL_SHORT, INTERNAL_LOAD     standards, when="after"

    channels: None = every port in the cooldown file, or a list like [4].
    A one-channel take still measures both sets of standards (the
    correction needs them). save(name, freq, data, metadata) is called
    after each position (db.Saver); it returns a run id or None.

    Returns {"take_id", "temp_k", "temp_str", "channels", "repeats":
    [{"before": {state: run_id}, "ports": {ch: run_id}, "after": {...}}]}.
    """
    ports = port_channels(cfg)
    channels = ports if channels is None else list(channels)
    if not channels:
        raise ValueError("No channels to measure: list ports in the cooldown file or pass channels=[...]")
    for ch in channels:
        if ch not in ports:
            raise ValueError(f"RF{ch} isn't in the cooldown file's ports "
                             f"({', '.join(f'RF{c}' for c in ports)}); nothing was measured")
    if len(set(channels)) != len(channels):
        raise ValueError(f"channels has duplicates: {channels}")

    temp_k = _start(cfg, vna, fridge)
    take_id = new_id()
    print(f"Take {take_id}: {', '.join(f'RF{c}' for c in channels)}, "
          f"{cfg['repeats']} repeat(s)")
    common = dict(_common(cfg, temp_k), take_id=take_id, note=note or None)

    plan = []                                   # (position, metadata) in order
    for k in range(1, cfg["repeats"] + 1):
        for when, positions in (("before", STANDARDS), ("ports", channels), ("after", STANDARDS)):
            for pos in positions:
                meta = dict(common, repeat=k, position=position_name(pos))
                if when == "ports":
                    meta.update(kind="raw", channel=pos, port=cfg["ports"][f"RF{pos}"])
                else:
                    meta.update(kind="standard", standards_when=when)
                plan.append((pos, meta, when))

    repeats = [{"before": {}, "ports": {}, "after": {}} for _ in range(cfg["repeats"])]

    def on_each(step, pos, freq, data, t):
        _, meta, when = plan[step - 1]
        meta = dict(meta, step=step, mxc_temp_k=t, **instrument_state(vna))
        run_id = save(position_name(pos), freq, data, meta) if save else None
        repeats[meta["repeat"] - 1][when][pos] = run_id

    run_positions([p for p, _, _ in plan], vna, switch, fridge, on_each)
    print(f"Take {take_id} complete.")
    return {"take_id": take_id, "temp_k": temp_k, "temp_str": temp_label(temp_k),
            "channels": channels, "repeats": repeats}


def port_channels(cfg):
    """The channel numbers listed in the cooldown file's ports, sorted: [1, 2, ...]."""
    return sorted(int(name[2:]) for name in cfg["ports"])


def stuck_check(cfg, vna, switch, fridge=None, save=None):
    """
    ALL_OPEN, INTERNAL_SHORT, ALL_OPEN, INTERNAL_LOAD, ALL_OPEN, then each
    port in the cooldown file followed by ALL_OPEN. Each position is
    compared with the first ALL_OPEN: median |S21 - first ALL_OPEN| (a
    complex difference, so phase counts). Every ALL_OPEN should be small
    and every closed position clearly bigger; see stuck_verdicts().

    save(name, freq, data, metadata) is called after each position, as in
    take(). Returns {"check_id", "temp_k", "rows", "freq"}; each row is
    {"step", "position", "label", "s21", "diff", "temp_k", "run_id"}.
    """
    seq = ["ALL_OPEN", "INTERNAL_SHORT", "ALL_OPEN", "INTERNAL_LOAD", "ALL_OPEN"]
    for ch in port_channels(cfg):
        seq += [ch, "ALL_OPEN"]
    return _check("stuck", seq, cfg, vna, switch, fridge, save)


def thru_check(cfg, vna, switch, fridge=None, save=None, ask=input):
    """
    The thru check through fridge line lines.thru, which is wired to switch
    channel thru_channel. Asks you to move VNA port 1's cable from the input
    line to the thru line (Enter; n stops before anything moves), measures
    ALL_OPEN, RF<thru>, ALL_OPEN, RF<thru>, ALL_OPEN, then asks you to move
    the cable back. S21 is then a transmission: high with the channel
    closed, at the switch's isolation with it open. Returns the same shape
    as stuck_check, plus "gap_db" (worst closed minus worst open median
    |S21|, dB) and "passed" (None with no thru_pass_db in the file).
    """
    ch = cfg["thru_channel"]
    if ch is None:
        raise ValueError("No thru_channel in the cooldown file: nothing to check")
    lines = cfg["lines"]
    reply = ask(f"Move VNA port 1's cable from fridge line {lines['input']} to line "
                f"{lines['thru']} (port 2 stays on {lines['return']}), then press Enter "
                "(n + Enter stops): ")
    if str(reply).strip().lower() in ("n", "no"):
        print("Stopped: nothing was measured.")
        return None
    try:
        out = _check("thru", ["ALL_OPEN", ch, "ALL_OPEN", ch, "ALL_OPEN"],
                     cfg, vna, switch, fridge, save)
    finally:
        ask(f"Now move VNA port 1's cable back to line {lines['input']}, then press Enter: ")
    level = {r["step"]: 20 * np.log10(np.median(np.abs(r["s21"]))) for r in out["rows"]}
    for r in out["rows"]:
        r["level_db"] = level[r["step"]]
    closed = [r["level_db"] for r in out["rows"] if r["position"] == ch]
    opened = [r["level_db"] for r in out["rows"] if r["position"] == "ALL_OPEN"]
    out["gap_db"] = min(closed) - max(opened)
    mark = cfg["stuck_check"]["thru_pass_db"]
    out["passed"] = None if mark is None else bool(out["gap_db"] > mark)
    return out


def _check(check, seq, cfg, vna, switch, fridge, save):
    temp_k = _start(cfg, vna, fridge)
    check_id = new_id()
    print(f"{check.capitalize()} check {check_id}: {len(seq)} positions")
    rows = []
    common = dict(_common(cfg, temp_k), check_type=check, check_id=check_id)

    def on_each(step, pos, freq, data, t):
        ref = rows[0]["s21"] if rows else data["S21"]          # the first ALL_OPEN
        diff = float(np.median(np.abs(data["S21"] - ref)))
        name = position_name(pos)
        port = cfg["ports"].get(name) if not isinstance(pos, str) else None
        meta = dict(common, step=step, position=name, port=port, mxc_temp_k=t,
                    median_abs_s21_minus_first_open=diff, **instrument_state(vna))
        run_id = save(name, freq, data, meta) if save else None
        rows.append({"step": step, "position": pos, "label": name + (f" ({port})" if port else ""),
                     "s21": data["S21"], "diff": diff, "temp_k": t, "run_id": run_id})

    measured = run_positions(seq, vna, switch, fridge, on_each)
    return {"check": check, "check_id": check_id, "temp_k": temp_k, "rows": rows,
            "freq": measured[0][1] if measured else None}


def stuck_verdicts(result, pass_db=None):
    """
    Read a stuck check by comparing each position with its neighbours (the
    chain's gain drifts while the fridge cools, so fixed levels don't work):

      closed position P:
          contrast = median|S21_P - the ALL_OPEN before it| / open-to-open noise
          (open-to-open noise: the median, over every pair of consecutive
          ALL_OPENs, of median|difference|; one stuck channel only spoils one pair)
      ALL_OPEN right after P:
          contrast = median|S21_open - S21_P| / median|S21_open - open before P|
          (it should look like the earlier ALL_OPEN, not like P)

    both in dB. Healthy rows are well above 0 dB; ~0 dB or below means the
    two things it compares can't be told apart. With pass_db (the cooldown
    file's stuck_check.pass_db): a closed position below it is flagged
    "STUCK OPEN?"; an ALL_OPEN below it (after a position that wasn't
    itself flagged) "not open after <P>: STUCK CLOSED?". Once one channel
    is stuck closed, later rows can be flagged too: the first flag is the
    one that counts. pass_db None: numbers only, nothing flagged.
    Adds "contrast_db" (None for the first row) and "flag" to each row;
    returns the list of flags.
    """
    rows = result["rows"]
    tiny = np.finfo(float).tiny
    med = lambda a, b: max(float(np.median(np.abs(a["s21"] - b["s21"]))), tiny)
    is_open = lambda r: r["position"] == "ALL_OPEN"
    opens = [r for r in rows if is_open(r)]
    pairs = [med(b, a) for a, b in zip(opens, opens[1:])]
    noise = float(np.median(pairs)) if pairs else None
    flags = []
    for i, r in enumerate(rows):
        r["contrast_db"], r["flag"] = None, ""
        before = next((rows[j] for j in range(i - 1, -1, -1) if is_open(rows[j])), None)
        if i == 0 or before is None:
            continue
        if not is_open(r):
            if noise is None:                       # stopped before a second ALL_OPEN
                continue
            r["contrast_db"] = 20 * np.log10(med(r, before) / noise)
            if pass_db is not None and r["contrast_db"] < pass_db:
                r["flag"] = "STUCK OPEN?"
        else:
            prev = rows[i - 1]
            if is_open(prev):
                continue
            earlier = next((rows[j] for j in range(i - 2, -1, -1) if is_open(rows[j])), None)
            if earlier is None:
                continue
            r["contrast_db"] = 20 * np.log10(med(r, prev) / med(r, earlier))
            if pass_db is not None and r["contrast_db"] < pass_db and not prev["flag"]:
                r["flag"] = f"not open after {prev['label']}: STUCK CLOSED?"
        if r["flag"]:
            flags.append(f"step {r['step']} {r['label']}: {r['flag']}")
    return flags


def print_check(result, pass_db=None):
    """Print a stuck check's table (stuck_verdicts) or a thru check's levels."""
    if result["check"] == "thru":
        for r in result["rows"]:
            print(f"{r['label']:>24}  median |S21| = {r['level_db']:6.1f} dB")
        print(f"\nclosed vs ALL_OPEN, worst case: {result['gap_db']:.1f} dB apart")
        if result["passed"] is None:
            print("No thru_pass_db in the cooldown file: no verdict.")
        else:
            print("OK: it closes and opens" if result["passed"] else
                  "LOOK AT THIS: closed and open are close together. Stuck open/closed, "
                  "or port 1 isn't on the thru line?")
        return []
    flags = stuck_verdicts(result, pass_db)
    for r in result["rows"]:
        t = "no reading" if r["temp_k"] is None else temp_label(r["temp_k"])
        c = "" if r["contrast_db"] is None else f"contrast {r['contrast_db']:+6.1f} dB"
        print(f"{r['step']:02d} {r['label']:>28}  median |S21 - first ALL_OPEN| = {r['diff']:.3g}"
              f"  {c:>18}  {t}  {r['flag']}")
    if pass_db is None:
        print("\nNo pass mark (stuck_check.pass_db is null): nothing flagged, read the rows. "
              "Every contrast should be well above 0 dB; ~0 dB or below: look at that row.")
    elif flags:
        print("\nFLAGGED:\n  " + "\n  ".join(flags))
        print("If anything is stuck, stop: don't keep switching it cold.")
    else:
        print(f"\nNothing flagged (pass mark {pass_db:g} dB).")
    return flags
