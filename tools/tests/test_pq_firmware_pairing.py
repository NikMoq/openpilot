"""Pure pairing tests using synthetic bytes, never an approved firmware identity."""
import importlib.util
from pathlib import Path

import pytest


_SOURCE = (Path(__file__).resolve().parents[2] /
           "opendbc_repo/opendbc/sunnypilot/car/volkswagen/pq_firmware_pairing.py")
_SPEC = importlib.util.spec_from_file_location("pq_firmware_pairing_under_test", _SOURCE)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
paired_pq_icbm = _MODULE.paired_pq_icbm

# Arbitrary fixtures only; equality with them does not approve a real artifact.
SIGNATURE = bytes(range(128))


def test_single_panda_exact_signature_matches():
  assert paired_pq_icbm(SIGNATURE, SIGNATURE, 1)


def test_missing_expected_signature_defaults_false():
  assert not paired_pq_icbm(SIGNATURE, panda_count=1)
  assert not paired_pq_icbm(SIGNATURE, None, 1)
  assert not paired_pq_icbm(SIGNATURE)


@pytest.mark.parametrize("count", [0, 2, 3, -1, True, False, 1.0, "1", None])
def test_disconnected_multiple_or_invalid_panda_count_fails_closed(count):
  assert not paired_pq_icbm(SIGNATURE, SIGNATURE, count)


@pytest.mark.parametrize("invalid", [None, "x"*128, bytearray(128), memoryview(bytes(128)),
                                    [0]*128, 128, True, b"", bytes(127), bytes(129)])
@pytest.mark.parametrize("position", ["raw", "expected"])
def test_malformed_signature_types_and_lengths_fail_closed(invalid, position):
  raw, expected = (invalid, SIGNATURE) if position == "raw" else (SIGNATURE, invalid)
  assert not paired_pq_icbm(raw, expected, 1)


@pytest.mark.parametrize("offset", [0, 44, 64, 127])
def test_any_signature_mismatch_fails(offset):
  changed = bytearray(SIGNATURE)
  changed[offset] ^= 1
  assert not paired_pq_icbm(bytes(changed), SIGNATURE, 1)


@pytest.mark.parametrize("signature", [b","*128, b"," + bytes(126) + b",", b"\0, \n"*32])
def test_binary_commas_and_whitespace_are_preserved_exactly(signature):
  assert len(signature) == 128
  assert paired_pq_icbm(signature, signature, 1)
  assert not paired_pq_icbm(signature + b",", signature, 1)


def test_multi_signature_blob_never_matches_even_if_one_record_matches():
  joined = SIGNATURE + b"," + b","*128
  assert not paired_pq_icbm(joined, SIGNATURE, 2)
  assert not paired_pq_icbm(joined, SIGNATURE, 1)


def test_whitespace_is_not_normalized_away():
  signature = b" " + bytes(126) + b"\n"
  assert paired_pq_icbm(signature, signature, 1)
  assert not paired_pq_icbm(signature[1:-1], signature, 1)
  assert not paired_pq_icbm(b" " + SIGNATURE + b"\n", SIGNATURE, 1)
