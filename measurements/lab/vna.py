"""
The take, the stuck check and the thru check: a list of switch positions, each measured
with the lab's scripts/switch_vna.py (`sv`). sv does the safe parts: no hot switching,
the power cap, raw data only, port 2 off in forward mode.

After each position the sequence calls save(name, freq, data, metadata) (db.Saver), so a
sequence that dies halfway keeps what it measured. The switch is always left ALL_OPEN.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# scripts/switch_vna.py lives in the framework folder above this one: put that folder on sys.path.
_HERE = Path(__file__).resolve().parent
_ROOT = next((p for p in (_HERE, *_HERE.parents) if (p / "scripts" / "switch_vna.py").is_file()), None)
if _ROOT is not None and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
try:
    from scripts import switch_vna as sv
except ImportError as e:
    raise ImportError("This code needs the lab framework's scripts/switch_vna.py: run it from inside "
                      "QCoDeS-Measurement-Framework, or put that folder on sys.path.") from e

STANDARDS = sv.STATES        # ALL_OPEN (the open), INTERNAL_SHORT, INTERNAL_LOAD


def setup_sweep(vna, sweep):
    """Set the VNA from a sweep block: the cooldown file's band and IF bandwidth plus the
    notebook's points, power and mode (db.with_sweep)."""
    missing = [k for k in ("points", "power_dbm") if k not in sweep]
    if missing:
        raise ValueError(f"No {' or '.join(missing)} for the sweep: pass "
                         "db.with_sweep(cfg, POINTS, POWER_DBM, SWEEP_MODE), not the bare cooldown file")
    sv.setup_vna(vna, sweep["start_hz"], sweep["stop_hz"], points=sweep["points"],
                 power_dbm=sweep["power_dbm"], if_bandwidth_hz=sweep["if_bandwidth_hz"],
                 mode=sweep.get("mode", sv.DEFAULT_SWEEP_MODE))


def temp_label(kelvin):
    """295.2 -> "295K", 0.012 -> "12mK", None -> "noT" (for folder names and titles)."""
    if kelvin is None:
        return "noT"
    return f"{kelvin:.0f}K" if kelvin >= 1 else f"{kelvin * 1e3:.0f}mK"


def port_channels(cfg):
    """The channel numbers in the cooldown file's ports, sorted: [1, 2, ...]."""
    return sorted(int(name[2:]) for name in cfg["ports"])


def _start(cfg, vna, fridge):
    """Before anything moves: set the VNA, check it, read the fridge. Returns (mode, temp_k,
    the metadata every run of this sequence carries)."""
    mode = cfg["sweep"].get("mode", sv.DEFAULT_SWEEP_MODE)
    setup_sweep(vna, cfg["sweep"])
    sv.check_ready(vna, mode)                     # refuse now, before the switch moves
    temp_k = sv.read_temperatures(fridge)["mxp"]
    print(f"Fridge (mixing plate): {temp_label(temp_k) if temp_k else 'no reading'}")
    common = {"cooldown": cfg["cooldown"], "switch_serial": cfg["switch_serial"],
              "start_temp_k": temp_k, "temp_str": temp_label(temp_k),
              "vna_sweep_mode": mode, "cooldown_json": cfg["_text"]}
    return mode, temp_k, common


def run_positions(positions, vna, switch, fridge, on_each, mode):
    """Measure each position in order (sv.measure) and call on_each(step, position, result)
    right after it (step counts from 1). Always ends ALL_OPEN. Returns the results."""
    out = []
    try:
        for step, pos in enumerate(positions, 1):
            r = sv.measure(switch, vna, pos, fridge=fridge, mode=mode)
            on_each(step, pos, r)
            out.append(r)
    finally:
        sv.all_open(switch, vna)
    return out


