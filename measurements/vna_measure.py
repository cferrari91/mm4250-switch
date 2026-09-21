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
"""

from qcodes.instrument import Instrument

DEFAULT_VNA_NAME = "ksvna"
DEFAULT_SWITCH_NAME = "switch"

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
        vna.power(power)
    if averages is not None:
        if averages > 1:
            vna.averages_enabled(True)
            vna.averages(averages)
        else:
            vna.averages_enabled(False)

    return sweep_settings(vna=vna)


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
            tr = vna.add_trace()
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
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    traces = ensure_traces(sparams, vna=vna)
    traces[0].run_sweep()

    # Reading .polar() would otherwise re-trigger its own sweep
    # (auto_sweep defaults to True); we already swept above, so turn that
    # off for the reads below -- otherwise each S-parameter would come
    # from a different sweep.
    prev_auto_sweep = vna.auto_sweep()
    vna.auto_sweep(False)
    try:
        data = {sparam: tr.polar() for sparam, tr in zip(sparams, traces)}
    finally:
        vna.auto_sweep(prev_auto_sweep)

    freq_hz = vna.frequency_axis()
    return freq_hz, data


def measure_sparam(sparam="S11", vna=None):
    """
    Trigger one sweep and read `sparam` back as complex data.
    Returns (freq_hz, values) as (np.ndarray, np.ndarray[complex]).

    One-parameter form of measure_sparams.
    """
    freq_hz, data = measure_sparams((sparam,), vna=vna)
    return freq_hz, data[sparam]


def _select(channel=None, state=None, switch=None):
    """
    Put the switch where the caller asked, if they asked at all.

    `channel` (1-6) selects an RF channel; `state` names any switch
    position directly ("ALL_OPEN", "INTERNAL_SHORT", "RFC_RF4", ...).
    Pass neither to measure whatever the switch is already set to, or if
    there's no switch in the setup at all. Returns a short label for the
    position, or None if nothing was changed.
    """
    if channel is not None and state is not None:
        raise ValueError("pass channel= or state=, not both")
    if channel is None and state is None:
        return None

    switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")
    if channel is not None:
        switch.channel(channel)
        label = f"RF{channel}"
    else:
        switch.state(state)
        label = state
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

    _select(channel, state, switch)

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

    _select(channel, state, switch)

    freq_hz, data = measure_sparams(SPARAMS_2PORT, vna=vna)
    print(f"Measured {', '.join(SPARAMS_2PORT)}: {len(freq_hz)} points, "
          f"{freq_hz[0] / 1e9:.6g} - {freq_hz[-1] / 1e9:.6g} GHz")
    return freq_hz, data
