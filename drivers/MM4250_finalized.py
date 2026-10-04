"""QCoDeS driver for the Menlo Micro MM4250 SP6T cryogenic RF switch.

The MM4250 is a single-pole six-throw RF MEMS switch (DC to 10 GHz, rated
down to 10 mK) with an internal LOAD and SHORT calibration standard on the
common port. It is controlled through Menlo's USB HiV Driver Board, which
shows up as a USB HID device (no VISA, no serial port). Each switch position
maps to a fixed bitmask of energized HV lines; the driver loads that bitmask
into the board's output buffer and then commits it to hardware.

Warnings:
    **No hot switching.** The MM4250 is not rated for switching with RF
    power or a bias applied. Turn the source off (or below about 0.5 V)
    before changing ``state`` or ``channel``, or the switch will degrade.
    See "Hot Switch Restrictions" in the datasheet.

    **Both outputs follow the same command.** The board has two Micro-D HV
    outputs (Bus A on J19, Bus B on J18) and this driver writes the same
    position to both, so two MM4250 modules on the split cable always
    switch together.

Requirements:
    This driver needs the ``hidapi`` package::

        pip install hidapi

    Note that the unrelated ``hid`` package on PyPI installs a module with
    the same name but a different API; it will not work here.

Example:
    >>> from qcodes_contrib_drivers.drivers.MenloMicro import MM4250
    >>> switch = MM4250("switch")
    >>> switch.channel(3)          # connect RFC to RF3
    >>> switch.state()             # read the position back from the board
    'RFC_RF3'
    >>> switch.state("INTERNAL_LOAD")
    >>> switch.open_all()
    >>> switch.close()

Documentation:

- Product page and datasheet: https://menlomicro.com/products/rf-switches
- Driver board HID protocol: "Programming Guide for MM4250 USB HiV Driver
  Board" in Menlo's MM4250 driver programming package.

See ``docs/examples/MenloMicro_MM4250.ipynb`` for an example notebook.

Written by Charlie Ferrari (Colorado School of Mines).
"""

from __future__ import annotations

import time
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any

from qcodes.instrument import Instrument
from qcodes.validators import Enum, Ints, Numbers

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

# Default settle time after each commanded switch. The datasheet gives 25 ms
# switching time, and the cryogenic app note recommends waiting about 25 ms
# after a gate transition before relying on the RF state.
DEFAULT_SETTLE_TIME_S = 0.025

# USB HID vendor/product IDs for the driver board.
MENLO_VENDOR_ID = 0x04D8
MENLO_PRODUCT_ID = 0xEDFB

# HID command bytes: load buffer, read buffer, commit buffer to hardware.
_CMD_SET_OUTPUT_BUFFER = 0x00
_CMD_READ_OUTPUT_BUFFER = 0x01
_CMD_WRITE_CURRENT_BUFFER = 0x02

# Replies are 8 bytes: the echoed command byte, then 7 data bytes.
_RESPONSE_LENGTH = 8
_READ_TIMEOUT_MS = 1000
# Replies to other commands are skipped while waiting for a read-buffer
# reply; this caps how many, so a misbehaving board can't loop forever.
_MAX_SKIPPED_REPLIES = 64

# Raw 6-byte HV bitmask per state, sent over HID (from Menlo's programming
# package). Bytes 0-2 drive Bus A and bytes 3-5 drive Bus B, each ordered
# (HV22..HV17), (HV16..HV9), (HV8..HV1).
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
    if isinstance(channel, bool):
        raise TypeError(f"channel must be an int 1-6, got {channel!r}")
    if not 1 <= channel <= 6:
        raise ValueError(f"channel must be 1-6, got {channel!r}")
    return RF_CHANNEL_STATES[channel - 1]


def state_to_channel(state: SP6TState) -> int | None:
    """Inverse of ``channel_to_state``; ``None`` for non-numbered states."""
    if state in RF_CHANNEL_STATES:
        return RF_CHANNEL_STATES.index(state) + 1
    return None


def _require_hidapi() -> None:
    if hid is None or not hasattr(hid, "device"):
        raise ImportError(
            "The MM4250 driver needs the `hidapi` package "
            "(`pip install hidapi`). The unrelated `hid` package provides "
            "a module with the same name but a different API."
        )


