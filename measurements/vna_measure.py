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
they did. See oneport_db_sweep.py for the batch sweep that saves
.s1p files and records QCoDeS runs.

1-port only for now. The trace/sweep helpers are already S-parameter
agnostic (measure_sparam takes "S11", "S21", ... ), so the 2-port version
is a short addition on top of them.
"""

from qcodes.instrument import Instrument

DEFAULT_VNA_NAME = "ksvna"
DEFAULT_SWITCH_NAME = "switch"


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


def ensure_trace(sparam="S11", vna=None):
    """
    Make sure a trace measuring `sparam` (e.g. "S11", "S21") exists on
    the VNA, reusing it if it's already there rather than adding a
    duplicate. Returns the trace object.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    for tr in vna.traces:
        if tr.trace() == sparam:
            return tr
    tr = vna.add_trace()
    tr.trace(sparam)
    return tr


def measure_sparam(sparam="S11", vna=None):
    """
    Trigger one sweep and read `sparam` back as complex data.
    Returns (freq_hz, values) as (np.ndarray, np.ndarray[complex]).

    This is the generic single-parameter read -- measure_s11 is a thin
    wrapper on it, and the 2-port version will be too.
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    tr = ensure_trace(sparam, vna=vna)
    tr.run_sweep()

    # Reading .polar() would otherwise re-trigger its own sweep
    # (auto_sweep defaults to True); we already swept above, so turn that
    # off for the read below.
    prev_auto_sweep = vna.auto_sweep()
    vna.auto_sweep(False)
    try:
        values = tr.polar()
    finally:
        vna.auto_sweep(prev_auto_sweep)

    freq_hz = vna.frequency_axis()
    return freq_hz, values


def measure_s11(channel=None, vna=None, switch=None):
    """
    Take a 1-port S11 measurement and return (freq_hz, s11).

    If `channel` is given (1-6), the MM4250 switch is set to that RF
    channel first; leave it out to measure whatever the switch is
    already set to, or if there's no switch in the setup at all.

        freq, s11 = measure_s11()      # measure as-is
        freq, s11 = measure_s11(3)     # switch to RF3, then measure
    """
    vna = _resolve_instrument(vna, DEFAULT_VNA_NAME, "VNA")

    if channel is not None:
        switch = _resolve_instrument(switch, DEFAULT_SWITCH_NAME, "switch")
        switch.channel(channel)
        print(f"Switch set to RF{channel}")

    freq_hz, s11 = measure_sparam("S11", vna=vna)
    print(f"Measured S11: {len(freq_hz)} points, "
          f"{freq_hz[0] / 1e9:.6g} - {freq_hz[-1] / 1e9:.6g} GHz")
    return freq_hz, s11
