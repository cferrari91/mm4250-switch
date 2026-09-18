# Putting the 1-port measurement on the lab computer

Copy three files into your folder on the measurement computer. That's it
-- no install, no repo clone, nothing else touched.

Written for the DAQ machine as it is today: Windows, code under
`C:\Users\QTSF_DAQ\Measuring_scripts\`, conda environment
`QTSF_QCoDeS_env` (activated in anaconda powershell).

---

## 1. Copy these three files

All three live in this repo's `measurements/` folder:

| File | What it is |
|---|---|
| `measurements/vna_measure.py` | the measurement -- `setup_sweep`, `measure_s11` |
| `measurements/oneport_db_sweep.py` | saving -- `.s1p` files and the QCoDeS database |
| `measurements/oneport_db_sweep.ipynb` | the notebook you run |

into

```
C:\Users\QTSF_DAQ\Measuring_scripts\QCoDeS-Measurement-Framework\users\Charlie Ferrari\
```

All three must sit in the **same folder** -- the notebook imports the
other two from beside itself.

Nothing else from this repo is needed. The drivers come from the
framework repo that's already on that machine.

### Nothing to install

`qcodes`, `pyvisa`, `hidapi` and `numpy` are all already in the lab's
`environment.yaml`. This code adds no new dependencies.

---

## 2. Run the notebook

Open `oneport_db_sweep.ipynb` from that folder in Jupyter, with
`QTSF_QCoDeS_env` active, and run the cells top to bottom:

1. **Imports** -- finds the drivers by walking up to the framework repo,
   and imports the two modules from beside the notebook. Prints both
   paths so you can check them.
2. **Connect** -- creates `ksvna` and `switch`.
3. **Setup** -- `setup_sweep(start=..., stop=..., points=...)`.
4. **Single measurement** -- `measure_s11(3)`, nothing saved.
5. **Batch sweep** -- edit `channels`/`date_str`/`temp_str`/
   `switch_serials`, then save the whole set.
6. **Close** -- releases the instruments.

No paths to edit. The notebook works out where it is on its own.

---

## 3. Where the outputs go

Everything lands in the same folder as the notebook:

```
users\Charlie Ferrari\
    vna_measure.py
    oneport_db_sweep.py
    oneport_db_sweep.ipynb
    Sweeps\<date>_<temp>\<serials>\raw\RF<n>.s1p     <- created by the sweep
    mm4250_oneport.db                                <- created by the sweep
```

The database accumulates across runs -- every sweep you ever take goes
into that one file. Each `run_oneport_sweep(...)` call is its own QCoDeS
*experiment* named `<date>_<temp>_<serials>`; each channel is a *run*
named `RF<n>` inside it. Browse it later with
`plottr-inspectr --db mm4250_oneport.db`.

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
from vna_measure import setup_sweep, sweep_settings, measure_s11
from oneport_db_sweep import run_oneport_sweep
```

Then:

```python
setup_sweep(start=1e9, stop=10e9, points=1001, if_bandwidth=1e3, power=-20)
freq, s11 = measure_s11(3)
run_oneport_sweep([1, 3, 5], "20260917", "295K", "SN0001")
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
| `mm4250_oneport.db` | `DEFAULT_DB_NAME` in `oneport_db_sweep.py` | you want a different database filename |
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

## Other things worth knowing

**Connecting resets the switch.** `MM4250.__init__` forces `ALL_OPEN` as
soon as it connects. If you re-run a connect cell mid-session, re-set
your channel afterwards.

**Calibration is not applied.** These are raw measurements. Calibrate the
VNA before sweeping, and note that changing frequency range, point count
or IF bandwidth invalidates the cal -- redo it after `setup_sweep`
changes any of those.

**Updating later.** These are plain copies, not a git clone. If you
change the code in the `mm4250-switch-sweep` repo, re-copy the changed
file(s) over. Only the three files above ever need copying.
