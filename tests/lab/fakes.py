"""
Fake instruments for the measurements/lab code: no hardware anywhere.

FakeVNA and FakeSwitch stand in for KeysightP5004B and the lab driver's
MM4250 at the level measurements/lab/vna.py talks to them (parameters,
traces, SDATA reads), so the real select / measure_2port / take code runs.
What the VNA reads comes from a `chain` with measure_2port(channel=,
state=): by default the made-up fridge in tests/test_ecal.py (per-port
error boxes, known DUTs, drift), so the e-cal has a right answer to hit.

    rig = make_rig(start=510e6, stop=610e6, points=201)
    rig.vna, rig.switch, rig.fridge     # pass these to vna.take(...) etc.

Extras for the checks:
  * switch.stuck_closed / stuck_open: channels that won't open / close;
  * vna.noise: complex Gaussian noise added to every read;
  * vna.port1_line: which fridge line VNA port 1 is on; on the thru line
    S21 is a transmission (THRU_CLOSED with the thru channel closed,
    THRU_OPEN otherwise);
  * vna.directions: per sweep, ("forward",) or ("forward", "reverse"), the
    way the real VNA picks them: with port 2's source state AUTO it sources
    only when a trace needs S12 or S22 ("reverse"); OFF never ("reverse,
    port 2 off" if a trace wanted it); ON always. vna.reverse_sweeps counts
    the sweeps in which port 2 sourced. vna.port2_mode is the state
    (SOUR1:POW2:MODE <state> / ?); vna.port2_supported = False makes the
    VNA ignore the command and refuse the query. CALC:PAR:DEL "<name>"
    deletes a trace.
"""

import importlib.util
import os
import sys
import types
from pathlib import Path

import numpy as np

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.append(str(TESTS))

THRU_CLOSED = 0.5        # -6 dB through line RF3 -> RF2 with the thru channel closed
THRU_OPEN = 1e-3         # -60 dB: the switch's isolation


class _Param:
    """A QCoDeS-style parameter: p() gets, p(x) sets, p.cache.get() gives the last value."""

    def __init__(self, value=None, on_set=None):
        self.value = value
        self.on_set = on_set
        self.cache = types.SimpleNamespace(get=lambda get_if_invalid=True: self.value)

    def __call__(self, *args):
        if not args:
            return self.value
        if self.on_set is not None:
            self.on_set(args[0])
        self.value = args[0]


class FakeTrace:
    def __init__(self, vna, num):
        self.vna = vna
        self.trace_num = num
        self.trace_name = f"CH1_TR{num}"
        self.trace = _Param("S11")

    def run_sweep(self):
        self.vna.sweep()
        return "CONT"

    def ask(self, query):
        raise RuntimeError(f"fake trace doesn't answer {query}")


