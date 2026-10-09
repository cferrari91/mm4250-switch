# mm4250-switch review (2026-10-07/08)

A read-only review. Nothing in the repo, the framework copy or any database was changed.

## How this was done

**Scope.** It covers the working tree as of 2026-10-08 13:40. That includes your 10-08 additions: `run_kit_set`, `make_ideals`, `"perfect"`, `plot_compare`, `vna_cal`, and notebook A2/D3.

**Who checked what.**
- I read every `measurements/*.py`, the notebook, the uncommitted diff and the branch diff.
- Eight reviewers then checked the rest, each followed by an adversarial verifier:
  - old notebooks; drivers + framework drivers; figure scripts; README/LAB_SETUP;
  - tests/lint; a trial merge of the branch; the DB migration; the new 10-08 code.

**Database safety.** Database work used only copies of `db_backups/mm4250_sweeps_20261002-172054.db` in my scratchpad.

**Tests: 122 passed, 0 skipped, 0 warnings** (2026-10-08).
- pyflakes: one hit (item S1).
- pyright: measurements/ is clean. tests/ has 11-13 errors (item S9).

**Tags.**
- **[C]** = certain: checked by reading or running. For items found by a reviewer, the verifier reproduced it.
- **[G]** = a guess, with the reason.
- Nothing here has run on hardware. The VNA and switch aren't reachable from the laptop.

**Scratchpad files.** Simulation scripts, the trial-merge clone and the migration script are in my scratchpad (`/private/tmp/claude-501/.../scratchpad/`). That folder may not survive a reboot, so copy anything you want to keep.

Size: S / M / L. "Where" uses file:line from the 10-08 working tree.

---

## 1. Bugs or things that are wrong (in the order I'd do them)

**1.1 The 20261001 figure script crashes on the current code.** [C] S
- Where: `figures/SN0077/20261001/295K/make_figures.py:155` calls the private `ecal._raw_standards` with 4 args. It now needs `sparam` (`ecal.py:396`).
- Why: the figure set can't be rebuilt. The staged `DAQ transfer/` already carries this `ecal.py`, so the DAQ copy breaks too.
- Change (verified to reproduce the committed CSV byte-for-byte): `ecal._raw_standards(block, "mean", DB, f_full, "S11")`.
  - Later: stop using private API. Let `correct_set` accept in-memory definitions.
  - Update the sweep-figures skill (D7).
- Risk: none.

**1.2 Quick look and section A measure S11, which can't see the switch with the circulator wiring.** [C] S
- Where: Quick look cell `96109a82` (`measure_s11(3)`) and A `12adeacf` (`run_oneport_sweep`, now `positions=[1], setup="RF1"`).
- Change:
  - Quick look: `measure_2port` + `summarize` + `plot_measurement(..., phase=True)`. `measure_2port` is already imported.
  - A: `run_twoport_sweep` + `plot_sweep(runs, sparam="S21")`.
  - Keep S11/`n_ports=1` as commented alternatives for single-line wiring. A2 (S11, no switch) is correct as is.
- Risk: none.

**1.3 Partly-finished sets crash the analysis, with errors that say nothing useful.** [C] M
Sets that die partway are backed up on purpose (`run_ecal_set`/`run_kit_set` call `_safe_backup` in `finally`). So these sets *will* be in the db.
- (a) A partial before/after block (e.g. it died after ALL_OPEN in "after"): `correct_set` (default `standards="mean"`), `drift` and `make_ideals` raise bare `KeyError('INTERNAL_SHORT')`. The check at `ecal.py:399` only asks whether the block is non-empty.
- (b) A set that died in its first standards: `correct_set(None)`/`drift(None)` raise a bare `StopIteration` (`ecal.py:381`). This happens even when an earlier good set exists.
- (c) A later repeat missing a channel: KeyError in the repeat mean (`ecal.py:472`).
- (d) A partial kit set: `make_ideals` makes the folder and writes the complete channels' files, then raises (`ecal.py:820-832`).
  - Default out_dir: the next kit set goes to a new folder, so nothing is blocked, just clutter.
  - Reused explicit out_dir: FileExistsError.

Change:
- Treat a block as present only if it has all three standards. Print "repeat k: after incomplete, using before only".
- Average each channel over the repeats that have it.
- Give `find_set(None)` a clear LookupError, or have it skip sets with no channel runs.
- In `make_ideals`, check every channel's three kit runs before `mkdir`.
- Add tests for each case.

