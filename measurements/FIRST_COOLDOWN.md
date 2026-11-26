# First cooldown: DUTs plus check standards

The plan for SN0077's first cooldown: the **DUTs** (the resonator and the SNTJ) on
some RF ports, and **check standards** (two flush shorts and a load) on others. At
each temperature, **one** e-cal set measures all of them together (notebook cell
**A4**, into `mm4250_sweeps.db`), and **A5** corrects it and scores the check
standards: how far a known short and load read from what they are is the evidence
for how good the correction is. The code is in `mm4250_sweeps.ipynb` (run its Setup
cells first); this file is the why and what to expect.

**Not this cooldown:** building your own cold definitions NIST-style (3+ different
standards per port, rotated across cooldowns). The check standards verify NIST's
definitions on your switch and give the error bar; they don't replace them.

Each step below says whether it's **certain** (checked against Menlo's app notes,
NIST's data or this repo's code) or **my guess**.

**Plan change (2026-10-22):** probably no stop at 3 K. The stuck check happens on the
way down, in the 30-80 K range, where the nitrogen inside the package can freeze a
switch closed (Menlo and NIST); then straight on to base. Steps 5, 6 and 8 below follow
that; the notebook's run-order table matches.

**Wiring** (`20261016_Wiring_Diagram_testrun.pdf`, slightly outdated: there is one LNA
on line RF2, not two; a newer diagram is coming): VNA port 1 -> fridge line RF4 ->
isolator -> circulator -> RFC, the reflection back up line RF2 through the LNA
to VNA port 2, so the switch's reflection is **S21**. Every set takes 2-port runs,
corrects on S21, and sweeps only `DUT_SPAN` (510-610 MHz): outside the circulator's
band the reflection doesn't come back. That band is the specified one for the
fridge's Quinstar circulator and isolator *(certain: quote S-QUO4203,
QCY-/QCI-M5106101AS, in the shared `Project 8/Amplifiers/`)*.

**Line RF3**: a second attenuated input line wired to one switch channel
(`THRU_CH`). With VNA port 1's cable moved from line RF4 to line RF3 at the top of
the fridge, S21 is a transmission through that channel: the thru check, cell **A2**.

## 0. Decide and write down before anything goes in

- **Which ports.** The port map in cell **A0**: DUTs in `DUTS`, the shorts and load in
  `STD_PORTS`, line RF3's channel in `THRU_CH`, the rest bare. Each channel once; the
  cells that move the switch refuse to run until all three are filled in or if a
  channel is used twice *(certain, tested against the fakes)*. Keep the map the same
  for the whole cooldown.
  - **Where the standards go barely matters in this band.** From NIST's definitions
    for their switch (SN0031), 510-610 MHz: correcting a device on one port with
    another port's 3 K definitions puts a short off by at most 0.028 and a load by at
    most 0.047; the same port's definitions from NIST's two cooldown series differ
    by 0.02-0.044 (short) and 0.008-0.035 (load) *(certain for SN0031, my calculation
    with `ecal.compare_definitions`; that SN0077 behaves the same is my guess)*.
    Closest pairs at 3 K, if you want them: by short 1-5, 2-3, 3-4, 5-6; by load
    5-6, 1-2, 3-4. Over 1-10 GHz the same comparison gives 0.1-0.4, matching
    Menlo's +/-2 dB, +/-40 deg, so this is a low-frequency result.
- **The standards file**, `Sweeps/calibration/standards.json` (copy
  `standards_example.json`, cell **C0**): one entry per physical standard, e.g.
  `short_A`, `short_B`, `load_1`, with type, part number, serial and `offset_mm` 0.
  *Why flush shorts:* NIST's flush short (MOS1) has one definition at all
  temperatures, so a flush short should read -1 at the connector warm and cold
  *(certain, NIST README)*. An offset short would have no ideal value to score
  against, only the bench run.
- **Cooldown label** (`COOLDOWN` in A0), `"CD<n>"` from `COOLDOWN_NUM` in the
  notebook's Setup: Databases cell (this cooldown is the fridge's cooldown 5, so
  `"CD5"`). A4 requires one when there are check standards.