class FakeVNA:
    """Keysight P5004B as vna.py uses it."""

    name = "ksvna"

    def __init__(self, chain, switch, thru=None, noise=0.0, seed=0):
        self.chain, self.switch = chain, switch
        self.thru = thru                       # (fridge line, channel) of the thru, or None
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        self.port1_line = "input"
        self.start, self.stop = _Param(100e6), _Param(10e9)
        self.points, self.if_bandwidth = _Param(201), _Param(1e3)
        self.power, self.trigger_source = _Param(-20.0), _Param("IMM")
        self.output = _Param(True)
        self.sweep_type = _Param("LIN")
        self.averages_enabled, self.averages = _Param(False), _Param(1)
        self.sweep_mode = _Param("CONT")
        self.active_trace = _Param(1)
        self.correction = 0
        self._traces = []
        self._next_trace = 1
        self._data = None
        self.writes = []
        self.sweeps = 0
        self.reverse_sweeps = 0
        self.directions = []
        self.port2_mode = "AUTO"              # the VNA's default
        self.port2_supported = True
        self.closed = False
        self.visa_handle = types.SimpleNamespace(query_binary_values=self._sdata)

    @property
    def traces(self):
        return list(self._traces)

    def add_trace(self):
        self._traces.append(FakeTrace(self, self._next_trace))
        self._next_trace += 1
        raise RuntimeError("driver's zip() bug: the trace was made anyway")   # as the real one can

    def write(self, cmd):
        self.writes.append(cmd)
        if cmd == "SENS:CORR:CSET:DEAC":
            self.correction = 0
        if cmd.startswith("SOUR1:POW2:MODE ") and self.port2_supported:
            self.port2_mode = cmd.split(" ", 1)[1].strip().upper()
        if cmd.startswith("CALC:PAR:DEL "):
            name = cmd.split(" ", 1)[1].strip('"')
            self._traces = [tr for tr in self._traces if tr.trace_name != name]

    def ask(self, query):
        if query == "SENS:CORR:STAT?":
            return str(self.correction)
        if query == "SOUR1:POW2:MODE?" and self.port2_supported:
            return self.port2_mode
        raise RuntimeError(f"fake VNA doesn't answer {query}")

    def frequency_axis(self):
        return np.linspace(self.start(), self.stop(), self.points())

    def sweep(self):
        """One triggered sweep: read the chain at the switch's actual position."""
        if not self.output():
            raise RuntimeError("fake VNA: sweep with the source off")
        wants_reverse = any(tr.trace() in ("S12", "S22") for tr in self._traces)
        sourced = self.port2_mode == "ON" or (wants_reverse and self.port2_mode == "AUTO")
        if sourced:
            self.directions.append(("forward", "reverse"))
        elif wants_reverse:
            self.directions.append(("forward", "reverse, port 2 off"))
        else:
            self.directions.append(("forward",))
        self.reverse_sweeps += sourced
        pos = self.switch.actual()
        if self.thru is not None and self.port1_line == self.thru[0]:
            n = self.points()
            s21 = np.full(n, THRU_CLOSED if pos == self.thru[1] else THRU_OPEN, complex)
            data = {"S11": np.full(n, 0.1 + 0j), "S12": s21, "S21": s21, "S22": np.full(n, 0.1 + 0j)}
        elif isinstance(pos, str):
            _, data = self.chain.measure_2port(state=pos)
        else:
            _, data = self.chain.measure_2port(channel=pos)
        if self.noise:
            data = {k: v + self.noise * (self.rng.normal(size=v.size) + 1j * self.rng.normal(size=v.size))
                    for k, v in data.items()}
        self._data = data
        self.sweeps += 1

    def _sdata(self, query, datatype="f", is_big_endian=True):
        tr = next(t for t in self._traces if t.trace_num == self.active_trace())
        z = np.asarray(self._data[tr.trace()], complex)
        return np.column_stack([z.real, z.imag]).ravel().tolist()

    def close(self):
        self.closed = True


class FakeSwitch:
    """
    The lab MM4250 driver as vna.py uses it. moves: every position set, with
    whether the VNA source was on at the time (hot switching).
    """

    def __init__(self):
        self.vna = None
        self.moves = []
        self.stuck_closed, self.stuck_open = set(), set()
        self._actual = "ALL_OPEN"
        self.state = _Param("ALL_OPEN", on_set=lambda v: self._move(v))
        self.channel = _Param(None, on_set=lambda v: self._move(v))
        self.fail_on = None
        self.closed = False

    def _move(self, pos):
        try:
            hot = bool(self.vna.output()) if self.vna is not None else None
        except Exception:
            hot = None                                  # the VNA link is down
        self.moves.append((pos, hot))
        if pos == self.fail_on:
            raise RuntimeError("simulated switch failure")
        if isinstance(self._actual, int) and self._actual in self.stuck_closed:
            pass                                        # can't leave a stuck-closed channel
        elif isinstance(pos, int) and pos in self.stuck_open:
            self._actual = "ALL_OPEN"
        else:
            self._actual = pos
        self.state.value = pos if isinstance(pos, str) else f"RFC_RF{pos}"

    def actual(self):
        return self._actual

    def open_all(self):
        self._move("ALL_OPEN")

    @property
    def last(self):
        return self.moves[-1][0] if self.moves else None

    def close(self):
        self.closed = True