def take(cfg, vna, switch, fridge=None, channels=None, note="", save=None):
    """
    One take, `cfg["repeats"]` times:

        ALL_OPEN, INTERNAL_SHORT, INTERNAL_LOAD     standards, when="before"
        RF<n> for each channel                      kind "raw"
        ALL_OPEN, INTERNAL_SHORT, INTERNAL_LOAD     standards, when="after" (the drift check)

    channels: None = every port in the cooldown file, or e.g. [4] (the standards are still
    measured: the correction needs them). save: db.Saver (or None).
    Returns {"take_id", "temp_k", "temp_str", "channels", "repeats": [{"before": {state:
    run_id}, "ports": {ch: run_id}, "after": {...}}]}.
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

    mode, temp_k, common = _start(cfg, vna, fridge)
    take_id = datetime.now().strftime("%Y%m%dT%H%M%S.%f")[:-3]     # e.g. 20261108T141502.318
    print(f"Take {take_id}: {', '.join(f'RF{c}' for c in channels)}, {cfg['repeats']} repeat(s)")
    common.update(take_id=take_id, note=note or None)

    plan = []                                     # (position, metadata, when), in order
    for k in range(1, cfg["repeats"] + 1):
        for when, positions in (("before", STANDARDS), ("ports", channels), ("after", STANDARDS)):
            for pos in positions:
                if when == "ports":
                    meta = {"kind": "raw", "channel": pos, "port": cfg["ports"][f"RF{pos}"]}
                else:
                    meta = {"kind": "standard", "standards_when": when}
                plan.append((pos, dict(meta, repeat=k), when))
    repeats = [{"before": {}, "ports": {}, "after": {}} for _ in range(cfg["repeats"])]

    def on_each(step, pos, r):
        _, extra, when = plan[step - 1]
        meta = {**common, **r["labels"], **extra, "step": step}
        run_id = save(sv.position_name(pos), r["freq"], r["data"], meta) if save else None
        repeats[extra["repeat"] - 1][when][pos] = run_id

    run_positions([p for p, _, _ in plan], vna, switch, fridge, on_each, mode)
    print(f"Take {take_id} complete.")
    return {"take_id": take_id, "temp_k": temp_k, "temp_str": temp_label(temp_k),
            "channels": channels, "repeats": repeats}


def stuck_check(cfg, vna, switch, fridge=None, save=None):
    """
    ALL_OPEN, INTERNAL_SHORT, ALL_OPEN, INTERNAL_LOAD, ALL_OPEN, then each port in the
    cooldown file with ALL_OPEN after it. Each position's "diff" is median |S21 - the first
    ALL_OPEN| (complex, so phase counts). Read it with stuck_verdicts / print_check.
    Returns {"check", "check_id", "temp_k", "rows", "freq"}.
    """
    seq = ["ALL_OPEN", "INTERNAL_SHORT", "ALL_OPEN", "INTERNAL_LOAD", "ALL_OPEN"]
    for ch in port_channels(cfg):
        seq += [ch, "ALL_OPEN"]
    return _check("stuck", seq, cfg, vna, switch, fridge, save)


def thru_check(cfg, vna, switch, fridge=None, save=None, ask=input):
    """
    The thru check: asks you to move VNA port 1's cable to the thru line (Enter; n stops
    before anything moves), measures ALL_OPEN, RF<thru>, ALL_OPEN, RF<thru>, ALL_OPEN, then
    asks you to move it back. S21 is then a transmission: high closed, low open.
    Same return as stuck_check, plus "gap_db" (worst closed minus worst open median |S21|)
    and "passed" (None if the cooldown file has no thru_pass_db).
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
        out = _check("thru", ["ALL_OPEN", ch, "ALL_OPEN", ch, "ALL_OPEN"], cfg, vna, switch, fridge, save)
    finally:
        ask(f"Now move VNA port 1's cable back to line {lines['input']}, then press Enter: ")
    for r in out["rows"]:
        r["level_db"] = 20 * np.log10(np.median(np.abs(r["s21"])))
    closed = [r["level_db"] for r in out["rows"] if r["position"] == ch]
    opened = [r["level_db"] for r in out["rows"] if r["position"] == "ALL_OPEN"]
    out["gap_db"] = min(closed) - max(opened)
    mark = cfg["stuck_check"]["thru_pass_db"]
    out["passed"] = None if mark is None else bool(out["gap_db"] > mark)
    return out


def _check(check, seq, cfg, vna, switch, fridge, save):
    """Measure `seq` for a stuck or thru check; one row per position."""
    mode, temp_k, common = _start(cfg, vna, fridge)
    check_id = datetime.now().strftime("%Y%m%dT%H%M%S.%f")[:-3]
    print(f"{check.capitalize()} check {check_id}: {len(seq)} positions")
    common.update(check_type=check, check_id=check_id)
    rows = []

    def on_each(step, pos, r):
        s21 = r["data"]["S21"]
        diff = float(np.median(np.abs(s21 - (rows[0]["s21"] if rows else s21))))   # vs the first ALL_OPEN
        name = sv.position_name(pos)
        port = cfg["ports"].get(name) if not isinstance(pos, str) else None
        meta = {**common, **r["labels"], "step": step, "port": port,
                "median_abs_s21_minus_first_open": diff}
        run_id = save(name, r["freq"], r["data"], meta) if save else None
        temps = {k: v for k, v in r["labels"].items() if k.endswith(("_k_before", "_k_after"))}
        rows.append({"step": step, "position": pos, "label": name + (f" ({port})" if port else ""),
                     "s21": s21, "diff": diff, "temp_k": r["labels"]["mxc_temp_k"], "temps": temps,
                     "run_id": run_id})

    measured = run_positions(seq, vna, switch, fridge, on_each, mode)
    return {"check": check, "check_id": check_id, "temp_k": temp_k, "rows": rows,
            "freq": measured[0]["freq"] if measured else None}


