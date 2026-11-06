"""Raw RLE vectors; no speed unit or camera-control assumptions."""
import importlib
from pathlib import Path
import sys
import types

import pytest

PACKAGE = '_yandex_native_limits_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
wire = importlib.import_module(PACKAGE + '.native_codec')
limits = importlib.import_module(PACKAGE + '.native_limits')


def snapshot(metadata, counts):
  return {'section_metadata_wire': tuple(metadata), 'section_segment_counts': tuple(counts)}


def test_fixed_two_run_vector():
  # Section5 -> SpeedLimits1[float1=1,count2=2],1[float1=2,count2=1].
  body = bytes.fromhex('2a120a070d0000803f10020a070d000000401001')
  assert limits.decode_section_limits(body, 3) == (1.0, 1.0, 2.0)


def test_unknown_speed_is_not_zero():
  assert limits.decode_section_limits(bytes.fromhex('2a040a021002'), 2) == (None, None)
  assert limits.decode_section_limits(bytes.fromhex('2a090a070d000000001001'), 1) == (0.0,)


def test_missing_sections_and_partial_absence():
  assert limits.decode_section_limits(None, 2) == (None, None)
  assert limits.decode_section_limits(b'', 1) == (None,)
  result = limits.decode_route_limits(snapshot([None, bytes.fromhex('2a090a070d0000803f1001'), b''], [2, 1, 1]))
  assert result == (None, None, 1.0, None)


@pytest.mark.parametrize('body,count', [
  ('2a00', 1), ('2a040a021002', 1), ('2a040a021001', 2), ('2a040a021000', 1),
  ('2a020a00', 1), ('2a0d0a0b10ffffffffffffffffff01', 1),
  ('2a090a070d0000807f1001', 1), ('2801', 1), ('2a002a00', 0),
  ('2a040a021001', 0), ('2a020801', 1),
])
def test_bad_runs(body, count):
  with pytest.raises(wire.WireError):
    limits.decode_section_limits(bytes.fromhex(body), count)


@pytest.mark.parametrize('count', [-1, True, 1.5, 100_001])
def test_bad_expected_count(count):
  with pytest.raises(wire.WireError):
    limits.decode_section_limits(None, count)


def test_zero_section_and_unknown_other_fields():
  assert limits.decode_section_limits(b'', 0) == ()
  assert limits.decode_section_limits(bytes.fromhex('0801'), 1) == (None,)


def test_snapshot_mismatch():
  with pytest.raises(wire.WireError):
    limits.decode_route_limits(snapshot([None], []))


def test_global_expansion_budget():
  with pytest.raises(wire.WireError):
    limits.decode_route_limits(snapshot([None, None], [60_000, 60_000]))


def test_aggregate_wire_budget(monkeypatch):
  monkeypatch.setattr(wire, 'MAX_FIELDS', 5)
  body = bytes.fromhex('2a040a021001')
  with pytest.raises(wire.WireError):
    limits.decode_route_limits(snapshot([body, body], [1, 1]))


def test_route_byte_budget(monkeypatch):
  monkeypatch.setattr(limits, 'MAX_ROUTE_BYTES', 1)
  with pytest.raises(wire.WireError):
    limits.decode_route_limits(snapshot([b'\x08\x01'], [1]))