- **Power at the switch.** NIST had about -52 dBm at the switch; -20 dBm at the VNA
  minus line RF4's 36 dB and the filter/isolator/cables gives roughly -58 to -60 dBm
  *(my estimate)*. `MAX_POWER_DBM` stays at 0 dBm. Cell **A3** checks the
  LNA isn't compressed. With one LNF-LNC0.3_14B (input P1dB about -45 dBm at 5 K,
  datasheet *(certain)*), that's roughly 15-20 dB of margin *(my estimate)*; line RF3
  (A2) brings about 8 dB more power, still under it.
- **The LNA's bias**, in `CHAIN` (A0), saved on every A4 run: warm values for the
  warm runs, 5 K values before cooling. Unpowered, the LNA passes about -73 dB at
  560 MHz (DAQ measurement, 2026-10-25), so nothing comes back up line RF2: the warm
  in-fridge runs need it on *(certain from that data)*.
- **Which plate the switch is on**, so `mxc_temp_k` gets the right reading (or
  `TEMP_READER` in A0, once the fridge's driver is connected).
- **Only SN0077 cabled to the driver board**, or remember that one board moves
  both switches *(certain, FINALIZED_DRIVER_DAQ_CHECKS.md)*.

## 1. Bench e-cal reference, 295 K (before it goes in the fridge)

The VNA's cal kit isn't used for this (Charlie, 2026-10-27). Instead, the switch
calibrates itself on the bench: VNA port 1 straight to RFC with a short cable, the
check standards (and any DUTs already on) on their ports exactly as in A0's port
map, VNA correction off, and one e-cal set like A4 but 1-port (S11). Corrected with
NIST's 295 K definitions, that gives each check standard's bench value. Saved to the
calibration database (`cooldown="bench"`).

Notebook cell **C1**. Paste the printed set id into `BENCH` in **A0**.

- *Certain:* the bench and fridge sets share the switch, the connectors and the
  definitions, so A5's "vs bench" number for a check standard is what the fridge
  path (warm) or cooling (cold) changes. Warm in the fridge it should be about zero
  if the e-cal removes cables, circulator and LNA; that is the test of the method
  through the real fridge path.
- *Certain:* it isn't an independent truth, since it went through NIST's
  definitions too. The ideal -1 (short) and 0 (load) stay the absolute check.
- *My reasoning:* same sweep as A4 (`DUT_SPAN`), so the bench covers A5's band; a
  bench that doesn't cover it is skipped with a note rather than extrapolated
  (that part is certain, code + tests).

## 2. Warm, before the fridge is closed: your own definitions on every port

Notebook cells **C2a** / **C2b**. Needs hands on the connectors. C2a puts a cal kit
on every port that has something on it (DUTs and check standards), which gives your
switch's own warm definitions there. C2b prints, port by port, how far NIST's 295 K
definitions are from yours (`ecal.compare_definitions`), then overlays a warm A4 set
corrected three ways.

Why: each check standard can only check **its own** port, but the resonator is on a
different one. If NIST-vs-yours is about the same size on the DUT ports as on the
check-standard ports warm, the cold check-standard scores are a fair stand-in for
the DUT ports *(my guess that warm similarity carries over cold, but it's the best
evidence available, since nobody can touch the connectors cold)*. For scale: NIST's
own switch, same port, two warm cooldowns: up to about 0.01 *(certain, their files)*.

## 3. Mount it, then one warm set in the fridge (295 K)

With the switch mounted and wired exactly as it will be cold, the LNA on at its
room-temperature bias, set `temp_str = "295K"`:

- **A2**, the thru check, once (port 1 on line RF3, then back to RF4): its closed and
  open levels are what "good" looks like cold.
- **A3**, the compression check, once: what "linear" looks like warm.
- **A4** + **A5**: the DUTs and check standards in one set, corrected with the 295 K
  definitions (A5 picks them from `temp_str`) and the check standards scored.
- **B1** + **B2**: the resonator's fit.

This checks the wiring gives enough signal and the method works through the real
fridge path, so the cold sets only add temperature. If the warm check standards
already look bad, fix that before cooling. `run_ecal_set` leaves the switch
`ALL_OPEN` when it finishes, even if it fails *(certain, code + tests)*.

## 4. Cooldown: everything open, nothing switches

- **LNA to its 5 K bias** before cooling, and `CHAIN` in A0 to match *(certain, LNF
  datasheet: "Do not cool down with the power supply set to the room temperature
  values")*. The unit's own values are in its test report.
- **All channels open before and throughout the cooldown** *(certain, Menlo
  APN-0021)*. Cooling with a channel closed can leave it stuck closed. Cell **A6**
  makes sure (source off for the move).
- Before starting: board LEDs show only D5 (the always-on status LED). LEDs D1-D4,
  D6-D9 all off means every channel and both internal standards are open.
- Don't run anything that moves the switch while cooling except the stuck check in
  step 5. That includes every other notebook section, the D1 Quick look included.
  Re-running Connect (`MM4250("switch")`) or Close (`switch.open_all()`) only sends
  ALL_OPEN, so those are safe *(certain, driver code)*.
- Optional, if you want to watch the line while it cools: `measure_2port(vna=ksvna)`
  with no channel measures without touching the switch. *My guess:* useful mainly
  to see the cables and the LNA settle, not the switch itself.

## 5. On the way down, 30-80 K: the stuck check

The nitrogen inside the package freezes somewhere in this range, which is where
Menlo and NIST say a switch can stick closed; finding out here, rather than at base,
means a warm-up is short *(the 30-80 K range is theirs, certain; that checking on
the way down is enough is the plan, not tested)*. Set `temp_str` to the reading
(e.g. `"60K"`), run **A1**, then **A6**, and let it carry on cooling. A1 only
plots and prints; nothing goes into a database.

**A1**, the stuck check: raw, switching to each internal standard and each port in
the port map once, with ALL_OPEN in between.

| What you see | Means |
|---|---|
| Every ALL_OPEN row tiny, every other row clearly bigger | All good |
| A closed position (channel or internal standard) about as small as the ALL_OPEN rows | That position didn't close: stuck open (or not actuating) |
| An ALL_OPEN row right after a position looks like that position (big) | That position didn't open: stuck closed |

*Certain:* the numbers are raw, so they scale with the fridge's loss and gain.
Compare rows with each other, not against a fixed threshold. While cooling the LNA's
gain is still moving, so compare neighbouring rows *(my reasoning)*. That the
circulator and isolator pass enough in band at 30-80 K is *my guess*: they're
specified cold, and A1 only needs some reflection back.

**A2** (optional here) and **A3** are for stops where you'll take e-cal sets: at 3 K
if you stop there, otherwise at base (step 8).

**A2**, the thru check, wherever you run it (move port 1 to line RF3, back afterwards): a transmission
through `THRU_CH`, high closed and at the switch's isolation open. It prints the gap;
under 20 dB (*my guess* at a pass mark) means that channel is stuck or the cable
isn't on line RF3. Compare with the warm run.

**A3**, the compression check, before the first A4 at each temperature (port 1 back
on line RF4): the internal short's |S21| at `DUT_SPAN`'s power and 10 dB below.
S-parameters are ratios, so a linear chain reads the same at both *(certain)*; lower
at the higher power means the LNA is compressed and the e-cal would be off, so lower the power. The 0.2 dB pass mark
is *my guess*.

*Certain (APN-0021):* if something is stuck, don't keep cycling it cold. Warm up;
switches usually come back between 30 K and 80 K. Check them on the way up, set
ALL_OPEN, and cool again with everything open.

## 6. 3 K set (probably skipped this cooldown)

Only if you stop at 3 K: once it's stable and A1-A3 came back clean, set `temp_str = "3K"` and take
**A4**, then **A5** and **B1** + **B2**. In A5's printout, the drift first: the
**calibrated** line is how far the internal standards moved between "before" and
"after", in reflection units at the switch. If that's much more than NIST's few
1e-2, the chain drifted during the set and the result can't be better than that.
Take it again, quicker if needed (fewer points or repeats). The raw line under it is
scaled by the chain's loss and gain *(certain: it's |e01e10| times the calibrated
one, to first order)*, so don't compare that one with NIST.

## 7. What the numbers mean: the error budget

A5 prints, per temperature, everything that goes into the error on a corrected DUT:

| Piece | Where it's printed | What it is |
|---|---|---|
| Noise | repeat spread (and "repeats differ by" per check standard) | the same set's repeats disagreeing |
| Drift during the set | drift, calibrated line | the chain moving while the set ran |
| Definitions (SN0031's on SN0077) | check standards: worst short, worst load | the correction's error at those ports' connectors |
| Carrying it to the DUT ports | C2b, warm | whether DUT ports and check-standard ports differ alike from NIST |
| Temperature | the same check standard at 295 K, 3 K, base | a short should read the same at all three |

How to read the check standards (`ecal.check_standards`):

- **Short:** |G - (-1)|, also as dB and degrees. Against the bench (if `BENCH` is
  set): the phase too, since a flush short barely changes cold.
- **Load:** |G| itself, i.e. the leftover offset (directivity). *Certain:* the load's
  cold value isn't known, so this is an upper limit, not an exact number. The short
  is the main check, the load the secondary one.
- **For the resonator** *(my reasoning, standard one-port error terms)*: the load
  number matters at the bottom of the dip (|G| ~ 0.09 warm), the short number off
  resonance (|G| ~ 1). Q_L comes from the dip's width and should barely care; beta
  and Q_0 depend on the depth and the off-resonance level, so they inherit these
  errors. Turning them into error bars on Q_0, Q_e and beta (refit with perturbed
  data) isn't written yet.

What to compare them with:

| Reference | Number | Source |
|---|---|---|
| NIST's own switch, own definitions, 25 mK, shorts | median within 0.1 dB of 0 dB, worst 0.5 dB | `tests/test_ecal.py` on NIST's data *(certain)* |
| NIST's own switch, same port, two cooldowns, 510-610 MHz | 3 K: short up to 0.02-0.044, load 0.008-0.035; 295 K: about 0.01 | `ecal.compare_definitions` on their tier2 files *(certain, my calculation)* |
| One port's definitions used on another, 510-610 MHz, 3 K | short up to 0.028, load up to 0.047 | same *(certain, my calculation)* |
| 3 K definitions used at other cryo temperatures | +/-0.5 dB, +/-3 deg | Menlo E-Cal procedure v1.1 *(certain)* |
| One channel's definitions used for another, full band | +/-2 dB, +/-40 deg | Menlo E-Cal procedure v1.1 *(certain)* |
| Another switch's definitions (yours is SN0077, NIST's is SN0031) | "data pending" | Menlo *(certain)*. This is the number the check standards measure. |

*My guess:* check standards within NIST's own cooldown-to-cooldown spread (a few
1e-2 cold) mean NIST's definitions fit your switch about as well as they fit theirs
from one cooldown to the next. Much bigger means the device-to-device gap dominates,
and that's the error bar to quote.