def stuck_verdicts(result, pass_db=None):
    """
    Read a stuck check by comparing each position with its neighbours (the chain drifts while
    the fridge cools, so fixed levels don't work). Contrasts in dB:

      closed position P:  median|P - the ALL_OPEN before it| / open-to-open noise
                          (noise: median over consecutive ALL_OPEN pairs, so one stuck
                          channel only spoils one pair)
      ALL_OPEN after P:   median|open - P| / median|open - the ALL_OPEN before P|

    Healthy rows are well above 0 dB. With pass_db, a closed position below it is flagged
    "STUCK OPEN?" and an ALL_OPEN below it "STUCK CLOSED?" (the first flag is the one that
    counts). pass_db None: numbers only. Adds "contrast_db" and "flag" to each row; returns
    the flags.
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
            if noise is None:                     # stopped before a second ALL_OPEN
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
    """Print a stuck check's table (with stuck_verdicts) or a thru check's levels."""
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


# ---------------------------------------------------------------------------
# Gate (HV line) test: each gate line of a channel on its own
# ---------------------------------------------------------------------------
#
# Each RF channel closes 4 HV gate lines (Menlo pinout; the driver's STATE_HV_PINS), e.g.
# RF4 = HV2 + HV8 (shared with RF3 and RF5) + HV10 + HV16 (RF4's own). The stuck check only
# sees whole channels: a gate that's frozen closed behind one that opens, or one of a
# channel's own gates that won't close while the others do, can pass it. This sends gate
# combinations that aren't in Menlo's state table, straight to the driver board, to see
# what each gate does. Source off for every move; always ends ALL_OPEN.

HV_LINES = range(1, 23)          # the driver board's HV1-HV22 (one Micro-D bus)


def _hv_num(line):
    """10, "HV10", "hv10" -> 10. Anything outside HV1-HV22: ValueError."""
    s = str(line).strip().upper()
    n = int(s[2:]) if s.startswith("HV") else int(s)
    if n not in HV_LINES:
        raise ValueError(f"{line!r} isn't an HV line (HV1-HV22)")
    return n


def hv_label(lines):
    """(2, 8, 10) -> "HV2+HV8+HV10"."""
    return "+".join(f"HV{n}" for n in sorted({_hv_num(x) for x in lines}))


def hv_wire_bytes(lines):
    """
    The 6 bytes that energize exactly these HV lines (Menlo's API, MM4250DriverBoardAPI.md):
    per Micro-D bus (NA NA HV22..HV17)(HV16..HV9)(HV8..HV1), the same on bus A and bus B,
    like every one of Menlo's own states. hv_wire_bytes([]) is ALL_OPEN.
    """
    nums = {_hv_num(x) for x in lines}
    hi = sum(1 << (n - 17) for n in nums if n >= 17)
    mid = sum(1 << (n - 9) for n in nums if 9 <= n <= 16)
    lo = sum(1 << (n - 1) for n in nums if n <= 8)
    return (hi, mid, lo) * 2


def _driver_tables(switch):
    """The switch driver's {state: HV lines} and {state: wire bytes}, keyed by state name,
    after checking hv_wire_bytes rebuilds every one of Menlo's states exactly (so the bit
    layout here can't be off). Raises before anything moves."""
    mod = sys.modules.get(type(switch).__module__)
    pins, wire = getattr(mod, "STATE_HV_PINS", None), getattr(mod, "STATE_WIRE_BYTES", None)
    if not pins or not wire:
        raise RuntimeError(f"{type(switch).__name__}'s module has no STATE_HV_PINS / STATE_WIRE_BYTES: "
                           "the gate test needs the lab MM4250 driver. Nothing moved.")
    name = lambda k: getattr(k, "value", k)
    pins = {name(k): tuple(_hv_num(x) for x in v) for k, v in pins.items()}
    wire = {name(k): tuple(int(b) for b in v) for k, v in wire.items()}
    bad = [s for s, b in wire.items() if hv_wire_bytes(pins.get(s, ())) != b]
    if bad:
        raise RuntimeError(f"hv_wire_bytes doesn't rebuild the driver's bytes for {bad}: the bit "
                           "layout is wrong, so nothing was sent. Nothing moved.")
    return pins, wire


def hv_plan(channel, pins):
    """
    The gate combinations for one channel, in order: each of its lines alone, then its
    shared lines (the ones another channel also uses), then shared + each own line. The full
    channel is measured separately the normal way. Returns (combos, shared, own).
    """
    me = f"RFC_RF{channel}"
    if me not in pins:
        raise ValueError(f"No RF{channel} in the driver's STATE_HV_PINS")
    lines = list(pins[me])
    others = {n for s, ls in pins.items() if s.startswith("RFC_RF") and s != me for n in ls}
    shared = [n for n in lines if n in others]
    own = [n for n in lines if n not in others]
    combos = [(n,) for n in lines]
    if shared and own:
        combos.append(tuple(shared))
        combos += [tuple(shared + [o]) for o in own]
    seen, out = set(), []
    for c in combos:
        key = tuple(sorted(c))
        if key not in seen and set(key) != set(lines):
            seen.add(key)
            out.append(key)
    return out, shared, own


def _send_hv(switch, wire):
    """Write 6 HV bytes to the driver board (load buffer, commit) and wait, like the driver does
    for its own states. The driver's cached state is now wrong, so don't read switch.state()
    until the next normal move (sv.all_open / set_switch write a full state)."""
    switch._device.write([0x00, 0x00, *wire])     # 0x00: set output buffer
    switch._device.write([0x00, 0x02])             # 0x02: write current buffer to the outputs
    mod = sys.modules.get(type(switch).__module__)
    time.sleep(getattr(mod, "TYPICAL_SWITCH_TIME_S", 0.025))   # the driver's own wait after a move


def set_hv_lines(switch, vna, lines):
    """Energize exactly `lines` (source off for the move, then back as it was)."""
    wire = hv_wire_bytes(lines)
    was_on = vna.output()
    if was_on:
        vna.output(False)
    try:
        _send_hv(switch, wire)
        time.sleep(sv.SWITCH_SETTLE_S)
    finally:
        if was_on:
            vna.output(True)
    print(f"Switch: {hv_label(lines)} only (bytes {' '.join(f'{b:02X}' for b in wire)})"
          + (" (source off during the move)" if was_on else ""))
    return wire


def _measure_hv(switch, vna, lines, fridge, mode):
    """Like sv.measure, for a gate combination: same labels, plus the lines and bytes."""
    sparams = sv._sparams(mode)
    sv.check_ready(vna, mode)                     # before anything moves
    wire = set_hv_lines(switch, vna, lines)
    before = sv.read_temperatures(fridge)
    data = sv._sweep(vna, sparams)
    after = sv.read_temperatures(fridge)
    freq = np.linspace(vna.start(), vna.stop(), vna.points())
    labels = {"position": hv_label(lines), "channel": None, "mxc_temp_k": before["mxp"],
              **{f"{s}_k_{when}": t[s] for when, t in (("before", before), ("after", after))
                 for s in sv.FRIDGE_SENSORS},
              **sv.vna_settings(vna), "vna_sweep_mode": mode,
              "hv_lines": hv_label(lines), "hv_wire_bytes": " ".join(f"{b:02X}" for b in wire)}
    return {"position": tuple(lines), "freq": freq, "data": data, "labels": labels}


def hv_test(cfg, vna, switch, fridge=None, channels=(4,), save=None, ask=input, combos=None,
            full=True):
    """
    The gate test, for each channel in `channels`:

        ALL_OPEN, INTERNAL_SHORT, ALL_OPEN, INTERNAL_LOAD, ALL_OPEN       standards
        each gate combination (hv_plan), ALL_OPEN after each
        the channel the normal way, ALL_OPEN                              (full=False: skipped)
        INTERNAL_SHORT, ALL_OPEN, INTERNAL_LOAD, ALL_OPEN                 standards again

    combos: your own list of line sets instead of hv_plan, e.g. [["HV2", "HV8"], [2, 8, 10]],
    for one channel (channels=[4]: the label and the NIST port for the "from connector"
    column). Only lines that some Menlo state uses are allowed (HV4-6 and HV22 aren't).

    Asks before anything moves (n stops). Same return as stuck_check (read it with
    print_check, ecal.hv_positions, plots.plot_hv_test), each row with "hv_channel" and
    "hv_role" ("alone", "shared", "shared+own", "custom", "full", or None for ALL_OPEN/standards).
    """
    pins, _ = _driver_tables(switch)              # also checks the byte layout
    channels = [int(c) for c in channels]
    used = {n for ls in pins.values() for n in ls}
    if combos is not None:
        if len(channels) != 1:
            raise ValueError("combos needs exactly one channel (channels=[4]): nothing moved")
        combos = [tuple(sorted({_hv_num(x) for x in c})) for c in combos]
        if not combos or any(not c for c in combos):
            raise ValueError("combos must be a list of non-empty line lists, e.g. [['HV2', 'HV8']]: nothing moved")
        bad = sorted({n for c in combos for n in c} - used)
        if bad:
            raise ValueError(f"{', '.join(f'HV{n}' for n in bad)}: no Menlo state uses "
                             f"{'it' if len(bad) == 1 else 'them'}, so not sent. Nothing moved.")
    seq, roles = ["ALL_OPEN", "INTERNAL_SHORT", "ALL_OPEN", "INTERNAL_LOAD", "ALL_OPEN"], [None] * 5
    lines_txt = []
    for ch in channels:
        if combos is None:
            plan, shared, own = hv_plan(ch, pins)
            head = f"  RF{ch}: shared {hv_label(shared) or '-'}, own {hv_label(own) or '-'}; "
        else:
            plan, shared, own = combos, None, None
            head = f"  RF{ch} (yours): "
        lines_txt.append(head + ", ".join(hv_label(c) for c in plan)
                         + (f", then RF{ch} normally" if full else ""))
        for c in plan:
            if combos is not None:
                role = "custom"
            else:
                role = "alone" if len(c) == 1 else ("shared" if set(c) == set(shared) else "shared+own")
            seq += [c, "ALL_OPEN"]
            roles += [(ch, role), None]
        if full:
            seq += [ch, "ALL_OPEN"]
            roles += [(ch, "full"), None]
    seq += ["INTERNAL_SHORT", "ALL_OPEN", "INTERNAL_LOAD", "ALL_OPEN"]
    roles += [None] * 4

    print("Gate test: these combinations are NOT in Menlo's state table (each line is still "
          "0 V or ~90 V, as in a normal state; RF source off for every move).")
    print("\n".join(lines_txt))
    print(f"{len(seq)} positions in all, ending ALL_OPEN.")
    reply = ask("Press Enter to start (n + Enter stops): ")
    if str(reply).strip().lower() in ("n", "no"):
        print("Stopped: nothing moved.")
        return None

    mode, temp_k, common = _start(cfg, vna, fridge)
    check_id = datetime.now().strftime("%Y%m%dT%H%M%S.%f")[:-3]
    print(f"Gate test {check_id}: {len(seq)} positions")
    common.update(check_type="hv_lines", check_id=check_id)
    rows, first_freq = [], None
    try:
        for step, (pos, role) in enumerate(zip(seq, roles), 1):
            if isinstance(pos, tuple):
                r = _measure_hv(switch, vna, pos, fridge, mode)
            else:
                r = sv.measure(switch, vna, pos, fridge=fridge, mode=mode)
            ch, kind = role if role else (None, None)
            if isinstance(pos, tuple):
                name = f"RF{ch}_{hv_label(pos)}"
            else:
                name = sv.position_name(pos)
            port = cfg["ports"].get(f"RF{pos}") if isinstance(pos, int) else None
            s21 = r["data"]["S21"]
            diff = float(np.median(np.abs(s21 - (rows[0]["s21"] if rows else s21))))
            meta = {**common, **r["labels"], "step": step, "port": port, "hv_test_channel": ch,
                    "hv_role": kind, "median_abs_s21_minus_first_open": diff}
            if isinstance(pos, int):
                meta["hv_lines"] = hv_label(pins[f"RFC_RF{pos}"])
            run_id = save(name, r["freq"], r["data"], meta) if save else None
            temps = {k: v for k, v in r["labels"].items() if k.endswith(("_k_before", "_k_after"))}
            label = (f"RF{ch}: {hv_label(pos)}" if isinstance(pos, tuple) else
                     name + (f" ({hv_label(pins[f'RFC_RF{pos}'])})" if isinstance(pos, int) else ""))
            rows.append({"step": step, "position": pos if not isinstance(pos, tuple) else hv_label(pos),
                         "label": label, "s21": s21, "diff": diff, "temp_k": r["labels"]["mxc_temp_k"],
                         "temps": temps, "run_id": run_id, "hv_channel": ch, "hv_role": kind})
            first_freq = r["freq"] if first_freq is None else first_freq
    finally:
        sv.all_open(switch, vna)                  # a full state: overwrites any gate combination
    print(f"Gate test {check_id} complete.")
    return {"check": "hv_lines", "check_id": check_id, "temp_k": temp_k, "rows": rows,
            "freq": first_freq}