Risk: low.

**1.4 `make_ideals` with `kit_defs` given as arrays writes files that can't be read back.** [C] S
- Where: `ecal.py:828` puts `str(kit_defs)` (a numpy repr with newlines) into the file comment. `_write_definition` (`:761-764`) writes it raw.
- What happens: `load_ideals` then fails with `could not convert string to float: '-0.00979984j,'`. Scalars, folders and paths are fine.
- Change: a one-line description of the kit, and make `_write_definition` strip newlines from the comment. Add a test.

**1.5 One timeout on `SENS:CORR:STAT?` blocks every sweep for the rest of the session.** [C on code; G on how often it happens] S
- Where: `vna_measure._try_ask` caches *any* exception in `_UNSUPPORTED_QUERIES` (`vna_measure.py:229-254`). It is used as a gate by `sweep_db._cal_state` (`:240`), so now also by `_check_correction`, and by the mid-sweep check (`:477-483`).
- What happens after one transient timeout:
  - every later sweep/set raises "Couldn't read the VNA's correction state";
  - mid-sweep, the position is lost with a misleading "correction changed mid-sweep" error.
- None of this is tested: the fixture stubs `_cal_state` (`tests/test_ecal.py:114-116`).
- Change: `_ask_required()` that retries once and never caches, used for the correction state. Add tests for timeout-then-answer and the mid-sweep flip.

**1.6 `load_run` returns S-parameters in a different order for different runs.** [C] S
- Where: `read_db.py:193-209` builds the dict in results-row order. QCoDeS writes those rows from a `set`, so the order depends on Python's hash seed.
- Evidence: in the real db's 62 two-port runs there are 4 different orders. With a scratch db under different `PYTHONHASHSEED` values, 5 orders.
- Effect: colours, legend order and `summarize` rows change between runs, and `colors=[...]` lists go to the wrong trace. `save_touchstone` is unaffected.
- Change: at the end of `load_run`, `data = {c.upper(): data[c.upper()] for c in cols[1:]}`. The complex64 upcast (5.1) goes in the same lines.

**1.7 Your own definitions written into a NIST folder are silently ignored.** [C] S
- Where: `_ideal_file` tries `port<n>_<std>_tier2.s1p` first (`ecal.py:188-190`). `make_ideals`' overwrite check only looks for `port<n>_<std>.s1p` (`:820-825`).
- What happens: pointing `out_dir` at `ideals_295K/` makes D3b quietly compare NIST with NIST.
- Change: refuse an out_dir that already holds definitions under any naming scheme.

**1.8 Sections B/C (and D1 with `vna_cal=True`) sweep 2001 points under a cal set taken at 10000.** [C on code; G on the VNA] S
- Where:
  - B `10658277` and C `152df2db` say "must match CAL_SET" but use `points=2001`.
  - D1's commented `ACT "{CAL_SET}",0` line inherits D1's 2001.
- [G] What the VNA does: either interpolates the cal (silently degraded) or turns correction off. If it turns it off, `_cal_state`/`_check_correction` catch it and refuse, which is safe.
- Change:
  - `CAL_SWEEP = dict(start=1e6, stop=10e9, points=10000, if_bandwidth=1e3)` next to `CAL_SET`.
  - `setup_sweep(**CAL_SWEEP, power=-20)` in B/C, and as a comment on D1's ACT line.
  - Record `SENS:CORR:INT?` and the active cal-set name in `VNA_STATE_QUERIES` (in the module, not the notebook).
- Also:
  - B/C are bench wiring only (VNA2 on an RF port): retitle them.
  - `CAL_SET`'s reference planes are the bench cable ends. So "VNA cal only" in D2/D3b means cables removed only if the cal was taken at the circulator wiring's cable ends.
  - Fix the "on?." typo in B's markdown.

**1.9 A2 can silently overwrite or mislabel the calibrated resonator trace.** [C] S
- Where: cell `53a038fd`.
  - `ONEPORT_CAL = "your_cal_set_name"` is sent as is.
  - `,0` relies on leftover VNA settings matching the cal.
  - No check that correction actually came on.
  - `save_touchstone(..., "Sweeps/no_switch/resonator_cal.s1p")` runs every time. The path is relative to the kernel's working directory, it overwrites, and the db gets nothing.
