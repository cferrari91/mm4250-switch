# mm4250-switch-sweep

QCoDeS driver for the Menlo Micro MM4250 (SP6T cryogenic RF MEMS switch,
via its USB HiV Driver Board), plus a 1-port S11 sweep across a chosen
set of RF channels on a Keysight VNA, saved as Touchstone `.s1p` files
and recorded to a QCoDeS database.

## Layout

- `drivers/MM4250_QCodes_driver.py` -- the switch driver. Hardware-only
  (connects for real in `__init__`, no software-only mode), matching the
  plain, print()-based style of this framework's other single-file
  drivers rather than heavier type-hinted/defensive code.
- `drivers/MM4250_QCodes_driver_commented.py` -- the same driver, heavily
  commented, meant to be read top to bottom as a tutorial on both this
  switch and QCoDeS driver-writing in general.
- `drivers/KeysightVNA_driver.py` + `drivers/N52xx.py` -- the Keysight
  P5004B VNA driver (`KeysightVNA_driver.py` is a thin subclass of the
  base PNA driver in `N52xx.py`). Needed by the sweep code; not written
  as part of this project.
- `vna_measure.py` -- the measurement itself, meant to be typed
  into a notebook: `setup_sweep` (set frequency range/points/IF
  bandwidth/power/averaging), `sweep_settings` (print what's currently
  set), `ensure_trace`, `measure_sparam` (trigger one sweep, read an
  S-parameter back as complex data), and `measure_s11`. Saves nothing --
  it returns numpy arrays. 1-port for now; `measure_sparam` is already
  S-parameter agnostic, so 2-port slots in on top of it.
- `oneport_db_sweep.py` -- the data layer on top of
  `vna_measure`: `save_s1p` (write Touchstone), `record_channel` (save
  one measurement as a QCoDeS run), and `run_oneport_sweep` (measure +
  save + record over a list of channels).
- `oneport_db_sweep.ipynb` -- runnable notebook for the above: connects
  to the VNA and switch, sweeps the channels listed in `channels`, and
  saves each to `Sweeps/<date>_<temp>/<switch_serials>/raw/RF<n>.s1p`
  plus a run in `mm4250_oneport.db`.
- `LAB_SETUP.md` -- how to copy this onto the lab measurement computer
  and run it from `users/<name>/`.
- `docs/MM4250_Instructions.md` -- driver usage notes and status.

## Taking a measurement by hand

Once a notebook has connected the VNA as `ksvna` (and optionally the
switch as `switch`) -- which `QCodesMeasurmentFramework.ipynb` already
does -- point Python at this folder and import:

```python
import sys
sys.path.insert(0, r"<path to the folder holding these files>")
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

Note that changing the frequency range, point count or IF bandwidth
invalidates whatever calibration is applied on the VNA -- re-run the cal
after changing them.

## Before running a batch sweep

- Edit `channels`/`date_str`/`temp_str`/`switch_serials` in
  `oneport_db_sweep.ipynb` to match the run. `channels` is any subset of
  1-6 (e.g. `[1, 3, 5]`), or `list(range(1, 7))` for all of them.
- Sweeps are saved under `Sweeps/<date>_<temp>/<switch_serials>/raw/`
  beside the code, and the database sits next to it. Both resolve from
  the module's own folder rather than the working directory, so copying
  these files somewhere else (the lab machine's `users/<name>/`, say)
  puts the outputs in that folder too. Override with `out_root=` /
  `db_path=`.
- Every run accumulates into one shared database file,
  `mm4250_oneport.db` at this repo's root (override with `db_path=`).
  Each `run_oneport_sweep(...)` call is its own QCoDeS *experiment*,
  named `<date_str>_<temp_str>_<switch_serials>` with
  `sample_name=switch_serials`; each channel measured in that call is
  one *run* named `RF<n>` inside it. Re-running the same
  date/temp/serials adds to that experiment rather than duplicating it.
- Each run carries `channel`, `s1p_path`, `switch_serials`, `date_str`
  and `temp_str` as dataset metadata, so any run traces back to the raw
  `.s1p` it was saved alongside.
- Browse the database afterwards with
  `plottr-inspectr --db mm4250_oneport.db`, or load runs in Python with
  `qcodes.dataset`'s `load_by_id`/`load_by_run_spec`.

No calibration or de-embedding is applied -- this is raw acquisition
only.

## Running from the lab's measurement framework

`run_oneport_sweep` takes optional `vna=`/`switch=` arguments. If you
leave them out, it looks up whatever QCoDeS instruments are registered
under the names `ksvna` and `switch` in the current kernel -- which is
what `QCodesMeasurmentFramework.ipynb` creates. So the same function
works either standalone (the notebook here connects its own instruments)
or from the framework notebook's kernel, where they already exist. Don't
do both in one kernel: two live `MM4250("switch")` instances collide on
the instrument name.

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

## Archive

Earlier work not part of this 1-port sweep -- the 2-port S-parameter
sweep (`sparam_sweep.py`, `switch_matrix_sweep.ipynb`) and the
SOL de-embedding pipeline (`deembed.py`, `oneport_deembed_sweep.ipynb`,
and the `oneport_sweep.py` module they shared) -- was moved to
`../Archive/mm4250-switch-sweep-prior/`. It is also still in this repo's
git history up to commit `5e64ff9`.
