"""Hardware-free tests for drivers/MM4250_finalized.py.

The USB HID driver board is replaced by FakeDriverBoard, which mimics the
board's load-buffer / commit / read-back protocol. Run from the repo root:

    python -m pytest tests/test_MM4250_finalized.py
"""

import json
import logging
import sys
import types
from pathlib import Path

import pytest
from qcodes.instrument import Instrument

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drivers.MM4250_finalized as mm  # noqa: E402
from drivers.MM4250_finalized import (  # noqa: E402
    STATE_HV_PINS,
    STATE_WIRE_BYTES,
    MenloMicroMM4250,
    SP6TState,
    channel_to_state,
    state_to_channel,
)


class FakeDriverBoard:
    """Stands in for a `hid.device()` connected to the HiV Driver Board.

    With ``replies_to_writes=True`` the board also queues a reply to every
    set/commit command, as Menlo's protocol guide says it may.
    """

    def __init__(self, serial="0001", replies_to_writes=False):
        self.serial = serial
        self.replies_to_writes = replies_to_writes
        self.opened_with = None
        self.closed = False
        self.writes = []
        self.pending = (0,) * 6
        self.output = (0,) * 6
        self.replies = []

    def open(self, vendor_id, product_id, serial_number=None):
        self.opened_with = (vendor_id, product_id, serial_number)

    def close(self):
        self.closed = True

    def write(self, data):
        self.writes.append(list(data))
        report_id, command, *payload = data
        if command == mm._CMD_SET_OUTPUT_BUFFER:
            self.pending = tuple(payload)
        elif command == mm._CMD_WRITE_CURRENT_BUFFER:
            self.output = self.pending
        if command == mm._CMD_READ_OUTPUT_BUFFER or self.replies_to_writes:
            self.replies.append([command, *self.output, 0x00])
        return len(data)

    def read(self, max_length, timeout_ms=0):
        assert timeout_ms > 0, "driver must never block forever on a read"
        if not self.replies:
            return []
        return self.replies.pop(0)[:max_length]

    def get_serial_number_string(self):
        return self.serial

    def commits(self):
        return [w for w in self.writes if w[1] == mm._CMD_WRITE_CURRENT_BUFFER]


def fake_hid(*boards):
    """A stand-in `hid` module whose device() hands out the given boards."""
    queue = list(boards)
    return types.SimpleNamespace(
        device=lambda: queue.pop(0),
        enumerate=lambda vendor_id, product_id: [
            {
                "serial_number": b.serial,
                "product_string": "USB HiV Driver Board",
                "manufacturer_string": "Menlo Micro",
                "path": f"path-{b.serial}".encode(),
                "vendor_id": vendor_id,
            }
            for b in boards
        ],
    )


@pytest.fixture
def board(monkeypatch):
    fake = FakeDriverBoard()
    monkeypatch.setattr(mm, "hid", fake_hid(fake))
    yield fake
    Instrument.close_all()


@pytest.fixture
def switch(board):
    return MenloMicroMM4250("switch", settle_time=0)


def test_connect_opens_board_and_resets_to_all_open(board, switch):
    assert board.opened_with == (mm.MENLO_VENDOR_ID, mm.MENLO_PRODUCT_ID, None)
    assert len(board.commits()) == 1
    assert board.output == STATE_WIRE_BYTES[SP6TState.ALL_OPEN]
    assert switch.state() == "ALL_OPEN"


def test_custom_ids_and_serial_passed_to_open(board):
    MenloMicroMM4250(
        "switch", vendor_id=0x1234, product_id=0x5678, serial_number="0042", settle_time=0
    )
    assert board.opened_with == (0x1234, 0x5678, "0042")


def test_reset_on_connect_false_leaves_switch_alone(board):
    board.output = STATE_WIRE_BYTES[SP6TState.RFC_RF4]
    switch = MenloMicroMM4250("switch", reset_on_connect=False)
    assert board.commits() == []
    assert switch.state.cache.get() == "RFC_RF4"


def test_reset_on_connect_false_with_unknown_buffer_warns(board, caplog):
    board.output = (0xFF,) * 6
    with caplog.at_level(logging.WARNING):
        MenloMicroMM4250("switch", reset_on_connect=False)
    assert "Could not determine switch state" in caplog.text


@pytest.mark.parametrize("state", list(SP6TState))
def test_every_state_round_trips_through_hardware(board, switch, state):
    switch.state(state.value)
    assert board.output == STATE_WIRE_BYTES[state]
    assert switch.state() == state.value


