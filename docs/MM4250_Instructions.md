# MM4250 Switch Instructions

QCoDeS driver for the **Menlo Micro MM4250**, a cryogenic SP6T RF MEMS switch (DC-10 GHz, rated to 10 mK). The common port RFC connects to one of RF1-RF6, to an internal 50 ohm load or internal short (for calibration), or to nothing (`ALL_OPEN`). It's driven over USB HID by Menlo's USB HiV Driver Board. Driver: `drivers/MM4250_QCodes_driver.py`.

---

## Two rules

1. **Never switch with signal on the line.** Turn the VNA source off while the switch moves (`safe_set` below). Never switch with DC bias on the line or a multimeter in resistance mode on it.
2. **Cool down with the switch `ALL_OPEN`** (or the driver board unpowered), and don't switch until the fridge is cold: a channel closed during cooldown can freeze closed. Run the stuck check (below) before trusting a channel cold. If one is stuck, stop switching it, warm to 30-80 K (switches recover there), set `ALL_OPEN`, and cool again.

Handling: ESD Class 0, so use a mat and a strap. Plug in the Micro-D cable with the USB unplugged, then plug in the USB (that powers the board). Unplug the USB before removing the switch.

---

## Setup

```
pip install hidapi 
```
QTSF_QCoDeS_env should already have this. In case it does not, do the following. Install `hidapi`, never `hid` (both import as `hid`, but only `hidapi` has `hid.device()`). On the DAQ, pip can say "Requirement already satisfied" and install nothing, because conda has a C library with the same name. If `import hid` still fails:
```python
%pip install --force-reinstall --no-deps hidapi   # then restart the kernel
```

Only one program can hold the board at a time: the Menlo GUI, another kernel, or one of the notebooks below. Close it there first (`sw.close()`, or that notebook's **Close** cell).

---

## In the framework notebook

The main notebook finds the board and makes the switch as `sw`:
```python
from drivers.MM4250_QCodes_driver import MM4250
import hid

mm4250 = len(hid.enumerate(0x04D8, 0xEDFB)) > 0   # is the board plugged in?
if mm4250:
    sw = MM4250("sw")        # connects, and sets ALL_OPEN right away
station = Station(ksvna, sw)
```
Every new `MM4250(...)` moves the switch to `ALL_OPEN`, so don't re-create it in the middle of a measurement.

---

## Commands

```python
sw.channel(3)                               # RFC to RF3 (1-6)
sw.state("INTERNAL_SHORT")                  # by name: "RFC_RF1".."RFC_RF6", "INTERNAL_LOAD", "INTERNAL_SHORT", "ALL_OPEN"
sw.open_all()                               # ALL_OPEN
sw.state.cache.get(get_if_invalid=False)    # the state you last set
sw.open_all(); sw.close()                   # close() alone does NOT open the switch
```
The driver waits 25 ms after every set, so you can measure right away.

**Don't trust `sw.state()` as a read.** The board answers every command and `state()` reads the oldest answer, so it can lag (RF3 set, it says `ALL_OPEN`). `sw.channel()` as a read reports the driver's stored value, which a `state()` read can overwrite with that stale answer. To see where the switch is, use the cache line above or the board LEDs: D1-D4 = RF1-RF4, D6 = RF5, D7 = RF6, D8 = load, D9 = short. D5 is always on (power), not channel 5.

**Switch with the VNA source off:**
```python
def safe_set(position):
    ksvna.output(False)          # RF off while the contacts move
    if isinstance(position, int):
        sw.channel(position)
    else:
        sw.state(position)
    ksvna.output(True)
```

---

## Charlie's measurement notebooks

In `Users/Charlie_Ferrari/`, with step-by-step instructions in its `README.md`. They make their own VNA and switch connections, so close `sw` in the framework notebook first. Each cooldown's setup (what's on each switch port, the fridge lines, the sweep band) is one file, `cooldowns/CD<NN>.json`.

| Notebook | What it does |
|---|---|
| `stuck_check.ipynb` | Checks that every channel opens and closes, plus a check through the fridge's thru line. Run it first at any temperature where you'll take data. Can also hold the thru line open while you use the VNA's own software. |
| `take_data.ipynb` | Internal standards, your channels, standards again. Saves raw and calibrated data to the cooldown's lab database. Plot, recall, export to Touchstone. Can also hold the thru line open while you use the VNA's own software.|
| `analysis.ipynb` | No instruments: calibrate, plot (\|S\| beside a Smith chart), resonance quick look, fridge temperatures, drift, export. |

---

## Calibrating your own data

**What you get:** the reflection coefficient of whatever is on RF n, at RF n's SMA connector, with everything between the VNA and the switch (cables, attenuators, circulator, LNA) taken out.

