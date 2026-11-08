# MM4250 QCoDeS Driver Documentation

QCoDeS driver for the **Menlo Micro MM4250 SP6T cryogenic RF switch**, controlled over USB HID through the Menlo USB HiV Driver Board. Source: `drivers/MM4250_QCodes_driver.py`.

This driver talks directly to the driver board's USB HID interface via the `hidapi` package rather than VISA/serial. Constructing the instrument immediately opens the HID connection and forces the switch to `ALL_OPEN`.

---

##  Requirements

- `pip install hidapi` in the active conda env (`QTSF_QCoDeS_env`) — imports as `import hid`.
- The USB HiV Driver Board physically connected and powered with the SP6T switch's Micro-D cable attached.

---

##  Initialization

```python
from drivers.MM4250_QCodes_driver import MM4250

switch = MM4250("switch")
```

Construction immediately drives the hardware to `ALL_OPEN` — expect the board to click/switch the instant this line runs, not on first use.

If two MM4250 switch modules are wired to the same driver board (via the split Micro-D cable), one `MM4250` instance drives both in parallel, aka whatever state you select applies to both switches at once.

---

##  Parameters

| Name      | Type | Values                                                                 | Description                                              |
|-----------|------|-------------------------------------------------------------------------|------------------------------------------------------------|
| `state`   | Enum | `"ALL_OPEN"`, `"RFC_RF1"`…`"RFC_RF6"`, `"INTERNAL_LOAD"`, `"INTERNAL_SHORT"` | Full switch position, by name                            |
| `channel` | Enum | `1`–`6`                                                                  | Convenience view of `state`, matching the RF1–RF6 SMA ports |

---

## Methods

### `open_all()`
Sets the switch to `ALL_OPEN`.

### `_get_state()`
Reads switch state via board's output buffer over HID

### `get_idn() -> dict`
Returns a static identity dict (vendor/model); the board has no queryable firmware/serial over this interface.

### `close()`
Closes the HID connection. Called automatically by `close_all_instruments(station)`.
Note this does **NOT** open all states!!! Call `open_all()` before.

### Setting vs. reading back

- **`channel(n)` and `state("RFC_RFn")` do the same thing.** `channel` only takes 1-6, so ALL_OPEN and the internal load/short need `state(...)`.
- **The two readbacks differ.** `state()` (no value) reads the board and can return a stale answer (see the Readback note below). `channel()` (no value) never talks to the board: it reports the driver's stored state, and returns `None` with a warning for ALL_OPEN or an internal standard.
- **To check where the switch is,** use `switch.state.cache.get(get_if_invalid=False)` (last state you set, no USB read) or the board LEDs.

## 💡 Example Usage

**Imports cell** — add alongside the other driver imports:
```python
from drivers.MM4250_QCodes_driver import MM4250
```

**Connection-testing cell** — this switch is HID, not VISA/serial, so it needs its own detection check (VID `0x04D8`, PID `0xEDFB`):
```python
import hid

menlo = False
for d in hid.enumerate():
    if d['vendor_id'] == 0x04D8 and d['product_id'] == 0xEDFB:
        menlo = True
        print("✅ Menlo MM4250 driver board is connected")
```

**Instrument-creation cell**:
```python
if menlo:
    switch = MM4250("switch")
    print('MM4250 Switch Instance Created')
```

**Station cell**:
```python
station = Station(ksvna, switch)
```

**Using it**:
```python
station.switch.channel(3)              # selects RFC-RF3
station.switch.channel()               # last channel set (stored in the driver, not read from hardware)
station.switch.state("INTERNAL_SHORT") # or drive by state name directly
```

## More Commands

**All states by name:**
```python
switch.state("RFC_RF1")          # ... "RFC_RF6": connect RFC to that SMA port
switch.state("INTERNAL_LOAD")    # RFC to the internal 50 ohm load
switch.state("INTERNAL_SHORT")   # RFC to the internal short
switch.state("ALL_OPEN")         # everything open (the internal "open" standard)
switch.open_all()                # same as state("ALL_OPEN")
```

