"""
Interactive VNA measurements, meant to be typed into a notebook.

Every function here finds the already-connected instruments by name, so
once QCodesMeasurmentFramework.ipynb (or any notebook) has created
`ksvna` and `switch`, you can just type:

    setup_sweep(start=1e9, stop=10e9, points=1001, if_bandwidth=1e3, power=-20)
    freq, s11 = measure_s11()

and that's the whole measurement. Pass vna=/switch= explicitly if your
instruments were registered under different names.

Nothing here saves anything -- these return numpy arrays and print what
they did. See sweep_db.py for the batch sweeps that save Touchstone
files and record QCoDeS runs.

Both 1-port and 2-port live here, on one shared core: ensure_traces /
measure_sparams take a list of S-parameters and read them all back from a
single sweep. measure_s11 and measure_2port are thin wrappers on that.

What comes back is the VNA's corrected, unformatted data (SDATA) -- see
_read_sdata for why that matters and what it still doesn't protect you
from. instrument_state() snapshots the settings that decide what a
measurement means; sweep_db attaches it to every run it records.
"""

import numpy as np

from qcodes.instrument import Instrument

DEFAULT_VNA_NAME = "ksvna"
DEFAULT_SWITCH_NAME = "switch"

# Highest source power these measurements will set or sweep at, in dBm.
#
# 0 dBm is where the P5004B's specified maximum output bottoms out across
# its full range -- +10 dBm from 10 MHz to 6.5 GHz, but only +4 dBm from
# 16-20 GHz and 0 dBm below 100 kHz -- so it is the most the instrument
# can deliver levelled at every frequency it covers. It also sits 7 dB
# under receiver compression at the top of a 1-10 GHz sweep, 27 dB under
# the +27 dBm damage level, and 4 dB under the MM4250's 0.5 V
# hot-switching limit. One number, four constraints.
#
# The reason it is enforced rather than documented: power is in dBm, so
# `power=-20` and `power=20` are one keystroke apart and 10,000x apart in
# watts. Nothing else stands between that typo and the hardware.
#
# To go higher deliberately, raise this:
#     import vna_measure
#     vna_measure.MAX_POWER_DBM = 5.0
# which is a visible, greppable act rather than an argument that can be
# passed by accident.
MAX_POWER_DBM = 0.0

# The four S-parameters of a full 2-port measurement, in the order a
# person reads them. NOT the order they go into a .s2p file -- Touchstone
# wants S11, S21, S12, S22. See save_touchstone() in sweep_db.py.
SPARAMS_2PORT = ("S11", "S12", "S21", "S22")


def _resolve_instrument(instrument, default_name, label):
    """Return `instrument` if given, else look up the already-registered
    QCoDeS instrument named `default_name` (e.g. the `ksvna`/`switch`
    instances created by QCodesMeasurmentFramework.ipynb)."""
    if instrument is not None:
        return instrument
    try:
        return Instrument.find_instrument(default_name)
    except KeyError:
        raise RuntimeError(
            f"No {label} instance given and none named {default_name!r} is "
            f"registered. Connect it first, or pass it explicitly, e.g. "
            f"measure_s11(vna=ksvna)."
        )