**How it works.** In the fridge the switch isn't on a VNA port. VNA port 1 goes down the input line to a circulator and into RFC, and the reflection comes back through the circulator and the LNA to port 2. So the switch's reflection shows up in the **raw S21**, which the VNA's own calibration can't handle. But everything in front of RFC acts as one unknown "error box" (leakage straight through the circulator is just its directivity), so a standard one-port open/short/load calibration on S21 removes it. The switch has the three standards built in: `ALL_OPEN` is the open, plus `INTERNAL_SHORT` and `INTERNAL_LOAD`. NIST measured what they look like at each RF port's connector (the "definitions", one set per port, at 295 K and at 3 K). Copies are in `Users/Charlie_Ferrari/ideals_295K/` and `ideals_3K/`.

**Rules for a good calibration:**
- **One session:** standards and channels with the same sweep (band, points, IF bandwidth, power), the same chain, at the same temperature, VNA correction off. If anything in the chain changes (LNA bias, a cable moved), measure the standards again.
- **Standards before and after** your channels. The code averages the two sets, which cancels drift that's linear in time.
- **LNA on and biased**, or nothing comes back.
- **Definitions:** `"3K"` at 4.5 K and below (good down to 25 mK), `"295K"` at room temperature. Neither fits between 4.5 and 250 K.
- Only above **100 MHz**: below that, NIST's three standards aren't distinct.

**Step 1: measure and save.** With the lab's `scripts/switch_vna.py`, so the VNA source is off during every switch move, the VNA's correction is off and port 2 never sources. `ksvna` and `sw` are the framework notebook's instruments:
```python
from scripts import switch_vna as sv

sv.setup_vna(ksvna, 510e6, 610e6, points=2001, power_dbm=-20)   # also: VNA correction off, port 2 source off
folder = "Sweeps/my_cal/20261119_RF4"                           # a new folder for each session
positions = ["ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD", 4,  # standards, your channels,
             "ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD"]     # standards again
try:
    for step, pos in enumerate(positions, 1):
        r = sv.measure(sw, ksvna, pos)                          # S11 + S21, raw
        sv.save_touchstone(r["freq"], r["data"], f"{folder}/{step:02d}_{sv.position_name(pos)}.s2p")
finally:
    sv.all_open(sw, ksvna)                                      # always end ALL_OPEN
```
Files from anywhere else work too (your own code, or a take's or stuck check's `Sweeps/` folder), as long as each file name contains its position: `ALL_OPEN`, `INTERNAL_SHORT`, `INTERNAL_LOAD` or `RF1`-`RF6`.

**Step 2: calibrate.** Either open `analysis.ipynb`, section **2b**, and set `FOLDER` to that folder (`DEFINITIONS` in Setup, `SPARAM = "S21"` in the fridge, `"S11"` on the bench), or in your own notebook:
```python
import sys; sys.path.insert(0, "Users/Charlie_Ferrari")
import analysis, plots
cal = analysis.calibrate_files(analysis.load_folder(folder), "3K", sparam="S21")
plots.plot_mag_smith(analysis.traces_from_calibration(cal))   # |S| beside a Smith chart
analysis.save_calibrated(cal, f"{folder}/calibrated")         # RF4_calibrated_NIST_3K.s1p
gamma, f = cal["corrected"][4], cal["freq"]                     # or use the numpy arrays directly
```

**Check that it worked:** calibrate a known part on a spare channel in the same session. A short should come out near 0 dB at about 180 deg, and a 50 ohm load well below -20 dB. NIST's definitions come from NIST's own unit, not ours; on cooldown 5 our short and loads came back within about 0.04 in |reflection coefficient|.

**On the bench** (RFC cabled straight to a VNA port), the VNA can calibrate directly: import Menlo's `.xkt` kit for the channel (CK018 shared OneDrive, `Instruments/Switches/Menlo/Keysight_Calib_Files/`), run **Cal > Other Cals > Smart Cal > 1 Port Cal** with connector **MM4250**, and set `ALL_OPEN`, `INTERNAL_SHORT`, `INTERNAL_LOAD` at the open, short and load prompts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `MM4250("sw")` fails to open | Board unplugged, or the Menlo GUI / another kernel / one of Charlie's notebooks holds it: close it there |
| `import hid` fails, or `hid` has no `device` | See Setup: `hidapi`, not `hid`, force-reinstalled |
| Command sent, nothing switches, no LED | Jumper J16 (charge pump) missing, or the Micro-D isn't seated |
| `state()` doesn't match what you set | The readback lags: use the cache or the LEDs |
| Switch jumped back to `ALL_OPEN` | A new `MM4250(...)` was made (the connect cell was re-run) |
