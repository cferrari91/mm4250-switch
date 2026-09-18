# mm4250-switch

A QCoDeS driver for the **Menlo Micro MM4250** — an SP6T cryogenic RF
MEMS switch, driven over USB HID through its HiV Driver Board — plus the
measurement code that uses it to sweep S11 across the switch's RF
channels on a Keysight VNA.

The driver is the point of this repo. The measurement code is what it's
for.

> **Status: active development.** This is an in-progress senior design
> project by Charlie Ferrari (Colorado School of Mines), built for the
> QTSF lab. It's public so the work can be read and reused, not because
> it's finished — interfaces, layout and file names may still change,
> and the hardware paths are exercised against one specific VNA and
> switch. Treat it as a working lab tool rather than a released package.
>
> Known gaps: 1-port only (2-port is planned), and no calibration or
> de-embedding is applied to the measurements.

## Layout

```
drivers/        the switch driver (and the VNA drivers it's used with)
measurements/   1-port S11 measurement and sweep
docs/           driver usage notes
```

### `drivers/` — the switch

- **`MM4250_QCodes_driver.py`** — the switch driver. Hardware-only
  (connects for real in `__init__`, no software-only mode), written in
  the plain, print()-based style of the lab framework's other
  single-file drivers rather than heavier type-hinted/defensive code.
  `switch.channel(1..6)` selects an RF port; `switch.state(...)` reaches
  the `ALL_OPEN` / `INTERNAL_SHORT` / `INTERNAL_LOAD` calibration
  standards.
- **`MM4250_QCodes_driver_commented.py`** — the same driver, heavily
  commented, meant to be read top to bottom as a tutorial on both this
  switch and QCoDeS driver-writing in general.

### `drivers/` — the VNA (third-party)

- `N52xx.py` and `KeysightVNA_driver.py` — the Keysight P5004B driver,
  vendored from [QCoDeS](https://github.com/microsoft/Qcodes) under its
  MIT license. Not original to this project; see
  [`drivers/THIRD_PARTY.md`](drivers/THIRD_PARTY.md).

### `measurements/`

- **`vna_measure.py`** — the measurement itself, meant to be typed into
  a notebook: `setup_sweep` (frequency range, points, IF bandwidth,
  power, averaging), `sweep_settings` (print what's currently set),
  `ensure_trace`, `measure_sparam` (trigger one sweep, read an
  S-parameter back as complex data), and `measure_s11`. Saves nothing —
  it returns numpy arrays. 1-port for now; `measure_sparam` is already
  S-parameter agnostic, so 2-port slots in on top of it.
- **`oneport_db_sweep.py`** — the data layer on top of it: `save_s1p`
  (write Touchstone), `record_channel` (save one measurement as a
  QCoDeS run), and `run_oneport_sweep` (measure + save + record over a
  list of channels).
- **`oneport_db_sweep.ipynb`** — runnable notebook: connects to the VNA
  and switch, sweeps the channels you list, saves each to
  `Sweeps/<date>_<temp>/<switch_serials>/raw/RF<n>.s1p` plus a run in
  `mm4250_oneport.db`.
- **`LAB_SETUP.md`** — how to copy this onto the lab measurement
  computer and run it from `users/<name>/`.

## Taking a measurement by hand

Once a notebook has connected the VNA as `ksvna` (and optionally the
switch as `switch`) — which the lab's `QCodesMeasurmentFramework.ipynb`
already does — point Python at `measurements/` and import:

```python
import sys
sys.path.insert(0, r"<path to>/mm4250-switch/measurements")
from vna_measure import setup_sweep, sweep_settings, measure_s11
from oneport_db_sweep import run_oneport_sweep
```

Then the whole measurement is two lines:

```python
setup_sweep(start=1e9, stop=10e9, points=1001, if_bandwidth=1e3, power=-20)
freq, s11 = measure_s11()
```

`setup_sweep` only changes the settings you actually pass, so
`setup_sweep(points=2001)` nudges one thing and leaves the rest alone.
`measure_s11(3)` sets the switch to RF3 first; `measure_s11()` measures
whatever it's already set to. Every function takes optional `vna=` /
`switch=` arguments if your instruments were registered under other
names.

Changing the frequency range, point count or IF bandwidth invalidates
whatever calibration is applied on the VNA — re-run the cal after
changing them.

## Running a batch sweep

- Edit `channels`/`date_str`/`temp_str`/`switch_serials` in
  `measurements/oneport_db_sweep.ipynb`. `channels` is any subset of 1-6
  (e.g. `[1, 3, 5]`), or `list(range(1, 7))` for all of them.
- Outputs land beside the code — `measurements/Sweeps/...` and
  `measurements/mm4250_oneport.db`. Both resolve from the module's own
  folder rather than the working directory, so copying these files
  somewhere else (the lab machine's `users/<name>/`, say) puts the
  outputs in that folder too. Override with `out_root=` / `db_path=`.
- Every run accumulates into that one database file. Each
  `run_oneport_sweep(...)` call is its own QCoDeS *experiment*, named
  `<date_str>_<temp_str>_<switch_serials>` with
  `sample_name=switch_serials`; each channel measured in that call is
  one *run* named `RF<n>` inside it. Re-running the same
  date/temp/serials adds to that experiment rather than duplicating it.
- Each run carries `channel`, `s1p_path`, `switch_serials`, `date_str`
  and `temp_str` as dataset metadata, so any run traces back to the raw
  `.s1p` it was saved alongside.
- Browse the database afterwards with
  `plottr-inspectr --db mm4250_oneport.db`, or load runs in Python with
  `qcodes.dataset`'s `load_by_id`/`load_by_run_spec`.

No calibration or de-embedding is applied — this is raw acquisition
only.

## Running from the lab's measurement framework

`run_oneport_sweep` and the `vna_measure` functions take optional
`vna=`/`switch=` arguments. Leave them out and they look up whatever
QCoDeS instruments are registered under the names `ksvna` and `switch`
in the current kernel — which is what `QCodesMeasurmentFramework.ipynb`
creates. So the same functions work either standalone (the notebook here
connects its own instruments) or from the framework notebook's kernel.

Don't do both in one kernel: the MM4250 opens an exclusive USB handle,
so a second `MM4250("switch")` will fail to connect.

See [`measurements/LAB_SETUP.md`](measurements/LAB_SETUP.md) for the
full walkthrough.

## Dependencies

- Python 3.9+
- `qcodes`
- `pyvisa` (VNA, VISA/TCPIP)
- `hidapi` (`pip install hidapi`) (switch, USB HID)
- `numpy`

## Hardware

- Menlo Micro MM4250 switch + USB HiV Driver Board (VID `0x04D8`, PID
  `0xEDFB`)
- Keysight P5004B VNA (9 kHz-20 GHz, 2-port)

## License

MIT — see [`LICENSE`](LICENSE). The vendored QCoDeS VNA drivers in
`drivers/` carry their own MIT license; see
[`drivers/THIRD_PARTY.md`](drivers/THIRD_PARTY.md).

## Archive

Earlier work not part of this 1-port sweep — the 2-port S-parameter
sweep (`sparam_sweep.py`, `switch_matrix_sweep.ipynb`) and the SOL
de-embedding pipeline (`deembed.py`, `oneport_deembed_sweep.ipynb`, and
the `oneport_sweep.py` module they shared) — was moved to
`../Archive/mm4250-switch-sweep-prior/`. The 2-port work is also still
in this repo's git history at commit `51c66ff`.
