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
  Qcodes_contrib_drivers), class `MM4250`. Tested without hardware by `tests/test_MM4250_finalized.py`;
  `tests/hardware_check_MM4250_finalized.py` is the DAQ hardware check (not collected by pytest); steps in `measurements/FINALIZED_DRIVER_DAQ_CHECKS.md`.
  `docs/MM4250_finalized_example.ipynb` is already the PR version (imports from `qcodes_contrib_drivers`), so it only runs where that's importable.
- `drivers/N52xx.py`, `KeysightVNA_driver.py`: vendored from QCoDeS (N52xx has local edits). See `THIRD_PARTY.md`.
- `measurements/vna_measure.py` (measure, returns numpy), `sweep_db.py` (save
  to QCoDeS db), `read_db.py` (read db with sqlite3 + numpy only),
  `plots.py`, `twoport_sweep.ipynb`, `oneport_sweep.ipynb`, `LAB_SETUP.md`.
  `measurements/FIRST_COOLDOWN.md`: the why and what-to-expect for the first cooldown (known short + load on RF ports, warm and 3 K e-cal sets, stuck check, scoring); its code is section F of `mm4250_sweeps.ipynb`.
- `measurements/figures/<serial>/<date>/<temp>/`: slide figure sets, each with a `make_figures.py` that rebuilds them.
  `figures/NIST_comparison/295K/` is the Sep 11 vs NIST tier-2 comparison (report figure). Scripts that need the NIST repo
  or the Sep 11 CSVs are laptop-only and exit with a message if the data is missing.

## Rules that matter

- **No hot switching.** Change switch state through `vna_measure._select`
  (it turns the VNA source off during the move). Setting `switch.channel()` by hand skips that.
- **Power ceiling** `MAX_POWER_DBM = 0.0` in `vna_measure.py` is deliberate. Don't raise it or bypass it unless I ask.
- **The database is the record.** `mm4250_sweeps.db` holds every sweep;
  Touchstone files only with `touchstone=True` or `export_touchstone()`.
  `Sweeps/`, `*.db`, `*.db-wal`, `*.db-shm` are gitignored. Never delete or
  overwrite a `.db`; snapshots live in `measurements/db_backups/`.
- **Backups:** `read_db.backup_db()` snapshots the db to `db_backups/` beside it (SQLite backup API, consistent while a kernel is open). `run_sweep`, `run_ecal_set` and every notebook's Close cell call it; skipped if nothing changed; newest 10 kept. Don't remove those calls. To move data between machines, copy a backup, not the live `.db`.
- Layout: `Sweeps/<serials>/<date>/<temp>/<setup>_<cal|uncal>/<position>_run<id>.s2p`.
  Exception: Sep 11 data is `Sweeps/SN00{77,78}/20260911/295K/vna_csv/`, front-panel CSV exports (log magnitude only,
  read with `plots.read_vna_csv`). Never import it into the db: there's no phase.
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

_Last updated: 2026-10-06_

- `main`: driver, 1-port and 2-port sweeps, db layer, plots, finalized driver + tests,
  `ecal.py` + tests, single `mm4250_sweeps.ipynb` for all sweeps (what I want long term),
  SN0077 2026-10-01 295 K e-cal figures, finalized-driver example notebook
  (`ecal-single-notebook` merged 2026-10-01, PR #2).
- Sep 11 data and the NIST comparison script are in the repo (PR #5 merged 2026-10-03);
  outputs verified byte-identical. The old Menlo NIST comparison folder is deleted (2026-10-03).
  `../../Menlo/Sweeps/` is deleted too (2026-10-04); `measurements/Sweeps/SN00{77,78}/20260911/` is the copy to use.
- Finalized driver HID hardening + DAQ hardware check: PR #7 merged 2026-10-04. Passes the hardware-free tests;
  not yet run on the real board (`measurements/FINALIZED_DRIVER_DAQ_CHECKS.md`).
- `CLAUDE.md` is tracked and public on GitHub (decided 2026-10-01). Keep secrets and tokens out of it.
- `run_ecal_set(terminations={2: "short", 5: "load"})` records what's on each RF port (per-run `termination`, set-wide `ecal_terminations` JSON; carried into `find_set`/`correct_set`/`plot_ecal`), via a new `run_sweep(position_metadata=...)`. Tests pass; committed 2026-10-07 on branch `ecal-terminations-first-cooldown` (pushed, no PR yet), not yet run on hardware.
- Not done: calibration/de-embedding in software (old SOL pipeline is in `../Archive/mm4250-switch-sweep-prior/`).
- TODO, later (leave until I ask): run everything neatly from the main notebook,
  with data saved to a specific or user-selected location. Today the `.db`,
  `Sweeps/` and `figures/` default to beside the code, and `run_sweep` in
  `sweep_db.py` hardcodes the path layout under `out_root`. Ask which notebook
  I mean before starting.

## Keeping this file current

Same rule as the project CLAUDE.md: when a task changes something durable
(status, a decision, a gotcha, a new file that matters), update the right
section here before you finish. Edit lines in place instead of appending a
log. End your reply with one line saying what you changed here, or "no CLAUDE.md changes".
