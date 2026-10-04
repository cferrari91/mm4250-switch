"""Hardware checks for drivers/MM4250_finalized.py, run on the lab DAQ.

Not a pytest file (pytest only collects test_*.py). It moves the switch, so
turn the VNA RF output off first. Run from a terminal in the env that has
hidapi:

    python hardware_check_MM4250_finalized.py
    python hardware_check_MM4250_finalized.py --driver C:\\path\\to\\MM4250_finalized.py
    python hardware_check_MM4250_finalized.py --unplug   # also run the unplug test

Paste the whole output back to Claude. Part 1 talks to the board with raw
hidapi calls (no driver) to answer protocol questions; part 2 runs the
driver itself.
"""

import argparse
import importlib.util
import logging
import sys
import time
from pathlib import Path

VID, PID = 0x04D8, 0xEDFB
STATES = {
    "ALL_OPEN": [0x00] * 6,
    "RFC_RF3": [0x01, 0x01, 0x82, 0x01, 0x01, 0x82],
}


def hexes(data):
    return " ".join(f"0x{b:02X}" for b in data) if data else "(nothing)"


def drain(dev, label):
    """Read until nothing arrives within 200 ms; print what was queued."""
    got = []
    while True:
        r = dev.read(8, 200)
        if not r:
            break
        got.append(r)
    print(f"  {label}: {len(got)} queued report(s)")
    for r in got:
        print(f"    {hexes(r)}")
    return got


def part0_environment():
    print("=== Part 0: environment")
    print(f"  python {sys.version.split()[0]}")
    import hid

    print(f"  hid module: {getattr(hid, '__file__', '?')}")
    print(f"  has hid.device (hidapi, correct): {hasattr(hid, 'device')}")
    if hasattr(hid, "version_str"):
        print(f"  hidapi version: {hid.version_str()}")
    return hid


def part1_raw_protocol(hid):
    print("\n=== Part 1: raw HID protocol (no driver)")
    boards = hid.enumerate(VID, PID)
    print(f"  boards found: {len(boards)}")
    for b in boards:
        print(
            f"    serial={b.get('serial_number')!r} product={b.get('product_string')!r} "
            f"manufacturer={b.get('manufacturer_string')!r} path={b.get('path')!r}"
        )

    dev = hid.device()
    dev.open(VID, PID)
    try:
        for getter in ("get_manufacturer_string", "get_product_string", "get_serial_number_string"):
            try:
                print(f"  {getter}() = {getattr(dev, getter)()!r}")
            except Exception as err:
                print(f"  {getter}() raised {type(err).__name__}: {err}")

        drain(dev, "1a. right after open")

        print("  1b. SET buffer to ALL_OPEN, then wait for a reply")
        n = dev.write([0x00, 0x00, *STATES["ALL_OPEN"]])
        print(f"    write() returned {n}")
        drain(dev, "reply to SET")

        print("  1c. COMMIT buffer, then wait for a reply")
        n = dev.write([0x00, 0x02])
        print(f"    write() returned {n}")
        drain(dev, "reply to COMMIT")

        print("  1d. SET + COMMIT RF3 back to back, no reads in between, then READ")
        dev.write([0x00, 0x00, *STATES["RFC_RF3"]])
        dev.write([0x00, 0x02])
        time.sleep(0.05)
        dev.write([0x00, 0x01])
        replies = drain(dev, "everything after READ")
        if replies:
            first = replies[0]
            print(f"    first reply echo byte: 0x{first[0]:02X} (0x01 means it answers READ)")
            print(f"    expected RF3 buffer: {hexes(STATES['RFC_RF3'])}")

        print("  1e. Full 9-byte READ (as Menlo's guide shows) vs the short form")
        dev.write([0x00, 0x01] + [0x00] * 7)
        drain(dev, "reply to 9-byte READ")

        print("  1f. Back to ALL_OPEN")
        dev.write([0x00, 0x00, *STATES["ALL_OPEN"]])
        dev.write([0x00, 0x02])
        drain(dev, "leftovers")
    finally:
        dev.close()
    return boards


