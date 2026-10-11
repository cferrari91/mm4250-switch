# First cooldown: DUTs plus a known short and load

The plan for SN0077's first cooldown: the **DUTs** (the resonator, ...) on some RF
ports, and a known **short** and **load** on two others. At each temperature, two
e-cal sets: one on the DUT ports (notebook section **F**, into `mm4250_sweeps.db`)
and one on the standards ports (section **G**, into its own database,
`Sweeps/calibration/mm4250_calibration.db`). The standards' set checks that the
e-cal (the switch's internal standards plus NIST's definitions of them) gives back
what a short and a load really are, and is the first raw data for building your
own definitions later. The code is in `mm4250_sweeps.ipynb` (run its Setup cells
first); this file is the why and what to expect.

Each step below says whether it's **certain** (checked against Menlo's app notes,
NIST's data or this repo's code) or **my guess**.

**Wiring:** the fridge uses the circulator wiring (VNA port 1 -> circulator -> RFC,
the reflection back up through the amplifier to VNA port 2), so the switch's
reflection is **S21**. Every F and G set takes 2-port runs and corrects on S21, and
sweeps only `F_SPAN` / `G_SPAN` (default 490-630 MHz): outside the circulator's band
the reflection doesn't come back and the correction is meaningless. That band is *my
estimate* from the circulator's part number; check it against the datasheet if you can.

## 0. Decide and write down before anything goes in

- **Which ports.** DUTs in `DUTS` (cell **F0**), the short and load in `STD_PORTS`
  (cell **G0**), the rest bare. Any ports work: `ecal.py` uses each port's own NIST
  definitions *(certain)*. Keep both dicts the same for the whole cooldown.
- **The standards file**, `Sweeps/calibration/standards.json` (copy
  `standards_example.json`): one entry per physical standard, with type, part
  number, serial and offset. *Why:* NIST's flush short (MOS1) has one definition at
  all temperatures, so a flush short should read S11 = -1 at the connector warm and
  cold *(certain, NIST README)*. An offset short is fine too, but then the bench
  measurement in step 1 is the only reference for its phase.
- **Cooldown label** (`COOLDOWN` in F0, `cooldown=` in G2), e.g. `"CD1"`. G2
  requires it; it's how standards runs from different cooldowns are told apart.
- **Wiring and power at the switch.** VNA port 1 reaches RFC through the fridge
  lines, so the reflection has to come back up with enough signal (cold coupler
  plus amplifier, not just one attenuated line). NIST had about -52 dBm at the
  switch. `MAX_POWER_DBM` stays at 0 dBm.
