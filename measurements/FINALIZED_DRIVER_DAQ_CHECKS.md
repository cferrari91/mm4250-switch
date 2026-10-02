# Finalized driver: DAQ hardware checks

Hardware checks for `drivers/MM4250_finalized.py` (class `MenloMicroMM4250`)
before it goes to QCoDeS/Qcodes_contrib_drivers. The hardware-free tests all
pass, but the new HID code (1 s read timeout, reply echo check, cleanup on a
failed connect, `serial_number=`) has not run on the real board yet. The
2026-09-15 validation was the lab driver, not this one.

## 1. Copy the files to the DAQ

Copy these two files into the DAQ folder
(`C:\Users\QTSF_DAQ\Measuring_scripts\QCoDeS-Measurement-Framework\Users\Charlie_Ferrari\`),
keeping `drivers/` and `tests/` side by side:

- `drivers/MM4250_finalized.py`
- `tests/hardware_check_MM4250_finalized.py`

If they end up somewhere else, pass the driver path explicitly with
`--driver <path to MM4250_finalized.py>`.

## 2. Safety first

- VNA RF output **off**.
- No other kernel or notebook has the switch open (the board takes an exclusive USB handle).
- Both switches move if both are cabled to the board, so only do this when the fridge plan allows it.

## 3. Run it

In a terminal with `QTSF_QCoDeS_env` active:

```
python hardware_check_MM4250_finalized.py
```

It asks you to confirm RF is off (`y`), then runs on its own and leaves the
switch on `ALL_OPEN`.

If you can reach the board's USB cable, add `--unplug`:

```
python hardware_check_MM4250_finalized.py --unplug
```

Partway through, it asks you to unplug the board and then plug it back in. This
checks that the driver raises an error within about 1 s instead of hanging.

## 4. Paste the whole output back to Claude

What each part answers and what changes depending on the answer:

| Output | Question | If yes / if no |
|---|---|---|
| Part 1b-1d | Does the board send a reply to SET and COMMIT? | Yes: read those replies right after each write (cleaner than skipping them later). No: keep the skip loop as a safeguard. |
| Part 1 `get_serial_number_string()` | Does the board have a USB serial number? | No: `serial_number=` and the IDN serial don't do anything useful, so drop them or document it. |
| Part 1 `get_manufacturer_string()` | What manufacturer does the board report? VID 0x04D8 belongs to Microchip. | Decides whether `get_idn()` uses this string or keeps "Menlo Micro" hardcoded. |
| Part 2 state table | Do all 9 states read back correctly, and how long do set and get take? | Last line should be `FAILURES: none`. |
| Part 2 "replies skipped" | Does the driver actually skip any replies on real hardware? | Confirms or rules out the stale-reply question from Part 1. |
| Part 3 (`--unplug`) | Does an unplugged board raise an error instead of hanging? | Should raise within about 1 s. |

## 5. Later: notebook outputs (after the code is adjusted from step 4)

Run `docs/MM4250_finalized_example.ipynb` on the DAQ once so the PR notebook
has saved outputs. It imports from `qcodes_contrib_drivers`, so it needs the
fork branch installed.

1. First run `pip show qcodes_contrib_drivers` on the DAQ and tell Claude what
   it says. Installing the fork replaces whatever version is there, so check
   before touching the lab environment.
2. Same safety rules as step 2: RF off, both switches will move.
3. Send the saved notebook to Claude before committing, so the outputs can be
   checked for DAQ paths and usernames.

After that: fork, branch and PR commands, then the newfragment once the PR has a number.
