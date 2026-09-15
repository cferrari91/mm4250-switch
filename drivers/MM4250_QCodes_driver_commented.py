# MM4250_QCodes_driver_commented.py
#
# A from-scratch QCoDeS driver for the Menlo Micro MM4250 -- a DC-10 GHz
# SP6T (single-pole, six-throw) cryogenic RF MEMS switch, controlled
# through its USB HiV Driver Board.
#
# This file is written to match the style of the other single-file
# drivers already in QCoDeS-Measurement-Framework/drivers/ (e.g.
# Spd1305X_QCodes_driver.py, SynthNVPro_QCodes_driver.py,
# TinySAQCodesDriver.py): one plain Python file, one class, the
# hardware-connection logic written directly inside that class instead
# of split across multiple files/abstraction layers -- and, just as
# importantly, the same *conventions* those files use: connect to real
# hardware unconditionally in __init__ (no software-only fallback),
# report status/errors with plain print() rather than self.log or raised
# exceptions, and keep type hints light-to-none. That's a deliberate
# simplification versus the earlier mm4250-qcodes-driver/
# mm4250-dut-fixture-driver folders next to this one, which split things
# into switch_states.py/transport.py/driver.py and lean on stricter
# typing -- fine for a standalone repo, but not how this framework's
# drivers are written. The plan is to move this exact file into
# QCoDeS-Measurement-Framework/drivers/ once it's solid.
#
# Every section below has a comment explaining *why* it's there, not
# just what it does -- read top to bottom for a guided tour of both this
# switch and of QCoDeS driver-writing in general.

import time
from enum import Enum as PyEnum
# ^ We import Python's built-in `Enum` under the name `PyEnum` because
# QCoDeS *also* has a validator class called `Enum` (imported below) --
# giving the built-in one a different local name avoids the two
# colliding/shadowing each other in this file.

from qcodes.instrument import Instrument
# ^ Every piece of lab hardware in QCoDeS is represented as a subclass
# of `Instrument`. Subclassing it is what gives us `add_parameter()`,
# automatic snapshotting (recording instrument state alongside your
# measurement data), a `name`, a `close()` you can override, etc. --
# all the plumbing so you don't have to write it yourself.

from qcodes.validators import Enum
# ^ NOTE for future-you: some of the older drivers in this framework
# (Spd1305X_QCodes_driver.py, Agilent33120A_driver.py,
# Moku_QCodes_driver.py) import this from `qcodes.utils.validators`
# instead. That import path no longer exists in the qcodes version
# installed in this project's `qcodes-conda` environment (qcodes 0.58) --
# I checked, it raises `ModuleNotFoundError`. `qcodes.validators` is the
# working path today, so that's what this file uses. Worth fixing those
# older files the same way at some point, but that's a separate task
# from this driver.

import hid
# ^ A thin wrapper around the cross-platform `hidapi` C library
# (`pip install hidapi`), imported at the top of the file like
# SynthNVPro_QCodes_driver.py imports `serial` -- this driver is
# hardware-only, so there's no reason to defer/hide the dependency the
# way a software-only-capable driver might.


# ======================================================================
# The physical truth table -- what the switch can actually do
# ======================================================================
# This section has NO hardware-connection logic in it at all -- it's
# just data, transcribed from Menlo's datasheet (Menlo/Switches.pdf,
# pages 4-5, "MM4250 - SP6T Cryogenic RF Switch Module" datasheet,
# Figure 1 and Table 5). Keeping it as plain data at the top of the file
# (rather than, say, hard-coded strings scattered through the class
# below) means it's easy to double check against the datasheet, and
# easy to reuse from more than one method.

