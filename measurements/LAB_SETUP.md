# Putting the measurement on the lab computer

Copy six files into your folder on the measurement computer. That's it
-- no install, no repo clone, nothing else touched.

Written for the DAQ machine as it is today: Windows, code under
`C:\Users\QTSF_DAQ\Measuring_scripts\`, conda environment
`QTSF_QCoDeS_env` (activated in anaconda powershell).

---

## 1. Copy these six files

All six live in this repo's `measurements/` folder:

| File | What it is |
|---|---|
| `measurements/vna_measure.py` | the measurement -- `setup_sweep`, `measure_2port`, `measure_s11` |
| `measurements/sweep_db.py` | saving -- the QCoDeS database (and Touchstone files, if asked) |
| `measurements/read_db.py` | reading it back -- `list_runs`, `load_run`, `export_touchstone` |
| `measurements/plots.py` | looking at it -- `plot_measurement`, `plot_sweep`, `summarize` |
| `measurements/ecal.py` | the fridge e-cal -- calibrated S11 at each channel's connector |
| `measurements/mm4250_sweeps.ipynb` | the one notebook you run, for every kind of sweep |

(`twoport_sweep.ipynb` and `oneport_sweep.ipynb` still work but are
superseded by `mm4250_sweeps.ipynb`; no need to copy them.)

**For the fridge e-cal (section D) also copy NIST's standard
definitions**, as folders beside the notebook:

| From (on the laptop) | To (beside the notebook) |
|---|---|
| `SD Code/nist_MM4250_calibration_data_2025/tier2_scikitrf_caldata/tier2_3k1/` | `ideals_3K/` |
| `SD Code/nist_MM4250_calibration_data_2025/tier2_scikitrf_caldata/tier2_295k1/` | `ideals_295K/` |

`ecal.find_ideals("3K")` looks for `ideals_3K/` there first. On the
laptop it finds the NIST repo on its own.

into

```
C:\Users\QTSF_DAQ\Measuring_scripts\QCoDeS-Measurement-Framework\Users\Charlie_Ferrari\
```

All of them must sit in the **same folder** -- the notebook imports
the modules from beside itself.

Nothing else from this repo is needed. The drivers come from the
framework repo that's already on that machine.

### Nothing to install

`qcodes`, `pyvisa` and `numpy` are already in the lab's
`environment.yaml`, and `matplotlib` comes in with qcodes. This code adds
no new dependencies -- the plots are matplotlib, the e-cal is plain
numpy rather than scikit-rf, and `read_db.py` reads the database with
Python's own `sqlite3`.

### Except hidapi, which you probably do have to install

The switch driver's `import hid` needs the Python bindings, and the
environment as shipped has only the C library. The two are both called
`hidapi`, which makes this confusing in a specific way: pip sees the name
satisfied by conda's C library and reports **"Requirement already
satisfied"** while doing nothing, so `import hid` keeps failing at line 7
of `MM4250_QCodes_driver.py` no matter how many times you install it.

Confirm that's what's happening -- `None` means the module isn't there at
all, whatever pip says:

```python
import importlib.util; print(importlib.util.find_spec("hid"))
```

Then force pip past its own resolver:

```python
%pip install --force-reinstall --no-deps hidapi
```

`--force-reinstall` overrides the already-satisfied check; `--no-deps`
keeps it from pulling anything else into a shared environment. Re-run the
`find_spec` line -- a path instead of `None` means it worked -- then
**restart the kernel**, which is the step that's easy to skip.

If it stays `None`, the conda package that carries the bindings has a
different name again:

```powershell
conda install -c conda-forge cython-hidapi
```

Install `hidapi`, never `hid`. Both give you a module called `hid`, but
only `hidapi` has the `hid.device()` API this driver uses -- the other
imports cleanly and then fails inside `MM4250("switch")`.

### Check the environment before anything else

Most import failures here are the wrong kernel, not missing code.
`sys.executable` is the interpreter actually running your cells; anything
else is inference.

```python
import sys, importlib
print(sys.executable)
for m in ("qcodes", "pyvisa", "hid", "numpy", "matplotlib"):
    try:
        mod = importlib.import_module(m)
        print(f"  {m:11s} {getattr(mod, '__version__', 'ok')}")
    except Exception as e:
        print(f"  {m:11s} FAILED -- {type(e).__name__}: {e}")