A set doesn't have to be in memory: `ecal.list_sets()` lists them and
`cal = ecal.find_set("<set id>")` loads one from a fresh kernel; then A5 as usual.
`ecal.export_corrected(result)` writes .s1p files if something else needs them.

## 8. Base temperature

The switch arrives here `ALL_OPEN` (A6 after the stuck check). Set `temp_str` to the
base label (e.g. `"10mK"`), then **A3** (the LNA is at its cold gain now), then A4 +
A5 and B1 + B2. A5 uses the **3K** definitions (NIST found they hold to 25 mK,
*certain*; `ecal.nist_definitions_for` picks them). A1 again first is cheap if you
want to see every channel move at base *(my suggestion)*; A4 also shows a stuck
channel, as a port that reads like ALL_OPEN.

## 9. Before warming up

**A6** (or the Close cell, or check that the LEDs show only D5), so it goes through
the warmup open too.

## Later cooldowns

If building your own cold definitions comes back: it needs at least 3 different
standards on a port at a temperature *(certain: three unknowns per frequency)*, so
the standards would rotate between ports, or swap to an open or offset short, from
one cooldown to the next. The check-standard runs from this cooldown already count
(they're tagged like any standards run, in the main database):
`ecal.list_standard_runs(default_db_path())` lists them,
`ecal.standards_coverage(default_db_path())` counts them per port and temperature. The solve itself
isn't written.
