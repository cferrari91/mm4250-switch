"""Hardware-free tests for drivers/MM4250_finalized.py.

The USB HID driver board is replaced by FakeDriverBoard, which mimics the
board's load-buffer / commit / read-back protocol. Run from the repo root:

    python -m pytest tests/test_MM4250_finalized.py
"""

import logging
import sys
import types
from pathlib import Path

import pytest
from qcodes.instrument import Instrument

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drivers.MM4250_finalized as mm  # noqa: E402
from drivers.MM4250_finalized import (  # noqa: E402
    MM4250,
    STATE_HV_PINS,
    STATE_WIRE_BYTES,
    SP6TState,
    channel_to_state,
    state_to_channel,
)


class FakeDriverBoard:
    """Stands in for a `hid.device()` connected to the HiV Driver Board."""

    def __init__(self):
        self.opened_with = None
        self.closed = False
        self.writes = []
        self.pending = (0,) * 6
        self.output = (0,) * 6
        self._response = []

    def open(self, vendor_id, product_id):
        self.opened_with = (vendor_id, product_id)

    def close(self):
        self.closed = True

    def write(self, data):
        self.writes.append(list(data))
        report_id, command, *payload = data
        if command == mm._CMD_SET_OUTPUT_BUFFER:
            self.pending = tuple(payload)
        elif command == mm._CMD_WRITE_CURRENT_BUFFER:
            self.output = self.pending
        elif command == mm._CMD_READ_OUTPUT_BUFFER:
            self._response = [command, *self.output, 0x00]
        return len(data)

    def read(self, max_length):
        response, self._response = self._response, []
        return response[:max_length]

    def commits(self):
        return [w for w in self.writes if w[1] == mm._CMD_WRITE_CURRENT_BUFFER]


@pytest.fixture
def board(monkeypatch):
    fake = FakeDriverBoard()
    monkeypatch.setattr(mm, "hid", types.SimpleNamespace(device=lambda: fake))
    monkeypatch.setattr(mm, "TYPICAL_SWITCH_TIME_S", 0)
    yield fake
    Instrument.close_all()


@pytest.fixture
def switch(board):
    return MM4250("switch")


def test_connect_opens_board_and_resets_to_all_open(board, switch):
    assert board.opened_with == (mm.MENLO_VENDOR_ID, mm.MENLO_PRODUCT_ID)
    assert len(board.commits()) == 1
    assert board.output == STATE_WIRE_BYTES[SP6TState.ALL_OPEN]
    assert switch.state() == "ALL_OPEN"


def test_reset_on_connect_false_leaves_switch_alone(board):
    board.output = STATE_WIRE_BYTES[SP6TState.RFC_RF4]
    switch = MM4250("switch", reset_on_connect=False)
    assert board.commits() == []
    assert switch.state.cache.get() == "RFC_RF4"


def test_reset_on_connect_false_with_unknown_buffer_warns(board, caplog):
    board.output = (0xFF,) * 6
    with caplog.at_level(logging.WARNING):
        MM4250("switch", reset_on_connect=False)
    assert "Could not determine switch state" in caplog.text


@pytest.mark.parametrize("state", list(SP6TState))
def test_every_state_round_trips_through_hardware(board, switch, state):
    switch.state(state.value)
    assert board.output == STATE_WIRE_BYTES[state]
    assert switch.state() == state.value


@pytest.mark.parametrize("channel", range(1, 7))
def test_channel_round_trip(board, switch, channel):
    switch.channel(channel)
    assert board.output == STATE_WIRE_BYTES[channel_to_state(channel)]
    assert switch.channel() == channel


def test_channel_reads_hardware_not_cache(board, switch):
    switch.channel(2)
    board.output = STATE_WIRE_BYTES[SP6TState.RFC_RF5]  # changed behind our back
    assert switch.channel() == 5


def test_channel_on_non_numbered_state_warns_and_returns_none(switch, caplog):
    switch.state("INTERNAL_LOAD")
    with caplog.at_level(logging.WARNING):
        assert switch.channel() is None
    assert "not a numbered RF channel" in caplog.text


@pytest.mark.parametrize("bad", [0, 7, "3"])
def test_invalid_channel_rejected_without_touching_hardware(board, switch, bad):
    writes_before = len(board.writes)
    with pytest.raises(ValueError):
        switch.channel(bad)
    assert len(board.writes) == writes_before


def test_invalid_state_rejected(switch):
    with pytest.raises(ValueError):
        switch.state("RFC_RF7")


def test_unknown_buffer_bytes_raise(board, switch):
    board.output = (0xFF,) * 6
    with pytest.raises(ValueError, match="don't match any known SP6TState"):
        switch.state()


def test_short_hid_response_raises(board, switch, monkeypatch):
    monkeypatch.setattr(board, "read", lambda max_length: [])
    with pytest.raises(ValueError, match="Short HID response"):
        switch.state()


def test_open_all(board, switch):
    switch.channel(6)
    switch.open_all()
    assert board.output == STATE_WIRE_BYTES[SP6TState.ALL_OPEN]


def test_active_hv_lines_follow_state(switch):
    switch.channel(3)
    assert switch.active_hv_lines() == STATE_HV_PINS[SP6TState.RFC_RF3]


def test_get_idn(switch):
    idn = switch.IDN()
    assert idn["vendor"] == "Menlo Micro"
    assert idn["model"].startswith("MM4250")


def test_close_releases_board_and_name(board, switch):
    switch.close()
    assert board.closed
    MM4250("switch")  # name is free again


def test_failed_open_does_not_register_name(board, monkeypatch):
    def refuse(vendor_id, product_id):
        raise OSError("open failed")

    monkeypatch.setattr(board, "open", refuse)
    with pytest.raises(OSError):
        MM4250("switch")
    monkeypatch.setattr(board, "open", FakeDriverBoard().open)
    MM4250("switch")  # would raise "name already in use" if registered


def test_duplicate_name_leaves_live_switch_alone(board, switch, monkeypatch):
    switch.channel(4)
    second = FakeDriverBoard()
    monkeypatch.setattr(mm, "hid", types.SimpleNamespace(device=lambda: second))
    with pytest.raises(KeyError, match="Another instrument has the name"):
        MM4250("switch")
    assert second.opened_with is None
    assert board.output == STATE_WIRE_BYTES[SP6TState.RFC_RF4]


@pytest.mark.parametrize("module", [None, types.SimpleNamespace(Device=object)])
def test_missing_or_wrong_hid_package(monkeypatch, module):
    monkeypatch.setattr(mm, "hid", module)
    with pytest.raises(ImportError, match="hidapi"):
        MM4250("switch")


def test_channel_state_helpers():
    for channel in range(1, 7):
        assert state_to_channel(channel_to_state(channel)) == channel
    assert state_to_channel(SP6TState.ALL_OPEN) is None
