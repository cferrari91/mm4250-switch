import time
from enum import Enum as PyEnum

from qcodes.instrument import Instrument
from qcodes.validators import Enum

import hid


# All 9 physical positions the switch can be in.
class SP6TState(str, PyEnum):
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


# Channel number (1-6) -> SP6TState.
def channel_to_state(channel):
    if not 1 <= channel <= 6:
        raise ValueError(f"channel must be 1-6, got {channel!r}")
    return RF_CHANNEL_STATES[channel - 1]


# Inverse of channel_to_state (None for non-numbered states).
def state_to_channel(state):
    if state in RF_CHANNEL_STATES:
        return RF_CHANNEL_STATES.index(state) + 1
    return None


# QCoDeS driver: holds and changes the switch's current position.
class MM4250(Instrument):
    def __init__(self, name, vendor_id=MENLO_VENDOR_ID, product_id=MENLO_PRODUCT_ID, **kwargs):
        super().__init__(name, **kwargs)

        self._state = SP6TState.ALL_OPEN  # best-known current state

        # Connect to the USB HiV Driver Board and force the safe ALL_OPEN state.
        self._device = hid.device()
        self._device.open(vendor_id, product_id)
        print(f"Connected to MM4250 driver board (VID=0x{vendor_id:04x}, PID=0x{product_id:04x}).")
        self._send_state_to_hardware(SP6TState.ALL_OPEN)

        # "state" parameter: get/set the full switch position.
        self.add_parameter(
            name="state",
            label="SP6T switch state",
            get_cmd=self._get_state,
            set_cmd=self._set_state,
            vals=Enum(*[s.value for s in SP6TState]),
            docstring="Full switch position: 'ALL_OPEN', 'RFC_RF1'..'RFC_RF6', 'INTERNAL_LOAD', or 'INTERNAL_SHORT'."
        )

        # "channel" parameter: friendlier 1-6 view of the same state.
        self.add_parameter(
            name="channel",
            label="Active RF channel",
            get_cmd=self._get_channel,
            set_cmd=self._set_channel,
            vals=Enum(1, 2, 3, 4, 5, 6),
            docstring="Convenience 1-6 view of `state`, matching the RF1-RF6 SMA ports."
        )

    # Re-read the board's output buffer over HID and decode it to a state.
    def _get_state(self):
        wire_bytes = self._read_output_buffer_from_hardware()
        state = self._decode_state(wire_bytes)
        self._state = state
        return state.value

    # Send a new state to hardware, then update the cache.
    def _set_state(self, value):
        state = SP6TState(value)
        self._send_state_to_hardware(state)
        self._state = state

    # State -> channel number; warns if not one of the 6 numbered states.
    def _get_channel(self):
        channel = state_to_channel(self._state)
        if channel is None:
            print(f"[WARNING] Switch is on {self._state.value}, not a numbered RF channel")
        return channel

    # Channel number -> state, via the state parameter's own setter.
    def _set_channel(self, value):
        self.state(channel_to_state(value).value)

    # Two-step HID write: load the buffer, then commit it to hardware.
    def _send_state_to_hardware(self, state):
        wire_bytes = self._encode_state(state)
        self._device.write([0x00, _CMD_SET_OUTPUT_BUFFER, *wire_bytes])
        self._device.write([0x00, _CMD_WRITE_CURRENT_BUFFER])
        time.sleep(TYPICAL_SWITCH_TIME_S)

    # State -> its 6-byte HV bitmask.
    def _encode_state(self, state):
        return STATE_WIRE_BYTES[state]

    # 6-byte HV bitmask -> the state it represents.
    def _decode_state(self, wire_bytes):
        wire_bytes = tuple(wire_bytes)
        for candidate_state, candidate_bytes in STATE_WIRE_BYTES.items():
            if candidate_bytes == wire_bytes:
                return candidate_state
        raise ValueError(f"Buffer bytes {wire_bytes!r} don't match any known SP6TState")

    # Query the board's current output buffer over HID.
    def _read_output_buffer_from_hardware(self):
        self._device.write([0x00, _CMD_READ_OUTPUT_BUFFER])
        response = self._device.read(8)
        return tuple(response[1:7])

    # Convenience: de-energize everything (the safe/default state).
    def open_all(self):
        self.state(SP6TState.ALL_OPEN.value)

    # Which HV lines *should* be energized for the current state (no hardware query).
    def active_hv_lines(self):
        return STATE_HV_PINS[self._state]

    # Static identity info -- there's no real *IDN? query for this hardware.
    def get_idn(self):
        return {
            "vendor": "Menlo Micro",
            "model": "MM4250 (SP6T Cryogenic RF Switch, via USB HiV Driver Board)",
            "serial": None,
            "firmware": None,
        }

    # Close the HID device, then let QCoDeS tear down the instrument.
    def close(self):
        device = getattr(self, "_device", None)
        if device is not None:
            device.close()
        super().close()