- Change:
  - Check the name is in `SENS:CORR:CSET:CAT? NAME`.
  - Print/assert `SENS:CORR:STAT?`.
  - [G] Use `,1` so the cal set's own stimulus is applied.
  - Name the file from `date_str`/`temp_str`/time under the db's `Sweeps/`, and refuse if it exists.
  - Later: record it in the db (S6).
  - The commented terminations cell `50a5d1c5` has the same fixed-name pattern.

**1.10 `setup_sweep` changes start/stop/points/IFBW before it refuses an over-ceiling power.** [C] S
- Where: `vna_measure.py:161-179`. `setup_sweep(points=..., power=0.5)` applies the points (which can invalidate an active cal), then raises.
- Change: validate `power` first. The power ceiling, no-hot-switching and switch-open fallback have **no tests** (vna_measure is 13% covered). See N3.

**1.11 `export_corrected` overwrites results corrected with different definitions.** [C, latent] S
- Where: the default folder `ecal_corrected_<set>` (`ecal.py:538-543`) doesn't include the definitions used. The file header doesn't say which definitions, sparam or vna_cal.
- Change: `ecal_corrected_<set>_<ideals name>/`, plus a header comment.

**1.12 `plots.read_touchstone` assumes Hz/RI when a file has no `#` option line.** [C] S
- Where: `plots.py:90`. The spec default is `# GHZ S MA R 50`; `ecal.read_s1p` already gets this right. Merge the readers (S2).

**1.13 The condition number is computed but never shown.** [C] S
- Where: `solve_error_terms` returns `cond` (`ecal.py:258`); nothing prints it.
- Why: correcting on a trace that barely sees the switch, or with poor definitions, gives no warning.
- Change: `correct_set` prints median/max cond per channel and warns above a threshold. [G] on the threshold.

**1.14 `vna_cal` is checked once per set.** [C] S
- Where: `sweep_db.py:790`. If the correction state flips during `run_kit_set`'s prompts, raw and corrected runs get mixed and `make_ideals` succeeds anyway.
- Each block does print the group and every run records `vna_correction_enabled`, so it's visible, but nothing raises.
- Change: call `_check_correction` before each block's `run_sweep`.

**1.15 Old runs 1-62 have legacy experiment names ("CabledRF1...").** [C by the old-notebooks reviewer] M
- Where: `read_db._group` (`read_db.py:212`) uses the whole name as the folder, so `export_touchstone` writes duplicate files into new folders.
- Change (your call): a legacy alias in `_group`. Don't touch the db.

**1.16 The lab switch driver ignores failed HID writes, and `channel()` returns the cache.** [C on code; G on how the board fails] S
- Where: `drivers/MM4250_QCodes_driver.py:144-148`.
- What happens: a dropped board would let a whole set run on one physical state, with every run labelled as intended. Only item 1.13's cond check would hint at it.
- Change: port the finalized driver's write check. This needs your OK, because the lab driver files are hook-protected and must stay in sync. Or move the notebook to `MM4250_finalized` after its DAQ check passes.

**1.17 The Connect cell's text is wrong.** [C] S
- Where: cell `f05cbe88` says re-running "resets the switch". In fact, re-running the cell fails with KeyError on the VNA line (the names are taken).
- [G, likely on Windows] Re-running `MM4250("switch")` alone moves the switch to ALL_OPEN *before* QCoDeS rejects the duplicate name.
- Change: reword to "run Close first; don't re-run `MM4250("switch")` by itself".

---

## 2. Docs and comments that are wrong or stale

Each was checked against the code [C] unless marked.

- **D1. README, LAB_SETUP and CLAUDE.md say no calibration is done in software.**
  - Where: `README.md:18-22,219-221,258-263`, LAB_SETUP:245-247. `ecal.py` does it.
  - Fix: describe two layers, the VNA cal set and `ecal` (NIST / own kit / perfect).
- **D2. The docs assume bench wiring.**
  - Where: "read isolation off `plot_sweep`" (`plots.py:481-483,501-504`, LAB_SETUP:456-460, README:162-173). That gives the wrong reading with the circulator.
  - Neither README nor LAB_SETUP mentions the circulator.
  - Fix: add a short "Wiring" section to LAB_SETUP (both wirings; what S11/S21 mean) and qualify those lines "(bench wiring)".
- **D3. The notebook intro (`0f956fd7`) is now wrong in two places.**
  - "Each section sets the VNA up itself, so order doesn't matter": A2 and D3a use the current settings on purpose, and D2/D3b need variables from D1/D2/D3a.
  - "D has to run raw": not with `vna_cal=True`.
