# MM4250 QCoDeS Driver Documentation

QCoDeS driver for the **Menlo Micro MM4250 SP6T cryogenic RF switch**, controlled over USB HID through the Menlo USB HiV Driver Board. Source: `drivers/MM4250_QCodes_driver.py`.

Unlike the other drivers in this framework, this one talks directly to the driver board's USB HID interface (via the `hidapi` package) rather than VISA/serial — there is no address to pass in, and no simulated mode. Constructing the instrument immediately opens the HID connection and forces the switch to `ALL_OPEN`.

---

## 🔧 Requirements

- `pip install hidapi` in the active conda env (`QTSF_QCoDeS_env`) — imports as `import hid`.
- The USB HiV Driver Board physically connected and powered (jumper J16 installed for the charge pump), with the SP6T switch's Micro-D cable attached.

---

## 🔧 Initialization

```python
from drivers.MM4250_QCodes_driver import MM4250

switch = MM4250("switch")
```

Construction immediately drives the hardware to `ALL_OPEN` — expect the board to click/switch the instant this line runs, not on first use.

If two MM4250 switch modules are wired to the same driver board (via the split Micro-D cable), one `MM4250` instance drives both in parallel — whatever state you select applies to both switches at once.

---

## 📐 Parameters

| Name      | Type | Values                                                                 | Description                                              |
|-----------|------|-------------------------------------------------------------------------|------------------------------------------------------------|
| `state`   | Enum | `"ALL_OPEN"`, `"RFC_RF1"`…`"RFC_RF6"`, `"INTERNAL_LOAD"`, `"INTERNAL_SHORT"` | Full switch position, by name                            |
| `channel` | Enum | `1`–`6`                                                                  | Convenience view of `state`, matching the RF1–RF6 SMA ports |

---

## 🛠 Methods

### `open_all()`
Sets the switch to `ALL_OPEN`.

### `active_hv_lines() -> tuple`
Returns the HV control lines (e.g. `("HV3", "HV14", "HV19", "HV21")`) the driver believes are currently energized for the last-commanded state, per the datasheet's Table 5 (Applied HV Control vs. RF Switch States).

### `get_idn() -> dict`
Returns a static identity dict (vendor/model); the board has no queryable firmware/serial over this interface.

### `close()`
Closes the HID connection. Called automatically by `close_all_instruments(station)`.

---

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
station.switch.channel()               # reads back current channel from hardware
station.switch.state("INTERNAL_SHORT") # or drive by state name directly
```

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
