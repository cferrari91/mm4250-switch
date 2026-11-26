# MM4250 measurements: the short version

Two notebooks, one file per cooldown that describes the setup, a few small modules. The instrument side (safe switch moves, sweeps, saving) is the lab's shared `scripts/switch_vna.py` in the framework; this folder adds the sequences, the cooldown file, the e-cal of a take, analysis and plots.

| File | What it's for |
|---|---|
| `cooldowns/CD05.json` | This cooldown: what's on each switch port, fridge lines, sweep. **The only file you edit.** |
| `stuck_check.ipynb` | Is every switch channel opening and closing? Stuck check + thru check, and the gate test (diagnosis: each HV gate line of a channel on its own). |
| `take_data.ipynb` | Take data: raw + corrected (e-cal) into the lab database. Plot, recall, export. |
| `vna.py` | The sequences: take, stuck check, thru check (each position measured with `scripts/switch_vna.py`). |
| `db.py` | The cooldown file, which database, takes in and out of the database, backups. |
| `ecal.py` | The e-cal: NIST definitions, error terms, correcting a take, drift; reading Touchstone files. |
| `plots.py` | Plots of a take, a check, or any runs. |
| `analysis.ipynb` | No instruments: calibrate raw data (a take in the database, or raw Touchstone files with the switch's own standards among them), plot from the database or files (|S| beside a Smith chart), resonance quick look, temperatures, drift and repeatability, export. |
| `analysis.py` | What analysis.ipynb uses: calibrating raw files, traces to plot, the resonance quick look. |

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
| `sweep` | `start_hz`, `stop_hz`, `if_bandwidth_hz`. Plain numbers: `510000000`, not `510e6` or `"510 MHz"`. Points, source power and sweep mode are set in each notebook (`POINTS`, `POWER_DBM` at most 0 dBm, `SWEEP_MODE`), so a finer or quieter sweep doesn't mean editing the file. |
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

- **Take**: the internal standards, each chosen channel (`CHANNELS = None` for every port in the file, or e.g. `[4]`), the standards again (the drift check), `repeats` times. Every position is saved raw (S11 and S21; all four with `SWEEP_MODE = "2port"`) to `<project>_Cooldown_<NN>_vna.db` as soon as it's measured. Then each channel is corrected with NIST's definitions (295 K at room temperature, 3 K when cold, picked from the fridge's mixing-plate reading) and saved as its own runs, one per channel per repeat, each linked to the raw runs it came from (`source_run_ids`).
- No fridge reading (no VPN), or a temperature between 4.5 K and 250 K (where neither NIST set fits)? The raw data is still saved; the correction is skipped with a warning. Set `DEFINITIONS = "295K"` or `"3K"` in the Take cell, or correct it later with `CORRECT_WITH` in Recall; those runs say `definitions_chosen` = `by hand` (vs `auto`).
- The printout ends with the **drift**: how far the standards moved during the take, in reflection units. NIST's repeatability was a few 1e-2; much more means the chain (usually the LNA) moved: take it again.
- **Plot latest take**: raw trace on the left, corrected on the right (mean over repeats).
- **Recall**: needs only Setup (no instruments). Lists every take in the database; plots one (`TAKE_ID`, `None` = newest) or a list of runs (`RUN_IDS`); `EXPORT = True` writes Touchstone files to `Sweeps/export/` beside the database.
- **Gate test (diagnosis)** (end of `stuck_check.ipynb`): for a channel the stuck check can't explain. Each RF channel closes 4 HV gate lines (driver `STATE_HV_PINS`; RF4 = HV2 + HV8, shared with RF3 and RF5, + its own HV10 + HV16). For each channel in `CHANNELS`: each of its lines alone, its shared lines, shared + each own line, then the channel normally, ALL_OPEN after each, the internal standards at both ends. These combinations aren't in Menlo's state table: ask Menlo first. Off unless `RUN_GATE_TEST = True`, so Run All never starts it. Bytes rebuilt from the driver's table and checked against every Menlo state before anything is sent; asks before moving; RF source off for every move; ends ALL_OPEN. Own database `..._Cooldown_<NN>_hv_lines.db` (`SAVE_DB`) and `Sweeps/hv_lines/` beside it (`SAVE_TOUCHSTONE`). Prints each position's contrast and where it reflects (`ecal.hv_positions`: one way from the internal standards, no NIST needed, and from the connector with NIST), plots with `plots.plot_hv_test`.
- **Your own HV lines (round-trip timing)** (right after the gate test): `MY_LINES` = the line sets to turn on, one measurement each (e.g. `["HV2", "HV8"]`), `CHANNEL` for labels and the NIST port, `ALSO_FULL` adds the channel the normal way. Same safety and saving as the gate test (`RUN_MY_LINES = True` to run; lines no Menlo state uses, HV4-6 and HV22, are refused). Prints round trip and one way delay (ns) from the internal standards and from the connector.
- **Hold the thru line open** / **Open it again** (also in `stuck_check.ipynb`): connects `thru_channel` to RFC and leaves it, so the VNA can be used from its own software (e.g. to check the HEMT), reading every fridge thermometer (MXP, MXP2, STP, CMN, STP2, CFP, PRP) every 10 s in the background: each reading is appended to `Sweeps/thru_hold/<time>_temps.csv` in the cooldown folder as it's taken (`SAVE_CSV = True`, the default) and shown in a table under the cell that updates in place; opening it again prints a summary (how many readings, the last one, the CSV's path). Don't hold it through a temperature change.
- **Close**: backs up the database, leaves the switch `ALL_OPEN`, releases the instruments.

## 4. Analysis (`analysis.ipynb`)

No instruments, so it runs while the switch is busy or on a laptop with a copy of the data. Setup picks the cooldown file (for the database's location) and `DEFINITIONS` (`"3K"` cold, `"295K"` warm). Sections: what's saved; calibrate a take from the database (2a) or a folder of raw Touchstone files (2b: positions from the file names, the switch's own ALL_OPEN / INTERNAL_SHORT / INTERNAL_LOAD files are the standards, `SPARAM` S21 in the fridge, S11 on the bench); plot (|S| beside a Smith chart, one figure per trace or together); resonance quick look (dip, depth, rough width and Q_L, over/undercoupled; not a fit); temperatures (MXP, MXP2, STP before/after each position); drift and repeatability; export. Nothing is saved unless a `SAVE...`/`EXPORT` option says so, and nothing overwrites a file.

## Where the data is

- On the DAQ: `E:\QCoDeS\<project>\Cooldown_<NN>\`, through the lab's `scripts/database_helper.py`. `..._vna.db` (takes) and `..._stuck.db` (checks) are separate files, so the two notebooks never write to the same one.
- Beside each database: `db_backups/` (a checked copy after every take and in Close, newest 10 kept) and `Sweeps/` (Touchstone files and figures, only when asked).
- If the helper can't be found (a laptop), the database goes to `local_databases/` beside this file, with a loud warning. On the DAQ that warning means something is wrong: stop and check.
- Each run's metadata: `take_id` (or `check_id`), `kind` (`standard`, `raw`, `corrected`), `position`, `standards_when` (`before`/`after`), `repeat`, `port` (from the file), `mxc_temp_k` (mixing plate, before each position; empty if no reading), `mxp_k_before`/`mxp_k_after`, `mxp2_k_before`/`mxp2_k_after`, `stp_k_before`/`stp_k_after` (the fridge's MXP, MXP2 and STP thermometers just before and just after each position, kelvin; empty if no reading; also columns in a check's `positions.csv`), `temp_str`, `cooldown`, `switch_serial`, `note`, `cooldown_json`, the VNA's settings (`vna_*`), and on corrected runs `definitions`, `definitions_chosen` (`auto` or `by hand`), `corrected_from` and `source_run_ids`. Browse with `plottr-inspectr --db <file>` or Recall.
- NIST's definitions: the code looks for `ideals_295K/` and `ideals_3K/` (copies of NIST's `tier2_scikitrf_caldata/tier2_295k1` and `tier2_3k1`) in this folder or any folder above it.

## Safety (built in, don't work around it)

- The VNA source is off whenever the switch moves (no hot switching). Don't set `switch.channel()` by hand.
- Source power is capped at 0 dBm (`MAX_POWER_DBM` in `scripts/switch_vna.py`).
- Forward-only sweeps by default (`SWEEP_MODE = "forward"`): only VNA port 1 sources, so nothing is sent down the return line into the LNA's output, and the VNA reads S11 and S21 (everything here is analysed from S21 in the fridge, S11 on the bench; they're the same numbers in either mode). `SWEEP_MODE = "2port"` adds S12/S22 with port 2 sourcing at `POWER_DBM` too. Forward mode also **locks VNA port 2's source Off on the instrument** (`SOUR1:POW2:MODE OFF`, read back, checked before every sweep), so port 2 can't source even with an S22 trace on the screen or while the VNA keeps sweeping during a hold. It stays Off after the code is done, until someone sets it back (Power > Port Power > Port 2 > Source State: Auto) or presets the VNA; `"2port"` sets it back to Auto itself. If the VNA won't take the command, nothing is measured (`sv.REQUIRE_PORT2_OFF = False` falls back on the traces alone). Forward mode also refuses to sweep while any other trace (e.g. S22) is on the VNA: delete it there or run `sv.remove_traces(ksvna)`. In a forward run's `.s2p` files, S12 and S22 are NaN (a comment line says so); `ecal.read_touchstone` leaves them out. Every run records `vna_sweep_mode`.
- The code measures raw: it turns the VNA's own calibration off and refuses to measure if it's still on.
- Every take and check leaves the switch `ALL_OPEN`, even if it fails partway.
- The lab driver's `switch.state()` readback lags; the code only sets the switch. Use the board LEDs, or `switch.state.cache.get(get_if_invalid=False)` for what was last sent.

## Tests

`python -m pytest tests/lab` from the repo root, no hardware (needs the framework checkout beside this repo, or `MM4250_FRAMEWORK`, for `scripts/switch_vna.py`): fake VNA, switch and fridge (`tests/lab/fakes.py`), a check against NIST's own corrected data, and both notebooks run top to bottom in a kernel (`tests/lab/notebook_runner.py`).
