"""Tests for plots.read_vna_csv. The real Sep 11 exports live under
Sweeps/, which is gitignored, so small files in the same format stand in.
Run from the repo root:

    python -m pytest tests/test_read_vna_csv.py
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "measurements"))

from plots import read_vna_csv  # noqa: E402

# Same shape as a P5004B front-panel export, Windows line endings included.
EXPORT = (
    "!CSV A.01.01\r\n"
    "!Keysight Technologies,P5004B,MY61400324,A.19.30.21\r\n"
    "!Date: Friday, September 11, 2026 14:48:21\r\n"
    "!Source: Standard\r\n"
    "\r\n"
    "BEGIN CH1_DATA\r\n"
    "Freq(Hz),{column}\r\n"
    "1000000,-0.18012078\r\n"
    "11009009.009009,-0.20479658\r\n"
    "10000000000,-3.1332707\r\n"
    "END\r\n"
    "\r\n"
)


def write(tmp_path, column):
    path = tmp_path / "RF1_S21.csv"
    path.write_bytes(EXPORT.format(column=column).encode())
    return path


def test_reads_frequency_and_db(tmp_path):
    freq_hz, db = read_vna_csv(write(tmp_path, "S21 Log Mag(dB)"))
    np.testing.assert_array_equal(freq_hz, [1e6, 11009009.009009, 1e10])
    np.testing.assert_array_equal(db, [-0.18012078, -0.20479658, -3.1332707])


def test_rejects_non_log_mag_export(tmp_path):
    with pytest.raises(ValueError, match="Log Mag"):
        read_vna_csv(write(tmp_path, "S21 Phase(deg)"))