```

The path must contain `envs\QTSF_QCoDeS_env`. If it says `base`,
activating the env in PowerShell won't fix a kernel that's already
running -- shut Jupyter down completely (Ctrl+C twice in its window, not
just the browser tab) and relaunch it from the activated environment.

---

## 2. Run the notebook

Open `mm4250_sweeps.ipynb` from that folder in Jupyter, with
`QTSF_QCoDeS_env` active.

**Setup, every session** -- run these four cells in order:

1. **Imports** -- finds the drivers by walking up to the framework repo,
   and imports the modules from beside the notebook. Prints both paths
   so you can check them.
2. **Connect** -- creates `ksvna` and `switch` (forces `ALL_OPEN`).
3. **Session** -- `date_str` (defaults to today), `temp_str`,
   `switch_serials`, and `CAL_SET`, the VNA cal set sections B/C use.
   Everything below files its runs under these.
4. **Stop here** -- raises on purpose, so *Run All* ends after Setup.
   Don't run it by hand; skip past it.

**Then run one section by hand.** Each one starts by setting the VNA up
itself (`setup_sweep` plus correction on or off), so the order doesn't
matter:

- **A. 1-port sweep** -- S11 at each position, raw.
- **B. 2-port sweep** -- S11/S12/S21/S22 with `CAL_SET` on. See
  [Calibrated sweeps](#calibrated-sweeps) below.
- **C. Cal vs. uncal** -- one channel measured with the cal set on, then
  off.
- **D. Fridge e-cal** -- D1 `run_ecal_set(...)` measures one set (the
  internal standards, every channel, the standards again, `repeats`
  times) with correction off; D2 corrects it with NIST's definitions and
  plots calibrated S11 at each channel's connector. One set per
  temperature.
- **E. Browse and plot** -- any runs from any session.

Also there: **Check the VNA** (cal sets, settings, correction state),
**Quick look** (one measurement, nothing saved), **Close** (VNA output
off, switch `ALL_OPEN`, instruments released) and the hidapi debugging
cells.

No paths to edit. The notebook works out where it is on its own.

---

## 3. Where the outputs go

Everything lands in the same folder as the notebook:

```
Users\Charlie_Ferrari\
    vna_measure.py
    sweep_db.py
    read_db.py
    plots.py
    ecal.py
    mm4250_sweeps.ipynb
    ideals_3K\  ideals_295K\                       <- NIST definitions, for section D
    mm4250_sweeps.db                                 <- every sweep, always
    db_backups\mm4250_sweeps_<YYYYMMDD-HHMMSS>.db   <- snapshot after each sweep and at Close (newest 10 kept)
    Sweeps\<serials>\<date>\<temp>\<setup>_<cal|uncal>\RF<n>_run<id>.s2p   <- only with touchstone=True
    Sweeps\<serials>\<date>\<temp>\ecal_corrected_<set>\RF<n>.s1p       <- ecal.export_corrected
    figures\<serials>\<date>\<temp>\                <- plots saved with save=, and a day's
                                                       slide-figure set with its make_figures.py
