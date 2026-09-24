# mm4250-switch

A QCoDeS driver for the **Menlo Micro MM4250** — an SP6T cryogenic RF
MEMS switch, driven over USB HID through its HiV Driver Board — plus the
measurement code that uses it to sweep S-parameters across the
switch's RF channels on a Keysight VNA.

The driver is the point of this repo. The measurement code is what it's
for.

> **Status: active development.** This is an in-progress senior design
> project by Charlie Ferrari (Colorado School of Mines), built for the
> QTSF lab. It's public so the work can be read and reused, not because
> it's finished — interfaces, layout and file names may still change,
> and the hardware paths are exercised against one specific VNA and
> switch. Treat it as a working lab tool rather than a released package.
>
> Known gaps: no calibration or de-embedding is applied to the
> measurements — they are raw S-parameters at the VNA's own port
> reference planes.

## Layout

```
drivers/        the switch driver (and the VNA drivers it's used with)
measurements/   1- and 2-port measurement and sweeps
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
  `ensure_traces`/`measure_sparams` (make sure the traces exist, then
  trigger **one** sweep and read them all back as complex data), and the
  two wrappers you'll actually type — `measure_s11` and `measure_2port`.
  Saves nothing; it returns numpy arrays.
- **`sweep_db.py`** — the data layer on top of it: `save_touchstone`
  (write `.s1p` or `.s2p`), `record_measurement` (save one measurement as
  a QCoDeS run), and `run_sweep` (measure + save + record over a list of
  switch positions), with `run_oneport_sweep` / `run_twoport_sweep` as
  the 1- and 2-port wrappers.
- **`plots.py`** — looking at what came back: `summarize` (min, max and
  marker values as a printed table), `plot_measurement` (one position,
  magnitude in dB, optional phase panel) and `plot_sweep` (every position
  of a finished sweep overlaid, which is how isolation reads off a
  plot). Reads the Touchstone files back itself — matplotlib and numpy
  only, and nothing imported from the other two modules, so it plots old
  sweeps with no instruments connected.
- **`twoport_sweep.ipynb`** — runnable notebook: connects to the VNA and
  switch, measures S11/S12/S21/S22 at each position you list, and saves
  each to `Sweeps/<date>_<temp>/<switch_serials>/raw/<position>_run<id>.s2p` plus
  a run in `mm4250_sweeps.db`.
- **`oneport_sweep.ipynb`** — the same, for S11 only, saved as `.s1p`.
- **`LAB_SETUP.md`** — how to copy this onto the lab measurement
  computer and run it from `users/<name>/`.

## Taking a measurement by hand

Once a notebook has connected the VNA as `ksvna` (and optionally the
switch as `switch`) — which the lab's `QCodesMeasurmentFramework.ipynb`
already does — point Python at `measurements/` and import:

```python
import sys
sys.path.insert(0, r"<path to>/mm4250-switch/measurements")
from vna_measure import setup_sweep, sweep_settings, measure_s11, measure_2port
from sweep_db import run_oneport_sweep, run_twoport_sweep
```

Then the whole measurement is two lines:

```python
setup_sweep(start=1e9, stop=10e9, points=1001, if_bandwidth=1e3, power=-20)
freq, data = measure_2port()      # data["S21"], data["S11"], ...
```

`setup_sweep` only changes the settings you actually pass, so
`setup_sweep(points=2001)` nudges one thing and leaves the rest alone.
`measure_2port(3)` sets the switch to RF3 first; `measure_2port()`
measures whatever it's already set to; `measure_2port(state="ALL_OPEN")`
drives the switch by state name instead. `measure_s11` takes the same
arguments and returns a bare array rather than a dict. Every function
takes optional `vna=` / `switch=` arguments if your instruments were
registered under other names.

All four S-parameters come out of a **single sweep** — `run_sweep()`
triggers every trace on the VNA's channel at once, and the P5004B takes
care of the reverse sweep needed for S12/S22 on its own.

Changing the frequency range, point count or IF bandwidth invalidates
whatever calibration is applied on the VNA — re-run the cal after
changing them.

## Running a batch sweep

- Edit `positions`/`date_str`/`temp_str`/`switch_serials` in
  `measurements/twoport_sweep.ipynb` (or `oneport_sweep.ipynb`).
- `positions` is a list of RF channel numbers, switch state names as
  strings, or a mix of both:

  | `positions` | what it measures |
  |---|---|
  | `[1, 3, 5]` | three RF channels |
  | `list(range(1, 7))` | all six |
  | `[3, "ALL_OPEN"]` | RF3 connected, then the same cabling with everything open |

  Mixing the two is how you get isolation without touching a cable: hold
  the cabling fixed, measure the channel that's connected, then measure
  again on a state that disconnects it. Valid state names are
  `"ALL_OPEN"`, `"RFC_RF1"`–`"RFC_RF6"`, `"INTERNAL_LOAD"` and
  `"INTERNAL_SHORT"`.
- Pass `prompt_between=True` if something has to be re-cabled by hand
  between positions — the sweep pauses and waits for Enter before each
  one.
- Outputs land beside the code — `measurements/Sweeps/...` and
  `measurements/mm4250_sweeps.db`. Both resolve from the module's own
  folder rather than the working directory, so copying these files
  somewhere else (the lab machine's `users/<name>/`, say) puts the
  outputs in that folder too. Override with `out_root=` / `db_path=`.
- Every run accumulates into that one database file. Each
  `run_twoport_sweep(...)` call is its own QCoDeS *experiment*, named
  `<date_str>_<temp_str>_<switch_serials>` with
  `sample_name=switch_serials`; each position measured in that call is
  one *run* named `RF<n>` (or the state name) inside it. Re-running the
  same date/temp/serials adds to that experiment rather than duplicating
  it. 1-port and 2-port runs can share an experiment — each records only
  the S-parameters it actually measured.
- Each run carries `n_ports`, `touchstone_path`, `switch_serials`,
  `date_str`, `temp_str` and either `channel` or `state` as dataset
  metadata, so any run traces back to the raw file it was saved
  alongside.
- Browse the database afterwards with
  `plottr-inspectr --db mm4250_sweeps.db`, or load runs in Python with
  `qcodes.dataset`'s `load_by_id`/`load_by_run_spec`.

No calibration or de-embedding is applied — this is raw acquisition
only.

## Running from the lab's measurement framework

`run_twoport_sweep` and the `vna_measure` functions take optional
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

The SOL de-embedding pipeline (`deembed.py`, `oneport_deembed_sweep.ipynb`
and the `oneport_sweep.py` module they shared) lives in
`../Archive/mm4250-switch-sweep-prior/`; it hasn't been brought onto the
current measurement code yet.

The earlier 2-port sweep (`sparam_sweep.py`, `switch_matrix_sweep.ipynb`)
is archived there too — it's superseded by `measure_2port` /
`run_twoport_sweep` here, which add the QCoDeS database layer, the
portable output paths and the state-based positions it lacked. It's also
still in this repo's git history at commit `51c66ff`.
