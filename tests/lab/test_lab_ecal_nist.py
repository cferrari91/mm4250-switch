"""
The lab e-cal against NIST's own output: their end-user notebook
(../Archive/mm4250-ecal/ecal.ipynb, scikit-rf OnePort with the internal
open/short/load and per-port tier-2 ideals) ships its inputs (rawdata/,
ideals/) and results (correcteddata/). Read here as data only; nothing
from that repo is run. Skipped if the folder isn't there.
"""

from pathlib import Path

import numpy as np
import pytest

from conftest import LAB, ecal

NIST_NB = LAB.parents[2] / "Archive" / "mm4250-ecal"
needs_nist = pytest.mark.skipif(not (NIST_NB / "correcteddata").is_dir(),
                                reason="../Archive/mm4250-ecal isn't beside this repo")


def _take_from_nist_files():
    """NIST's raw files in the shape db.load_take returns (S11 only, one repeat, no after set)."""
    raw = NIST_NB / "rawdata"
    run = lambda path: {"run_id": None, "data": {"S11": ecal.read_s1p(path)[1]}, "meta": {}}
    freq = ecal.read_s1p(raw / "port1.s1p")[0]
    std_files = {"ALL_OPEN": "ecal_open", "INTERNAL_SHORT": "ecal_short", "INTERNAL_LOAD": "ecal_load"}
    block = {"before": {s: run(raw / f"{f}.s1p") for s, f in std_files.items()},
             "after": {},
             "ports": {n: run(raw / f"port{n}.s1p") for n in range(1, 7)}}
    for part in ("before", "ports"):
        for r in block[part].values():
            assert np.array_equal(ecal.read_s1p(raw / "port1.s1p")[0], freq)
    return {"take_id": "nist-end-user", "freq": freq, "temp_k": None, "channels": list(range(1, 7)),
            "cfg": {"correct_sparam": "S11", "ports": {f"RF{n}": f"port {n}" for n in range(1, 7)}},
            "repeats": [block]}


@needs_nist
def test_reproduces_nists_corrected_data_on_all_six_ports():
    take = _take_from_nist_files()
    result = ecal.correct_take(take, definitions=NIST_NB / "ideals")
    worst = 0.0
    for n in range(1, 7):
        f, want = ecal.read_s1p(NIST_NB / "correcteddata" / f"port{n}_corrected.s1p")
        np.testing.assert_allclose(result["freq"], f, rtol=1e-12)
        got = result["corrected"][0][n]
        worst = max(worst, np.max(np.abs(got - want)))
    print(f"worst |difference| from NIST's corrected data: {worst:.2g}")
    assert worst < 1e-11


@needs_nist
def test_nist_end_user_ideals_match_the_tier2_3k_files():
    tier2 = LAB.parents[2] / "nist_MM4250_calibration_data_2025" / "tier2_scikitrf_caldata" / "tier2_3k1"
    if not tier2.is_dir():
        pytest.skip("tier2 data not here")
    for n in range(1, 7):
        for s in ("open", "short", "load"):
            a = (NIST_NB / "ideals" / f"ideal_port{n}_{s}.s1p").read_bytes()
            b = (tier2 / f"port{n}_{s}_tier2.s1p").read_bytes()
            assert a == b


def test_nist_definitions_for():
    assert ecal.nist_definitions_for(295.3) == "295K"
    assert ecal.nist_definitions_for("10mK") == "3K"
    assert ecal.nist_definitions_for(None) is None
    assert ecal.nist_definitions_for(60.0) == "3K"            # with a warning
    with pytest.raises(ValueError):
        ecal.nist_definitions_for("warm")


def test_find_definitions_on_this_laptop():
    if not (LAB.parents[2] / "nist_MM4250_calibration_data_2025").is_dir():
        pytest.skip("NIST data not beside this repo")
    assert ecal.find_definitions("295K").name == "tier2_295k1"
    assert ecal.find_definitions("3K").name == "tier2_3k1"
    with pytest.raises(ValueError):
        ecal.find_definitions("77K")


def test_find_definitions_prefers_a_copy_beside_the_code(tmp_path):
    folder = tmp_path / "ideals_295K"
    folder.mkdir()
    (folder / "port1_open_tier2.s1p").write_text("# HZ S RI R 50\n1 1 0\n")
    assert ecal.find_definitions("295K", start=tmp_path / "lab") == folder