def sweep_settings(vna=None):
    """
    Return the VNA's current sweep settings as a dict, and print them.
    Handy for checking what you're about to measure with.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    settings = {
        "start": vna.start(),
        "stop": vna.stop(),
        "points": vna.points(),
        "if_bandwidth": vna.if_bandwidth(),
        "power": vna.power(),
        "averages_enabled": vna.averages_enabled(),
        "averages": vna.averages(),
        "sweep_type": vna.sweep_type(),
        "trigger_source": vna.trigger_source(),
    }

    print("VNA sweep settings:")
    print(f"  {settings['start'] / 1e9:.6g} - {settings['stop'] / 1e9:.6g} GHz, "
          f"{settings['points']} points ({settings['sweep_type']})")
    print(f"  IF bandwidth {settings['if_bandwidth']:.6g} Hz, power {settings['power']:.6g} dBm")
    if settings["averages_enabled"]:
        print(f"  averaging ON, {settings['averages']} averages")
    else:
        print("  averaging OFF")
    print(f"  trigger source {settings['trigger_source']}")
    return settings


def setup_sweep(start=None, stop=None, points=None, if_bandwidth=None,
                power=None, averages=None, vna=None):
    """
    Set up the VNA sweep. Only the arguments you actually pass are
    changed -- everything else is left as-is on the instrument, so you
    can nudge one setting without restating the rest:

        setup_sweep(points=2001)

    Frequencies are in Hz, power in dBm. `averages=1` (or 0) turns
    averaging off; anything higher turns it on and sets the count.

    `power` is refused above MAX_POWER_DBM (0 dBm); see that constant for
    why, and for how to raise it deliberately if you ever need to.

    Two things are always set regardless of what you pass: the trigger
    source to "IMM" and the RF output on. Those aren't sweep settings so
    much as the preconditions for a sweep finishing at all -- see
    _check_ready() for what each one does if left wrong.

    Prints and returns the resulting settings.

    NOTE: changing the frequency range, number of points or IF bandwidth
    invalidates any calibration currently applied on the VNA. Re-run the
    cal after changing these.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    if start is not None:
        vna.start(start)
    if stop is not None:
        vna.stop(stop)
    if points is not None:
        vna.points(points)
    if if_bandwidth is not None:
        vna.if_bandwidth(if_bandwidth)
    if power is not None:
        if power > MAX_POWER_DBM:
            raise ValueError(
                f"Refusing to set {power:g} dBm: the ceiling is "
                f"{MAX_POWER_DBM:g} dBm. Above it the source is no longer "
                f"levelled across the full band, and the margin to receiver "
                f"compression and to the switch's 0.5 V hot-switching limit "
                f"starts running out. If you meant it, raise "
                f"vna_measure.MAX_POWER_DBM."
            )
        vna.power(power)
    if averages is not None:
        if averages > 1:
            vna.averages_enabled(True)
            vna.averages(averages)
        else:
            vna.averages_enabled(False)

    # Not sweep settings, but a sweep can't complete without them: the
    # VNA has to trigger itself, and the source has to be on.
    vna.trigger_source("IMM")
    vna.output(True)

    return sweep_settings(vna=vna)


# Settings that change what a measurement means but leave no trace in a
# Touchstone file. Recorded with every run by instrument_state(); the
# keys become metadata names, prefixed with "vna_".
VNA_STATE_QUERIES = {
    "correction_enabled": "SENS:CORR:STAT?",
    "port_extensions_enabled": "SENS:CORR:EXT:STAT?",
    "port1_extension_s": "SENS:CORR:EXT:PORT1:TIME?",
    "port2_extension_s": "SENS:CORR:EXT:PORT2:TIME?",
    "fixturing_enabled": "CALC:FSIM:STAT?",
}

# The same, per trace. These are CALC: settings, so they apply to
# whichever measurement is active and can differ between S-parameters --
# which is why they're queried per trace rather than read off the
# instrument-level parameters the driver exposes.
TRACE_STATE_QUERIES = {
    "electrical_delay_s": "CALC:CORR:EDEL:TIME?",
    "phase_offset_deg": "CALC:CORR:OFFS:PHAS?",
    "magnitude_offset_db": "CALC:CORR:OFFS:MAGN?",
    "smoothing_enabled": "CALC:SMO:STAT?",
    "smoothing_aperture": "CALC:SMO:APER?",
    "trace_math": "CALC:MATH:FUNC?",
}


