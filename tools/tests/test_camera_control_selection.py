"""Real parser/cache/map publisher/planner with only native I/O replaced offline."""

import importlib.util
import math
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest


ROOT = Path(__file__).resolve().parents[2]
for name, relative in (
  ('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'),
  ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd'),
  ('openpilot.sunnypilot.selfdrive', 'sunnypilot/selfdrive'),
  ('openpilot.sunnypilot.selfdrive.controls', 'sunnypilot/selfdrive/controls'),
  ('openpilot.sunnypilot.selfdrive.controls.lib', 'sunnypilot/selfdrive/controls/lib'),
):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, save_database
from openpilot.sunnypilot.mapd.speed_cameras import parse_camera_snapshot
from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_planner import SpeedCameraPlanner


def latitude(meters):
  return 55 + math.degrees(meters / 6_371_000)


def bkm_row(kind, meters, speed=60, azimuth=180):
  return f'{kind}|at-{meters}|{latitude(meters)}|37|1709|{speed}|1713|1|1705|{azimuth}|\n'


@pytest.fixture
def map_pipeline(monkeypatch, tmp_path):
  """Load production Base/OsmMapData; no messaging, hardware, or native Params."""
  def stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, name, module)
    return module

  def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module

  def message(*_args):
    return NS(valid=False, liveMapDataSP=NS(
      speedCameraAheadValid=False, speedCameraControlVersion=0,
      speedCameraControlValid=False))

  messaging = stub('cereal.messaging', new_message=message)
  stub('cereal', messaging=messaging, log=NS())
  stub('openpilot.common.params', Params=object)
  stub('openpilot.common.constants', CV=NS(KPH_TO_MS=1 / 3.6, MPH_TO_MS=0.44704))
  stub('openpilot.selfdrive.car.cruise', V_CRUISE_UNSET=255)
  stub('openpilot.sunnypilot.navd.helpers', Coordinate=NS, coordinate_from_param=lambda *_: None)
  stub('openpilot.system.hardware.hw', Paths=NS(mapd_root=lambda: str(tmp_path)))
  load('openpilot.sunnypilot.mapd.live_map_data.base_map_data', 'sunnypilot/mapd/live_map_data/base_map_data.py')
  module = load('offline_osm_camera_test', 'sunnypilot/mapd/live_map_data/osm_map_data.py')
  instance = module.OsmMapData.__new__(module.OsmMapData)
  cache = tmp_path / 'cameras.json'
  config = {'SpeedCameraDatabasePath': str(cache), 'SpeedCameraControlEnabled': '1', 'SpeedCameraEnabled': '1'}
  instance.camera_params = NS(get=config.get)
  instance.params = NS(get_bool=lambda _key: True)
  instance.mem_params = NS(get=lambda _key: None)
  instance.camera_database_path = str(cache)
  instance.camera_database = instance.camera_database_mtime = None
  instance.camera_match = instance.camera_control_match = None
  instance.localizer_valid = True
  instance.last_position = NS(latitude=55, longitude=37)
  instance.last_bearing = 0
  instance.sm = {'liveLocationKalman': NS(gpsOK=True)}
  sent = []
  instance.pm = NS(send=lambda service, msg: sent.append((service, msg)))

  def publish(rows):
    cameras = tuple(parse_camera_snapshot(('2|Radars|65001\n' + rows).encode()))
    save_database(str(cache), CameraDatabase('offline-selection', time.time(), cameras))  # noqa: TID251 - cache uses Unix time
    instance.publish()
    assert sent[-1][0] == 'liveMapDataSP' and sent[-1][1].valid
    return sent[-1][1].liveMapDataSP, cameras

  return instance, config, publish


def plan(lmd, **options):
  now = time.time()  # noqa: TID251 - GPS and planner use Unix time
  cs = NS(buttonEvents=[], gasPressed=False, brakePressed=False,
          cruiseState=NS(enabled=True, speed=30, speedCluster=30))
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  gps = NS(horizontalAccuracy=3, bearingAccuracyDeg=2, hasFix=True,
           unixTimestampMillis=int(now * 1000), latitude=55, longitude=37, bearingDeg=0)
  return SpeedCameraPlanner().update(
    cs, cc, gps, lmd, enabled=True, gps_valid=True, gps_alive=True,
    lmd_valid=True, lmd_alive=True, lmd_age_s=0, user_cruise_mps=30,
    minimum_speed_mps=30 / 3.6, now_s=now, adapt_accel_mps2=-1, **options)