def load_driver(path):
    spec = importlib.util.spec_from_file_location("MM4250_finalized", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CountSkips(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.skipped = 0

    def emit(self, record):
        if "Skipping reply" in record.getMessage():
            self.skipped += 1


def part2_driver(mm, boards):
    print("\n=== Part 2: driver")
    counter = CountSkips()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(counter)

    print(f"  list_connected_boards(): {mm.MenloMicroMM4250.list_connected_boards()}")

    switch = mm.MenloMicroMM4250("switch")
    try:
        print(f"  IDN: {switch.IDN()}")
        print(f"  after connect: state={switch.state()} channel={switch.channel()}")

        failures = []
        for state in mm.SP6TState:
            t0 = time.perf_counter()
            switch.state(state.value)
            t_set = time.perf_counter() - t0
            t0 = time.perf_counter()
            readback = switch.state()
            t_get = time.perf_counter() - t0
            ok = readback == state.value
            if not ok:
                failures.append((state.value, readback))
            print(
                f"  {state.value:15s} readback={readback:15s} channel={switch.channel()!s:5s} "
                f"set {t_set * 1e3:5.1f} ms  get {t_get * 1e3:5.1f} ms  {'ok' if ok else 'MISMATCH'}"
            )

        print("  50 rapid channel changes with readback")
        for i in range(50):
            ch = i % 6 + 1
            switch.channel(ch)
            if switch.channel() != ch:
                failures.append((f"rapid RF{ch}", switch.state()))
        print(f"  replies skipped by the driver so far: {counter.skipped}")

        switch.channel(4)
        switch.close()
        switch = mm.MenloMicroMM4250("switch", reset_on_connect=False)
        print(f"  reconnect without reset: channel={switch.channel()} (expect 4)")
        if switch.channel() != 4:
            failures.append(("reconnect", switch.state()))
        switch.close()

        serial = boards[0].get("serial_number") if boards else None
        if serial:
            switch = mm.MenloMicroMM4250("switch", serial_number=serial)
            print(f"  opened by serial_number={serial!r}: state={switch.state()}")
        else:
            print("  board has no USB serial; skipping serial_number open")
            switch = mm.MenloMicroMM4250("switch")

        print(f"  total replies skipped: {counter.skipped}")
        print(f"  FAILURES: {failures or 'none'}")
    finally:
        if mm.MenloMicroMM4250.exist("switch"):
            switch.open_all()
            switch.close()
        root.removeHandler(counter)


def part3_unplug(mm):
    print("\n=== Part 3: unplug test")
    switch = mm.MenloMicroMM4250("switch")
    input("  Unplug the driver board's USB cable now, then press Enter...")
    t0 = time.perf_counter()
    try:
        print(f"  state() returned {switch.state()!r} (unexpected)")
    except Exception as err:
        print(f"  state() raised {type(err).__name__}: {err}")
    print(f"  took {time.perf_counter() - t0:.2f} s (should be about 1 s or less, not a hang)")
    try:
        switch.close()
    except Exception as err:
        print(f"  close() raised {type(err).__name__}: {err}")
    input("  Plug the board back in, then press Enter...")


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", default=str(here.parent / "drivers" / "MM4250_finalized.py"))
    parser.add_argument("--unplug", action="store_true")
    args = parser.parse_args()

    answer = input("VNA RF output OFF and nothing powered through the switch? [y/N] ")
    if answer.strip().lower() != "y":
        print("Stopping. Turn the RF off first.")
        return

    hid = part0_environment()
    boards = part1_raw_protocol(hid)
    print(f"\n  driver file: {args.driver}")
    mm = load_driver(args.driver)
    part2_driver(mm, boards)
    if args.unplug:
        part3_unplug(mm)
    print("\nDone. Switch left on ALL_OPEN.")


if __name__ == "__main__":
    main()
