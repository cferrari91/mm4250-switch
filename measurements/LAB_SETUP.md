# Putting the measurement on the lab computer

Copy four files into your folder on the measurement computer. That's it
-- no install, no repo clone, nothing else touched.

Written for the DAQ machine as it is today: Windows, code under
`C:\Users\QTSF_DAQ\Measuring_scripts\`, conda environment
`QTSF_QCoDeS_env` (activated in anaconda powershell).

---

## 1. Copy these four files

All four live in this repo's `measurements/` folder:

| File | What it is |
|---|---|
| `measurements/vna_measure.py` | the measurement -- `setup_sweep`, `measure_2port`, `measure_s11` |
| `measurements/sweep_db.py` | saving -- Touchstone files and the QCoDeS database |
| `measurements/twoport_sweep.ipynb` | the 2-port notebook you run |
| `measurements/oneport_sweep.ipynb` | the 1-port notebook, if you want S11 only |

into

```
C:\Users\QTSF_DAQ\Measuring_scripts\QCoDeS-Measurement-Framework\users\Charlie Ferrari\
```

All of them must sit in the **same folder** -- each notebook imports
the two modules from beside itself. You only need whichever notebook
you're actually running, but the two `.py` files are required either
way.

Nothing else from this repo is needed. The drivers come from the
framework repo that's already on that machine.

### Nothing to install

`qcodes`, `pyvisa`, `hidapi` and `numpy` are all already in the lab's
`environment.yaml`. This code adds no new dependencies.

---

## 2. Run the notebook

Open `twoport_sweep.ipynb` from that folder in Jupyter, with
`QTSF_QCoDeS_env` active, and run the cells top to bottom:

1. **Imports** -- finds the drivers by walking up to the framework repo,
   and imports the two modules from beside the notebook. Prints both
   paths so you can check them.
2. **Connect** -- creates `ksvna` and `switch`.
3. **Setup** -- `setup_sweep(start=..., stop=..., points=...)`. Also
   puts the VNA in a state where a sweep can finish: trigger source
   `IMM`, RF output on.
4. **Single measurement** -- `measure_2port(3)`, nothing saved.
5. **Batch sweep** -- edit `positions`/`date_str`/`temp_str`/
   `switch_serials`, then save the whole set.
6. **Close** -- releases the instruments.

No paths to edit. The notebook works out where it is on its own.

---

## 3. Where the outputs go

Everything lands in the same folder as the notebook:

```
users\Charlie Ferrari\
    vna_measure.py
    sweep_db.py
    twoport_sweep.ipynb
    oneport_sweep.ipynb
    Sweeps\<date>_<temp>\<serials>\raw\RF<n>_run<id>.s2p   <- created by the sweep
    mm4250_sweeps.db                                 <- created by the sweep
```

The database accumulates across runs -- every sweep you ever take goes
into that one file, 1-port and 2-port alike. Each `run_twoport_sweep(...)`
call is its own QCoDeS *experiment* named `<date>_<temp>_<serials>`; each
position is a *run* named `RF<n>` (or the switch state name) inside it.
Browse it later with `plottr-inspectr --db mm4250_sweeps.db`.

To put sweeps somewhere else, pass `out_root=` (and `db_path=` for the
database).

---

## Running from the framework notebook instead

If you'd rather work in `QCodesMeasurmentFramework.ipynb`, where the
instruments are already connected, skip this notebook's connect cell and
add one cell there after the instrument cells:

```python
import sys
sys.path.insert(0, r"C:\Users\QTSF_DAQ\Measuring_scripts\QCoDeS-Measurement-Framework\users\Charlie Ferrari")
from vna_measure import setup_sweep, sweep_settings, measure_s11, measure_2port
from sweep_db import run_oneport_sweep, run_twoport_sweep
```

Then:

```python
setup_sweep(start=1e9, stop=10e9, points=1001, if_bandwidth=1e3, power=-20)
freq, data = measure_2port(3)
run_twoport_sweep([1, 3, 5], "20260917", "295K", "SN0001")
```

No `vna=`/`switch=` arguments needed -- the functions find the
instruments the framework notebook already created, by name (`ksvna` and
`switch`). Outputs still land in your `users\Charlie Ferrari\` folder,
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
| the `users\Charlie Ferrari` path | the `sys.path.insert` line above | only needed for the framework-notebook route, and only if you put the files elsewhere |
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

## Other things worth knowing

**Connecting resets the switch.** `MM4250.__init__` forces `ALL_OPEN` as
soon as it connects. If you re-run a connect cell mid-session, re-set
your channel afterwards.

**Calibration is not applied.** These are raw measurements. Calibrate the
VNA before sweeping, and note that changing frequency range, point count
or IF bandwidth invalidates the cal -- redo it after `setup_sweep`
changes any of those. The receiver attenuator counts too: the datasheet
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
over. Only the four files above ever need copying.

**Already have an `mm4250_oneport.db` on that machine?** That's the old
database name, from before 1- and 2-port sweeps shared one file. Existing
runs in it are untouched and still readable; new sweeps go to
`mm4250_sweeps.db`. To keep adding to the old file instead, pass
`db_path="mm4250_oneport.db"` to the sweep call.