```

**The database is the record; `.s2p` files are optional.** Every number a
Touchstone file would hold is in `mm4250_sweeps.db` at full precision,
plus what the VNA was doing, so sweeps save there only unless you pass
`touchstone=True`. When something outside QCoDeS needs files --
scikit-rf, the `mm4250-ecal` notebook, Keysight or NIST software,
sending data to someone -- write them afterwards:

```python
from read_db import export_touchstone
export_touchstone(runs)                  # the list a sweep returned
export_touchstone([43, 44, 45, 46])      # or any run ids
```

They land exactly where `touchstone=True` would have put them, with the
same contents. `list_runs()` shows what's in the database; `load_run(43)`
returns one run as `(freq, data)`. `read_db.py` needs only numpy, so all
of this works on a laptop without QCoDeS.

**Backups.** After every sweep, e-cal set and Close cell, `backup_db()`
writes a snapshot of the database to `db_backups\` beside it, using
SQLite's backup API, so the copy is consistent even with the kernel still
running, and it's one self-contained file (no `-wal`/`-shm`). Nothing is
written if no runs are new since the last snapshot, and only the newest 10
are kept. To bring data home, copy the newest file from `db_backups\`
rather than the live `mm4250_sweeps.db`. These backups sit on the same
disk, so they guard against a bad write or a mistaken delete, not against
losing the machine.

A day's slide figures sit in its `figures\<serials>\<date>\<temp>\`
folder with the `make_figures.py` that rebuilds them from the Touchstone
files in `Sweeps\` -- `python make_figures.py` from
anywhere, numpy + matplotlib only. The SN0077 and SN0078 scripts, and
`figures\NIST_comparison\295K\`, also read the 11 Sep VNA CSV exports in
`Sweeps\SN00xx\20260911\295K\vna_csv\` (magnitude only, so they aren't in
the database), and the SN0077 and NIST ones read
`SD Code\nist_MM4250_calibration_data_2025\`. Those are laptop analysis
scripts: they stop with a message if that data is missing. Don't copy the
NIST repo or the 11 Sep CSVs to the DAQ.

The database accumulates across runs -- every sweep you ever take goes
into that one file, 1-port and 2-port alike. Each `run_twoport_sweep(...)`
call is its own QCoDeS *experiment* named
`<date>_<temp>_<serials>_<setup>_<cal|uncal>`; each
position is a *run* named `RF<n>` (or the switch state name) inside it.
Browse it later with `plottr-inspectr --db mm4250_sweeps.db`.

The folders go switch -> date -> temperature -> cabling, e.g.
`Sweeps\SN0077\20260925\295K\RF3_cal\`. Name the cabling with
`setup="RF3"`; the `_cal` / `_uncal` half is read off the VNA's
correction state, so it can't be mislabeled. With no `setup` the folder
is plain `cal` or `uncal`. If the correction changes partway through a
sweep, the sweep stops rather than file the next position under the
wrong label.

To put sweeps somewhere else, pass `out_root=` (and `db_path=` for the
database).

---

## Calibrated sweeps

The VNA applies its own saved calibration. Nothing is corrected in
Python -- the code turns the instrument's correction on or off and
records which one it was.

Sections B and C sweep 1 MHz-10 GHz, 10000 points, IF bandwidth
1 kHz, -20 dBm, matching cal set `20260911_1MHz_10GHz_female` (2-port,
reference plane at the cable ends that mate with the switch):

```python
setup_sweep(start=1e6, stop=10e9, points=10000, if_bandwidth=1e3, power=-20, vna=ksvna)

import vna_measure
vna_measure.VNA_STATE_QUERIES["cal_set"] = "SENS:CORR:CSET:ACT? NAME"
ksvna.write('SENS:CORR:CSET:ACT "20260911_1MHz_10GHz_female",0')
```

The trailing `,0` keeps the current sweep settings; `1` would reset them
to the cal set's. The `VNA_STATE_QUERIES` line makes each run's metadata
record the active cal set by name, as `vna_cal_set`. Check what's on before sweeping:

```python
print(ksvna.ask("SENS:CORR:CSET:CAT? NAME"))   # every saved cal set
print(ksvna.ask("SENS:CORR:STAT?"))            # 1 = correction on
print(ksvna.ask("SENS:CORR:CSET:ACT? NAME"))   # which one
```

The cal / uncal cell measures one channel with the same cabling both
ways -- VNA port 1 on RFC, port 2 on RF`n` -- so the two can be subtracted:

```python
n = 6
positions = [n, "ALL_OPEN", "INTERNAL_LOAD", "INTERNAL_SHORT"]

ksvna.write('SENS:CORR:CSET:ACT "20260911_1MHz_10GHz_female",0')
run_twoport_sweep(positions, date_str, temp_str, switch_serials, setup=f"RF{n}", ...)   # -> RF6_cal/

