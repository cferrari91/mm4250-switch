# MM4250 switch: lab guide (without this repo's scripts)

_Last updated 2026-10-29_

How to set up, drive, calibrate and cool the Menlo Micro MM4250 safely, for
anyone in QTSF using it with their own code, Menlo's GUI, or the lab's QCoDeS
driver instead of the measurement code in this repo. The two rules that matter
most: **never switch with signal on the line**, and **cool down with every
channel open**.

The MM4250 is a single-pole six-throw (SP6T) MEMS RF switch, DC to 10 GHz,
rated to 10 mK. A common port (RFC) connects to one of six SMA ports (RF1-RF6),
or to an internal load or internal short used for calibration. It is driven at
about 89 V through a 25-pin Micro-D cable from Menlo's USB HiV Driver Board. The
lab has two units, **SN0077** (the first one going in the Maybell fridge) and
**SN0078**, on one driver board.

Everything here comes from Menlo's documents (datasheet v0.4, Driver Board
Instructions v1.1, Cryogenic Operation app note APN-0021, E-Cal procedure v1.1,
AN-0002), NIST's paper and data, or our own bench tests in Sep-Oct 2026. Where
a line is our reasoning rather than a checked fact, it says **(our guess)**.

Contents: [Quick facts](#quick-facts),
[Hardware setup](#hardware-setup),
[Controlling the switch](#controlling-the-switch),
[Safe switching](#safe-switching),
[Calibrated measurements](#calibrated-measurements-with-the-internal-standards),
[Cryogenic use](#cryogenic-use),
[Troubleshooting](#troubleshooting),
[Where to find things](#where-to-find-things)

---

## Quick facts

Typical values from Menlo's datasheet v0.4, at 295 K unless noted.

| Parameter | Value | Note |
| --- | --- | --- |
| Frequency range | DC to 10 GHz | Menlo cal kits cover 1 MHz to 10 GHz |
| Insertion loss | 4 dB at 10 GHz | much less in our 0.5-0.6 GHz band |
| Isolation (off state, and channel to channel) | 40 dB at 10 GHz | |
| RFC return loss | 7 dB at 10 GHz | |
| On resistance | 2.4 ohm at 295 K, 1.2 ohm at 3 K | |
| Channel-to-channel delay matching | 3 ps | |
| Hot-switching limit | 0.5 V across the contacts | see [Safe switching](#safe-switching) |
| Closed-state carry voltage | +/-5 V | |
| Max CW power | 5 W at 6 GHz | |
| Gate voltage (VBB) | 87-91 V, typ. 89 V, about 2 nA | from the driver board |
| Switching time | 25 ms | also the settle time Menlo recommends cold |
| Lifetime | > 1e9 cycles | at 295 K, 1 kHz, no hot switching |
| Actuation power | < 1 uW | no joule heating at the mK stage |
| Temperature range | 10 mK to 295 K | |
| ESD class | Class 0 (most sensitive) | open-state limit +/-150 V on any connector |
| Package | 4.5 x 4.5 x 3.6 cm, gold-plated brass, SMA, 4-hole plate | Micro-D 25-pin female control connector |

---

## Hardware setup

The board must be unpowered whenever the Micro-D cable goes on or comes off,
and jumper J16 must be in or the board never makes 90 V.

1. **Handle it as ESD Class 0.** Open the bags only on an ESD mat with a wrist
   strap. The 150 V limit applies to static too.
2. **Check the driver board jumpers.** J16 enables the charge pump (no 90 V
   without it). Jumpers on J20 and J27 tie the ground pins of the two Micro-D
   connectors (J18 and J19) to the board's ground plane.
3. **Connect the switch to the board with USB unplugged.** Micro-D 25-pin
   cable, board connector (male) to switch connector (female).
4. **Plug in the mini-USB cable.** That powers the board. To power down, unplug
   USB. Never unplug the switch while the board is powered.
5. **Check the LEDs.** With everything open only D5 should be lit (always-on
   status LED). See the map below.

**Board LEDs are not 1:1 with channels** (checked on our board, 2026-09-15):

| LED | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Meaning | RF1 | RF2 | RF3 | RF4 | always on | RF5 | RF6 | internal load | internal short |

Only D5 lit means every channel and both internal standards are open.

**Two switches, one board.** The board has two Micro-D outputs (J19 = bus A,
J18 = bus B), and Menlo also sells a split cable. Menlo's GUI, Menlo's example
scripts and the lab driver all send the same pattern to both buses, so both
switches always sit in the same state. The protocol does allow a different
pattern per bus (first 3 bytes bus A, last 3 bus B), but we have not tried
independent control.

**In the fridge**, the switch sits on the mixing-chamber plate in the lab's
holder, and the driver board stays at room temperature. Menlo says gate drivers
belong outside the cryostat, with the gate lines brought in on
low-thermal-conductance wiring.

---

## Controlling the switch

There are three ways in: Menlo's Windows GUI, the lab's QCoDeS driver, or raw
USB HID from your own code. Only one program can hold the board at a time, so
close the GUI (or the other Jupyter kernel) before connecting from Python.

### Menlo EVK Control (GUI)

Installer: `Menlo_Micro_EVK_Control_Setup_2.0.21.zip`, in the shared Menlo
folder under `Driver Board/` (also on the Menlo Support Portal). Open it, click
**Scan USB Ports**, open **4250**, then click a channel to close it. **ALL OFF**
opens everything; **INPUT ECAL SHORT** and **INPUT ECAL LOAD** select the
internal standards. Good for a quick bench check.

### Lab QCoDeS driver

`drivers/MM4250_QCodes_driver.py` in the QCoDeS-Measurement-Framework repo
(same code as `drivers/MM4250_QCodes_driver.py` here; full reference in
[`MM4250_Instructions.md`](MM4250_Instructions.md)). It needs `hidapi`.

```python
from drivers.MM4250_QCodes_driver import MM4250

switch = MM4250("switch")        # connects AND sets ALL_OPEN immediately
switch.channel(3)                # RFC-RF3 (waits 25 ms after the command)
switch.state("INTERNAL_SHORT")   # or "INTERNAL_LOAD", "ALL_OPEN", "RFC_RF1"...
switch.open_all()
switch.close()                   # release the USB handle
```

- **Don't trust the readback.** The board replies to every command, and this
  driver's `state()` / `channel()` *get* reads the oldest queued reply, so it
  can lag (for example: RF3 set, readback says ALL_OPEN). Setting works fine.
  Check the LEDs or use `switch.state.cache.get()` instead.
- **Constructing it switches the hardware** to ALL_OPEN, so re-running a
  connect cell mid-measurement drops your channel.
- **Installing hidapi on the DAQ:** pip can say "Requirement already
  satisfied" while installing nothing, because conda's C library has the same
  name. Check with `importlib.util.find_spec("hid")` (None = missing), then
  `%pip install --force-reinstall --no-deps hidapi` and restart the kernel.
  Install `hidapi`, never `hid`: both import as `hid`, but only `hidapi` has
  `hid.device()`.
- `switch.channel(n)` does not turn the VNA source off. Do that yourself (see
  [Safe switching](#safe-switching)).

### Raw HID (your own code)

The board is a USB HID device, VID `0x04D8`, PID `0xEDFB`. Every message starts
with a blank `0x00` byte, then a command byte. Setting a state is two writes:
load the buffer, then apply it.

| Command | Send | Board replies (8 bytes) |
| --- | --- | --- |
| Set output buffer | `0x00, 0x00, b1..b6` | echo of the buffer: `0x00, b1..b6, 0x00` |
| Read output buffer | `0x00, 0x01` | `0x01, b1..b6, 0x00` |
| Apply buffer to outputs | `0x00, 0x02` | `0x02`, then zeros |

| State | b1..b6 (same 3 bytes for bus A and bus B) | Gate lines (HV#) |
| --- | --- | --- |
| ALL_OPEN | `00 00 00 00 00 00` | none |
| RFC-RF1 | `14 20 04 14 20 04` | HV3, HV14, HV19, HV21 |
| RFC-RF2 | `04 10 44 04 10 44` | HV3, HV7, HV13, HV19 |
| RFC-RF3 | `01 01 82 01 01 82` | HV2, HV8, HV9, HV17 |
| RFC-RF4 | `00 82 82 00 82 82` | HV2, HV8, HV10, HV16 |
| RFC-RF5 | `02 04 82 02 04 82` | HV2, HV8, HV11, HV18 |
| RFC-RF6 | `0C 40 04 0C 40 04` | HV3, HV15, HV19, HV20 |
| Internal load | `00 00 01 00 00 01` | HV1 |
| Internal short | `00 08 00 00 08 00` | HV12 |

```python
import hid, time

dev = hid.device()
dev.open(0x04D8, 0xEDFB)

def set_state(b6):
    dev.write([0x00, 0x00, *b6]); dev.read(8)   # set buffer, discard its echo
    dev.write([0x00, 0x02]);      dev.read(8)   # apply, discard its reply
    time.sleep(0.025)                           # let the contacts settle

set_state([0x00] * 6)                           # start ALL_OPEN
set_state([0x01, 0x01, 0x82, 0x01, 0x01, 0x82]) # RFC-RF3
dev.write([0x00, 0x01]); print(dev.read(8))     # readback: [1, 1, 1, 130, 1, 1, 130, 0]
set_state([0x00] * 6)
dev.close()
```

The replies to set and apply were seen on our board on 2026-10-22 (Menlo's API
note says every command gets one). If you don't read them, your next read
returns a stale reply instead of the buffer. Menlo's own example scripts
(`Driver Board/MM4250_Driver_Programming_Package_202511/`) skip this and also
don't open all channels first. The snippet above follows the same write/read
sequence as `drivers/MM4250_finalized.py`, which passed a hardware check on the
DAQ (2026-10-22); the snippet itself has not been run.

---

## Safe switching

The contacts must see less than 0.5 V when they open or close. Breaking this
rule doesn't kill the switch on the spot; it erodes the contacts and quietly
eats into the 1e9-cycle lifetime, with nothing in the data to warn you.

- **VNA power.** Menlo says switching with a VNA sweep running is generally
  safe. For scale: 0.5 V peak into a matched 50 ohm load is +4 dBm, and a
  reflecting port (open, short, resonator) can see up to about twice the
  voltage, so roughly -2 dBm (our arithmetic). We run -20 dBm and cap at
  0 dBm. For extra margin our code turns the VNA output off for each move;
  doing the same in your code costs nothing (`ksvna.output(False)`, switch,
  `ksvna.output(True)`).
- **Multimeters.** Never measure resistance or continuity across the switch
  while it changes state. Menlo has seen DMMs put 3-7 V on the leads in those
  modes.
- **DC on the lines.** Keep any DC bias off (below 0.5 V) whenever the state
  changes. A closed channel can carry up to +/-5 V.
- **Wait 25 ms** after a command before trusting the RF state. The lab driver
  already waits; raw-HID code has to do it itself.
- **Power and cables.** Never plug or unplug the Micro-D with the board on,
  and don't touch the gate pins: they are at 89 V and the part is ESD Class 0.
- **Cold:** cool down with everything open, and don't keep cycling a switch
  that seems stuck (see [Cryogenic use](#cryogenic-use)).

---

## Calibrated measurements with the internal standards

The switch's internal open (ALL_OPEN), short and load let you calibrate at the
RF connectors without touching cables, but you need someone's *definitions* of
those standards. The usable ones are NIST's, from a different unit (SN0031), so
expect about the accuracies in the table below on our switches. There are two
routes.

### Route 1: VNA Smart Cal with Menlo's cal kits

Menlo's Keysight cal kit files (`.xkt`, one per channel, at 295 K and 3 K) are
in `Keysight_Calib_Files/` in the shared Menlo folder. They are generic, not
made for our serial numbers.

1. Import: **Cal > Cal Sets & Cal Kits > Cal Kit... > Import...**, pick the kit(s).
2. Set the sweep (inside 1 MHz to 10 GHz), then **Cal > Main > Other Cals >
   Smart Cal...** > **1 Port Cal**, DUT connector **MM4250**, the kit for the
   channel you'll measure.
3. Measure the three standards when prompted: switch to ALL_OPEN (open), then
   the internal short, then the internal load.
4. Save the cal set, close the channel you calibrated for, and the VNA now
   reads calibrated S11 at that channel's connector.

A 2-port version needs two MM4250s back to back with a thru on one channel
(Menlo E-Cal procedure v1.1, pp. 11-18). One Smart Cal covers one channel:
switching to another means a new cal with that channel's kit, unless the
+/-2 dB, +/-40 deg channel-to-channel error is acceptable.

### Route 2: record raw, correct afterwards

Record raw S11 (VNA correction off) for ALL_OPEN, short, load and your
channels, then correct offline with NIST's definitions. Menlo points to NIST's
notebook, [lafefspietz/mm4250-ecal](https://github.com/lafefspietz/mm4250-ecal);
the full data set is
[nist_MM4250_calibration_data_2025](https://github.com/lafefspietz/nist_MM4250_calibration_data_2025).
This is a standard one-port open/short/load correction, so scikit-rf's
`OnePort` does the same job. Measure the standards both before and after your
channels: the difference tells you how much the line drifted.

### How good is it?

| Situation | Typical error | Source |
| --- | --- | --- |
| 3 K definitions used at other cryo temperatures (down to mK) | +/-0.5 dB, +/-3 deg | Menlo E-Cal v1.1 |
| One channel's definitions used on another, full band | +/-2 dB, +/-40 deg | Menlo E-Cal v1.1 |
| NIST's switch, same port, two cooldowns, 510-610 MHz | 0.02-0.04 (short), 0.01-0.04 (load), in reflection units | our calculation from NIST's files |
| NIST's definitions on a different switch (ours) | unknown (Menlo: "data pending") | being measured with check standards on the first cooldown |

At 295 K this works on our hardware: a resonator on SN0077's RF1, corrected
with NIST's 295 K definitions, matched the same resonator measured alone with a
normal VNA cal (dip 548.80 vs 548.65 MHz, loaded Q 39.9 vs 39.6; 2026-10-08).

**Gotchas.** The cal is only valid at the frequencies, points and IF bandwidth
it was taken at. Use the 295 K files warm and the 3 K files cold (NIST found
3 K holds to 25 mK). With a circulator in the line (our fridge setup), the
switch's reflection comes back as S21, not S11, and only inside the
circulator's band (510-610 MHz).

---

## Cryogenic use

Cool down with every channel open (gate lines at 0 V) and switch only once the
fridge is at temperature and stable. A channel that is closed while cooling can
freeze closed.

### Cooldown

1. Before starting, set ALL_OPEN and check the LEDs: only D5 lit.
2. Leave the board at ALL_OPEN (or unpowered) for the whole cooldown. Menlo
   told us the board holds the gate lines at ground, so either is fine. Don't
   run anything that moves the switch on the way down, except a deliberate
   stuck check.
3. At temperature and stable, start switching. Menlo recommends the 25 ms
   settle time cold, since the gate lines charge more slowly through fridge
   wiring.
4. Before warming up, set ALL_OPEN again.

### Stuck switches

- **Why it happens.** The units are sealed with air inside. NIST and Menlo
  told us the nitrogen can freeze and hold a switch closed, and that this can't
  be fixed on the current units (NIST and Menlo are working on helium-filled
  ones). Rarely, a switch also sticks open even after a correct cooldown.
- **It is not permanent.** Stuck switches recover on warming, usually between
  30 K and 80 K, so you don't need to go all the way to room temperature.
- **Recovery (APN-0021):** stop cycling it cold, start warming, toggle the
  gate as it warms to find where it responds again, set ALL_OPEN, then cool
  again with everything open.
- **Check on the way down.** Our plan is to check every position once in the
  30-80 K range while cooling, so a problem costs a short warm-up instead of a
  full one (our plan, not yet tested).
- **How to check without special code:** step through each position with
  ALL_OPEN in between and look at raw |S|. Closed positions should look
  clearly different from ALL_OPEN. A position that looks like ALL_OPEN didn't
  close; an ALL_OPEN that still looks like the position before it didn't open.
  Compare neighbouring positions with each other, not with a fixed number:
  raw values scale with the fridge's loss and amplifier gain.

### Grounding and floating nodes

Menlo's rule (AN-0002) is that every RF node should have a DC path to ground,
so no charge builds up on an isolated line and then discharges through the
contacts when they close. One path per node is enough. In order of preference:

1. A DC path through the circuit itself (a DC-coupled load or short, a bias
   tee).
2. A large inductor to ground (practical at GHz).
3. A large resistor to ground, 100 kohm to 10 Mohm; the high end for
   resonators and other high-Q nodes. NIST suggested the same: a large-value
   surface-mount resistor from centre conductor to ground barely changes the
   microwave behaviour.

In practice, Menlo and NIST later told us floating nodes are not a big issue
compared with the nitrogen problem. Our resonator has no DC path to ground and
we accepted that for the first cooldown.

### Bias tees for diagnosis

Menlo recommends bias tees on the RF lines at the room-temperature boundary, so
you can check a switch's DC continuity without warming up. Use a
high-impedance voltage measurement, not a meter's resistance or continuity
mode (those push current into the cold stage and can hot-switch the contacts),
and remove the DC before changing state.

---

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `hid.device().open(...)` fails, or `MM4250(...)` can't connect | Board unplugged, or the GUI or another kernel already holds it. Close the other program. `hid.enumerate()` should list VID 0x04D8 / PID 0xEDFB |
| `import hid` fails although pip says hidapi is installed | The DAQ hidapi trap: force-reinstall `hidapi` and restart the kernel (see [Lab QCoDeS driver](#lab-qcodes-driver)) |
| `hid` imports but `hid.device` doesn't exist | The `hid` package was installed instead of `hidapi`. Uninstall it, install `hidapi` |
| Commands go through but no LED changes and nothing switches | J16 jumper missing (no 90 V), or the Micro-D cable isn't seated |
| Readback disagrees with what you set | Unread replies queued on the board. Trust the LEDs or `state.cache.get()`; in raw code read after every write |
| Channel suddenly back to ALL_OPEN | Something created a new driver instance (connect cell re-run) |
| D5 always lit | Normal: status LED |
| Both switches change together | Expected with one board: both buses get the same pattern |
| Calibrated trace looks wrong | Cal taken at different sweep settings, kit for another channel, 295 K files used cold (or the reverse), or VNA fixturing/port extensions left on |
| No reflection back through the fridge outside 510-610 MHz | The circulator's band, not the switch |
| A position reads like ALL_OPEN cold, or ALL_OPEN reads like a closed position | Stuck switch: stop cycling, follow [Stuck switches](#stuck-switches) |

---

## Where to find things

- **Menlo documents:** the shared CK018 folder, `Instruments/Switches/Menlo/`:
  datasheet v0.4, Driver Board Instructions v1.1, Cryogenic Operation app note
  (APN-0021), E-Calibration Procedure v1.1, AN-0002 (floating nodes), pinout
  sheet, cable assembly diagram, `Keysight_Calib_Files/`, and `Driver Board/`
  (GUI installer, programming package, board schematics). Newer versions on
  the [Menlo Support Portal](https://menlomicro.com) (request access from the
  login page).
- **NIST paper:** L. F. Spietz et al., "Cryogenic RF MEMS Switch With
  Electronic Calibration Capability," IEEE J. Microwaves 6(1), 251-262 (2026),
  [doi:10.1109/JMW.2025.3638273](https://doi.org/10.1109/JMW.2025.3638273).
  Data: [nist_MM4250_calibration_data_2025](https://github.com/lafefspietz/nist_MM4250_calibration_data_2025).
- **This repo:** [`MM4250_Instructions.md`](MM4250_Instructions.md) (lab
  driver reference), `../measurements/LAB_SETUP.md` and
  `../measurements/FIRST_COOLDOWN.md` (the scripted route and the first
  cooldown plan).
- **Questions:** Charlie Ferrari (QTSF) for how the lab has it set up;
  support@menlomicro.com for the hardware.
