"""Bench checks for drivers/MM4250_finalized.py, run on the lab DAQ.

Follow-up to hardware_check_MM4250_finalized.py (which passed 2026-10-22).
These answer the questions still open before the driver is changed:

  1. Many sets with no readback, then one read: how many replies pile up,
     and does the driver's read still work? (skip limit is 64)
  2. Unplug and replug the board, then reconnect in the same Python session.
  3. What state the board reports right after it comes back from losing
     USB power (read with reset_on_connect=False, before anything is set).
  4. What a second connection gets while the board is already open
     (same process, then a separate process like a second notebook kernel).

Not a pytest file. It moves the switch, so turn the VNA RF output off first
(an unplugged VNA is fine). Nothing else may have the board open. Run from
this tests folder in the env that has hidapi:

    python bench_check_MM4250_finalized.py              # all four
    python bench_check_MM4250_finalized.py --only 1 4   # just some
    python bench_check_MM4250_finalized.py --driver C:\\path\\to\\MM4250_finalized.py

Checks 2 and 3 ask you to unplug and replug the board's USB cable. Paste the
whole output back to Claude. The switch is left on ALL_OPEN.
"""

import argparse
import subprocess
import sys
import time
import logging
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from hardware_check_MM4250_finalized import (  # noqa: E402
    PID,
    VID,
    CountSkips,
    hexes,
    load_driver,
)


def count_queued(dev, timeout_ms=200):
    """Read raw reports until none arrive within timeout_ms; return them."""
    got = []
    while True:
        r = dev.read(8, timeout_ms)
        if not r:
            return got
        got.append(r)


def close_quietly(mm, name):
    if mm.MM4250.exist(name):
        inst = mm.MM4250.find_instrument(name)
        try:
            inst.open_all()
        except Exception as err:
            print(f"    open_all() on {name} raised {type(err).__name__}: {err}")
        try:
            inst.close()
        except Exception as err:
            print(f"    close() on {name} raised {type(err).__name__}: {err}")
            # Still free the name so later checks can reuse it.
            try:
                mm.MM4250.remove_instance(inst)
            except Exception:
                pass


def check1_queue(mm, counter):
    print("\n=== Check 1: many sets with no readback, then one read")
    switch = mm.MM4250("switch")
    try:
        count_queued(switch._device)  # start from an empty queue
        for n in (10, 31, 40, 100):
            # a) raw: how many replies are actually waiting after n sets
            for i in range(n):
                switch.channel(i % 6 + 1)
            waiting = count_queued(switch._device)
            print(
                f"  {n:3d} sets -> {len(waiting):3d} replies waiting "
                f"(2 per set would be {2 * n}); first: {hexes(waiting[0]) if waiting else '-'}"
            )
            # b) driver: n sets, then one read through the driver
            for i in range(n):
                switch.channel(i % 6 + 1)
            expected = (n - 1) % 6 + 1
            skipped_before = counter.skipped
            t0 = time.perf_counter()
            try:
                got = switch.channel()
                ok = "ok" if got == expected else "MISMATCH"
                result = f"channel()={got} (expect {expected}) {ok}"
            except Exception as err:
                result = f"channel() raised {type(err).__name__}: {err}"
            dt = (time.perf_counter() - t0) * 1e3
            print(
                f"      then a driver read: {result}; skipped {counter.skipped - skipped_before} "
                f"replies, took {dt:.1f} ms"
            )
            count_queued(switch._device)  # clear anything left before the next n
    finally:
        close_quietly(mm, "switch")


