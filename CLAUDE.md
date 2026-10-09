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
- **Backups:** `read_db.backup_db()` snapshots the db to `db_backups/` beside it (SQLite backup API, consistent while a kernel is open). `run_sweep`, `run_ecal_set` and every notebook's Close cell call it; skipped if nothing changed; newest 10 kept. Don't remove those calls. To move data between machines, copy a backup, not the live `.db`. If you do copy a live one, its `-wal`/`-shm` must be renamed with it: a `-wal` left beside a different `.db` of the same name gets replayed into that database (nearly happened 2026-10-08).
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

_Last updated: 2026-10-08_

- `main`: driver, 1-port and 2-port sweeps, db layer, plots, finalized driver + tests,
  `ecal.py` + tests, single `mm4250_sweeps.ipynb` for all sweeps (what I want long term),
  SN0077 2026-10-01 295 K e-cal figures, finalized-driver example notebook.
- Finalized driver: passes the hardware-free tests; not yet run on the real board (`measurements/FINALIZED_DRIVER_DAQ_CHECKS.md`).
- `CLAUDE.md` is tracked and public on GitHub (decided 2026-10-01). Keep secrets and tokens out of it.
- Fridge wiring is a circulator (VNA port 1 -> circulator -> RFC, reflection back up to VNA port 2), so the switch's reflection is **S21**. `run_ecal_set(n_ports=2)` (now the default) saves all four S-params; `ecal.correct_set`/`drift`/`correct_and_plot(sparam=...)` correct any of S11/S12/S21/S22 (default: S21 if the runs have it, else S11, so old 1-port sets are unchanged). Notebook D uses these with `sparam="S21"`. Tests pass (2026-10-07, uncommitted, not run on hardware). The unmerged `ecal-terminations-first-cooldown` branch edits the same functions and cell D1, so expect conflicts merging it.
- Per-channel labels: `run_ecal_set(labels={1: "resonator"})` (and `run_sweep(labels=...)`, keyed by position) saves each label on that position's runs as `dut_label`; it comes back in `find_set`/`correct_set` results, `list_runs()` rows and the `plot_ecal` legend ("RF1 (resonator)"). Bad keys fail before anything is measured. `note` stays for whole-set context. Tests pass (2026-10-07, uncommitted, not run on hardware).
- Definitions besides NIST's (2026-10-08, uncommitted, tests pass, not run on hardware): `correct_set(cal, "perfect")` assumes ideal internal standards (plane inside the switch, so the RFC->RF n path stays in). `sweep_db.run_kit_set` (warm only; prompts for an external kit's open/short/load on each RF connector, tags runs `kit_set`/`kit_role`/`kit_std`) + `ecal.make_ideals(kit)` write your own switch's per-port definitions (`port<n>_<std>.s1p`, beside the db) that `correct_set` takes like NIST's. `ecal.plot_compare` overlays results. Notebook D3 does all three. `vna_cal=True` on `run_ecal_set`/`run_kit_set` measures with the VNA's own cal on underneath (refuses if the state doesn't match the flag); results carry `vna_cal` and plots label the trace "VNA cal only".
- Databases (2026-10-08): `measurements/mm4250_sweeps.db` is now the DAQ's database (copied over with its `-wal`/`-shm`; the 295 K resonator runs from 2026-10-08). The laptop's old one (runs 1-90, through 2026-10-01) is archived as `db_backups/mm4250_sweeps_laptop_through_20261001.db`, a name `backup_db`'s pruning never matches, so it's never auto-deleted; `figures/SN0077/20261001/295K/make_figures.py` reads it from there. Not merged. `Sweeps/` was replaced the same day by the DAQ's copy (a superset, checked file by file).
- Notebook A2 (2026-10-08): S11 with one cable and the VNA's own 1-port Smart Cal (no switch, no circulator); activates a saved cal set by name (`ONEPORT_CAL`) and plots it. Nothing goes into the database.
- 295 K resonator check (2026-10-08, `figures/SN0077/20261008/295K/`): e-cal set `20261008T160234.969` (520-580 MHz, resonator on RF1 via a short cable) de-embedded with NIST 295K definitions agrees with the VNA-calibrated resonator alone: dip 548.80 vs 548.65 MHz, half-depth width 4.14 vs 4.02 MHz; leftover ~1.45 ns 2-way delay / -0.2 dB off resonance (mostly the cable). Both dips read -20.8 dB, but at |G| ~ 0.09 that match is within the error bar, not proof of 0.02 dB accuracy. Cross-checked against scikit-rf OnePort (agree to 1e-15). Q fit (gitignored for now, laptop only: `fit_resonator_q.py`, `q_*.ipynb` and the `*q_fit*`/`*qfit*` figures; `measurements/q_fit_resonator_20261008.ipynb`, adapted from Zack's `q_measurements.ipynb`; scikit-rf Qfactor `reflection`, NLQFIT7, window 2 f_L/Q_L, no edge normalisation; needs `pip install scikit-rf`): alone Q_L 39.6 / Q_0 72.6 / Q_e 87.1 / beta 0.83 (undercoupled); through switch + NIST e-cal 39.9 / 73.0 / 88.1 / 0.83. Zack's exact settings give Q_0 ~90-105 here because his edge normalisation assumes the window edges are off resonance (true for his high-beta stub, not for this Q_L ~40 resonator). The notebook writes `05_q_fit.pdf` (all three), `06`-`08_qfit_*.pdf` (Zack-style data vs fit) and `q_fit_values.csv` in `figures/SN0077/20261008/295K/`. `fit_resonator_q.py` is the earlier standalone version (also has a raw-S21 row: wrong Q_e ~156, beta 0.50). Gotcha: skrf 2.1's `Qfactor.fitted_s`/`fitted_network` drop NLQFIT7's line-length term, so they plot a rotated circle; both rebuild the curve themselves. "perfect" standards give 548.29 MHz, -24.0 dB, -1.2 dB off-res (RFC->RF1 path left in). The VNA-only zoomed .s1p was saved with an interpolated cal (`C*`).
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