class SP6TState(str, PyEnum):
    """
    Every position the MM4250's internal mechanical switch can be put
    in. It's "single pole, six throw": the common port RFC can connect
    to exactly one of 6 external RF ports (RF1-RF6) or 2 built-in
    calibration standards, or to nothing at all (ALL_OPEN) -- never more
    than one at once, since it's a physical mechanical switch.

    Subclassing *both* `str` and `Enum` is a small trick worth knowing:
    it means `SP6TState.RFC_RF3 == "RFC_RF3"` is `True`, so we can use
    plain strings when talking to QCoDeS (which wants plain,
    snapshot-friendly values, not custom objects) while still getting a
    real Python Enum's safety in our own code (a typo like
    `SP6TState.RFC_RFF3` fails immediately with an `AttributeError`
    instead of silently creating a new, wrong value).
    """
    ALL_OPEN = "ALL_OPEN"              # RFC connected to nothing (safe/default state)
    RFC_RF1 = "RFC_RF1"                # RFC -> external SMA port RF1
    RFC_RF2 = "RFC_RF2"                # RFC -> external SMA port RF2
    RFC_RF3 = "RFC_RF3"                # RFC -> external SMA port RF3
    RFC_RF4 = "RFC_RF4"                # RFC -> external SMA port RF4
    RFC_RF5 = "RFC_RF5"                # RFC -> external SMA port RF5
    RFC_RF6 = "RFC_RF6"                # RFC -> external SMA port RF6
    INTERNAL_LOAD = "INTERNAL_LOAD"    # RFC -> built-in 50 ohm calibration load
    INTERNAL_SHORT = "INTERNAL_SHORT"  # RFC -> built-in short calibration standard


# The 6 "numbered" states, in channel order (index 0 = channel 1, etc.)
# -- pulled out as its own tuple so code that wants "channel N" can look
# it up by plain list-indexing instead of an if/elif chain.
RF_CHANNEL_STATES = (
    SP6TState.RFC_RF1,
    SP6TState.RFC_RF2,
    SP6TState.RFC_RF3,
    SP6TState.RFC_RF4,
    SP6TState.RFC_RF5,
    SP6TState.RFC_RF6,
)


# Which USB HiV Driver Board "HV#" output lines get energized to ~89V
# for each state -- transcribed from Table 5 ("Applied HV Control vs. RF
# Switch States") in the datasheet. This isn't needed to *command* the
# switch (the driver board's own firmware does that translation once it
# receives a state-select command over USB) -- it's here purely so this
# code (and status messages) can cite real, checkable numbers instead of
# hand-waving, and so `active_hv_lines()` below has something real to
# return.
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

#: The driver board (and switch) needs about this long to physically
#: finish moving between states -- Table 2's "typical" USB switching
#: time (and confirmed again in the Cryogenic Operation App Note,
#: APN-0021, as the recommended settling time after a gate voltage
#: transition). We sleep this long after every commanded switch, so
#: code written against this driver behaves consistently.
TYPICAL_SWITCH_TIME_S = 0.025


# ======================================================================
# The real USB HID wire protocol
# ======================================================================
# Everything below comes from Menlo's programming package
# (Menlo/MM4250_Driver_Programming_Package_202511/, downloaded once
# Support Portal access came through): `mm4250.py` (their own reference
# driver) and `MM4250DriverBoardAPI.md` (the protocol writeup). Unlike
# the datasheet-derived data above, none of this could be verified
# before -- it's Menlo's private API, not published anywhere public.

#: The USB HiV Driver Board's HID vendor/product IDs. Real, known
#: values now (straight from `mm4250.py`) -- previously these had to be
#: discovered by hand with `hid.enumerate()` once a board was plugged
#: in (still a useful trick if you ever get a different board revision
#: with different IDs).
MENLO_VENDOR_ID = 0x04D8
MENLO_PRODUCT_ID = 0xEDFB

# The three commands the driver board's HID interface understands
# (MM4250DriverBoardAPI.md's "Commands" section). Every command is sent
# as `[0x00 (blank HID report-ID byte), <command byte>, ...up to 7 more
# bytes...]`.
_CMD_SET_OUTPUT_BUFFER = 0x00
#: Loads a new 6-byte HV-line bitmask into the board's *buffer* --
#: doesn't move the switch yet on its own (see _CMD_WRITE_CURRENT_BUFFER).
_CMD_READ_OUTPUT_BUFFER = 0x01
#: Reads back whatever bitmask is currently sitting in the buffer -- a
#: genuine hardware query, which is why `_get_state` below re-reads the
#: real hardware every time instead of trusting our own in-memory
#: bookkeeping.
_CMD_WRITE_CURRENT_BUFFER = 0x02
#: Actually *applies* the buffered bitmask to the physical HV outputs.
#: Nothing happens to the real switch until this is sent -- Set Output
#: Buffer + Write Current Buffer is always a two-step sequence.

