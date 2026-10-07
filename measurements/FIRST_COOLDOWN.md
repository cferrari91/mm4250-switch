# First cooldown: testing the e-cal against a known short and load

The plan for SN0077's first cooldown: put a **short** and a **load** on two RF
ports, take e-cal sets warm and cold, and check that the e-cal (the switch's
internal standards plus NIST's definitions of them) gives back what those two
terminations really are. The code is section **F** of `mm4250_sweeps.ipynb`
(run its Setup cells first); this file is the why and what to expect.

Each step below says whether it's **certain** (checked against Menlo's app notes,
NIST's data or this repo's code) or **my guess**.

## 0. Decide and write down before anything goes in

- **Which ports.** Short on RF`a`, load on RF`b`, other ports bare. Any ports work:
  `ecal.py` uses each port's own NIST definitions *(certain)*. Use the same dict
  for every set this cooldown: set `TERMS` in notebook cell **F0**.
- **Part numbers** of the short and load, and whether the short is flush or offset.
  *Why:* NIST's flush short (MOS1) has one definition at all temperatures, so a
  flush short should read S11 = -1 at the connector warm and cold *(certain, NIST
  README)*. An offset short is fine too, but then the bench measurement in step 1
  is the only reference for its phase.
- **Wiring and power at the switch.** VNA port 1 reaches RFC through the fridge
  lines, so the reflection has to come back up with enough signal (section D's
  notes: cold coupler plus amplifier, not just one attenuated line). NIST had
  about -52 dBm at the switch. `MAX_POWER_DBM` stays at 0 dBm.
- **Which plate the switch is on**, so `mxc_temp_k` gets the right reading.
- **Only SN0077 cabled to the driver board**, or remember that one board moves
  both switches *(certain, FINALIZED_DRIVER_DAQ_CHECKS.md)*.

## 1. Bench reference, 295 K, VNA-calibrated (before it goes in the fridge)

Measure the short and load straight on VNA port 1's cable with the VNA's own cal,
so there's a "truth" to compare the e-cal against. No switch in the path.
This goes into the database like everything else (the notebook's optional
termination cell only writes a file).

Notebook cell **F1**.

- *Certain:* `CAL_SET` is a 2-port cal, which covers port 1's S11, valid only at
  1 MHz to 10 GHz, 10000 points, 1 kHz.
- *My guess, worth checking:* the short and load have to mate directly with the
  connector the cal's reference plane is at (the cal set is named `..._female`).
  If they need an adapter, the adapter ends up in the reference and the phase
  comparison in step 6 is off by it.

## 2. Mount it, then one warm e-cal set in the fridge (295 K)

With the switch mounted and wired exactly as it will be cold, take a warm set
(it's corrected with the **295K** definitions in F5).

Notebook cell **F2**.

This checks the wiring gives enough signal and the method works through the real
fridge path, so the cold set only adds temperature. If the warm result already
looks bad (step 6), fix that before cooling.

`run_ecal_set` leaves the switch `ALL_OPEN` when it finishes, even if it fails
*(certain, code + tests)*.

## 3. Cooldown: everything open, nothing switches

- **All channels open before and throughout the cooldown** *(certain, Menlo
  APN-0021)*. Cooling with a channel closed can leave it stuck closed.
- Before starting: board LEDs show only D5 (the always-on status LED). D1-D4,
  D6-D9 all off means every channel and both internal standards are open.
- Don't run anything that moves the switch until the target temperature is
  reached **and stable**. That includes notebook sections A-D and the Quick look.
  Re-running Connect (`MM4250("switch")`) or Close (`switch.open_all()`) only sends
  ALL_OPEN, so those are safe *(certain, driver code)*.
- Optional, if you want to watch the line while it cools: `measure_s11(vna=ksvna)`
  with no channel measures without touching the switch. *My guess:* useful mainly
  to see the cables and amplifier settle, not the switch itself.

## 4. At 3 K, stable: stuck check first

The first actuation cold is where a channel could be stuck. The short and load
make this easy to see. Measure raw, switching to each thing once with ALL_OPEN in
between:

Notebook cell **F3**.

How to read it:

| What you see | Means |
|---|---|
| Every ALL_OPEN row tiny, every other row clearly bigger | All good, go to step 5 |
| A closed position (channel or internal standard) about as small as the ALL_OPEN rows | That position didn't close: stuck open (or not actuating) |
| An ALL_OPEN row right after a position looks like that position (big) | That position didn't open: stuck closed |

- *Certain:* the numbers are raw, so they shrink with the fridge's round-trip loss.
  Compare rows with each other, not against a fixed threshold.
- *Certain (APN-0021):* if something is stuck, don't keep cycling it cold. Warm
  up; switches usually come back between 30 K and 80 K. Check them on the way up,
  set ALL_OPEN, and cool again with everything open.

## 5. 3 K e-cal set

Cold, once 3 K is stable and F3 came back clean:

Notebook cell **F4**.

Corrected with the **3K** definitions in F5. Look at the drift printout first: if the
raw standards moved much more than NIST's few 1e-2 between "before" and
"after", the chain drifted during the set and the result can't be better than
that. Take it again, quicker if needed (fewer points or channels).

## 6. Post-processing: does the e-cal give back the short and load?

`check_terminations(result, BENCH)` prints, for each terminated port, the
calibrated |S11| and its difference from the bench run.

Notebook cells **F5**.

Neither `cal_warm` nor `cal_cold` has to be in memory: `ecal.list_sets()` lists every
set, and `ecal.correct_set("<set id>", ideals)` works from a fresh kernel.
`result["terminations"]` comes back from the database.

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
- **Bare ports** (`termination="none"`): an open SMA connector, near 0 dB but not a
  well-defined standard. Sanity check only.
- Warm vs. cold: the same port's corrected short at 295 K and 3 K should be
  nearly the same. A change there that the bench can't explain points at the
  definitions, not the short.

`ecal.plot_ecal(result, show_raw=True)` labels each channel with its termination.
`ecal.export_corrected(result)` writes .s1p files if something else needs them.

## 7. Base temperature

F4 again at base (set `temp` to the base label), still with the **3K** definitions (NIST found they hold
to 25 mK, *certain*). No new stuck check needed unless something changed, but the
cooldown from 3 K to base is a temperature change, so leave everything open while it
happens.

## 8. Before warming up

Switch ALL_OPEN (run the Close cell, or check that the LEDs show only D5), so it
goes through the warmup open too.