- **Which plate the switch is on**, so `mxc_temp_k` gets the right reading (or
  `TEMP_READER` in F0, once the fridge's driver is connected).
- **Only SN0077 cabled to the driver board**, or remember that one board moves
  both switches *(certain, FINALIZED_DRIVER_DAQ_CHECKS.md)*.

## 1. Bench reference, 295 K, VNA-calibrated (before it goes in the fridge)

Measure the short and load straight on VNA port 1's cable with the VNA's own cal,
so there's a "truth" to compare the e-cal against. No switch in the path. Saved
to the calibration database, tagged `cal_role="bench"`.

Notebook cell **G3a** (`run_bench_standards`). Paste its printout into `BENCH` in G0.

- *Certain:* `CAL_SET` is a 2-port cal, which covers port 1's S11, valid only at
  1 MHz to 10 GHz, 10000 points, 1 kHz. G3a sweeps exactly that, so the cal isn't
  interpolated.
- *My guess, worth checking:* the short and load have to mate directly with the
  connector the cal's reference plane is at (the cal set is named `..._female`).
  If they need an adapter, the adapter ends up in the reference and the phase
  comparison in step 7 is off by it.

## 2. Optional, warm only: your own definitions from a cal kit

Notebook cells **G1a** / **G1b**. Needs hands on the DUT ports' connectors, so do it
before the fridge is closed up. It gives your switch's own definitions on the DUT
ports, warm, and G1b shows how far NIST's are from them on a warm F2 set. *My
guess:* if they agree warm, NIST's are a reasonable stand-in cold; if they don't,
that difference is roughly what the cold result could be off by.

## 3. Mount it, then one warm pair of sets in the fridge (295 K)

With the switch mounted and wired exactly as it will be cold, set `temp_str =
"295K"` and take both sets:

Notebook cells **F2** (DUT ports) and **G2** (standards ports).

Score them with the **295K** definitions: **F3** for the DUTs, **G3b** for the
standards (step 7). This checks the wiring gives enough signal and the method works
through the real fridge path, so the cold sets only add temperature. If the warm
result already looks bad, fix that before cooling.

`run_ecal_set` leaves the switch `ALL_OPEN` when it finishes, even if it fails
*(certain, code + tests)*.

## 4. Cooldown: everything open, nothing switches

- **All channels open before and throughout the cooldown** *(certain, Menlo
  APN-0021)*. Cooling with a channel closed can leave it stuck closed. Cell **F4**
  makes sure (source off for the move).
- Before starting: board LEDs show only D5 (the always-on status LED). D1-D4,
  D6-D9 all off means every channel and both internal standards are open.
- Don't run anything that moves the switch until the target temperature is
  reached **and stable**. That includes every notebook section and the Quick look.
  Re-running Connect (`MM4250("switch")`) or Close (`switch.open_all()`) only sends
  ALL_OPEN, so those are safe *(certain, driver code)*.
- Optional, if you want to watch the line while it cools: `measure_2port(vna=ksvna)`
  with no channel measures without touching the switch. *My guess:* useful mainly
  to see the cables and amplifier settle, not the switch itself.

## 5. At 3 K, stable: stuck check first

The first actuation cold is where a channel could be stuck. Measure raw, switching
to each internal standard and each port once with ALL_OPEN in between. Set `CHECK`
to the DUT ports plus the standards ports (the comment in the cell shows how).

Notebook cell **F1**.

How to read it:

| What you see | Means |
|---|---|
| Every ALL_OPEN row tiny, every other row clearly bigger | All good, go to step 6 |
| A closed position (channel or internal standard) about as small as the ALL_OPEN rows | That position didn't close: stuck open (or not actuating) |
| An ALL_OPEN row right after a position looks like that position (big) | That position didn't open: stuck closed |

- *Certain:* the numbers are raw, so they shrink with the fridge's round-trip loss.
  Compare rows with each other, not against a fixed threshold.
- *Certain (APN-0021):* if something is stuck, don't keep cycling it cold. Warm
  up; switches usually come back between 30 K and 80 K. Check them on the way up,
  set ALL_OPEN, and cool again with everything open.

## 6. 3 K pair of sets

Cold, once 3 K is stable and F1 came back clean, set `temp_str = "3K"` and take
**F2** and **G2** again. Look at each drift printout first: if the raw standards
moved much more than NIST's few 1e-2 between "before" and "after", the chain
drifted during the set and the result can't be better than that. Take it again,
quicker if needed (fewer points or channels).

## 7. Post-processing: does the e-cal give back the short and load?

Notebook cell **G3b**: `ecal.compare_to_bench(result_std, BENCH)` prints, for each
standard's port, the calibrated |S11| and its difference from the bench run.
Corrected with the **3K** definitions cold, **295K** warm. **F3** does the DUTs.

Neither set has to be in memory: `ecal.list_sets()` lists the DUT sets and
`ecal.list_sets(default_cal_db_path())` the standards sets; `ecal.find_set("<set
id>", db_path)` loads one from a fresh kernel. `ecal.list_standard_runs()` lists every
standard's run with its port, temperature, cooldown and time.

What to compare it with:

| Reference | Number | Source |
|---|---|---|
| NIST's own switch, own definitions, 25 mK, shorts | median within 0.1 dB of 0 dB, worst 0.5 dB | `tests/test_ecal.py` on NIST's data *(certain)* |
| 3 K definitions used at other cryo temperatures | +/-0.5 dB, +/-3 deg | Menlo E-Cal procedure v1.1 *(certain)* |
| One channel's definitions used for another channel | +/-2 dB, +/-40 deg | Menlo E-Cal procedure v1.1 *(certain)* |
| Another switch's definitions (yours is SN0077, NIST's is SN0031) | "data pending" | Menlo *(certain)*. This is the number you're measuring. |

- **Short:** should be near 0 dB. Against the bench: the phase too, since a short
  barely changes cold. *My guess:* expect worse than NIST's 0.1 dB because of the
  device-to-device gap; how much worse is the result.
- **Load:** look at the vector difference against the bench, not dB or phase
  (a near-zero reflection has no meaningful phase). *My guess:* the load's own
  value may shift cold, so a warm-to-cold change in the load alone doesn't prove the
  e-cal is off. The short is the main check, the load a secondary one.
- **Score with NIST's definitions** (or G1's). Definitions built later from these
  same standards would reproduce them by construction, which proves nothing
  *(certain: that's how the solve works)*.
- Warm vs. cold: the same port's corrected short at 295 K and 3 K should be
  nearly the same. A change there that the bench can't explain points at the
  definitions, not the short.

`ecal.plot_ecal(result, show_raw=True)` labels each channel with its DUT or standard.
`ecal.export_corrected(result)` writes .s1p files if something else needs them.

## 8. Base temperature

**F4** first (ALL_OPEN: the cooldown from 3 K to base is a temperature change).
Then F2 and G2 again at base, with `temp_str` set to the base label (e.g.
`"25mK"`), still corrected with the **3K** definitions (NIST found they hold to
25 mK, *certain*). No new stuck check needed unless something changed.

## 9. Before warming up

**F4** (or the Close cell, or check that the LEDs show only D5), so it goes through
the warmup open too.

## Later cooldowns

`ecal.standards_coverage()` (cell **G4**) shows which standards each port has seen at
each temperature. Building a port's own definitions needs at least 3 different
standards on it at that temperature *(certain: three unknowns per frequency)*, so
move the standards to different ports, or swap in an open or an offset short, from
one cooldown to the next. The solve itself isn't written yet.