def test_warning_100m_does_not_mask_control_camera_500m(map_pipeline):
  instance, _, publish = map_pipeline
  lmd, cameras = publish(bkm_row(18952, 100) + bkm_row(18059, 500))
  assert len(cameras) == len(instance.camera_database.cameras) == 2
  assert lmd.speedCameraType == 'speedcamonline-cg77:18952'
  assert lmd.speedCameraAheadDistance == pytest.approx(100)
  assert lmd.speedCameraSpeedLimit == pytest.approx(60 / 3.6)
  output = plan(lmd)
  assert output.speed_cap_mps is not None
  assert output.camera_id == f'{latitude(500):.7f},37.0000000:speedcamonline-cg77:18059'
  assert lmd.speedCameraControlVersion == 1 and lmd.speedCameraControlValid
  assert lmd.speedCameraControlDistance == pytest.approx(500)
  assert lmd.speedCameraControlSpeedLimit == pytest.approx(60 / 3.6)


@pytest.mark.parametrize('row', [
  bkm_row(18059, 200, speed=20),  # Offset must never qualify a raw low limit.
  bkm_row(18059, 200, azimuth=0),  # Camera faces the opposite travel direction.
  bkm_row(18059, -200),
  bkm_row(18059, 6_000),
])
def test_ineligible_nearer_record_cannot_mask_eligible_control_camera(map_pipeline, row):
  _, _, publish = map_pipeline
  lmd, cameras = publish(bkm_row(18952, 100) + row + bkm_row(18059, 500))
  assert len(cameras) == 3
  assert lmd.speedCameraType == 'speedcamonline-cg77:18952'
  assert lmd.speedCameraControlDistance == pytest.approx(500)
  assert plan(lmd, camera_speed_offset_kph=30).speed_cap_mps is not None


@pytest.mark.parametrize('mode', ['no-eligible', 'opposite', 'low-limit', 'control-disabled', 'warnings-disabled', 'no-heading'])
def test_explicit_no_candidate_never_falls_back_to_warning(map_pipeline, mode):
  instance, config, publish = map_pipeline
  rows = bkm_row(18952, 100)
  if mode == 'opposite':
    rows += bkm_row(18059, 500, azimuth=0)
  elif mode == 'low-limit':
    rows += bkm_row(18059, 500, speed=20)
  elif mode in ('control-disabled', 'warnings-disabled', 'no-heading'):
    rows = bkm_row(18059, 100)
  if mode == 'control-disabled':
    config['SpeedCameraControlEnabled'] = '0'
  elif mode == 'warnings-disabled':
    config['SpeedCameraEnabled'] = '0'
  elif mode == 'no-heading':
    instance.last_bearing = None
  lmd, cameras = publish(rows)
  assert len(cameras) >= 1
  assert lmd.speedCameraControlVersion == 1 and not lmd.speedCameraControlValid
  assert lmd.speedCameraAheadValid is (mode != 'warnings-disabled')
  assert plan(lmd, camera_speed_offset_kph=30).speed_cap_mps is None


@pytest.mark.parametrize('metric,expected_distance', [(True, 100), (False, 500)])
def test_control_uses_existing_metric_or_imperial_stock_minimum(map_pipeline, metric, expected_distance):
  instance, _, publish = map_pipeline
  instance.params.get_bool = lambda _key: metric
  lmd, cameras = publish(bkm_row(18059, 100, speed=30) + bkm_row(18059, 500, speed=60))
  assert len(cameras) == 2 and lmd.speedCameraSpeedLimit == pytest.approx(30 / 3.6)
  assert lmd.speedCameraControlDistance == pytest.approx(expected_distance)


def test_control_disable_clears_prior_candidate_without_hiding_warning(map_pipeline):
  _, config, publish = map_pipeline
  rows = bkm_row(18952, 100) + bkm_row(18059, 500)
  lmd, _ = publish(rows)
  assert lmd.speedCameraControlValid
  config['SpeedCameraControlEnabled'] = '0'
  lmd, _ = publish(rows)
  assert not lmd.speedCameraControlValid and lmd.speedCameraAheadValid
  assert lmd.speedCameraType == 'speedcamonline-cg77:18952'