def test_state_accepts_enum_members(board, switch):
    switch.state(SP6TState.INTERNAL_SHORT)
    assert board.output == STATE_WIRE_BYTES[SP6TState.INTERNAL_SHORT]
    assert switch.state() == "INTERNAL_SHORT"


@pytest.mark.parametrize("channel", range(1, 7))
def test_channel_round_trip(board, switch, channel):
    switch.channel(channel)
    assert board.output == STATE_WIRE_BYTES[channel_to_state(channel)]
    assert switch.channel() == channel


def test_channel_reads_hardware_not_cache(board, switch):
    switch.channel(2)
    board.output = STATE_WIRE_BYTES[SP6TState.RFC_RF5]  # changed behind our back
    assert switch.channel() == 5


@pytest.mark.parametrize("state", ["ALL_OPEN", "INTERNAL_LOAD", "INTERNAL_SHORT"])
def test_channel_on_non_numbered_state_is_none_without_warning(switch, caplog, state):
    switch.state(state)
    with caplog.at_level(logging.WARNING):
        assert switch.channel() is None
    assert caplog.records == []


@pytest.mark.parametrize("bad", [0, 7, "3", 3.0, None])
def test_invalid_channel_rejected_without_touching_hardware(board, switch, bad):
    writes_before = len(board.writes)
    with pytest.raises((ValueError, TypeError)):
        switch.channel(bad)
    assert len(board.writes) == writes_before


def test_bool_channel_rejected_without_touching_hardware(board, switch):
    writes_before = len(board.writes)
    with pytest.raises(TypeError):
        switch.channel(True)
    assert len(board.writes) == writes_before


@pytest.mark.parametrize("bad", ["RFC_RF7", "rfc_rf1", "ALL_OPEN ", 3])
def test_invalid_state_rejected_without_touching_hardware(board, switch, bad):
    writes_before = len(board.writes)
    with pytest.raises(ValueError):
        switch.state(bad)
    assert len(board.writes) == writes_before


def test_unknown_buffer_bytes_raise(board, switch):
    board.output = (0xFF,) * 6
    with pytest.raises(ValueError, match="don't match any known SP6TState"):
        switch.state()


def test_short_hid_response_raises(board, switch, monkeypatch):
    monkeypatch.setattr(board, "read", lambda max_length, timeout_ms=0: [0x01, 0x00])
    with pytest.raises(ValueError, match="Short HID response"):
        switch.state()


def test_no_reply_times_out_instead_of_hanging(board, switch, monkeypatch):
    monkeypatch.setattr(board, "read", lambda max_length, timeout_ms=0: [])
    with pytest.raises(TimeoutError, match="No reply"):
        switch.state()


def test_replies_to_other_commands_are_skipped(monkeypatch):
    chatty = FakeDriverBoard(replies_to_writes=True)
    monkeypatch.setattr(mm, "hid", fake_hid(chatty))
    switch = MenloMicroMM4250("switch", settle_time=0)
    try:
        for channel in (1, 4, 6):
            switch.channel(channel)  # queues stale set/commit replies
            assert switch.channel() == channel
    finally:
        switch.close()


def test_board_that_never_answers_the_read_raises(board, switch, monkeypatch):
    monkeypatch.setattr(
        board, "read", lambda max_length, timeout_ms=0: [mm._CMD_WRITE_CURRENT_BUFFER] + [0] * 7
    )
    with pytest.raises(ValueError, match="without answering"):
        switch.state()


def test_failed_write_raises(board, switch, monkeypatch):
    monkeypatch.setattr(board, "write", lambda data: -1)
    with pytest.raises(OSError, match="HID write"):
        switch.channel(1)


def test_open_all(board, switch):
    switch.channel(6)
    switch.open_all()
    assert board.output == STATE_WIRE_BYTES[SP6TState.ALL_OPEN]


def test_settle_time_used_after_each_switch(board, monkeypatch):
    sleeps = []
    monkeypatch.setattr(mm.time, "sleep", sleeps.append)
    switch = MenloMicroMM4250("switch")
    assert sleeps == [mm.DEFAULT_SETTLE_TIME_S]
    switch.settle_time(0.1)
    switch.channel(2)
    assert sleeps[-1] == 0.1


def test_negative_settle_time_rejected(switch):
    with pytest.raises(ValueError):
        switch.settle_time(-1)


