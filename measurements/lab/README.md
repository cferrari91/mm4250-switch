# MM4250 measurements: the short version

Two notebooks, one file per cooldown that describes the setup, four small modules.

| File | What it's for |
|---|---|
| `cooldowns/CD05.json` | This cooldown: what's on each switch port, fridge lines, sweep. **The only file you edit.** |
| `stuck_check.ipynb` | Is every switch channel opening and closing? Stuck check + thru check. |
| `take_data.ipynb` | Take data: raw + corrected (e-cal) into the lab database. Plot, recall, export. |
| `vna.py` | Instruments: sweep setup, measure a switch position (source off while the switch moves), the sequences. |
| `db.py` | The cooldown file, the databases, saving/reading runs, Touchstone files, backups. |
| `ecal.py` | The e-cal: NIST definitions, error terms, correcting a take, drift. |
| `plots.py` | Plots of a take, a check, or any runs. |

Only one notebook can hold the switch at a time: run the other one's **Close** cell first.

## 1. Fill in the cooldown file

Copy `cooldowns/example.json` to `cooldowns/CD<NN>.json` and edit it. JSON has no comments, so here is what each key means:

| Key | Meaning |
|---|---|
| `cooldown` | The fridge's cooldown number (a whole number). Picks the database folder. |
| `switch_serial` | The switch in the fridge, e.g. `"SN0077"`. |
| `database_project` | The lab database project, e.g. `"MM4250"`: data goes to `E:\QCoDeS\<project>\Cooldown_<NN>\`. |
| `ports` | Switch port -> what's on it, `"RF1"` to `"RF6"`. Leave out a port with nothing on it. These descriptions go on every run. |
| `thru_channel` | The switch channel the fridge's thru line is wired to (a number, e.g. `2`), or `null` for none. Must be one of `ports`. |
| `lines` | Fridge lines: `input` (VNA port 1 normally), `return` (VNA port 2), `thru` (needed if `thru_channel` is set), `notes` (anything: LNA, attenuators). |
| `sweep` | `start_hz`, `stop_hz`, `points`, `if_bandwidth_hz`, `power_dbm` (at most 0 dBm). Plain numbers: `510000000`, not `510e6` or `"510 MHz"`. |
| `repeats` | How many times a take repeats its whole block (1 is fine; 2 shows the switch's repeatability). |
| `correct_sparam` | The raw trace the switch's reflection is on: `"S21"` in the circulator wiring (port 1 in, port 2 out). |
| `stuck_check` | `pass_db`: the stuck check's pass mark in dB (`null` = no mark yet: numbers only, nothing flagged). `thru_pass_db`: how far apart (dB) closed and open must be in the thru check. |

The notebooks check the file when they load it and list every problem at once (missing key, port not RF1-RF6, thru channel not in ports, power above the cap, ...). The whole file is stored on every run, so a database alone says how the fridge was wired.

Then set `COOLDOWN_FILE` at the top of each notebook's Setup cell.

## 2. Stuck check (`stuck_check.ipynb`)

On the way down (30-80 K, where trapped nitrogen can freeze a switch closed) and first thing at any temperature where you'll take data. VNA port 1 on the input line, port 2 on the return line, LNA on.

- **Stuck check**: ALL_OPEN, internal short, ALL_OPEN, internal load, ALL_OPEN, then every port in the file with ALL_OPEN after each. Each row gets a *contrast* in dB (how far it stands from what it should differ from). Every row well above 0 dB = fine. A closed position near 0 dB didn't close; an ALL_OPEN near 0 dB didn't open after the position before it. The first bad row is the one that counts. **If anything is stuck, stop.**
- **Thru check**: asks you to move VNA port 1's cable to the thru line, closes and opens `thru_channel` twice, compares with `thru_pass_db`, then asks you to move the cable back.
- Saving is off by default. `SAVE_DB = True`: every position into `<project>_Cooldown_<NN>_stuck.db`. `SAVE_TOUCHSTONE = True`: one `.s2p` per position plus `positions.csv` in `Sweeps/stuck_check/` (or `Sweeps/thru_check/`) beside that database.

## 3. Take data (`take_data.ipynb`)

- **Take**: the internal standards, each chosen channel (`CHANNELS = None` for every port in the file, or e.g. `[4]`), the standards again (the drift check), `repeats` times. Every position is saved raw (S11, S12, S21, S22) to `<project>_Cooldown_<NN>_vna.db` as soon as it's measured. Then each channel is corrected with NIST's definitions (295 K at room temperature, 3 K when cold, picked from the fridge's mixing-plate reading) and saved as its own runs, one per channel per repeat, each linked to the raw runs it came from (`source_run_ids`).
- No fridge reading (no VPN)? The raw data is still saved; the correction is skipped with a warning. Set `DEFINITIONS = "295K"` or `"3K"` in the Take cell, or correct it later with `CORRECT_WITH` in Recall.
- The printout ends with the **drift**: how far the standards moved during the take, in reflection units. NIST's repeatability was a few 1e-2; much more means the chain (usually the LNA) moved: take it again.
- **Plot latest take**: raw trace on the left, corrected on the right (mean over repeats).
- **Recall**: lists every take in the database; plots one (`TAKE_ID`, `None` = newest) or a list of runs (`RUN_IDS`); `EXPORT = True` writes Touchstone files to `Sweeps/export/` beside the database.
- **Close**: backs up the database, leaves the switch `ALL_OPEN`, releases the instruments.

## Where the data is

- On the DAQ: `E:\QCoDeS\<project>\Cooldown_<NN>\`, through the lab's `scripts/database_helper.py`. `..._vna.db` (takes) and `..._stuck.db` (checks) are separate files, so the two notebooks never write to the same one.
- Beside each database: `db_backups/` (a checked copy after every take and in Close, newest 10 kept) and `Sweeps/` (Touchstone files and figures, only when asked).
- If the helper can't be found (a laptop), the database goes to `local_databases/` beside this file, with a loud warning. On the DAQ that warning means something is wrong: stop and check.
- Each run's metadata: `take_id` (or `check_id`), `kind` (`standard`, `raw`, `corrected`), `position`, `standards_when` (`before`/`after`), `repeat`, `port` (from the file), `mxc_temp_k` (mixing plate, before each position; empty if no reading), `temp_str`, `cooldown`, `switch_serial`, `note`, `cooldown_json`, the VNA's settings (`vna_*`), and on corrected runs `definitions`, `corrected_from` and `source_run_ids`. Browse with `plottr-inspectr --db <file>` or Recall.
- NIST's definitions: the code looks for `ideals_295K/` and `ideals_3K/` (copies of NIST's `tier2_scikitrf_caldata/tier2_295k1` and `tier2_3k1`) in this folder or any folder above it.

## Safety (built in, don't work around it)

- The VNA source is off whenever the switch moves (no hot switching). Don't set `switch.channel()` by hand.
- Source power is capped at 0 dBm (`vna.MAX_POWER_DBM`).
- The code measures raw: it turns the VNA's own calibration off and refuses to measure if it's still on.
- Every take and check leaves the switch `ALL_OPEN`, even if it fails partway.
- The lab driver's `switch.state()` readback lags; the code only sets the switch. Use the board LEDs, or `switch.state.cache.get(get_if_invalid=False)` for what was last sent.

## Tests

`python -m pytest tests/lab` from the repo root, no hardware: fake VNA, switch and fridge (`tests/lab/fakes.py`), a check against NIST's own corrected data, and both notebooks run top to bottom in a kernel (`tests/lab/notebook_runner.py`).