ksvna.write('SENS:CORR:CSET:DEAC')
run_twoport_sweep(positions, date_str, temp_str, switch_serials, setup=f"RF{n}", ...)   # -> RF6_uncal/
```

The two calls are identical -- the folder follows the VNA. Change `n`,
re-cable, run again. Each call is its own experiment in the database
(`<date>_<temp>_<serials>_RF<n>_cal` / `_uncal`).

The cal set is only valid for the frequency range, point count and IF
bandwidth it was taken at. Change any of those in `setup_sweep` and the
cal no longer matches the sweep -- take a new cal.

---

## Running from the framework notebook instead

If you'd rather work in `QCodesMeasurmentFramework.ipynb`, where the
instruments are already connected, skip this notebook's connect cell and
add one cell there after the instrument cells:

```python
import sys
sys.path.insert(0, r"C:\Users\QTSF_DAQ\Measuring_scripts\QCoDeS-Measurement-Framework\Users\Charlie_Ferrari")
from vna_measure import setup_sweep, sweep_settings, measure_s11, measure_2port
from sweep_db import run_oneport_sweep, run_twoport_sweep
from plots import plot_measurement, plot_sweep, summarize
```

Then:

```python
setup_sweep(start=1e9, stop=10e9, points=1001, if_bandwidth=1e3, power=-20)
freq, data = measure_2port(3)
summarize(freq, data)
plot_measurement(freq, data)

