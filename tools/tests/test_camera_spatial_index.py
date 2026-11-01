"""Index must be a candidate superset, with exact legacy match/order semantics."""

import math
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in (('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'),
                       ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')):
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.speed_cameras import (
  EARTH_RADIUS_M, CameraSpatialIndex, SpeedCamera, _distance_and_bearing,
  find_nearest_camera, find_nearest_control_camera,
)


def destination(lat, lon, bearing, distance):
  lat, lon, bearing, angle = map(math.radians, (lat, lon, bearing, math.degrees(distance / EARTH_RADIUS_M)))
  target_lat = math.asin(max(-1, min(1, math.sin(lat) * math.cos(angle) + math.cos(lat) * math.sin(angle) * math.cos(bearing))))
  target_lon = lon + math.atan2(math.sin(bearing) * math.sin(angle) * math.cos(lat),
                              math.cos(angle) - math.sin(lat) * math.sin(target_lat))
  return math.degrees(target_lat), (math.degrees(target_lon) + 180) % 360 - 180


def camera(lat, lon, **kwargs):
  return SpeedCamera(lat, lon, **({'speed_limit_kph': 60, 'camera_type': 'speedcamonline:1'} | kwargs))


def assert_same_match(expected, actual):
  assert actual == expected
  if expected is not None:
    assert actual.camera is expected.camera


@pytest.mark.parametrize('lat', [-90, -89.999999, -89.95, -55, 0, 55, 89.95, 89.999999, 90])
@pytest.mark.parametrize('lon', [-180, -179.999999, 0, 179.999999, 180])
def test_spherical_boundary_superset_and_exact_matches(lat, lon):
  for radius in (0, 0.001, 1000, 5000, 100_000, math.pi * EARTH_RADIUS_M):
    cameras = [camera(lat, lon)]
    # Tight boundary cases plus all compass directions, with polar longitude wrap.
    for distance in (max(0, radius - 1e-6), radius, radius + 1e-6):
      if distance >= math.pi * EARTH_RADIUS_M:
        continue  # Legacy haversine itself is not robust at all exact antipodes.
      for bearing in range(0, 360, 15):
        cameras.append(camera(*destination(lat, lon, bearing, distance)))
    index = CameraSpatialIndex(cameras)
    selected = list(index.query(lat, lon, radius))
    selected_ids = {id(c) for c in selected}
    for c in cameras:
      if _distance_and_bearing(lat, lon, c)[0] <= radius:
        assert id(c) in selected_ids
    assert selected == [c for c in cameras if id(c) in selected_ids]
    for heading in (None, 0, 90, 180, 270):
      assert_same_match(find_nearest_camera(cameras, lat, lon, heading, radius),
                        find_nearest_camera(index, lat, lon, heading, radius))


def test_randomized_global_local_queries_and_control_eligibility():
  rng = random.Random(20261027)
  global_cameras = [camera(rng.uniform(-90, 90), rng.uniform(-180, 180)) for _ in range(1000)]
  for _ in range(160):
    lat, lon = rng.uniform(-90, 90), rng.uniform(-180, 180)
    cameras = list(global_cameras)
    for _ in range(80):
      point = destination(lat, lon, rng.uniform(0, 360), rng.uniform(0, 12000))
      cameras.append(camera(*point, speed_limit_kph=rng.choice([None, 20, 30, 60, 90]),
                            camera_type=rng.choice(['unknown', 'speedcamonline:1', 'speedcamonline:4', 'speedcamonline:5']),
                            direction_type=rng.choice([None, 0, 1, 2]), direction_deg=rng.uniform(0, 360),
                            second_direction_deg=rng.choice([None, rng.uniform(0, 360)])))
    rng.shuffle(cameras)
    index = CameraSpatialIndex(cameras)
    for radius in (0, 1000, 5000, 10000):
      heading = rng.choice([None, rng.uniform(-720, 720)])
      assert_same_match(find_nearest_camera(cameras, lat, lon, heading, radius),
                        find_nearest_camera(index, lat, lon, heading, radius))
      for minimum in (30, 32.18688, 80):
        assert_same_match(find_nearest_control_camera(cameras, lat, lon, heading, minimum, radius),
                          find_nearest_control_camera(index, lat, lon, heading, minimum, radius))


def test_grid_boundaries_and_original_order_including_duplicates():
  # Points whose x coordinate sits exactly on each grid face; query both sides.
  for x in range(-999, 1000, 17):
    lon = math.degrees(math.acos(x * CameraSpatialIndex._CELL_SIZE))
    cameras = [camera(0, lon), camera(0, lon, speed_limit_kph=90), camera(0, lon - 0.001)]
    cameras.append(cameras[0])
    index = CameraSpatialIndex(cameras)
    for delta in (-1e-10, 0, 1e-10):
      assert_same_match(find_nearest_camera(cameras, 0, lon + delta, max_distance_m=1000),
                        find_nearest_camera(index, 0, lon + delta, max_distance_m=1000))
    assert list(index.query(0, lon, 1000)) == cameras
    assert find_nearest_control_camera(index, 0, lon, 0, 30).camera is cameras[0]


@pytest.mark.parametrize('radius', [-1, float('nan'), float('inf'), -float('inf'), None, 'bad'])
def test_invalid_radius_preserves_full_scan_fallback(radius):
  cameras = [camera(0, 0), camera(1, 1)]
  index = CameraSpatialIndex(cameras)
  assert list(index.query(0, 0, radius)) == cameras
  try:
    expected = find_nearest_camera(cameras, 0, 0, max_distance_m=radius)
  except (TypeError, ValueError) as error:
    with pytest.raises(type(error)):
      find_nearest_camera(index, 0, 0, max_distance_m=radius)
  else:
    assert_same_match(expected, find_nearest_camera(index, 0, 0, max_distance_m=radius))


@pytest.mark.parametrize('lat,lon', [(91, 0), (0, 181), (float('nan'), 0), (float('inf'), 0), (None, 0), (0, 'bad')])
def test_invalid_position_does_not_gain_new_index_semantics(lat, lon):
  cameras = [camera(0, 0), camera(1, 1)]
  index = CameraSpatialIndex(cameras)
  assert list(index.query(lat, lon, 5000)) == cameras
  try:
    expected = find_nearest_camera(cameras, lat, lon)
  except (TypeError, ValueError) as error:
    with pytest.raises(type(error)):
      find_nearest_camera(index, lat, lon)
  else:
    actual = find_nearest_camera(index, lat, lon)
    assert (actual is None) == (expected is None)
    if expected is not None:
      assert actual.camera is expected.camera  # NaN floats do not compare equal.


def test_unindexable_records_are_retained_and_empty_index_works():
  cameras = [camera(91, 181), camera(float('nan'), 0), camera(None, 0), camera(0, float('inf'))]
  assert list(CameraSpatialIndex(cameras).query(55, 37, 0)) == cameras
  assert find_nearest_camera(CameraSpatialIndex([]), 55, 37) is None


def test_warning_and_control_selection_remain_independent_and_raw():
  cameras = [camera(0.0001, 0, camera_type='unknown'), camera(0.0002, 0, speed_limit_kph=20),
             camera(0.0003, 0, direction_type=1, direction_deg=180), camera(0.0004, 0)]
  index = CameraSpatialIndex(cameras)
  assert find_nearest_camera(index, 0, 0, 0).camera is cameras[0]
  assert find_nearest_control_camera(index, 0, 0, 0, 30).camera is cameras[3]


def test_local_queries_do_not_visit_every_eligibility_record():
  class CountedCamera:
    latitude = 0
    longitude = 0
    checks = 0

    @property
    def allowed_auto_control(self):
      self.checks += 1
      return False

  far = CountedCamera()
  near = camera(55.001, 37)
  assert find_nearest_control_camera(CameraSpatialIndex([far, near]), 55, 37, 0, 30).camera is near
  assert far.checks == 0
