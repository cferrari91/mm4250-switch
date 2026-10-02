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
> Known gaps: no calibration or de-embedding is done in software. A
> sweep is calibrated only if a cal set is active on the VNA (the
> 2-port notebook can take the same channel with correction on and
> off); otherwise it's raw S-parameters at the VNA's own port
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
- **`sweep_db.py`** — the data layer on top of it: `record_measurement`
  (save one measurement as a QCoDeS run) and `run_sweep` (measure and
  record over a list of switch positions, returning the run ids), with
  `run_oneport_sweep` / `run_twoport_sweep` as the 1- and 2-port
  wrappers, and `run_ecal_set` (one fridge e-cal set: standards, every
  channel, standards again, tagged so `ecal.py` can find it). Saves to
  the database only unless you pass `touchstone=True`.
- **`ecal.py`** — calibrated S11 at each RF channel's connector from
  the switch's internal open/short/load and NIST's definitions of them
  (the Menlo/NIST "e-cal"): `find_ideals`, `correct_set`, `drift`,
  `repeatability`, `plot_ecal`, `export_corrected`. numpy only, so it
  runs on the DAQ machine at the fridge; checked against scikit-rf's
  `OnePort` on NIST's dilution-fridge data in `tests/test_ecal.py`.
- **`read_db.py`** — reading the database back, with numpy and
  `sqlite3` only (no QCoDeS, so it works on a laptop): `list_runs`,
  `load_run` (one run as `(freq, data)`), and `export_touchstone` (write
  `.s1p`/`.s2p` files from the database, for scikit-rf or anyone without
  QCoDeS). Also holds the Touchstone writer and the `Sweeps/` layout, so
  files written during a sweep and exported later are identical.
- **`plots.py`** — looking at what came back: `summarize` (min, max and
  marker values as a printed table), `plot_measurement` (one position,
  magnitude in dB, optional phase panel) and `plot_sweep` (every position
  of a finished sweep overlaid, which is how isolation reads off a
  plot) — by run id from the database, or from a folder of Touchstone
  files. matplotlib, numpy and `read_db` only, so it plots old sweeps
  with no instruments connected and no QCoDeS installed.
- **`mm4250_sweeps.ipynb`** — **the notebook to use**: one shared setup
  (imports, connect, session info), then a section per kind of
  measurement — A 1-port sweep, B VNA-calibrated 2-port sweep, C cal vs
  uncal, D fridge e-cal, E browse and plot. Each section sets up the VNA
  itself, so they can run in any order.
- **`twoport_sweep.ipynb`** — the older 2-port-only notebook, superseded
  by `mm4250_sweeps.ipynb` and kept until that one has run on hardware: connects to the VNA and
  switch, measures S11/S12/S21/S22 at each position you list, and saves
  each as a run in `mm4250_sweeps.db` (plus
  `Sweeps/<serials>/<date>/<temp>/<setup>_<cal|uncal>/<position>_run<id>.s2p`
  with `touchstone=True`). Also activates a VNA cal set and runs
  paired cal / uncal sweeps of the same cabling.
- **`oneport_sweep.ipynb`** — the same, for S11 only (also superseded).
- **`figures/`** — saved plots. `plot_sweep(..., save=True)` and
  `plot_measurement(..., save=True)` file them under
  `figures/<serials>/<date>/<temp>/`, the same layout as `Sweeps/`.
  Both plots also take `xlim`, `ylim`, `colors`, `labels` and `title`.
  A day's slide-figure set lives in the same folder with the
  `make_figures.py` that rebuilds it, e.g.
  `figures/SN0077/20260925/295K/make_figures.py`.
- **`LAB_SETUP.md`** — how to copy this onto the lab measurement
  computer, run it from `Users/Charlie_Ferrari/`, and bring the results
  back here.

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

- Set `date_str`/`temp_str`/`switch_serials` once in the Session cell of
  `measurements/mm4250_sweeps.ipynb`, then edit `positions` in the
  section you're running.
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
- Each sweep returns its run ids: `runs = run_twoport_sweep(...)`. Plot
  them with `plot_sweep(runs)`, load one with `load_run(runs[0])`.
- The database is the record. Sweeps save there only, unless you pass
  `touchstone=True` to also write `.s1p`/`.s2p` files; files can be
  written any time later with `export_touchstone(runs)` and come out
  identical, in the same place.
- Pass `setup=` to name the cabling, e.g. `setup="RF3"` for VNA port 2
  on RF3. The runs are grouped (experiment name and, with files, folder)
  as `<setup>_cal` or `<setup>_uncal` — whether the VNA's correction was on is read off
  the instrument, not typed, and the sweep stops if it changes
  mid-sweep. Switch first, so one unit's whole history (every date,
  every temperature) is under one folder:

  ```
  Sweeps/
    SN0077/20260925/295K/RF1_cal/    RF1_run31.s2p  ALL_OPEN_run32.s2p ...
    SN0077/20260925/295K/RF1_uncal/
    SN0078/20260924/295K/RF1_uncal/
  ```
- Pass `prompt_between=True` if something has to be re-cabled by hand
  between positions — the sweep pauses and waits for Enter before each
  one.
- Outputs land beside the code — `measurements/Sweeps/...` and
  `measurements/mm4250_sweeps.db`. Both resolve from the module's own
  folder rather than the working directory, so copying these files
  somewhere else (the lab machine's `Users/Charlie_Ferrari/`, say) puts the
  outputs in that folder too. Override with `out_root=` / `db_path=`.
- Every run accumulates into that one database file. Each
  `run_twoport_sweep(...)` call is its own QCoDeS *experiment*, named
  `<date_str>_<temp_str>_<switch_serials>_<setup>_<cal|uncal>` with
  `sample_name=switch_serials`; each position measured in that call is
  one *run* named `RF<n>` (or the state name) inside it. Re-running the
  same date/temp/serials/setup adds to that experiment rather than duplicating
  it. 1-port and 2-port runs can share an experiment — each records only
  the S-parameters it actually measured.
- Each run carries `n_ports`, `touchstone_path`, `switch_serials`,
  `date_str`, `temp_str` and either `channel` or `state` as dataset
  metadata. `touchstone_path` is set only when the sweep wrote a file,
  relative to the database's folder, so the link survives copying the
  folder to another machine.
- `run_sweep` checkpoints the database after every position, so
  `mm4250_sweeps.db` is complete on its own even while the kernel that
  wrote it is still open.
- Browse the database afterwards with
  `plottr-inspectr --db mm4250_sweeps.db`, or load runs in Python with
  `qcodes.dataset`'s `load_by_id`/`load_by_run_spec`.

No calibration or de-embedding is done in Python — the data is
whatever the VNA hands back, corrected only if a cal set is active on
the instrument. See `LAB_SETUP.md`'s "Calibrated sweeps" section.

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
