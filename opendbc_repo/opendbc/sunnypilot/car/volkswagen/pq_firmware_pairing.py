"""Pure, inactive firmware-identity prerequisite for the new PQ ICBM path.

The caller must independently establish that expected_signature comes from an
approved signed artifact. This comparison does not approve or verify an artifact,
attest hardware, load files/keys, or enable Card, flags, a backend, or a device.
No expected signature is supplied here; absent approval evidence stays false.
"""
from hmac import compare_digest


def paired_pq_icbm(raw_signatures: bytes | None, expected_signature: bytes | None = None, panda_count: int = 0) -> bool:
  """Match exactly one Panda's raw 128-byte signature against approved identity.

  Multiple/disconnected Pandas and malformed inputs fail closed. Binary commas
  are signature bytes; no splitting, stripping, decoding, or normalization occurs.
  """
  if type(panda_count) is not int or panda_count != 1:
    return False
  if not isinstance(raw_signatures, bytes) or not isinstance(expected_signature, bytes):
    return False
  if len(raw_signatures) != 128 or len(expected_signature) != 128:
    return False
  return compare_digest(raw_signatures, expected_signature)