class FakeFridge:
    """MaybellFridge as the notebooks use it: .mxp/.mxp2/.stp.temperature()."""

    def __init__(self, kelvin=295.0):
        self.kelvin = kelvin              # a number, None, or an Exception to raise
        mk = lambda: types.SimpleNamespace(temperature=self._read)
        self.mxp, self.mxp2, self.stp = mk(), mk(), mk()
        self.closed = False

    def _read(self):
        if isinstance(self.kelvin, Exception):
            raise self.kelvin
        return self.kelvin

    def close(self):
        self.closed = True


def make_chain(start, stop, points, seed=1):
    """test_ecal's Fridge on this sweep's frequency axis."""
    from test_ecal import Fridge
    chain = Fridge(n=points, seed=seed)
    chain.freq = np.linspace(start, stop, points)
    return chain


def make_rig(start=510e6, stop=610e6, points=201, kelvin=295.0, thru=("RF3", 2), noise=0.0,
             seed=1):
    chain = make_chain(start, stop, points, seed)
    switch = FakeSwitch()
    vna = FakeVNA(chain, switch, thru=thru, noise=noise)
    switch.vna = vna
    return types.SimpleNamespace(chain=chain, vna=vna, switch=switch, fridge=FakeFridge(kelvin))


# ---------------------------------------------------------------------------
# A switch with gates, for the gate test (vna.hv_test)
# ---------------------------------------------------------------------------
#
# The switch tables (STATE_HV_PINS / STATE_WIRE_BYTES) are the lab driver's own, loaded from
# the framework's drivers/MM4250_QCodes_driver.py with a stub `hid`, so the byte layout is
# checked against Menlo's real states. GateSwitch decodes whatever bytes it's sent into
# energized HV lines; GateChain reflects at the first open gate of a made-up tree:
#
#     RFC -- internal standards -- [HV2 & HV8] -- [HV10] -- [HV16] -- RF4 connector -- open
#
# (the real topology isn't known; this one is in series so every gate shows up).

def _framework():
    return Path(os.environ.get("MM4250_FRAMEWORK", TESTS.parents[1] / "QCoDeS-Measurement-Framework"))