#: The exact 6 bytes to load into the output buffer for each state, to
#: energize the right HV lines -- transcribed from `mm4250.py`'s
#: `CHANNEL_BITMASK`/`ALL_CHANNELS_OFF`/`INPUT_ECAL_LOAD_COMMAND`/
#: `INPUT_ECAL_SHORT_COMMAND`. Each state's 6 bytes are really two
#: identical 3-byte halves -- one per Micro-D connector (one per
#: physical MM4250 switch module) -- which is exactly the hardware fact
#: that this fixture's two switches are ganged to one driver board and
#: always move together: there's only one buffer to set, and it always
#: gets mirrored onto both switches at once.
#:
#: Cross-checked against STATE_HV_PINS above (a completely
#: independently-sourced table, from the public datasheet rather than
#: this private API doc) during development -- decoding these bytes
#: bit-by-bit into HV# line names reproduces STATE_HV_PINS exactly for
#: every state, which is a strong sign neither table has a transcription
#: error in it.
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


def channel_to_state(channel):
    """Turn a human-friendly channel number (1-6) into the matching
    `SP6TState`. Raises `ValueError` outside that range -- use the
    `SP6TState` names directly (`ALL_OPEN`, `INTERNAL_LOAD`,
    `INTERNAL_SHORT`) for the 3 non-numbered positions."""
    if not 1 <= channel <= 6:
        raise ValueError(f"channel must be 1-6, got {channel!r}")
    return RF_CHANNEL_STATES[channel - 1]


def state_to_channel(state):
    """Inverse of `channel_to_state`. Returns `None` for `ALL_OPEN`,
    `INTERNAL_LOAD`, or `INTERNAL_SHORT`, since those aren't one of the
    6 numbered channels."""
    if state in RF_CHANNEL_STATES:
        return RF_CHANNEL_STATES.index(state) + 1
    return None


# ======================================================================
# The QCoDeS instrument itself
# ======================================================================