def _try_ask(instrument, query):
    """
    Ask `query` and return the reply as a number where it parses as one,
    a string otherwise, or None if the instrument won't answer.

    Provenance is best-effort on purpose. These are model- and
    firmware-dependent SCPI queries, and one a given P5004B doesn't
    implement should cost you a missing metadata key, not a failed
    measurement -- hence the broad except. record_measurement drops None
    values, so an unanswered query simply doesn't appear on the run.
    """
    try:
        reply = instrument.ask(query).strip().strip('"')
    except Exception:
        return None
    try:
        value = float(reply)
    except ValueError:
        return reply
    return int(value) if value.is_integer() else value


def instrument_state(sparams=(), vna=None):
    """
    Snapshot everything about the VNA that decides what a measurement
    means, as a flat dict ready to attach to a QCoDeS run as metadata.

    Reading SDATA keeps the formatting chain out of the numbers (see
    _read_sdata), but the correction stage upstream of it still shapes
    them and leaves no trace in a Touchstone file: port extensions, and
    the instrument's own fixturing/de-embedding. Recording those is what
    makes a file auditable a year later -- and what lets you notice a run
    that got de-embedded twice, once on the instrument and once in post.

    `sparams` names the S-parameters to snapshot per-trace settings for.
    Read-only: traces are looked up by S-parameter and none are created,
    so this is safe to call on an instrument you don't want to touch.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

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

    if sparams:
        existing = {tr.trace(): tr for tr in vna.traces}
        for sparam in sparams:
            trace = existing.get(sparam)
            if trace is None:
                continue
            for key, query in TRACE_STATE_QUERIES.items():
                state[f"vna_{key}_{sparam}"] = _try_ask(trace, query)

    return state


def _read_sdata(trace, vna):
    """
    Read one trace's corrected complex S-parameter data.

    Queries SDATA rather than going through the driver's .polar()
    parameter, which reads FDATA -- the *formatted* data, meaning
    everything the VNA does downstream of error correction: electrical
    delay, phase and magnitude offsets, trace math, smoothing,
    time-domain gating. On a clean instrument the two are identical,
    which is exactly what makes FDATA risky. Dialing in electrical delay
    to flatten a phase trace on screen is routine, and under FDATA it
    would rotate the phase of every file saved afterwards, silently and
    unrecoverably -- the delay is not written into a Touchstone file, so
    nothing in the file would say it happened.

    SDATA is corrected but unformatted, so none of that reaches the data.
    It does not bypass the error correction itself, nor anything applied
    as part of it: port extensions and on-instrument fixturing sit
    upstream of SDATA and do reach the data. instrument_state() records
    those on every run and _check_ready() warns when they're on.

    Returns a complex numpy array, one entry per sweep point.
    """
    vna.active_trace(trace.trace_num)
    raw = vna.visa_handle.query_binary_values(
        "CALC:DATA? SDATA", datatype="f", is_big_endian=True
    )
    raw = np.asarray(raw, dtype=np.float64)

    points = vna.points()
    if raw.size != 2 * points:
        raise RuntimeError(
            f"Expected {2 * points} numbers back for a {points} point sweep "
            f"-- SDATA is a real/imaginary pair per point -- but got "
            f"{raw.size}. The sweep may have been interrupted."
        )
    return raw.reshape((-1, 2)).view(np.complex128).ravel()


# Sweep types whose frequency axis can be reconstructed, mapped to the
# driver parameter that reconstructs it. Anything not in here has an
# x-axis that start/stop/points don't describe.
FREQUENCY_AXES = {
    "LIN": "frequency_axis",
    "LOG": "frequency_log_axis",
}


def _frequency_axis(vna):
    """
    Return the frequency points of the sweep, as a numpy array.

    The VNA is never asked what its x-axis actually is -- the driver
    computes it from start/stop/points -- so the formula has to match the
    sweep type. np.linspace is right for a linear sweep and np.geomspace
    for a log one; for a segment, CW or power sweep neither is, and the
    wrong one doesn't fail, it just writes plausible-looking frequencies
    the data never came from. Refuse those instead.

    Computing the axis beats querying the instrument for it here. The
    driver sets FORM REAL,32 at startup, and float32 steps by 1024 Hz
    around 10 GHz and 2048 Hz around 20 GHz -- so on a 1-10 GHz, 1001
    point sweep a queried axis would write 8632000512.0 where the
    computed one writes 8632000000.0. Round endpoints happen to survive
    (10 GHz is exactly representable), which only makes the ones that
    don't harder to notice, and it's enough to stop frequencies matching
    exactly between a measurement and a cal or model file. If segment
    sweeps are ever needed, that's the point to add a CALC:X? query under
    FORM REAL,64, not before.
    """
    sweep_type = vna.sweep_type()
    axis = FREQUENCY_AXES.get(sweep_type)
    if axis is None:
        raise RuntimeError(
            f"VNA sweep type is {sweep_type!r}, and only "
            f"{'/'.join(sorted(FREQUENCY_AXES))} have a frequency axis this "
            f"code can reconstruct -- measuring one of the others would "
            f"save S-parameters against frequencies they didn't come from. "
            f"Set a linear sweep with {vna.name}.sweep_type('LIN')."
        )
    return getattr(vna, axis)()


def _check_ready(vna):
    """
    Refuse to trigger a sweep the VNA can't finish, or can only finish
    with nothing connected to the source.

    None of these raises an error of its own, which is what makes them
    worth checking: source power above the ceiling costs you flatness and
    margin, a trigger source other than "IMM" leaves run_sweep() waiting
    forever for a trigger that isn't coming, RF output off writes the
    noise floor into a Touchstone file that looks perfectly valid, and a
    sweep type whose axis can't be reconstructed mislabels every
    frequency. setup_sweep() sets these correctly; this catches the case
    where it wasn't run, or where someone changed things at the front
    panel afterwards.
    """
    power = vna.power()
    if power > MAX_POWER_DBM:
        raise RuntimeError(
            f"VNA source power is {power:g} dBm, above the "
            f"{MAX_POWER_DBM:g} dBm ceiling. Set it with setup_sweep(power=...), "
            f"or raise vna_measure.MAX_POWER_DBM if you meant it."
        )

    source = vna.trigger_source()
    if source != "IMM":
        raise RuntimeError(
            f"VNA trigger source is {source!r}, so the sweep would wait "
            f"forever for a trigger that isn't coming. Run setup_sweep(), "
            f"or set it directly with {vna.name}.trigger_source('IMM')."
        )
    if not vna.output():
        raise RuntimeError(
            f"VNA RF output is off, so the sweep would record the noise "
            f"floor. Run setup_sweep(), or turn it on with "
            f"{vna.name}.output(True)."
        )

    # Only some sweep types have a frequency axis this code can work out.
    # Find that out now rather than after spending a sweep on it.
    _frequency_axis(vna)

    # Reading SDATA keeps the formatting chain out of the data, but the
    # correction stage is upstream of it and does reach the numbers. Both
    # of these are legitimate things to have on, so warn rather than
    # refuse -- but say so, because a setup that de-embeds on the
    # instrument and again in post de-embeds twice, and nothing about the
    # result looks wrong.
    for key, what in (
        ("fixturing_enabled", "fixturing/de-embedding"),
        ("port_extensions_enabled", "port extensions"),
    ):
        if _try_ask(vna, VNA_STATE_QUERIES[key]):
            print(
                f"[WARNING] VNA {what} is ON. It is applied before the data is "
                f"read, so it will be baked into the saved files. Recorded on "
                f"the run as vna_{key}."
            )


def _add_trace(vna):
    """
    Add one trace to the VNA and return it.

    Wrapper around vna.add_trace(), which can't be trusted to report what
    it did: it matches the new trace catalog against the old one with
    zip(), and zip() stops at the shorter list -- so when the PNA appends
    the new trace at the end (the usual case) the added trace is the one
    entry the loop never reaches, and it raises RuntimeError even though
    the trace was created fine. The same truncation can also make it
    return a trace that already existed if the catalog ever comes back
    reordered, which would be worse: ensure_traces would then repoint a
    trace it had already set up, silently clobbering an S-parameter.

    So ignore what add_trace() returns, and identify the new trace by
    diffing the catalog against itself by name. That's correct whatever
    order the PNA lists things in.
    """
    before = {tr.trace_name for tr in vna.traces}
    try:
        vna.add_trace()
    except RuntimeError:
        # The write went out before it gave up, so the trace is there.
        pass

    added = [tr for tr in vna.traces if tr.trace_name not in before]
    if len(added) != 1:
        raise RuntimeError(
            f"Expected exactly one new trace on the VNA, found {len(added)}. "
            f"Traces before: {sorted(before)}. If this is 0, the PNA "
            f"refused to add one -- check it isn't already at its trace "
            f"limit, and that nothing else is driving it."
        )
    return added[0]


def ensure_traces(sparams, vna=None):
    """
    Make sure a trace exists on the VNA for each S-parameter in
    `sparams` (e.g. ("S11", "S21")), reusing whichever are already
    configured rather than adding duplicates. Returns the trace objects
    in the same order as `sparams`.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    # vna.traces re-queries the instrument every time it's read, so take
    # the catalog once up front rather than once per S-parameter.
    existing = {tr.trace(): tr for tr in vna.traces}

    traces = []
    for sparam in sparams:
        tr = existing.get(sparam)
        if tr is None:
            tr = _add_trace(vna)
            tr.trace(sparam)
            existing[sparam] = tr
        traces.append(tr)
    return traces