**Which gate lines should be on** for the last state you set (from the datasheet's Table 5, no hardware query):
```python
switch.active_hv_lines()         # e.g. ('HV3', 'HV14', 'HV19', 'HV21') for RF1
```

**Identity and snapshot:**
```python
switch.get_idn()                         # static vendor/model dict
switch.print_readable_snapshot()         # parameters from cache (update=True would hit the lagging readback)
```

**Switch with the VNA source off** (avoids hot switching; the driver itself doesn't touch the VNA):
```python
def safe_set(position):
    ksvna.output(False)          # RF off
    if isinstance(position, int):
        switch.channel(position)
    else:
        switch.state(position)   # the driver already waits 25 ms after a set
    ksvna.output(True)           # RF back on

for ch in range(1, 7):
    safe_set(ch)
    # ... measure ...
safe_set("ALL_OPEN")
```

**Reconnect / release:**
```python
switch.close()                   # frees the USB handle (needed before the Menlo GUI or another kernel can connect)
switch = MM4250("switch")        # reconnects, and sets ALL_OPEN again
```
Only one program can hold the board: close the Menlo GUI or any other kernel first.

## Calibrating in the Fridge (Internal Standards)

In our fridge the switch isn't on a VNA port: VNA port 1 -> isolator -> circulator -> RFC, and the reflection comes back through the circulator and LNA to port 2. So the switch's reflection is **S21**, and only inside the circulator's band. The VNA's own Smart Cal can't calibrate that; instead, record raw data and correct it afterwards. The whole chain in front of RFC acts as one error box (leakage through the circulator is its directivity term), so the standard one-port open/short/load correction works on S21.

**1. Record raw S21** (correction off), standards first and again at the end:
```python
ksvna.write('SENS:CORR:CSET:DEAC')   # VNA correction off: raw data
for pos in ["ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD", 1, 2, "ALL_OPEN", "INTERNAL_SHORT", "INTERNAL_LOAD"]:
    safe_set(pos)                    # from More Commands: VNA source off while switching
    # ... save S21 for this position (e.g. a .s2p from the VNA) ...
safe_set("ALL_OPEN")
```
ALL_OPEN is the "open" standard. The second round of standards shows how much the chain drifted during the set; if they moved a lot, take the set again, faster.

**2. Correct offline** with NIST's definitions for the channel your DUT is on (`portN_*` = the internal standards as seen at RF N's connector). Copies of NIST's files are in this repo at `Users/Charlie_Ferrari/ideals_295K/` (warm) and `Users/Charlie_Ferrari/ideals_3K/` (cold); paths below are from the repo root:
```python
import skrf as rf

n = 1                                   # switch channel the DUT is on
d = "Users/Charlie_Ferrari/ideals_295K"   # or ideals_3K cold
freq = rf.Network("dut.s2p").frequency
ideals = [rf.Network(f"{d}/port{n}_{s}_tier2.s1p").interpolate(freq) for s in ("open", "short", "load")]
measured = [rf.Network(f).s21 for f in ("open.s2p", "short.s2p", "load.s2p")]   # S21 as a 1-port
cal = rf.OnePort(ideals=ideals, measured=measured)
cal.run()
dut = cal.apply_cal(rf.Network("dut.s2p").s21)   # calibrated reflection at RF n's connector
```
NIST's own notebook does the same thing: https://github.com/lafefspietz/mm4250-ecal (data: https://github.com/lafefspietz/nist_MM4250_calibration_data_2025).

**How good:** NIST's definitions come from their unit, not ours. Menlo quotes +/-2 dB, +/-40 deg for one channel's definitions used on another, and +/-0.5 dB, +/-3 deg for 3 K definitions at other cryogenic temperatures (E-Cal procedure v1.1); how well they fit our switch is what the check standards on the first cooldown measure. The method has only been checked warm so far, not cold with the LNA in the chain.

**On the bench** (RFC cabled straight to a VNA port), the VNA can do it directly: import Menlo's `.xkt` kit for the channel (CK018 shared OneDrive, `Instruments/Switches/Menlo/Keysight_Calib_Files/`), run **Cal > Other Cals > Smart Cal > 1 Port Cal** with connector **MM4250**, and set `ALL_OPEN`, `INTERNAL_SHORT`, `INTERNAL_LOAD` at the open/short/load prompts. Reuse a saved cal set with `ksvna.write('SENS:CORR:CSET:ACT "name",0')`.

---

## Rules

- **No hot switching:** less than 0.5 V across the contacts when they move. VNA at -20 dBm is fine (Menlo says switching during a VNA sweep is generally safe); turning the source off is extra margin. Never switch with a DMM in resistance/continuity mode on the line (3-7 V seen) or DC bias on.
- **Cool down with ALL_OPEN** and don't switch until the fridge is at temperature. A channel closed during cooldown can freeze closed.
- **Stuck cold?** Stop cycling it. Warm up; switches recover between 30 K and 80 K. Set ALL_OPEN, cool again (Menlo APN-0021).
- **Wait 25 ms** after a set before measuring (the driver does this; raw HID code must).
- **ESD Class 0:** handle on a mat with a strap; never plug/unplug the Micro-D with the board powered.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `MM4250("switch")` / `dev.open` fails | Board unplugged, or the GUI / another kernel holds it. Close it, or `switch.close()` there |
| `import hid` fails | See the hidapi note under Requirements |
| `hid` has no `device` | Wrong package: `pip uninstall hid`, then install `hidapi` |
| Command sent, nothing switches, no LED | Jumper J16 (charge pump) missing, or Micro-D not seated |
| Readback doesn't match what you set | Queued replies; use `state.cache.get(get_if_invalid=False)` or the LEDs |
| Switch jumped back to ALL_OPEN | A new `MM4250(...)` was created (connect cell re-run) |

---

## 🔎 Hardware Validation Notes (09/15/2026)

The real HID wire protocol (`STATE_WIRE_BYTES`) was cross-checked against the datasheet's HV-pin truth table (Table 5) and tested against a mocked HID device before ever touching real hardware. It has since been run against the physical USB HiV Driver Board and validated:

- `ALL_OPEN` on construction correctly de-energizes all channels (confirmed by board LEDs, all off).
- All 6 RF channels (1–6) plus `INTERNAL_LOAD` and `INTERNAL_SHORT` each produce a distinct, correct state — confirmed both by `channel()`/`state()` read-back matching what was set, and by the board's per-state indicator LEDs.
- **Board LED numbering quirk:** the driver board's labeled LEDs (D1–D9) are *not* a plain 1:1 map to channel number. D5 is an always-on status LED unrelated to channel selection (most likely power/HV-enabled or USB-connected), which sits in the middle of the sequence. The actual per-state indicators run:

  `D1 = RF1, D2 = RF2, D3 = RF3, D4 = RF4, D6 = RF5, D7 = RF6, D8 = INTERNAL_LOAD, D9 = INTERNAL_SHORT`

  (D5 is always lit regardless of state — don't read it as "channel 5 selected".) This is a property of the driver board's silkscreen/LED wiring, not the QCoDeS driver — noted here so it doesn't get mistaken for a bug next time someone's watching the lights.

Note: the read-back (`_read_output_buffer_from_hardware`) confirms the commanded byte pattern was written and echoed correctly — it does not independently verify voltages at the HV pins. The LED cross-check above is what closes that gap for a full end-to-end validation.

---

© QTSF Research — QCoDeS Driver for Menlo Micro MM4250