class MM4250(Instrument):
    """
    QCoDeS driver for the Menlo Micro MM4250 SP6T cryogenic RF switch,
    via its USB HiV Driver Board. Its whole job is to hold and change
    which of the 9 `SP6TState` positions the switch is currently in --
    nothing about calibration workflows, DUT bookkeeping, or automated
    sweeps belongs in this file; that's for whatever measurement script
    *uses* this driver to build.

    This driver is hardware-only, matching SynthNVPro_QCodes_driver.py
    and Spd1305X_QCodes_driver.py in this framework: it connects for
    real the moment you construct it, the same way SynthNVPro opens its
    serial port unconditionally in __init__. There's no
    software-only/no-hardware mode here -- if you want to read through
    the driver without a board plugged in, just read the code below.

    Parameters
    ----------
    name:
        QCoDeS instrument name, e.g. "mm4250". Every QCoDeS instrument
        needs one -- it's how you look the instrument back up later
        (`Instrument.find_instrument("mm4250")`) and how it's labeled in
        snapshots/saved data.
    vendor_id, product_id:
        The USB HiV Driver Board's HID vendor/product IDs. Default to
        `MENLO_VENDOR_ID`/`MENLO_PRODUCT_ID` (the module-level constants
        above), so the common case is just `MM4250("mm4250")` with a
        real board plugged in. Only pass different values if you ever
        encounter a board revision with different IDs (see
        `_connect_to_hardware`'s docstring for how to find them by
        hand).
    **kwargs:
        Forwarded to `qcodes.instrument.Instrument.__init__` (lets you
        pass things like `metadata=...` without this driver needing to
        know about every possible option).

    Examples
    --------
    >>> switch = MM4250("mm4250")   # connects immediately, forces ALL_OPEN
    >>> switch.channel(3)           # really energizes the RF3 HV lines
    >>> switch.state()              # really reads the buffer back over HID
    'RFC_RF3'
    >>> switch.close()
    """

    def __init__(self, name, vendor_id=MENLO_VENDOR_ID, product_id=MENLO_PRODUCT_ID, **kwargs):
        # Always call the parent class's __init__ first. This is what
        # actually registers the instrument with QCoDeS (adds it to the
        # global instrument registry under `name`, sets up
        # `self.parameters`, `self.metadata`, `self.log`, etc.) --
        # nothing below here would work without it.
        super().__init__(name, **kwargs)

        # Our best-known current state. Kept in sync below every time we
        # command or read the hardware, so it's always accurate rather
        # than something we have to separately query first.
        self._state = SP6TState.ALL_OPEN

        # Connect for real, right here, unconditionally -- exactly like
        # SynthNVPro_QCodes_driver.py opens its serial port in __init__
        # with no "should I connect?" branch. If no board is plugged in,
        # `self._device.open(...)` raises and construction fails loudly,
        # which is the correct behavior for a hardware-only driver.
        self._device = hid.device()
        self._device.open(vendor_id, product_id)
        print(f"Connected to MM4250 driver board (VID=0x{vendor_id:04x}, PID=0x{product_id:04x}).")

        # Force the switch to the safe, de-energized ALL_OPEN state the
        # moment we connect, before anything else can touch it. This
        # mirrors Menlo's own reference driver (`mm4250.py`'s `open()`
        # re-sends `ALL_CHANNELS_OFF` right after opening the device),
        # and it's not just belt-and-braces habit: the Cryogenic
        # Operation App Note (APN-0021) is explicit that all channels
        # must be open *before and throughout cooldown*, or a channel
        # left closed can get mechanically stuck at cryo temperatures.
        # Starting every connection from a known-open state makes that
        # much harder to get wrong by accident.
        self._send_state_to_hardware(SP6TState.ALL_OPEN)

        # --- Register the QCoDeS Parameter -------------------------------
        #
        # This is the one line that actually makes this a *QCoDeS*
        # driver instead of just a Python class with methods. Instead of
        # writing separate `get_state()`/`set_state()` methods, we
        # register a single `Parameter` named "state":
        self.add_parameter(
            name="state",
            label="SP6T switch state",
            # get_cmd/set_cmd are plain Python callables (they don't
            # have to be SCPI strings -- that's only true for
            # VisaInstrument subclasses like Spd1305X). QCoDeS calls
            # these for you whenever you do `switch.state()` (runs
            # get_cmd) or `switch.state("RFC_RF3")` (runs set_cmd with
            # that value).
            get_cmd=self._get_state,
            set_cmd=self._set_state,
            # `vals=` is a *validator*: QCoDeS checks the value against
            # it BEFORE calling set_cmd. Enum(*[...]) means "must be
            # exactly one of these strings" -- so
            # `switch.state("RFC_RF9")` raises immediately, with a clear
            # error, instead of reaching `_set_state` and doing
            # something undefined on real 90V hardware. This is the
            # single biggest reason to use a QCoDeS Parameter instead of
            # a plain method.
            vals=Enum(*[s.value for s in SP6TState]),
            docstring="Full switch position: 'ALL_OPEN', 'RFC_RF1'..'RFC_RF6', 'INTERNAL_LOAD', or 'INTERNAL_SHORT'."
        )

        # A second Parameter, "channel" -- a friendlier 1-6 view of the
        # same underlying state, for the common case where you don't
        # care about ALL_OPEN/calibration standards and just want "RF
        # port number". Two Parameters can represent the same physical
        # quantity from different angles like this; nothing stops you
        # from mixing and matching `state()` and `channel()` on the same
        # instrument.
        self.add_parameter(
            name="channel",
            label="Active RF channel",
            get_cmd=self._get_channel,
            set_cmd=self._set_channel,
            vals=Enum(1, 2, 3, 4, 5, 6),
            docstring="Convenience 1-6 view of `state`, matching the RF1-RF6 SMA ports."
        )

    # ------------------------------------------------------------------
    # Parameter get_cmd / set_cmd implementations
    # ------------------------------------------------------------------
    # QCoDeS calls these for us -- we never call them directly from
    # outside this class (well-behaved QCoDeS drivers go through
    # `switch.state(...)`, not `switch._set_state(...)`, so that
    # validation/logging/snapshotting always happen).

    def _get_state(self):
        # Actually re-read the board's output buffer over HID rather
        # than trusting our own bookkeeping -- see
        # _read_output_buffer_from_hardware/_decode_state below. Keep
        # self._state in sync with what we just read.
        wire_bytes = self._read_output_buffer_from_hardware()
        state = self._decode_state(wire_bytes)
        self._state = state
        # `.value` unwraps the SP6TState enum member back down to its
        # plain string ("RFC_RF3"), since that's the plain data type
        # QCoDeS Parameters are meant to hand back.
        return state.value

    def _set_state(self, value):
        # `value` already passed the `vals=Enum(...)` check in
        # add_parameter, so we know it's a legal state name here --
        # SP6TState(value) turns the validated string back into our
        # richer enum type so the rest of this method can work with it.
        state = SP6TState(value)
        self._send_state_to_hardware(state)
        # Only update our tracked state after actually attempting to
        # send it -- if `_send_state_to_hardware` raises (e.g. a real
        # HID write/connection error), `self._state` is left unchanged,
        # which is the correct, honest behavior: we shouldn't claim to
        # be in a new position we never actually reached.
        self._state = state

    def _get_channel(self):
        channel = state_to_channel(self._state)
        if channel is None:
            # `self._state` is ALL_OPEN or one of the calibration
            # standards -- neither has a channel number. Print a
            # warning and hand back `None` rather than raising, matching
            # the permissive, print()-based error handling this
            # framework's other drivers use (see e.g.
            # SynthNVPro_QCodes_driver.py's parse-failure handling).
            print(f"[WARNING] Switch is on {self._state.value}, not a numbered RF channel")
        return channel

    def _set_channel(self, value):
        # Reuse the `state` Parameter's own setter (which itself does
        # the actual work) instead of duplicating that logic here --
        # `self.state(...)` re-runs `state`'s validator too, for free.
        self.state(channel_to_state(value).value)

    # ------------------------------------------------------------------
    # Talking to the hardware
    # ------------------------------------------------------------------

    def _send_state_to_hardware(self, state):
        """
        Actually move the switch to `state`. This is a *two-step* HID
        write, per MM4250DriverBoardAPI.md -- setting the output buffer
        alone does nothing to the physical switch until the second
        command tells the board to actually apply it.
        """
        wire_bytes = self._encode_state(state)

        # Step 1 -- Set Output Buffer: [blank byte, command byte,
        # 6 HV-bitmask bytes]. This only loads the buffer; the
        # physical HV outputs haven't changed yet.
        self._device.write([0x00, _CMD_SET_OUTPUT_BUFFER, *wire_bytes])

        # Step 2 -- Write Current Buffer: [blank byte, command byte].
        # *This* is the command that actually energizes/de-energizes
        # the HV lines to match whatever was just buffered.
        self._device.write([0x00, _CMD_WRITE_CURRENT_BUFFER])

        # Wait for the switch to physically settle before returning --
        # see TYPICAL_SWITCH_TIME_S's definition above.
        time.sleep(TYPICAL_SWITCH_TIME_S)

    def _encode_state(self, state):
        """
        Turn `state` into the 6-byte HV-line bitmask the board expects
        in its output buffer -- a plain lookup in the `STATE_WIRE_BYTES`
        table above (from Menlo's programming package).
        """
        return STATE_WIRE_BYTES[state]

    def _decode_state(self, wire_bytes):
        """
        Inverse of `_encode_state`: turn 6 raw buffer bytes read back
        from the board into the `SP6TState` they represent. Used by
        `_get_state`.

        Raises `ValueError` if the bytes don't match any known state --
        this would mean either a bit flip in transit, or a channel
        combination this driver doesn't know about (e.g. if something
        else already wrote a raw bitmask straight to the board outside
        this driver). Unlike the friendlier print()-and-continue style
        used elsewhere in this file, this one really does raise: there's
        no sane state to fall back to if we can't tell where the switch
        actually is.
        """
        wire_bytes = tuple(wire_bytes)
        for candidate_state, candidate_bytes in STATE_WIRE_BYTES.items():
            if candidate_bytes == wire_bytes:
                return candidate_state
        raise ValueError(
            f"Buffer bytes {wire_bytes!r} read back from the driver "
            "board don't match any known SP6TState -- see "
            "STATE_WIRE_BYTES."
        )

    def _read_output_buffer_from_hardware(self):
        """
        Ask the board (command `_CMD_READ_OUTPUT_BUFFER`) what's
        currently in its output buffer, and return the 6 HV-bitmask
        bytes out of the response.

        Per MM4250DriverBoardAPI.md, a command that expects a response
        gets back `[echoed command byte, 7 more bytes]` (8 bytes total)
        -- so byte 0 of the response is just `_CMD_READ_OUTPUT_BUFFER`
        echoed back (ignored here), bytes 1-6 are the 6 HV-bitmask
        bytes we actually want, and byte 7 is unused padding.
        """
        self._device.write([0x00, _CMD_READ_OUTPUT_BUFFER])
        response = self._device.read(8)
        return tuple(response[1:7])

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------
    # Plain Python methods (not Parameters) for actions rather than
    # values -- same pattern SPD1305X uses for `output_on()`/
    # `output_off()`. Both of these are just thin, readable wrappers
    # around setting `state`.

    def open_all(self):
        """Disconnect RFC from everything -- the switch's safe/default
        state (Table 5's "All Open" row: no HV lines energized).

        Also the state every channel must be in before and throughout a
        dilution-fridge cooldown (Cryogenic Operation App Note,
        APN-0021) -- a channel left closed while cooling can get
        mechanically stuck closed at cryo temperatures (recoverable by
        warming to 30-80 K, not permanent damage, but disruptive). Call
        this before starting a cooldown."""
        self.state(SP6TState.ALL_OPEN.value)

    def active_hv_lines(self):
        """Which USB HiV Driver Board 'HV#' lines *should* be energized
        for the current state, straight from the datasheet's Table 5.
        Doesn't talk to hardware -- purely a debugging/teaching
        helper."""
        return STATE_HV_PINS[self._state]

    # ------------------------------------------------------------------
    # Required/conventional QCoDeS Instrument methods
    # ------------------------------------------------------------------

    def get_idn(self):
        """
        QCoDeS instruments conventionally implement `get_idn()` --
        usually by sending a SCPI `*IDN?` query. The MM4250/driver board
        has no such query, so we return fixed identity info from the
        datasheet instead, matching how Spd1305X_QCodes_driver.py's
        get_idn() returns a hand-written dict rather than querying real
        hardware. This is what shows up in
        `switch.print_readable_snapshot()` and in any QCoDeS station
        snapshot saved alongside your data -- worth having even though
        it's static, so your saved data always records *which*
        instrument produced it.
        """
        return {
            "vendor": "Menlo Micro",
            "model": "MM4250 (SP6T Cryogenic RF Switch, via USB HiV Driver Board)",
            "serial": None,
            "firmware": None,
        }

    def close(self):
        """
        Disconnect the HID device, then let QCoDeS tear down the
        Instrument itself (removes it from the global instrument
        registry, closes its Parameters, etc.), the same way
        SynthNVPro_QCodes_driver.py's close() closes its serial port
        before calling super().close().

        `getattr(self, "_device", None)` rather than `self._device`
        directly: QCoDeS's own `Instrument.close()` strips every
        instance attribute once it runs, so this guards against being
        called a second time on an already-closed instrument (e.g. a
        notebook cell re-run, or a test fixture's teardown).
        """
        device = getattr(self, "_device", None)
        if device is not None:
            device.close()
        super().close()
