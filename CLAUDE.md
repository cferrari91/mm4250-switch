# mm4250-switch

QCoDeS driver for the Menlo MM4250 SP6T switch (USB HiV Driver Board, USB HID)
plus the Keysight P5004B VNA measurement code built on it. This is THE active
repo; everything in `../Archive/` is superseded. Public on GitHub
(`cferrari91/mm4250-switch`), MIT. See the project-level CLAUDE.md one folder
up (`../../CLAUDE.md`) for how I want you to work.

## Layout

- `drivers/MM4250_QCodes_driver.py`: the driver used in the lab. Hardware-only
  (no simulated mode), plain `print()` style matching the lab framework's
  drivers. Constructing `MM4250("switch")` opens the HID handle and forces
  `ALL_OPEN` immediately.
- `drivers/MM4250_QCodes_driver_commented.py`: same driver, tutorial comments. **Keep the two in sync** (`tests/test_driver_sync.py` fails if their code differs; comments/docstrings are ignored, strings are not).
- `drivers/MM4250_finalized.py`: type-hinted, contrib-style version (aimed at
  Qcodes_contrib_drivers). Tested without hardware by `tests/test_MM4250_finalized.py`.
- `drivers/N52xx.py`, `KeysightVNA_driver.py`: vendored from QCoDeS (N52xx has local edits). See `THIRD_PARTY.md`.
- `measurements/vna_measure.py` (measure, returns numpy), `sweep_db.py` (save
  to QCoDeS db), `read_db.py` (read db with sqlite3 + numpy only),
  `plots.py`, `twoport_sweep.ipynb`, `oneport_sweep.ipynb`, `LAB_SETUP.md`.
- `measurements/figures/<serial>/<date>/<temp>/`: slide figure sets, each with a `make_figures.py` that rebuilds them.

## Rules that matter

- **No hot switching.** Change switch state through `vna_measure._select`
  (it turns the VNA source off during the move). Setting `switch.channel()` by hand skips that.
- **Power ceiling** `MAX_POWER_DBM = 0.0` in `vna_measure.py` is deliberate. Don't raise it or bypass it unless I ask.
- **The database is the record.** `mm4250_sweeps.db` holds every sweep;
  Touchstone files only with `touchstone=True` or `export_touchstone()`.
  `Sweeps/`, `*.db`, `*.db-wal`, `*.db-shm` are gitignored. Never delete or
  overwrite a `.db` (there's a `mm4250_sweeps_laptop_backup_20261001.db`).
- Layout: `Sweeps/<serials>/<date>/<temp>/<setup>_<cal|uncal>/<position>_run<id>.s2p`.
  cal/uncal is read from the VNA, never typed.
- Complex arrays in QCoDeS: `paramtype="array"`, never `"complex"` (writes fine, fails on read-back).
- Use `qcodes.validators`, not `qcodes.utils.validators` (gone in qcodes 0.58).
- Install `hidapi`, never `hid`. On the DAQ, pip may falsely say "already satisfied"; see LAB_SETUP.md.
- One MM4250 instance per kernel (exclusive USB handle). One driver board drives both switches.
- Board LEDs aren't 1:1 with channels: D1-D4 = RF1-RF4, D5 = always-on status, D6 = RF5, D7 = RF6, D8 = LOAD, D9 = SHORT.

## Environments and where code runs

- Laptop (macOS): conda env `qcodes-conda` (`/opt/miniconda3/envs/qcodes-conda`).
- Lab DAQ (Windows): env `QTSF_QCoDeS_env`, code copied (not cloned) to
  `C:\Users\QTSF_DAQ\Measuring_scripts\QCoDeS-Measurement-Framework\Users\Charlie_Ferrari\`.
  Drivers there come from the framework repo.
- Hardware (VNA, switch board) is only reachable from the DAQ machine. You can't test against real hardware from here; say so instead of guessing.
- Tests: `python -m pytest tests/` from the repo root (no hardware needed).

## Current status (update this section; keep it short)

_Last updated: 2026-10-01_

- `main`: driver, 1-port and 2-port sweeps, db layer, plots, finalized driver + tests.
- Branch `ecal-single-notebook` (pushed, **not merged**): `ecal.py` + tests,
  single `mm4250_sweeps.ipynb` for all sweeps (what I want long term),
  SN0077 2026-10-01 295 K e-cal figures, finalized-driver example notebook.
- Not done: calibration/de-embedding in software (old SOL pipeline is in `../Archive/mm4250-switch-sweep-prior/`).
- TODO: `run_sweep` in `sweep_db.py` hardcodes the output path layout under
  `out_root`; I want to change that later. Leave it until I ask.

## Keeping this file current

Same rule as the project CLAUDE.md: when a task changes something durable
(status, a decision, a gotcha, a new file that matters), update the right
section here before you finish. Edit lines in place instead of appending a
log. End your reply with one line saying what you changed here, or "no CLAUDE.md changes".