runs = run_twoport_sweep([1, 3, 5], "20260917", "295K", "SN0001", setup="RF1")
plot_sweep(runs)
```

No `vna=`/`switch=` arguments needed -- the functions find the
instruments the framework notebook already created, by name (`ksvna` and
`switch`). Outputs still land in your `Users\Charlie_Ferrari\` folder,
because that's where the code is.

The `r"..."` prefix is required on Windows. Without it Python reads the
backslashes as escape characters and the path breaks.

**Don't run both notebooks at once.** The MM4250 driver opens an
exclusive USB handle -- whichever kernel connects first holds the switch,
and the second one fails.

---

## Names you may need to change

| What | Where | Change it if |
|---|---|---|
| the `Users\Charlie_Ferrari` path | the `sys.path.insert` line above | only needed for the framework-notebook route, and only if you put the files elsewhere |
| `ksvna` / `switch` | nothing, normally | the framework notebook registers instruments under other names. Then pass `vna=`/`switch=` explicitly |
| `mm4250_sweeps.db` | `DEFAULT_DB_NAME` in `sweep_db.py` | you want a different database filename |
| the VNA address | the connect cell in the notebook | the VNA moves or gets re-addressed |

---

## Known issues in the lab notebook

These are in `QCodesMeasurmentFramework.ipynb`, not in this code, and
only matter if you take the framework-notebook route above.

**Both instruments default to "not present."** The discovery cell starts
with `keysightVNA = False` and `menlo = False`, and nothing sets them
`True` -- it only scans serial ports, and the VNA is TCPIP while the
switch is USB HID. So the instrument cell skips both, and
`station = Station(ksvna, switch)` fails with `NameError`. Set both
flags `True` by hand before running that cell.

**`pna_address` is undefined.** With `keysightVNA = True`, the
instrument cell hits

```python
visa_handle = rm.open_resource(pna_address)
```

and `pna_address` is never defined anywhere -- `NameError`. The line is
vestigial; the next line creates the VNA with a hardcoded address and
never uses `visa_handle`. Comment out that line and the
`rm = pyvisa.ResourceManager()` above it.

---

## Limits the code enforces

Two hardware limits are checked rather than left to memory. Both protect
against failures that produce no error of their own.

**Source power is capped at 0 dBm.** `setup_sweep(power=...)` refuses
anything higher, and the pre-sweep check refuses to measure if the
instrument is above it -- which catches a power raised at the front panel
rather than in the notebook.

0 dBm is where the P5004B's specified maximum output bottoms out across
its range (+10 dBm from 10 MHz to 6.5 GHz, but only +4 dBm from 16-20 GHz
and 0 dBm below 100 kHz), so it is the most the instrument delivers
levelled at every frequency it covers. It also sits 7 dB under receiver
compression at the top of a 1-10 GHz sweep, 27 dB under the +27 dBm
damage level, and 4 dB under the MM4250's 0.5 V hot-switching limit.

The notebooks run -20 dBm. That is the working default; 0 dBm is a
ceiling, not a target. **If you need more dynamic range -- and you will,
measuring 40 dB of switch isolation -- lower `if_bandwidth` before you
raise power.** 1 kHz to 100 Hz buys about 10 dB for ten times the sweep
time. Power is the capped lever; IF bandwidth and averaging are not.

To go above the ceiling deliberately:

```python
import vna_measure
vna_measure.MAX_POWER_DBM = 5.0
```

**The source is switched off while the switch moves.** The MM4250 is an
ohmic MEMS switch, and moving its contacts with RF flowing erodes and
slowly welds them -- "hot switching", which collapses the rated 1.1e9
cycles rather than failing the part outright, with nothing about the
measurement looking wrong meanwhile. `_select()` drops the output for the
move and restores it afterwards, so you will see

```
Switch set to RF3 (source off during the move)
```

That protection lives in `_select()`. Driving the switch by hand --
`switch.channel(3)` in a cell of your own -- goes straight past it. Turn
the source off yourself first, or go through `measure_s11` /
`measure_2port`.

---

## If it refuses to measure

The code stops rather than record data that looks valid and isn't. Each
message names the fix.

| Message | What happened |
|---|---|
| `Refusing to set 20 dBm: the ceiling is 0 dBm` | The typo guard. `power=-20` and `power=20` are one character apart and 10,000x apart in watts |
| `VNA source power is 10 dBm, above the 0 dBm ceiling` | Same ceiling, caught at sweep time -- so the power was raised at the front panel |
| `VNA trigger source is 'MAN'` | The sweep would wait forever. Re-run `setup_sweep()` |
| `VNA RF output is off` | The sweep would record the noise floor into a valid-looking file |
| `VNA sweep type is 'SEGM'` | The frequency axis is computed from start/stop/points, so only LIN and LOG can be reconstructed. `ksvna.sweep_type('LIN')` |
| `Expected exactly one new trace on the VNA, found 0` | The PNA wouldn't add a trace. Usually it is at its trace limit -- clear unused traces at the front panel |
| `[WARNING] VNA fixturing/de-embedding is ON` | A warning, not a stop. It is applied before the data is read, so it lands in the files -- and if you also de-embed in post, you de-embed twice |

**The first measurement of a session may pause a few seconds.** Each run
records a snapshot of the VNA's state, and any of those SCPI queries this
firmware doesn't implement costs one VISA timeout. Each is asked once per
session and then remembered, so only the first position is slow.

---

## Looking at the data

Three functions, all in `plots.py`, all matplotlib -- no scikit-rf.

`summarize(freq, data)` prints min and max magnitude across the band plus
the value at three marker frequencies (bottom, middle, top by default;
pass `markers=[2e9, 6e9, 10e9]` in Hz for your own). Markers report the
nearest measured point, so nothing is interpolated that wasn't measured.

`plot_measurement(freq, data)` plots one position -- magnitude in dB, one
line per S-parameter. `phase=True` adds an unwrapped-phase panel
underneath, which is the view for checking electrical length or chasing a
cable problem.

`plot_sweep(runs)` overlays every position of a finished sweep.
`S21` by default for 2-port data and `S11` for 1-port; pass `sparam=` to
choose. **This is the plot worth taking to a meeting**: connected
channels sit near the top, an isolated state drops to the floor, and the
gap between them is the isolation.

It reads the data back from the database rather than using anything in
memory, so it works on any runs you've ever taken, from a kernel with no
instruments connected -- or on a folder of Touchstone files:

```python
plot_sweep([43, 44, 45, 46])
plot_sweep([43, 44, 45, 46], sparam="S11")
plot_sweep("Sweeps/SN0077/20260925/295K/RF3_cal")
plot_measurement(*load_run(43))           # one run
```

Runs from more than one sweep plotted together (cal and uncal, say) get
their group in the legend, e.g. `RF1 (RF1_cal, run 31)`.

**Adjusting and saving.** Both `plot_sweep` and `plot_measurement` take
the same optional arguments, all defaulting to the plain plot:

```python
plot_sweep(runs,
           xlim=(0, 6), ylim=(-100, 5),        # GHz, dB
           colors=["black", "tab:red"],        # in run order, or {31: "black", 32: "tab:red"}
           labels=["thru", "open"],            # legend text, same forms
           title="SN0077 RF1, 295 K",
           save=True)
