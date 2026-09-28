"""QCoDeS driver for the Menlo Micro MM4250 SP6T cryogenic RF switch.

The switch is controlled through Menlo's USB HiV Driver Board, which shows
up as a USB HID device (no VISA, no serial port). Each switch position maps
to a fixed 6-byte bitmask of energized HV lines; the driver loads that
bitmask into the board's output buffer and then commits it to hardware.

Requirements:
    This driver needs the ``hidapi`` package::

        pip install hidapi

    Note that the unrelated ``hid`` package on PyPI installs a module with
    the same name but a different API; it will not work here.

Example:
    >>> from drivers.MM4250_finalized import MM4250
    >>> switch = MM4250("switch")
    >>> switch.channel(3)          # connect RFC to RF3
    >>> switch.state()             # read the position back from the board
    'RFC_RF3'
    >>> switch.state("INTERNAL_LOAD")
    >>> switch.open_all()
    >>> switch.close()
"""

from __future__ import annotations

import time
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from qcodes.instrument import Instrument
from qcodes.validators import Enum

try:
    import hid
except ImportError:
    hid = None

if TYPE_CHECKING:
    from qcodes.instrument import InstrumentBaseKWArgs
    from qcodes.parameters import Parameter
    from typing_extensions import Unpack


class SP6TState(str, PyEnum):
    """All 9 physical positions the switch can be in."""

    ALL_OPEN = "ALL_OPEN"
    RFC_RF1 = "RFC_RF1"
    RFC_RF2 = "RFC_RF2"
    RFC_RF3 = "RFC_RF3"
    RFC_RF4 = "RFC_RF4"
    RFC_RF5 = "RFC_RF5"
    RFC_RF6 = "RFC_RF6"
    INTERNAL_LOAD = "INTERNAL_LOAD"
    INTERNAL_SHORT = "INTERNAL_SHORT"


# The 6 numbered states, in channel order.
RF_CHANNEL_STATES = (
    SP6TState.RFC_RF1,
    SP6TState.RFC_RF2,
    SP6TState.RFC_RF3,
    SP6TState.RFC_RF4,
    SP6TState.RFC_RF5,
    SP6TState.RFC_RF6,
)

# Which HV driver-board lines energize for each state (from the datasheet).
STATE_HV_PINS = {
    SP6TState.ALL_OPEN: (),
    SP6TState.RFC_RF1: ("HV3", "HV14", "HV19", "HV21"),
    SP6TState.RFC_RF2: ("HV3", "HV7", "HV13", "HV19"),
    SP6TState.RFC_RF3: ("HV2", "HV8", "HV9", "HV17"),
    SP6TState.RFC_RF4: ("HV2", "HV8", "HV10", "HV16"),
    SP6TState.RFC_RF5: ("HV2", "HV8", "HV11", "HV18"),
    SP6TState.RFC_RF6: ("HV3", "HV15", "HV19", "HV20"),
    SP6TState.INTERNAL_LOAD: ("HV1",),
    SP6TState.INTERNAL_SHORT: ("HV12",),
}

# Settle time after each commanded switch (datasheet typical).
TYPICAL_SWITCH_TIME_S = 0.025

# USB HID vendor/product IDs for the driver board.
MENLO_VENDOR_ID = 0x04D8
MENLO_PRODUCT_ID = 0xEDFB

# HID command bytes: load buffer, read buffer, commit buffer to hardware.
_CMD_SET_OUTPUT_BUFFER = 0x00
_CMD_READ_OUTPUT_BUFFER = 0x01
_CMD_WRITE_CURRENT_BUFFER = 0x02