def check2_and_3_replug(mm, run2, run3):
    print("\n=== Checks 2/3: unplug, replug, reconnect in the same session")
    switch = mm.MM4250("switch")
    switch.channel(2)
    print(f"  before unplug: state={switch.state()} (set to RF2 on purpose)")
    input("  Unplug the driver board's USB cable now, then press Enter...")
    try:
        print(f"  state() returned {switch.state()!r} (unexpected)")
    except Exception as err:
        print(f"  state() raised {type(err).__name__}: {err}")
    try:
        switch.close()
        print("  close() after unplug: ok")
    except Exception as err:
        print(f"  close() after unplug raised {type(err).__name__}: {err}")
    print(f"  name 'switch' still registered after close: {mm.MM4250.exist('switch')}")
    close_quietly(mm, "switch")

    input("  Plug the board back in, wait ~3 s, then press Enter...")
    for attempt in range(1, 4):
        boards = mm.MM4250.list_connected_boards()
        print(f"  attempt {attempt}: list_connected_boards() found {len(boards)} board(s)")
        try:
            # reset_on_connect=False so nothing is written before we read (check 3).
            switch = mm.MM4250("switch", reset_on_connect=False)
        except Exception as err:
            print(f"    reconnect raised {type(err).__name__}: {err}")
            time.sleep(2)
            continue
        print("    reconnect without restarting Python: ok")
        if run3:
            cached = switch.state.cache.get(get_if_invalid=False)
            print(f"  Check 3: state read on connect after power loss = {cached!r}")
            raw = count_queued(switch._device)
            if raw:
                print(f"    (leftover reports: {[hexes(r) for r in raw]})")
            switch._device.write([0x00, 0x01])
            reply = count_queued(switch._device)
            print(f"    raw READ reply: {[hexes(r) for r in reply] or '(nothing)'}")
            print(
                "    for reference, a READ reply on RF2 is 0x01 0x04 0x10 0x44 0x04 0x10 0x44 0x00;"
                " on ALL_OPEN it is 0x01 then zeros"
            )
        if run2:
            switch.channel(5)
            print(f"  Check 2: after reconnect, set RF5 -> readback {switch.channel()} (expect 5)")
        close_quietly(mm, "switch")
        return
    print("  could not reconnect after 3 attempts")


SECOND_PROCESS = r"""
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("MM4250_finalized", sys.argv[1])
mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
try:
    s = mm.MM4250("other", reset_on_connect=False)
except Exception as err:
    print(f"OPEN FAILED {type(err).__name__}: {err}")
else:
    try:
        print(f"OPENED; state={s.state()!r}")
    except Exception as err:
        print(f"OPENED but state() raised {type(err).__name__}: {err}")
    s.close()
"""


def check4_second_connection(mm, driver_path):
    print("\n=== Check 4: second connection while the board is open")
    first = mm.MM4250("first")
    first.channel(3)
    print(f"  first connection open, set RF3, state={first.state()}")
    try:
        # Same process, through the driver, without moving the switch.
        try:
            second = mm.MM4250("second", reset_on_connect=False)
        except Exception as err:
            print(f"  same process: open raised {type(err).__name__}: {err}")
        else:
            print(f"  same process: OPENED (not exclusive); second sees state={second.state.cache.get(False)!r}")
            second.channel(4)
            try:
                print(f"    second set RF4; first now reads {first.state()!r}")
            except Exception as err:
                print(f"    second set RF4; first.state() raised {type(err).__name__}: {err}")
            print(f"    replies waiting on first's handle: {len(count_queued(first._device))}")
            close_quietly(mm, "second")

        # Separate process, like a second notebook kernel.
        out = subprocess.run(
            [sys.executable, "-c", SECOND_PROCESS, str(driver_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        lines = [l for l in (out.stdout + out.stderr).splitlines() if l.strip()]
        print("  separate process:")
        for line in lines[-6:]:
            print(f"    {line}")
        try:
            print(f"  first connection still works after that: state={first.state()!r}")
        except Exception as err:
            print(f"  first.state() afterwards raised {type(err).__name__}: {err}")
    finally:
        close_quietly(mm, "first")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", default=str(HERE.parent / "drivers" / "MM4250_finalized.py"))
    parser.add_argument("--only", nargs="+", type=int, choices=[1, 2, 3, 4])
    args = parser.parse_args()
    run = set(args.only or [1, 2, 3, 4])

    answer = input("VNA RF output OFF (or VNA unplugged) and nothing else has the board open? [y/N] ")
    if answer.strip().lower() != "y":
        print("Stopping.")
        return

    print(f"driver file: {args.driver}")
    mm = load_driver(args.driver)
    counter = CountSkips()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(counter)
    try:
        if 1 in run:
            check1_queue(mm, counter)
        if run & {2, 3}:
            check2_and_3_replug(mm, 2 in run, 3 in run)
        if 4 in run:
            check4_second_connection(mm, args.driver)
    finally:
        for name in ("switch", "first", "second"):
            close_quietly(mm, name)
        root.removeHandler(counter)
    print("\nDone. Switch left on ALL_OPEN.")


if __name__ == "__main__":
    main()