def test_active_hv_lines_follow_state(switch):
    switch.channel(3)
    assert switch.active_hv_lines() == STATE_HV_PINS[SP6TState.RFC_RF3]


@pytest.mark.parametrize("state", list(SP6TState))
def test_hv_pin_table_matches_wire_bytes(state):
    # The two tables come from different Menlo documents; check they agree.
    # Each bus is 3 bytes ordered (HV22..HV17), (HV16..HV9), (HV8..HV1).
    bits = 0
    for pin in STATE_HV_PINS[state]:
        bits |= 1 << (int(pin.removeprefix("HV")) - 1)
    bus = ((bits >> 16) & 0xFF, (bits >> 8) & 0xFF, bits & 0xFF)
    assert STATE_WIRE_BYTES[state] == bus + bus


def test_get_idn_reports_board_serial(board, switch):
    idn = switch.IDN()
    assert idn["vendor"] == "Menlo Micro"
    assert idn["model"].startswith("MM4250")
    assert idn["serial"] == "0001"


@pytest.mark.parametrize("serial", ["", None])
def test_get_idn_without_usb_serial(monkeypatch, serial):
    monkeypatch.setattr(mm, "hid", fake_hid(FakeDriverBoard(serial=serial)))
    switch = MenloMicroMM4250("switch", settle_time=0)
    try:
        assert switch.get_idn()["serial"] is None
    finally:
        switch.close()


def test_list_connected_boards(monkeypatch):
    monkeypatch.setattr(mm, "hid", fake_hid(FakeDriverBoard("A1"), FakeDriverBoard("B2")))
    boards = MenloMicroMM4250.list_connected_boards()
    assert [b["serial_number"] for b in boards] == ["A1", "B2"]
    assert set(boards[0]) == {"serial_number", "product_string", "manufacturer_string", "path"}


@pytest.mark.parametrize("state", list(SP6TState))
def test_snapshot_is_json_serializable_on_every_state(switch, state):
    switch.state(state)
    snapshot = switch.snapshot(update=True)
    json.dumps(snapshot)
    assert snapshot["parameters"]["state"]["value"] == state.value
    assert snapshot["parameters"]["channel"]["value"] == state_to_channel(state)


def test_close_releases_board_and_name(monkeypatch):
    first, second = FakeDriverBoard(), FakeDriverBoard()
    monkeypatch.setattr(mm, "hid", fake_hid(first, second))
    MenloMicroMM4250("switch", settle_time=0).close()
    assert first.closed
    MenloMicroMM4250("switch", settle_time=0).close()  # name is free again


def test_failed_open_does_not_register_name(board, monkeypatch):
    def refuse(vendor_id, product_id, serial_number=None):
        raise OSError("open failed")

    monkeypatch.setattr(board, "open", refuse)
    with pytest.raises(OSError):
        MenloMicroMM4250("switch")
    monkeypatch.setattr(mm, "hid", fake_hid(FakeDriverBoard()))
    MenloMicroMM4250("switch", settle_time=0)  # would raise "name already in use" if registered


def test_failed_reset_on_connect_closes_board_and_frees_name(board, monkeypatch):
    monkeypatch.setattr(board, "write", lambda data: -1)
    with pytest.raises(OSError):
        MenloMicroMM4250("switch")
    assert board.closed
    assert not Instrument.exist("switch")


def test_duplicate_name_leaves_live_switch_alone(board, switch, monkeypatch):
    switch.channel(4)
    second = FakeDriverBoard()
    monkeypatch.setattr(mm, "hid", fake_hid(second))
    with pytest.raises(KeyError, match="Another instrument has the name"):
        MenloMicroMM4250("switch")
    assert second.opened_with is None
    assert board.output == STATE_WIRE_BYTES[SP6TState.RFC_RF4]


@pytest.mark.parametrize("module", [None, types.SimpleNamespace(Device=object)])
def test_missing_or_wrong_hid_package(monkeypatch, module):
    monkeypatch.setattr(mm, "hid", module)
    with pytest.raises(ImportError, match="hidapi"):
        MenloMicroMM4250("switch")
    with pytest.raises(ImportError, match="hidapi"):
        MenloMicroMM4250.list_connected_boards()


def test_channel_state_helpers():
    for channel in range(1, 7):
        assert state_to_channel(channel_to_state(channel)) == channel
    assert state_to_channel(SP6TState.ALL_OPEN) is None
