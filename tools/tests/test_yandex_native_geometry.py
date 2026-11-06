import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("native_geometry", Path(__file__).resolve().parents[2] / "sunnypilot/mapd/yandex/native_geometry.py")
geometry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geometry)


def test_fixed_vector_and_negative_deltas():
  wire = bytes.fromhex("0a060802120203001206080412020302")
  points = ((1, 2), (-1, 0), (-1, 1))
  assert geometry.decode_polyline_scaled(wire) == points
  assert geometry.encode_polyline_scaled(points) == wire


@pytest.mark.parametrize("wire", [b"", b"\x0a", b"\x0a\x02\x08", b"\x18\x00",
                                  b"\x0a\x02\x08\x00", b"\x0a\x02\x18\x00",
                                  b"\x0a\x04\x08\x00\x08\x01",
                                  b"\x0a\x06\x08\xff\xff\xff\xff\x1f"])
def test_malformed_rejected(wire):
  with pytest.raises(ValueError):
    geometry.decode_polyline_scaled(wire)


@pytest.mark.parametrize("points", [[], [(True, 0)], [(1.0, 0)], [(float("nan"), 0)],
                                    [(180_000_001, 0)], [(0, -90_000_001)], [(1,)]])
def test_invalid_points(points):
  with pytest.raises(ValueError):
    geometry.encode_polyline_scaled(points)


def test_join_preserves_internal_duplicates():
  first = geometry.encode_polyline_scaled(((0, 0), (1, 2), (1, 2)))
  second = geometry.encode_polyline_scaled(((1, 2), (3, 4)))
  assert geometry.decode_polyline_scaled(geometry.join_polylines([first, second])) == ((0, 0), (1, 2), (1, 2), (3, 4))
  with pytest.raises(ValueError, match="disconnected"):
    geometry.join_polylines([second, first])
  with pytest.raises(ValueError):
    geometry.join_polylines([])


def test_budgets(monkeypatch):
  wire = geometry.encode_polyline_scaled(((0, 0), (1, 1), (2, 2)))
  monkeypatch.setattr(geometry, "MAX_POINTS", 2)
  with pytest.raises(ValueError, match="budget"):
    geometry.decode_polyline_scaled(wire)
  with pytest.raises(ValueError, match="budget"):
    geometry.encode_polyline_scaled(((0, 0),) * 3)
  monkeypatch.setattr(geometry, "MAX_BYTES", len(wire) - 1)
  with pytest.raises(ValueError, match="budget"):
    geometry.decode_polyline_scaled(wire)


def test_boundary_coordinates():
  points = ((-180_000_000, -90_000_000), (180_000_000, 90_000_000))
  assert geometry.decode_polyline_scaled(geometry.encode_polyline_scaled(points)) == points


def test_duplicate_axes_unknown_fields_and_mismatched_lengths():
  wire = geometry.encode_polyline_scaled(((0, 0),))
  for malformed in (wire + b"\x18\x00", wire + wire[:4], wire[:4] + b"\x12\x05\x08\x00\x12\x01\x00"):
    with pytest.raises(ValueError):
      geometry.decode_polyline_scaled(malformed)