- **D4. LAB_SETUP is wrong about the switch driver and the copy list.**
  - LAB_SETUP:48-49 and repo CLAUDE.md:53 say the drivers come from the framework repo. The switch driver `MM4250_QCodes_driver.py` was **never committed** to the framework (only its `.pyc` is in `cf88ae7`), so a fresh clone can't import it.
  - LAB_SETUP §1 still says "six files" (and `:543` "only the six"), and its "into <path>" block is cut off from its table.
  - Fix: make §1 the one full copy list, with a row for the switch driver into `FRAMEWORK\drivers\` "only if absent". Never put a `drivers\` folder inside `Users\Charlie_Ferrari\`.
- **D5. LAB_SETUP "Bringing work back" (`:547-566`) says to copy the live `mm4250_sweeps.db*`.**
  - That contradicts "copy a backup, not the live .db" (LAB_SETUP:207, README:213, CLAUDE.md).
  - Fix: either make the newest DAQ backup the laptop's db by hand (after comparing `list_runs` on both), or keep DAQ backups in a separate ignored folder and pass `db_path=`. Not into `db_backups/`.
- **D6. Pointers to `sweep_db` for things that live in `read_db`.**
  - LAB_SETUP:341 points to `DEFAULT_DB_NAME` in sweep_db; it's in `read_db.py:38`. Changing sweep_db's imported copy would split writes from reads.
  - Same for `vna_measure.py:70` ("save_touchstone() in sweep_db.py").
  - `read_db.py:91` writes "!Created by sweep_db.py".
- **D7. The sweep-figures skill says the wrong things.**
  - Where: `claude-setup/skills/sweep-figures/SKILL.md:8-12,28-33` says "copy the latest make_figures.py", "numpy+matplotlib only" and "read Touchstone files". The latest script is db-based and broken (1.1).
  - Fix: use public `read_db`/`ecal` calls with an explicit `db_path`; make 20261001 (fixed) the e-cal template; re-run `install.sh`.
- **D8. Figure-script "laptop-only" text is stale.**
  - Where: `figures/SN0078/.../make_figures.py:6,24`, `NIST_comparison/...:15-16`, and CLAUDE.md's "laptop-only" line.
  - The Sep 11 CSVs and the 24/25 Sep Touchstones are on GitHub main and the DAQ.
  - Only NIST_comparison and SN0077/20260925 need the NIST repo. SN0078 runs on the DAQ, and so does 20261001 once the modules are there and 1.1 is fixed.
- **D9. "Each call is its own experiment" is wrong.**
  - Where: `sweep_db.py:40`, README:195, LAB_SETUP:223-227 and :286.
  - `load_or_create_experiment` reuses the name. `run_ecal_set`'s docstring already says this correctly.
- **D10. The settle comment is wrong.**
  - Where: `SWITCH_SETTLE_S` comment (`vna_measure.py:81-82`) says the lab driver doesn't wait. It sleeps 25 ms (`MM4250_QCodes_driver.py:47,148`), so the real wait is 75 ms.
- **D11. The hidapi Debugging cell runs the wrong command.**
  - Where: `4287e0eb` runs plain `pip install hidapi` in the shared env, which LAB_SETUP:53-66 says does nothing on the DAQ.
  - Fix: run the `find_spec` check first, then a commented `%pip install --force-reinstall --no-deps hidapi`, then restart.
- **D12. LAB_SETUP's framework-notebook route and "Known issues" (`:295-368`) are stale.**
  - They describe a framework notebook that no longer matches `cf88ae7`, which doesn't connect the switch. Your call: drop them or update them.
- **D13. README and LAB_SETUP predate 10-06 to 10-08.**
  - Neither mentions `run_kit_set`, `make_ideals`, `plot_compare`, `"perfect"`, `vna_cal`, `dut_label`, `find_set`, `correct_and_plot`, or the finalized driver (README).
  - The good news: every documented call still binds to the current signatures (27 checked with `inspect.signature().bind`).
- **D14. `docs/MM4250_Instructions.md:88,99` says `channel()` reads the hardware.** In the lab driver it returns the cache; only `state()` queries the board.
- **D15. Four wrong teaching comments in `MM4250_QCodes_driver_commented.py`** (`:115-122, :255-262, :278-283`, and a path that's now in Archive). The sync test strips comments, so nothing catches these.
- **D16. `make_ideals`: "a perfect kit is fine to a few GHz" (`ecal.py:784-789`) is too strong.** [C, simulated]
  - With an offset-standard SMA kit, |S11| in dB stays within about 0.05-0.3 dB to a few GHz, but the phase/complex value is off by about 1 GHz (it's a reference-plane shift of the kit's offset delay).
  - Fix: say "magnitude only to a few GHz; phase needs kit_defs".
  - A practical route to real `kit_defs`: measure each kit standard through the A2 Smart Cal and save open/short/load.s1p. [G] that the Smart Cal there is plain SOL.
- **D17. README says Python 3.9+, but the DAQ env runs Python 3.14.7.**
  - Where: README:240. The DAQ version comes from the saved kernel metadata in the framework notebook and the `cpython-314` pycs; the suite has only ever run on 3.11.
  - Fix: say "3.11+", and run the hardware-free suite once on the DAQ from a folder outside the framework repo. [G] on the DAQ's qcodes version, which nothing records.
- **D18. Stale CLAUDE.md lines** (you asked me to check these, not fix them).
  - Project CLAUDE.md "team repo has no working ignore file": stale. A `.gitignore` exists, but it has `<<<<<<<`/`>>>>>>>` markers. The patterns inside still work.
  - Project CLAUDE.md points at `measurements/FIRST_COOLDOWN.md`, which exists only on the branch.
  - Repo CLAUDE.md:22-23 lists the old notebooks as current, and :53 has the driver claim (D4).
- **D19. Old `1.9` items still true.**
  - `correct_set` docstring "raw/terms (repeat 1)" (`ecal.py:431-432`) means "the first repeat corrected".
  - The `IDEALS_CANDIDATES` comment says `{t}`, but the first pattern uses `{T}`.
  - The `_dh[0]` comment in Setup: it's the kernel's start folder, normally the notebook folder. [G] it differs if VS Code's `notebookFileRoot` is changed.
  - [G] The `mm4250-ecal/ideals` candidate can't match from the repo, because it is in `SD Code/Archive/`.
  - The D2 comment is now fixed.
- **D20. Doc ownership, to stop the same thing going stale in 2 to 5 places.**
  - Module docstrings own the API.
  - LAB_SETUP owns procedures (copying, wiring, cal sets, moving data, troubleshooting).
  - Notebook cell 0 owns "which section does what".
  - README is an index.
  - CLAUDE.md holds rules, decisions and status only.
  - Everything else becomes a pointer.

---

## 3. Simplifications

- **S1. Dead code.** [C, grepped in the repo, branch, framework copy and `DAQ transfer/` incl. the zip] S
  - Remove `sweep_db.save_s1p`, `save_s2p`, `record_channel`, `vna_measure.ensure_trace`, and the `TOUCHSTONE_2PORT_ORDER` import (`sweep_db.py:103`, the one pyflakes hit).
  - Reword the re-export comment at `:95-99`. `_as_dict` is *not* dead.
  - Optional: drop `run_sweep(exp_name=)`.
- **S2. One Touchstone reader.**
  - Move `plots.read_touchstone` (with the spec defaults, 1.12) into `read_db` (no matplotlib).
  - Delete `ecal.read_s1p`, and replace the 3 more copies in the figure scripts (`20260925:70-94`, `20261001:72-96`, `NIST_comparison:61-63`). `plots.read_touchstone` was verified as a drop-in there.
  - Merge the `_as_dict` copies.
- **S3. One home for the S-parameter tuple.** `SPARAMS_2PORT`/`ecal.SPARAMS` -> `read_db`.
- **S4. One magnitude/phase figure helper.**
  - `plot_ecal` and `plot_compare` (`ecal.py:553-671`) are near-duplicates, and both differ from `plots.py` (dpi 150 vs 200, a plain save path vs `figures/<serials>/<date>/<temp>/`, wrapped vs unwrapped phase).
  - Share one helper and use `plots._save`. Document `xlim`/`ylim_db`, and stop ignoring `ylim_db` when `show_raw=True`.
  - Rename `plot_compare(labels=)` to `names=`: everywhere else, "labels" means DUT labels.
- **S5. Options with today's defaults.**
  - `correct_set(channels=None)`: today D3b fails outright if D1 measured a channel the kit set didn't.
  - `correct_and_plot` passes `repeat`, `standards`, `fmin`, `channels`, `xlim`, `ylim_db`, `verbose` through.
  - Optionally, `drift`'s positional order matches `correct_set`'s.
- **S6. A supported way to record a measurement that doesn't move the switch.**
  - Three cells measure a termination on the bare cable: `50a5d1c5`, A2, and branch F1. Only F1 saves to the db, and it does so through the private `_open_experiment` + `record_measurement`.
  - One public helper would cover all three: `record_external(name, cal_set=None, label=...)`, or `run_sweep` positions that don't move the switch.
- **S7. Shorter notebook calls.**
  - `use_cal(name=None)` in `vna_measure`: activate or deactivate, then print the correction and interpolation state.
  - Put the cal-set-name query into `VNA_STATE_QUERIES` itself.
  - Drop `vna=ksvna, switch=switch`: the defaults find the instruments by those names.
  - Define `SWEEP`/`CAL_SWEEP` once.
- **S8. D2 runs `correct_and_plot` twice**, so drift and repeatability print twice and the comment is redundant. Keep one (NIST) and leave the comparison to D3b's `plot_compare`.
- **S9. Clean pyright for the tests** (verified on a copy: 0 errors).
  - A repo-root `pyproject.toml` with `pytest pythonpath = [".", "measurements"]` and `pyright extraPaths = ["measurements"]`, a typed fake `Fridge`, and three asserts.
  - The per-file `sys.path.insert` + `noqa` lines then go.
- **S10. Old notebooks.**
  - Archive `oneport_sweep.ipynb` now; archive `twoport_sweep.ipynb` after carrying over its Quick look default and the `prompt_between`/plot-option hints.
  - Never delete; your call. Then update CLAUDE.md:22-23, README:92 and LAB_SETUP:25.
- **S11. Figure scripts.**
  - Remove the dead values in the SN0077 scripts (`20260925:158,237,279`).
  - Optional: move `deembed`/`cinterp` into `ecal` and the shared style into `figures/_style.py`.

## 4. Nice to have

- **N1. Tests for 1.3, 1.4, 1.6 and 1.7.** Each one would have been a one-test catch. Also: a kit_defs folder round trip, `correct_set(repeat=2)`, and an interrupted `run_kit_set` leaving the switch ALL_OPEN.
- **N2. Touchstone/export round-trip test.** It would pin the S12/S21 column order that `read_db.py:43-46` warns about, and add a no-option-line case for 1.12 and a `_group` legacy case.
- **N3. A small fake-VNA test file for the safety rules.**
  - Power ceiling (0.04 dB readback accepted, 0.06 refused, nothing set on refusal).
  - `_select` order [output off, move, output on], with the source restored on error.
  - `_leave_switch_open`'s fallback.
  - `_read_sdata` decoding.
- **N4. Optional smoke test for the figure scripts:** rebuild each in tmp and `cmp` the CSVs, skipping if inputs are missing. It would have caught 1.1.
- **N5. Clearer messages when figure-script inputs are missing.** Add the inputs to each script's guard; today you get a bare `AssertionError`/`IndexError`.
- **N6. Say what the `N52xx.py` local edits are** in `THIRD_PARTY.md`. They're typing-only and behaviour is identical. The code equals the framework's `drivers/` (commit 15deeb6).
- **N7. README: one bullet for the finalized driver**, its tests, the DAQ check and the example notebook.
- **N8. Framework `CLAUDE.local.md`:** fix its LAB_SETUP pointer. Add the rules a DAQ Claude session needs most: never open the live db, no hot switching, the power ceiling, the team-repo/PR rule.
- **N9. Ignore rules for generated definitions.**
  - `make_ideals`' `ideals_<temp>_<serials>_kit<id>/` and `Sweeps/no_switch/` aren't ignored by the repo `.gitignore`, the framework's, or the DAQ folder's.
  - Add `ideals_*/` deliberately (it's your data; your call whether it belongs in the team repo).
- **N10. Committed SN0077 figures mix two fonts.** The report has the older 03/04/09. Optional re-render after 1.1.

## 5. Database (`mm4250_sweeps.db`)

Every number below was tested end-to-end on copies [C].

**What's in it.**
- 66,461,696 B, 90 runs (62 two-port, 28 one-port), 0 free pages, so VACUUM alone gains nothing.
- S-parameters (complex128): 43.6 MB. Frequency (float64): 21.8 MB.
- QCoDeS writes each S-parameter as its own row with its own copy of the frequency axis (`qcodes/dataset/data_set.py:1248-1309`), and setpoint shapes must match (`measurements.py:443-466`). A two-port run therefore stores the axis 4 times, and there's no lossless way around that inside QCoDeS's layout.
- All 276 S-parameter arrays are exactly float32 values, because the driver reads `FORM REAL,32` (`vna_measure.py:324`).

**5.1 New runs: store S-parameters as complex64.** S
- Change `vna_measure.py:325`: keep float32, then `.view(np.complex64)`. Or, alternatively, downcast in `record_measurement` only when it's exact; that keeps in-session data identical to replays.
- `save_touchstone` output stays byte-identical. QCoDeS stores and loads complex64 fine.
- **The `load_run` upcast to complex128 is required**, not cosmetic. Without it, `correct_set` differs by up to 6e-6 relative and `drift` returns float32 (the `np.mean`s run in float32). With it, everything is bit-identical.
- Per 2001-point 2-port run: about 257 KB -> 193 KB.

**5.2 Old runs: optional migration.** M
- The script copies a backup, rewrites each S-parameter blob as complex64 using QCoDeS's own writer, checks every run, VACUUMs and runs `integrity_check`.
- Result: 66,461,696 -> 44,257,280 B (-33%). All 90 runs match via `read_db` and via QCoDeS (`get_parameter_data`, `to_xarray_dataset`). `backup_db`'s fingerprint is unchanged.
- **Swap procedure, because a stale `-wal` corrupts the swap:**
  1. Close every kernel and plottr.
  2. Check that no `mm4250_sweeps.db-wal`/`-shm` exists.
  3. `backup_db(force=True)` and migrate that backup.
  4. Verify.
  5. Rename the live file aside, together with any `-wal`, and move the new file in.
  6. Open it and check that the run count and `max(run_id)` match, not just `quick_check`.
- Tested: SQLite adopts a leftover `-wal` even for a file in DELETE mode, and `quick_check` still says "ok".
- The script should refuse a WAL-mode input.

**5.3 Perspective.**
- At 2001 points, runs are small either way. The 10000-point runs made the file big.
- After migration, the frequency axis is about 49% of the file.
- Backups keep 10 full copies (about 660 MB now, about 440 MB after migration), so lowering `keep` is the bigger lever.

**5.4 Readability in DB Browser.** S
- QCoDeS works fine with an extra `VIEW` [C], but a view's column list is frozen when it's created: `dut_label`/`kit_*` don't exist in older dbs, and new columns won't appear.
- Either rebuild it with a small `DROP VIEW IF EXISTS` + `CREATE` helper after sweeps (not in `read_db`, which promises never to write), or keep a saved SELECT in DB Browser's Execute SQL tab.

## 6. Branch `ecal-terminations-first-cooldown`

The trial merge was done in a scratch clone [C].

**Conflicts:** 5 files: CLAUDE.md (2 hunks), `ecal.py` (6), `sweep_db.py` (10), `tests/test_ecal.py` (1), notebook (2). `FIRST_COOLDOWN.md` and the 14 section-F cells merge in cleanly.

**Plan (verified, 123 passed / 2 NIST-skipped in the clone).**
1. Commit the WIP first.
2. Merge.
3. **Take the WIP's `sweep_db.py` and `ecal.py` whole** (`git checkout --ours ...`), then check `git diff HEAD` on them is empty.
   - Why whole: resolving hunk by hunk leaves branch lines behind that reference `position_metadata`. Every `run_sweep` then raises NameError, after spending a sweep. [C]
4. Convert the 3 useful branch tests to label tests.
5. Keep `labels`/`dut_label` as the one per-port mechanism. Drop `terminations`, `termination="none"` and `position_metadata`.

**Section F and `FIRST_COOLDOWN.md` need:**
- `TERMS` -> `labels`.
- F2/F4 drop `terminations=` (otherwise TypeError).
- F3 uses `run_twoport_sweep` and S21. Its "difference from the first ALL_OPEN" metric still works on S21 (simulated).
- `check_terminations` reads `result["labels"]` and moves into `ecal.py`.
- F1 uses `points=10000`.
- The wiring text describes the circulator.

**Also:**
- **Don't depend on the exact label text.** F3/F5 only work if the label is exactly "short"/"load". D1's own example "50 ohm load" makes F3 raise StopIteration and F5 skip the bench comparison silently. Use `SHORT_CH, LOAD_CH = 2, 5` in F0 and key `BENCH` by channel.
- **Worth keeping from the branch:** label bare ports explicitly (`"bare"`), so F5 prints them. Optionally have `list_sets` return labels.
- **F vs D3:** F answers whether NIST's definitions give back known terminations, so keep F5 on NIST's. Scoring a short/load channel with *own* definitions made from that same short/load is circular. If you want own definitions in F5, run D3a warm on all six channels before pumpdown.

The resolution diff is in the scratchpad (`p2-branch/resolution.diff`).

## 7. New 10-08 code: kit sets, own definitions, "perfect", vna_cal

**The maths is right** [C, simulated with a random 3-port outside network and a switch model].
- `make_ideals` -> `correct_set` recovers a DUT on RF n's connector to 1e-14. This holds through a *different* outside network for the e-cal set, on S21, S11, S12 and S22. The definitions belong to the switch alone, as documented.
- `"perfect"` returns the DUT seen through the RFC->RF n path, mapped so that the internal standards read +1/-1/0. A 50 ohm load on RF1 read about -15 dB in the model, not -inf. The docstring is accurate; the "plane" only exists if the internals are ideal.
- `vna_cal=True` is exact when the VNA's error terms are exact. A stale cal leaves a residual of about 1e-4 in the e-cal (circulator case), but the "VNA cal only" trace itself can be off (1.8).

**This replaces the original comparison plan.** The original plan was a kit at the RFC cable end. The kit-on-each-RF-connector approach is better: it gives *your* switch's tier-2 definitions.

**Remaining gaps:** 1.3(d), 1.4, 1.7, 1.11, 1.14, S4, S5, S6, D16, N9.

## 8. Copying to the framework / DAQ

**VNA drivers: no action needed** [C]. The framework's `drivers/N52xx.py` and `KeysightVNA_driver.py` have the same code as the repo's; only headers differ. Every attribute `vna_measure` uses is present. This corrects my earlier guess.

**Switch driver: not in git** [C]. `drivers/MM4250_QCodes_driver.py` is absent from the framework's git (D4).
- [G] The DAQ's on-disk copy is the 09-25 version (from the `.pyc` header). It differs from the current one only in one error message, so keeping it is fine.

**The DAQ last ran September modules** [C from the pyc headers; G that those files are still on disk].
- The committed `.pyc` headers match the Sept 21/22 `sweep_db.py`/`vna_measure.py`. Those have no `run_ecal_set`, and there was no `read_db`/`ecal` yet.
- When committing, use the 10-08 `DAQ transfer/Charlie_Ferrari/` copies (byte-identical to the working tree), not whatever is on the DAQ disk.
- Add the expected sizes to `DAQ_git_fix_guide.md` Step 1: 14673 / 27477 / 7071 bytes.

**Copy over:** the 5 modules, the notebook, LAB_SETUP.md, the folder `.gitignore`, and `FIRST_COOLDOWN.md` after the merge. Also the fixed 20261001 `make_figures.py` (1.1): the transfer copy of `ecal.py` breaks the old one.
- **Don't copy:** `tests/`, the live `.db` (copy a backup), or a `drivers/` folder into `Users\Charlie_Ferrari\`.

**Ideals:** `ideals_295K/` and `ideals_3K/` there are byte-identical to NIST's `tier2_295k1`/`tier2_3k1`, and `find_ideals` picks them up [C].

## 9. Notebook reorganization (proposal; every current capability stays reachable)

1. **Setup:** imports, connect, Session.
   - Session holds `date_str`, `temp_str`, `switch_serials`, `SPARAM = "S21"`, `SWEEP`, `CAL_SET` + `CAL_SWEEP`, and `ONEPORT_CAL`.
2. **Stop here.**
3. **Check the VNA:** add the interpolation state.
4. **Circulator wiring:**
   - Quick look: 2-port, plot `SPARAM`.
   - A: raw sweep.
   - D1: e-cal set.
   - D2: correct and plot with NIST, once.
   - D3a/D3b: kit set and comparison.
   - F: first cooldown, after the merge, updated for S21.
5. **Bench wiring:**
   - B/C (VNA2 on RF n), with `CAL_SWEEP`.
   - A2: one cable, 1-port cal, no switch. Recorded to the db via S6.
6. **E: browse/plot/export.**
7. **Close**, then **Debugging** (fixed per D11).

Each sweep cell then starts with `setup_sweep(**SWEEP)` + `use_cal(None)`. The intro states which cells depend on earlier ones (D3).
