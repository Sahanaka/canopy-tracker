"""Unit tests for SCL and QA_PIXEL masks (synthetic arrays)."""

import numpy as np
import pytest

from highland.masking import (
    SCL_CLASSES,
    qa_clear,
    qa_flags,
    scl_class_shares,
    scl_clear,
)


def test_scl_classes_complete() -> None:
    assert sorted(SCL_CLASSES) == list(range(12))


def test_scl_shares_sum_to_one_and_ignore_outside() -> None:
    scl = np.array([[4, 4, 2, 7], [9, 0, 5, 5]], dtype=np.uint8)
    inside = np.array([[1, 1, 1, 1], [0, 0, 1, 1]], dtype=bool)
    sh = scl_class_shares(scl, inside)
    assert sum(sh.values()) == pytest.approx(1.0)
    assert sh[4] == pytest.approx(2 / 6)
    assert sh[5] == pytest.approx(2 / 6)
    assert sh[2] == pytest.approx(1 / 6)
    assert sh[7] == pytest.approx(1 / 6)
    assert sh[9] == 0.0 and sh[0] == 0.0  # outside pixels are ignored


def test_scl_shares_errors() -> None:
    with pytest.raises(ValueError):
        scl_class_shares(np.zeros((2, 2), np.uint8), np.zeros((2, 2), bool))
    with pytest.raises(ValueError):
        scl_class_shares(np.full((2, 2), 12, np.uint8), np.ones((2, 2), bool))
    with pytest.raises(ValueError):
        scl_class_shares(np.zeros((2, 2), np.uint8), np.ones((3, 3), bool))


@pytest.mark.parametrize(
    ("k2", "k7", "expected"),
    [
        (False, False, {4, 5, 6}),
        (True, False, {2, 4, 5, 6}),
        (False, True, {4, 5, 6, 7}),
        (True, True, {2, 4, 5, 6, 7}),
    ],
)
def test_scl_clear_variants(k2: bool, k7: bool, expected: set[int]) -> None:
    scl = np.arange(12, dtype=np.uint8)
    got = {int(c) for c in scl[scl_clear(scl, keep_cast_shadow=k2, keep_unclassified=k7)]}
    assert got == expected


def _bit(*bits: int) -> int:
    return sum(1 << b for b in bits)


def test_qa_single_bits_l47_ignore_bit2_and_bit6() -> None:
    for b in (0, 1, 3, 4, 5):
        assert not qa_clear(np.array([_bit(b)], np.uint16), "landsat-7")[0]
    # Bit 2 is unused on L4-7; bit 6 (clear) and the confidence bits never matter.
    for b in (2, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
        assert qa_clear(np.array([_bit(b)], np.uint16), "landsat-5")[0]


def test_qa_cirrus_toggle_l89() -> None:
    qa = np.array([_bit(2)], np.uint16)
    for p in ("landsat-8", "landsat-9"):
        assert not qa_clear(qa, p)[0]
        assert qa_clear(qa, p, mask_cirrus=False)[0]
    assert "cirrus" in qa_flags(qa, "landsat-8")
    assert "cirrus" not in qa_flags(qa, "landsat-7")


def test_qa_bit6_no_effect() -> None:
    qa = np.array([0, _bit(6)], np.uint16)
    assert qa_clear(qa, "landsat-8").all()


def test_qa_documented_example_values() -> None:
    # Example values of LSDS-1618 Table 5-6, decoded here from their binary bits:
    # 1 = fill; 5440 = bits 6,8,10,12; 5442 = 5440 + dilated (1); 5896 = cloud (3);
    # 7440 = cloud shadow (4); 13664 = snow (5) with bits 6,8,10,12,13.
    vals = np.array([1, 5440, 5442, 5896, 7440, 13664], np.uint16)
    expected_clear = [False, True, False, False, False, False]
    assert qa_clear(vals, "landsat-7").tolist() == expected_clear
    assert qa_clear(vals, "landsat-9").tolist() == expected_clear
    f = qa_flags(vals, "landsat-8")
    assert f["fill"].tolist() == [True, False, False, False, False, False]
    assert f["dilated"].tolist() == [False, False, True, False, False, False]
    assert f["cloud"].tolist() == [False, False, False, True, False, False]
    assert f["shadow"].tolist() == [False, False, False, False, True, False]
    assert f["snow"].tolist() == [False, False, False, False, False, True]


def test_qa_uint16_high_bits_no_overflow() -> None:
    qa = np.array([0xFFFF, 0x8000], np.uint16)
    f = qa_flags(qa, "landsat-9")
    assert f["fill"].tolist() == [True, False]
    assert f["cirrus"].tolist() == [True, False]


def test_qa_errors() -> None:
    with pytest.raises(ValueError):
        qa_flags(np.zeros(2, np.uint16), "sentinel-2a")
    with pytest.raises(TypeError):
        qa_flags(np.zeros(2, np.float32), "landsat-8")