class MM4250(Instrument):
    """Menlo Micro MM4250 SP6T switch, driven by the USB HiV Driver Board.

    Args:
        name: Name of the instrument.
        vendor_id: USB vendor ID of the driver board.
        product_id: USB product ID of the driver board.
        serial_number: USB serial number of the driver board to open. Only
            needed when more than one board is plugged in; see
            ``list_connected_boards``. If None, the first board found is
            opened.
        reset_on_connect: If True (default), drive the switch to
            ``ALL_OPEN`` when connecting. Set to False to leave the switch
            where it is, e.g. when reconnecting mid-experiment; the current
            position is then read back from the board instead.
        settle_time: Seconds to wait after each switch command before
            returning. Also settable later through the ``settle_time``
            parameter.
        **kwargs: Forwarded to the ``Instrument`` base class.
    """

    def __init__(
        self,
        name: str,
        vendor_id: int = MENLO_VENDOR_ID,
        product_id: int = MENLO_PRODUCT_ID,
        serial_number: str | None = None,
        reset_on_connect: bool = True,
        settle_time: float = DEFAULT_SETTLE_TIME_S,
        **kwargs: Unpack[InstrumentBaseKWArgs],
    ) -> None:
        _require_hidapi()

        # QCoDeS only rejects a duplicate name after __init__ finishes, by
        # which point we'd already have reset the live switch. Check first.
        if Instrument.exist(name):
            raise KeyError(f"Another instrument has the name: {name}")

        begin_time = time.time()
        super().__init__(name, **kwargs)

        self._device = hid.device()
        self._device.open(vendor_id, product_id, serial_number)

        # The board takes an exclusive USB handle, so release it if anything
        # below fails; otherwise reconnecting needs a kernel restart.
        try:
            self.settle_time: Parameter = self.add_parameter(
                name="settle_time",
                label="Settle time after switching",
                unit="s",
                get_cmd=None,
                set_cmd=None,
                initial_value=settle_time,
                vals=Numbers(min_value=0),
                docstring="Seconds to wait after each switch command before returning.",
            )
            """Seconds to wait after each switch command before returning."""

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
            """Full switch position: 'ALL_OPEN', 'RFC_RF1'..'RFC_RF6',
            'INTERNAL_LOAD', or 'INTERNAL_SHORT'. Getting it reads the
            board's output buffer."""

            self.channel: Parameter = self.add_parameter(
                name="channel",
                label="Active RF channel",
                get_cmd=self._get_channel,
                set_cmd=self._set_channel,
                vals=Ints(1, 6),
                docstring=(
                    "Convenience 1-6 view of `state`, matching the RF1-RF6 SMA "
                    "ports. Returns None if the switch is not on a numbered port."
                ),
            )
            """Convenience 1-6 view of `state`, matching the RF1-RF6 SMA
            ports. Returns None if the switch is not on a numbered port."""

            if reset_on_connect:
                self.open_all()
            else:
                try:
                    self.state.get()
                except ValueError as err:
                    self.log.warning(f"Could not determine switch state on connect: {err}")
        except BaseException:
            self._device.close()
            raise

        self.connect_message(begin_time=begin_time)

    @staticmethod
    def list_connected_boards(
        vendor_id: int = MENLO_VENDOR_ID,
        product_id: int = MENLO_PRODUCT_ID,
    ) -> list[dict[str, Any]]:
        """List the driver boards plugged into this computer.

        Each entry has the board's ``serial_number`` (pass it to the
        constructor to pick that board), ``product_string``,
        ``manufacturer_string`` and OS ``path``.
        """
        _require_hidapi()
        keys = ("serial_number", "product_string", "manufacturer_string", "path")
        return [
            {key: info.get(key) for key in keys}
            for info in hid.enumerate(vendor_id, product_id)
        ]

    def _get_state(self) -> str:
        """Read the board's output buffer and decode it to a state."""
        wire_bytes = self._read_output_buffer_from_hardware()
        return self._decode_state(wire_bytes).value

    def _set_state(self, value: str) -> None:
        self._send_state_to_hardware(SP6TState(value))

    def _get_channel(self) -> int | None:
        return state_to_channel(SP6TState(self.state()))

    def _set_channel(self, value: int) -> None:
        self.state(channel_to_state(value).value)

    def _write(self, data: list[int]) -> None:
        # hidapi reports a failed write by returning -1 rather than raising.
        if self._device.write(data) < 0:
            raise OSError(f"HID write to driver board failed: {data!r}")

    def _send_state_to_hardware(self, state: SP6TState) -> None:
        """Two-step HID write: load the buffer, then commit it to hardware."""
        wire_bytes = self._encode_state(state)
        self._write([0x00, _CMD_SET_OUTPUT_BUFFER, *wire_bytes])
        self._write([0x00, _CMD_WRITE_CURRENT_BUFFER])
        time.sleep(self.settle_time())

    def _encode_state(self, state: SP6TState) -> tuple[int, ...]:
        return STATE_WIRE_BYTES[state]

    def _decode_state(self, wire_bytes: tuple[int, ...]) -> SP6TState:
        wire_bytes = tuple(wire_bytes)
        for candidate_state, candidate_bytes in STATE_WIRE_BYTES.items():
            if candidate_bytes == wire_bytes:
                return candidate_state
        raise ValueError(f"Buffer bytes {wire_bytes!r} don't match any known SP6TState")

    def _read_output_buffer_from_hardware(self) -> tuple[int, ...]:
        """Query the board's current 6-byte output buffer over HID.

        Every reply starts with the command byte it answers. Replies to
        other commands are skipped, so a stale reply already queued in the
        HID input buffer can't be mistaken for the buffer contents.
        """
        self._write([0x00, _CMD_READ_OUTPUT_BUFFER])
        for _ in range(_MAX_SKIPPED_REPLIES + 1):
            response = self._device.read(_RESPONSE_LENGTH, _READ_TIMEOUT_MS)
            if not response:
                raise TimeoutError(
                    f"No reply from driver board within {_READ_TIMEOUT_MS} ms"
                )
            if response[0] == _CMD_READ_OUTPUT_BUFFER:
                break
            self.log.debug(f"Skipping reply to command 0x{response[0]:02X}: {list(response)!r}")
        else:
            raise ValueError(
                f"Driver board sent {_MAX_SKIPPED_REPLIES + 1} replies without "
                "answering the read-buffer command"
            )
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

    def _usb_string(self, getter_name: str) -> str | None:
        try:
            value = getattr(self._device, getter_name)()
        except (OSError, ValueError):
            return None
        return value or None

    def get_idn(self) -> dict[str, str | None]:
        """Identity info. The driver board has no ``*IDN?`` query, so the
        serial is the board's USB serial number (not the switch module's).
        """
        return {
            "vendor": "Menlo Micro",
            "model": "MM4250 (SP6T Cryogenic RF Switch, via USB HiV Driver Board)",
            "serial": self._usb_string("get_serial_number_string"),
            "firmware": None,
        }

    def close(self) -> None:
        """Close the HID device, then let QCoDeS tear down the instrument."""
        device = getattr(self, "_device", None)
        if device is not None:
            device.close()
        super().close()