plot_measurement(run=43, phase=True, colors={"S21": "black"}, save="rf3.pdf")
```

For `plot_measurement`, colors and labels go by S-parameter instead of
run. `save=` puts the figure in `figures\<serials>\<date>\<temp>\`
beside the code -- the same switch -> date -> temperature layout as
`Sweeps\`, going only as deep as all the runs agree (two switches on one
plot go in `figures\` itself):

| `save=` | where it goes |
|---|---|
| `True` | that folder, named for you -- e.g. `S21_RF1_cal+RF1_uncal_runs31-36.png` |
| `"isolation.pdf"` | that folder, your name; PNG, PDF or SVG by extension (`.png` if none) |
| `"C:/somewhere/x.png"` | exactly that path |

A measurement that isn't in the database yet (`plot_measurement(freq,
data)`) has no switch or date to file under, so it goes in `figures\`.
Everything returns the matplotlib Axes, so anything else matplotlib can
do still works afterwards. For side-by-side plots, make the subplots
yourself and pass `ax=`; `save=` on either one saves the whole figure.

Lines are ordered by run id, so the legend follows the order you measured
in. `read_touchstone(path)` is there too if you want the raw arrays; it
handles RI, MA and DB formats and all four frequency units, so it opens
files the VNA wrote as well as ours.

---

## Other things worth knowing

**Connecting resets the switch.** `MM4250.__init__` forces `ALL_OPEN` as
soon as it connects. If you re-run a connect cell mid-session, re-set
your channel afterwards.

**Calibration is whatever the VNA has on.** The code does no correction
of its own -- a sweep is calibrated only if a cal set is active on the
instrument (see [Calibrated sweeps](#calibrated-sweeps)), and raw
otherwise. Changing frequency range, point count or IF bandwidth
invalidates the cal -- redo it after `setup_sweep` changes any of those. The receiver attenuator counts too: the datasheet
requires adding input attenuation switching uncertainty if it changes
after a user calibration.

**What the data is.** The corrected, unformatted S-parameters (SDATA),
not the formatted trace (FDATA) the driver's `.polar()` returns. That
keeps electrical delay, phase offset, smoothing and trace math out of the
saved files -- any of which someone could set on the front panel while
looking at a trace, and none of which a Touchstone file would record.
Port extensions and on-instrument fixturing are upstream of SDATA and do
reach the data, which is why the code warns when either is on.

**Every run records what the numbers mean.** Alongside the S-parameters,
each QCoDeS run carries a `vna_*` metadata snapshot -- power, IF
bandwidth, averages, whether a cal was on, port extensions, and per-trace
electrical delay, phase offset and smoothing. Read it back with
`load_by_id(<run>).metadata`. A Touchstone file on its own records none
of this.

**Updating later.** These are plain copies, not a git clone. If you
change the code in the `mm4250-switch` repo, re-copy the changed file(s)
over. Only the six files above ever need copying.

**Bringing work back.** The folder is named differently in the two
places: `Users\Charlie_Ferrari\` on the DAQ, `measurements/` in this repo.
Nothing depends on the name -- the notebooks find their modules beside
themselves and walk up to `drivers/` -- so copy the *contents* of
`Users\Charlie_Ferrari\` into `mm4250-switch/measurements/`, not the
folder itself. `Sweeps/` and `*.db` are gitignored, so the data comes
along on disk but not into commits. Before copying:

1. **Save the notebook** (Ctrl+S), or the copy is missing your latest
   edits and outputs.
2. **Kernel -> Shut Down Kernel.** QCoDeS keeps the database in WAL mode:
   new runs are written to `mm4250_sweeps.db-wal` first and folded into
   `mm4250_sweeps.db` later. Shutting the kernel down closes the
   database, which folds everything in. `run_sweep` also does this after
   every position, so the `.db` should already be complete -- this is
   the backstop.
3. **Copy all `mm4250_sweeps.db*` files together** if any `-wal` / `-shm`
   are still there. A `.db` copied without its `-wal` can be missing
   the newest runs with no error to tell you -- that happened with runs
   61-62 on 25 Sep before the per-position checkpoint existed.

To check a copied database is complete on its own, from a notebook cell
in the folder it's in:

```python
import sqlite3
con = sqlite3.connect("file:mm4250_sweeps.db?mode=ro&immutable=1", uri=True)
print(con.execute("select count(*), max(run_id) from runs").fetchone())
```

`immutable=1` reads the `.db` while ignoring any `-wal`, so if the two
numbers match what the notebook last printed, nothing is stranded.

**Already have an `mm4250_oneport.db` on that machine?** That's the old
database name, from before 1- and 2-port sweeps shared one file. Existing
runs in it are untouched and still readable; new sweeps go to
`mm4250_sweeps.db`. To keep adding to the old file instead, pass
`db_path="mm4250_oneport.db"` to the sweep call.