def _load_driver():
    name = "mm4250_driver_for_hv_tests"
    if name in sys.modules:
        return sys.modules[name]
    sys.modules.setdefault("hid", types.ModuleType("hid"))     # only the HID calls need it
    spec = importlib.util.spec_from_file_location(name, _framework() / "drivers" / "MM4250_QCodes_driver.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


DRV = _load_driver()
PINS = {k.value: tuple(int(x[2:]) for x in v) for k, v in DRV.STATE_HV_PINS.items()}
WIRE = {k.value: tuple(v) for k, v in DRV.STATE_WIRE_BYTES.items()}

# Where each stage of the fake RF4 path reflects when it's the first open one (round trip, ns,
# from the internal standards). The connector is ~0.5 ns round trip out (warm SN0077).
TAU = {"internal": 0.0, "HV10": 0.12, "HV16": 0.26, "beyond": 0.60}


def _bytes_to_lines(wire):
    hi, mid, lo = wire[:3]
    lines = {n + 1 for n in range(8) if lo >> n & 1}
    lines |= {n + 9 for n in range(8) if mid >> n & 1}
    lines |= {n + 17 for n in range(6) if hi >> n & 1}
    return frozenset(lines)


class _Device:
    def __init__(self, sw):
        self.sw, self.writes, self.buffer = sw, [], None

    def write(self, msg):
        self.writes.append(list(msg))
        if msg[1] == 0x00:
            self.buffer = tuple(msg[2:8])
        elif msg[1] == 0x02:
            self.sw._raw(self.buffer)


class GateSwitch(FakeSwitch):
    """FakeSwitch + a driver-board `_device`. Normal moves energize the state's lines too.
    dead: lines whose gate never closes; frozen: lines whose gate is always closed."""

    def __init__(self):
        super().__init__()
        self._device = _Device(self)
        self.dead, self.frozen = set(), set()
        self.lines = frozenset()
        self.raw_moves = []                      # (bytes, source on?) for every raw write

    def _move(self, pos):
        super()._move(pos)
        state = pos if isinstance(pos, str) else f"RFC_RF{pos}"
        self.lines = frozenset(PINS[state])

    def _raw(self, wire):
        hot = bool(self.vna.output()) if self.vna is not None else None
        self.raw_moves.append((wire, hot))
        self.lines = _bytes_to_lines(wire)
        self._actual = ("lines", self.lines)

    def gate_closed(self, n):
        return (n in self.lines and n not in self.dead) or n in self.frozen


GateSwitch.__module__ = DRV.__name__              # so vna._driver_tables finds the tables


class GateChain:
    """Raw S21 = e00 + e01e10 G / (1 - e11 G), G from the first open gate (RF4 path) or the
    internal standards; other channels: a 50 ohm load past their connector."""

    def __init__(self, sw, freq, seed=3):
        self.sw, self.freq = sw, freq
        rng = np.random.default_rng(seed)
        w = 2 * np.pi * freq
        self.e00 = 0.15 * np.exp(-1j * w * 3e-9) + 0.01 * rng.normal()
        self.e11 = 0.1 * np.exp(-1j * w * 1e-9)
        self.e01e10 = 0.8 * np.exp(-1j * w * 90e-9)

    def _gamma(self):
        sw, w = self.sw, 2 * np.pi * self.freq
        at = lambda tau_ns: np.exp(-1j * w * tau_ns * 1e-9)          # an open tau_ns round trip out
        if sw.gate_closed(12):
            return -np.ones_like(w, complex)                           # internal short
        if sw.gate_closed(1):
            return np.zeros_like(w, complex)                           # internal load
        if sw.gate_closed(2) and sw.gate_closed(8):                              # RF3/4/5 trunk
            if sw.gate_closed(9) and sw.gate_closed(17):
                return 0.03 * at(TAU["beyond"])                       # RF3: a load
            if sw.gate_closed(11) and sw.gate_closed(18):
                return 0.03 * at(TAU["beyond"])                       # RF5: a load
            if not sw.gate_closed(10):
                return at(TAU["HV10"])
            if not sw.gate_closed(16):
                return at(TAU["HV16"])
            return at(TAU["beyond"])                                   # RF4 all the way out
        if sw.gate_closed(3) and sw.gate_closed(19):
            return 0.03 * at(TAU["beyond"])                           # RF1/2/6 side: loads
        return at(TAU["internal"])                                     # ALL_OPEN

    def measure_2port(self, channel=None, state=None):
        g = self._gamma()
        s21 = self.e00 + self.e01e10 * g / (1 - self.e11 * g)
        n = self.freq.size
        return self.freq, {"S11": np.full(n, 0.1 + 0j), "S21": s21, "S12": s21, "S22": np.full(n, 0.1 + 0j)}


def make_gate_rig(start=510e6, stop=610e6, points=201, kelvin=0.058, thru=None, noise=1e-5):
    """Like make_rig, with GateSwitch + GateChain (thru: (fridge line, channel) or None)."""
    sw = GateSwitch()
    chain = GateChain(sw, np.linspace(start, stop, points))
    v = FakeVNA(chain, sw, thru=thru, noise=noise)
    sw.vna = v
    return types.SimpleNamespace(vna=v, switch=sw, fridge=FakeFridge(kelvin), chain=chain)