# Raw 6-byte HV bitmask per state, sent over HID (from Menlo's programming package).
STATE_WIRE_BYTES = {
    SP6TState.ALL_OPEN: (0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    SP6TState.RFC_RF1: (0x14, 0x20, 0x04, 0x14, 0x20, 0x04),
    SP6TState.RFC_RF2: (0x04, 0x10, 0x44, 0x04, 0x10, 0x44),
    SP6TState.RFC_RF3: (0x01, 0x01, 0x82, 0x01, 0x01, 0x82),
    SP6TState.RFC_RF4: (0x00, 0x82, 0x82, 0x00, 0x82, 0x82),
    SP6TState.RFC_RF5: (0x02, 0x04, 0x82, 0x02, 0x04, 0x82),
    SP6TState.RFC_RF6: (0x0C, 0x40, 0x04, 0x0C, 0x40, 0x04),
    SP6TState.INTERNAL_LOAD: (0x00, 0x00, 0x01, 0x00, 0x00, 0x01),
    SP6TState.INTERNAL_SHORT: (0x00, 0x08, 0x00, 0x00, 0x08, 0x00),
}


def channel_to_state(channel: int) -> SP6TState:
    """Map an RF channel number (1-6) to its ``SP6TState``."""
    if not 1 <= channel <= 6:
        raise ValueError(f"channel must be 1-6, got {channel!r}")
    return RF_CHANNEL_STATES[channel - 1]


def state_to_channel(state: SP6TState) -> int | None:
    """Inverse of ``channel_to_state``; ``None`` for non-numbered states."""
    if state in RF_CHANNEL_STATES:
        return RF_CHANNEL_STATES.index(state) + 1
    return None


class MM4250(Instrument):
    """Menlo Micro MM4250 SP6T switch, driven by the USB HiV Driver Board.

    Args:
        name: Name of the instrument.
        vendor_id: USB vendor ID of the driver board.
        product_id: USB product ID of the driver board.
        reset_on_connect: If True (default), drive the switch to
            ``ALL_OPEN`` when connecting. Set to False to leave the switch
            where it is, e.g. when reconnecting mid-experiment; the current
            position is then read back from the board instead.
        **kwargs: Forwarded to the ``Instrument`` base class.
    """

    def __init__(
        self,
        name: str,
        vendor_id: int = MENLO_VENDOR_ID,
        product_id: int = MENLO_PRODUCT_ID,
        reset_on_connect: bool = True,
        **kwargs: Unpack[InstrumentBaseKWArgs],
    ) -> None:
        if hid is None or not hasattr(hid, "device"):
            raise ImportError(
                "The MM4250 driver needs the `hidapi` package "
                "(`pip install hidapi`). The unrelated `hid` package provides "
                "a module with the same name but a different API."
            )

        # QCoDeS only rejects a duplicate name after __init__ finishes, by
        # which point we'd already have reset the live switch. Check first.
        if Instrument.exist(name):
            raise KeyError(f"Another instrument has the name: {name}")

        begin_time = time.time()
        super().__init__(name, **kwargs)

        self._device = hid.device()
        self._device.open(vendor_id, product_id)

        self.state: Parameter = self.add_parameter(
            name="state",
            label="SP6T switch state",
            get_cmd=self._get_state,
            set_cmd=self._set_state,
            vals=Enum(*[s.value for s in SP6TState]),
            docstring=(
                "Full switch position: 'ALL_OPEN', 'RFC_RF1'..'RFC_RF6', "
                "'INTERNAL_LOAD', or 'INTERNAL_SHORT'. Getting it reads the "
                "board's output buffer."
            ),
        )
        """Parameter state"""

        self.channel: Parameter = self.add_parameter(
            name="channel",
            label="Active RF channel",
            get_cmd=self._get_channel,
            set_cmd=self._set_channel,
            vals=Enum(1, 2, 3, 4, 5, 6),
            docstring=(
                "Convenience 1-6 view of `state`, matching the RF1-RF6 SMA "
                "ports. Returns None if the switch is not on a numbered port."
            ),
        )
        """Parameter channel"""

        if reset_on_connect:
            self.open_all()
        else:
            try:
                self.state.get()
            except ValueError as err:
                self.log.warning(f"Could not determine switch state on connect: {err}")

        self.connect_message(begin_time=begin_time)

    def _get_state(self) -> str:
        """Read the board's output buffer and decode it to a state."""
        wire_bytes = self._read_output_buffer_from_hardware()
        return self._decode_state(wire_bytes).value

    def _set_state(self, value: str) -> None:
        self._send_state_to_hardware(SP6TState(value))

    def _get_channel(self) -> int | None:
        state = SP6TState(self.state())
        channel = state_to_channel(state)
        if channel is None:
            self.log.warning(f"Switch is on {state.value}, not a numbered RF channel")
        return channel

    def _set_channel(self, value: int) -> None:
        self.state(channel_to_state(value).value)

    def _send_state_to_hardware(self, state: SP6TState) -> None:
        """Two-step HID write: load the buffer, then commit it to hardware."""
        wire_bytes = self._encode_state(state)
        self._device.write([0x00, _CMD_SET_OUTPUT_BUFFER, *wire_bytes])
        self._device.write([0x00, _CMD_WRITE_CURRENT_BUFFER])
        time.sleep(TYPICAL_SWITCH_TIME_S)

    def _encode_state(self, state: SP6TState) -> tuple[int, ...]:
        return STATE_WIRE_BYTES[state]

    def _decode_state(self, wire_bytes: tuple[int, ...]) -> SP6TState:
        wire_bytes = tuple(wire_bytes)
        for candidate_state, candidate_bytes in STATE_WIRE_BYTES.items():
            if candidate_bytes == wire_bytes:
                return candidate_state
        raise ValueError(f"Buffer bytes {wire_bytes!r} don't match any known SP6TState")

    def _read_output_buffer_from_hardware(self) -> tuple[int, ...]:
        """Query the board's current 6-byte output buffer over HID."""
        self._device.write([0x00, _CMD_READ_OUTPUT_BUFFER])
        response = self._device.read(8)
        if len(response) < 7:
            raise ValueError(f"Short HID response from driver board: {list(response)!r}")
        return tuple(response[1:7])

    def open_all(self) -> None:
        """De-energize every HV line (the safe/default state)."""
        self.state(SP6TState.ALL_OPEN.value)

    def active_hv_lines(self) -> tuple[str, ...]:
        """HV lines that should be energized for the current state.

        Uses the cached ``state`` value, reading the board only if no value
        has been cached yet.
        """
        return STATE_HV_PINS[SP6TState(self.state.cache.get())]

    def get_idn(self) -> dict[str, str | None]:
        """Static identity info; the driver board has no ``*IDN?`` query."""
        return {
            "vendor": "Menlo Micro",
            "model": "MM4250 (SP6T Cryogenic RF Switch, via USB HiV Driver Board)",
            "serial": None,
            "firmware": None,
        }

    def close(self) -> None:
        """Close the HID device, then let QCoDeS tear down the instrument."""
        device = getattr(self, "_device", None)
        if device is not None:
            device.close()
        super().close()
