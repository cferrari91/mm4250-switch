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
    THRU_OPEN otherwise).
"""

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
        self._data = None
        self.writes = []
        self.sweeps = 0
        self.closed = False
        self.visa_handle = types.SimpleNamespace(query_binary_values=self._sdata)

    @property
    def traces(self):
        return list(self._traces)

    def add_trace(self):
        self._traces.append(FakeTrace(self, len(self._traces) + 1))
        raise RuntimeError("driver's zip() bug: the trace was made anyway")   # as the real one can

    def write(self, cmd):
        self.writes.append(cmd)
        if cmd == "SENS:CORR:CSET:DEAC":
            self.correction = 0

    def ask(self, query):
        if query == "SENS:CORR:STAT?":
            return str(self.correction)
        raise RuntimeError(f"fake VNA doesn't answer {query}")

    def frequency_axis(self):
        return np.linspace(self.start(), self.stop(), self.points())

    def sweep(self):
        """One triggered sweep: read the chain at the switch's actual position."""
        if not self.output():
            raise RuntimeError("fake VNA: sweep with the source off")
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
        tr = self._traces[self.active_trace() - 1]
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