def ensure_trace(sparam="S11", vna=None):
    """
    Make sure a single trace measuring `sparam` exists, and return it.
    One-parameter form of ensure_traces.
    """
    return ensure_traces((sparam,), vna=vna)[0]


def measure_sparams(sparams=SPARAMS_2PORT, vna=None):
    """
    Trigger ONE sweep and read back every S-parameter in `sparams` from
    it. Returns (freq_hz, data), where data maps each S-parameter name to
    a complex numpy array:

        freq, data = measure_sparams(("S11", "S21"))
        data["S21"]        # complex ndarray

    One trigger covers all of them -- run_sweep() runs every trace on the
    VNA's channel, and the P5004B takes care of the reverse sweep needed
    for S12/S22 on its own. This is the generic read that measure_s11 and
    measure_2port are both built on.

    Only linear and log sweeps are supported; see _frequency_axis().
    The reads themselves never trigger anything, so all the values come
    from the one sweep by construction.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")
    _check_ready(vna)

    traces = ensure_traces(sparams, vna=vna)

    # run_sweep() parks the VNA in HOLD and hands back the sweep mode it
    # was in beforehand. Keep that and put it back at the end -- left
    # alone, the instrument stops sweeping after every measurement and
    # the screen goes static, which looks like a hang at the bench.
    prev_mode = traces[0].run_sweep()

    # _read_sdata only queries -- unlike the driver's .polar(), which
    # re-triggers a sweep of its own unless auto_sweep is turned off
    # first. So every S-parameter here comes from the one sweep above
    # without having to disable anything to make that true.
    try:
        data = {sparam: _read_sdata(tr, vna) for sparam, tr in zip(sparams, traces)}
    finally:
        vna.sweep_mode(prev_mode)

    freq_hz = _frequency_axis(vna)
    return freq_hz, data


def measure_sparam(sparam="S11", vna=None):
    """
    Trigger one sweep and read `sparam` back as complex data.
    Returns (freq_hz, values) as (np.ndarray, np.ndarray[complex]).

    One-parameter form of measure_sparams.
    """
    freq_hz, data = measure_sparams((sparam,), vna=vna)
    return freq_hz, data[sparam]


def _select(channel=None, state=None, switch=None, vna=None):
    """
    Put the switch where the caller asked, if they asked at all, with the
    source off while the contacts move.

    `channel` (1-6) selects an RF channel; `state` names any switch
    position directly ("ALL_OPEN", "INTERNAL_SHORT", "RFC_RF4", ...).
    Pass neither to measure whatever the switch is already set to, or if
    there's no switch in the setup at all. Returns a short label for the
    position, or None if nothing was changed.

    The MM4250 is an ohmic MEMS switch -- real metal contacts that
    physically make and break. Moving them with RF flowing draws a
    micro-arc across the closing gap that erodes and slowly welds the
    contact. That's "hot switching", capped at 0.5 V by the datasheet
    (about +4 dBm into 50 ohms), and it doesn't fail the part outright;
    it collapses the 1.1e9 cycle rating until a channel sticks closed or
    goes high-resistance. Nothing about the measurement looks wrong while
    it happens.

    So the source is dropped for the move and restored afterwards. At the
    -20 dBm the notebooks use there is already 24 dB of margin and this
    changes nothing; it matters the day someone raises the power and
    doesn't think about the switch. It costs a few milliseconds, and it
    removes the question rather than leaving it to be remembered.

    Setting `switch.channel(...)` or `switch.state(...)` by hand skips
    this -- go through here, or turn the source off yourself first.
    """
    if channel is not None and state is not None:
        raise ValueError("pass channel= or state=, not both")
    if channel is None and state is None:
        return None

    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    source_was_on = vna.output()
    if source_was_on:
        vna.output(False)
    try:
        if channel is not None:
            switch.channel(channel)
            label = f"RF{channel}"
        else:
            switch.state(state)
            label = state
    finally:
        # Restore the source even if the switch refused the position --
        # otherwise a bad channel number would leave the VNA dark and the
        # next measurement would fail for an unrelated-looking reason.
        if source_was_on:
            vna.output(True)

    if source_was_on:
        print(f"Switch set to {label} (source off during the move)")
    else:
        print(f"Switch set to {label}")
    return label


def measure_s11(channel=None, state=None, vna=None, switch=None):
    """
    Take a 1-port S11 measurement and return (freq_hz, s11).

    If `channel` is given (1-6), the MM4250 switch is set to that RF
    channel first; `state` sets any switch position by name instead.
    Leave both out to measure whatever the switch is already set to, or
    if there's no switch in the setup at all.

        freq, s11 = measure_s11()                      # measure as-is
        freq, s11 = measure_s11(3)                     # switch to RF3, then measure
        freq, s11 = measure_s11(state="INTERNAL_LOAD") # a built-in standard
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    _select(channel, state, switch, vna=vna)

    freq_hz, s11 = measure_sparam("S11", vna=vna)
    print(f"Measured S11: {len(freq_hz)} points, "
          f"{freq_hz[0] / 1e9:.6g} - {freq_hz[-1] / 1e9:.6g} GHz")
    return freq_hz, s11


def measure_2port(channel=None, state=None, vna=None, switch=None):
    """
    Take a full 2-port measurement and return (freq_hz, data), where
    data is {"S11": ..., "S12": ..., "S21": ..., "S22": ...} of complex
    numpy arrays -- all four read from a single sweep.

    `channel`/`state` work exactly as in measure_s11: set the switch
    first, or leave both out and measure it where it stands.

        freq, data = measure_2port()             # measure as-is
        freq, data = measure_2port(3)            # switch to RF3, then measure
        freq, data = measure_2port(state="ALL_OPEN")

    What the numbers mean depends entirely on how the VNA is cabled --
    these are raw S-parameters at the VNA's own port reference planes,
    with no calibration or de-embedding applied.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    _select(channel, state, switch, vna=vna)

    freq_hz, data = measure_sparams(SPARAMS_2PORT, vna=vna)
    print(f"Measured {', '.join(SPARAMS_2PORT)}: {len(freq_hz)} points, "
          f"{freq_hz[0] / 1e9:.6g} - {freq_hz[-1] / 1e9:.6g} GHz")
    return freq_hz, data
