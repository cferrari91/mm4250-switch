# mm4250-switch-sweep

QCoDeS driver for the Menlo Micro MM4250 (SP6T cryogenic RF MEMS switch,
via its USB HiV Driver Board), plus tooling to sweep a full 2-port
S-parameter measurement across all 6 RF channels on a Keysight VNA.

Extracted from a larger lab measurement framework repo, trimmed down to
just what this driver + sweep need to run standalone.

## Layout

- `drivers/MM4250_QCodes_driver.py` -- the switch driver. Hardware-only
  (connects for real in `__init__`, no software-only mode), matching the
  plain, print()-based style of this framework's other single-file
  drivers rather than heavier type-hinted/defensive code.
- `drivers/MM4250_QCodes_driver_commented.py` -- the same driver, heavily
  commented, meant to be read top to bottom as a tutorial on both this
  switch and QCoDeS driver-writing in general.
- `drivers/KeysightVNA_driver.py` + `drivers/N52xx.py` -- the Keysight
  P5004B VNA driver (`KeysightVNA_driver.py` is a thin subclass of the
  base PNA driver in `N52xx.py`). Needed by the sweep code; not written
  as part of this project.
- `scripts/sparam_sweep.py` -- `ensure_full_sparam_traces`,
  `measure_2port`, `save_s2p`, and `sweep_all_channels`: sets the switch,
  triggers one VNA sweep per channel, and writes a standard Touchstone
  `.s2p` file per RF port.
- `switch_matrix_sweep.ipynb` -- runnable notebook: connects to the VNA
  and switch, then sweeps RF1-RF6 and saves each to
  `Sweeps/<date>_<temp>/<switch_serials>/RF<n>/RF<n>.s2p`.
- `docs/MM4250_Instructions.md` -- driver usage notes and status.

## Before running a sweep

- Set `THRU_CHANNEL` in `scripts/sparam_sweep.py` to whichever RF port is
  wired as a straight thru cable between the two switches (used as the
  thru standard for a full 2-port cal through the switch matrix). It's
  measured identically to every other channel -- this constant only
  controls how it's labeled in the sweep's printed log.
- Edit `date_str`/`temp_str`/`switch_serials` in `switch_matrix_sweep.ipynb`
  to match the run.

## Dependencies

- Python 3.9+
- `qcodes`
- `pyvisa` (VNA, VISA/TCPIP)
- `hidapi` (`pip install hidapi`) (switch, USB HID)
- `numpy`

## Hardware

- Menlo Micro MM4250 switch + USB HiV Driver Board (VID `0x04D8`, PID
  `0xEDFB`)
- Keysight P5004B VNA (9 kHz-20 GHz, 2-port)
