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
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, camera_identity
from openpilot.sunnypilot.selfdrive.controls.lib.camera_speed_adapter import CameraSpeedAdapter


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

  import capnp
  schema = capnp.load(str(ROOT / 'cereal/custom.capnp'))

  def message(*_args):
    return NS(valid=False, liveMapDataSP=schema.LiveMapDataSP.new_message())

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
  instance.camera_params = NS(get=config.get, put=lambda key, value: config.__setitem__(key, value))
  instance.params = NS(get_bool=lambda _key: True)
  instance.mem_params = NS(get=lambda _key: None)
  instance.camera_database_path = str(cache)
  instance.camera_database = instance.camera_database_mtime = None
  instance.camera_index = None
  instance.camera_match = instance.camera_control_match = None
  instance.camera_speed_source = CameraSpeedSource()
  instance.camera_selection = instance.camera_speed_source.selection
  instance.camera_gps_service = 'gpsLocationExternal'
  # Actual installed-mapd packed wire format, not a separate camera graph.
  offline_schema = capnp.load(str(ROOT / 'sunnypilot/mapd/camera_osm_bridge.capnp'))
  tile = offline_schema.Offline.new_message(minLat=54.9, minLon=36.9, maxLat=55.1, maxLon=37.1, overlap=.001)
  ways = tile.init('ways', 1)
  ways[0].oneWay = True
  ways[0].minLat, ways[0].minLon, ways[0].maxLat, ways[0].maxLon = latitude(-500), 37, latitude(6500), 37
  nodes = ways[0].init('nodes', 2)
  nodes[0].latitude, nodes[0].longitude = latitude(-500), 37
  nodes[1].latitude, nodes[1].longitude = latitude(6500), 37
  folder = tmp_path / 'offline' / '54' / '36'
  folder.mkdir(parents=True)
  (folder / '54.900000_36.900000_55.100000_37.100000').write_bytes(tile.to_bytes_packed())
  instance.camera_osm_bridge = module.OfflineOsmBridge(str(tmp_path))
  instance.camera_osm_bridge.tick(55, 37)
  instance.camera_osm_bridge._worker.join(5)
  assert instance.camera_osm_bridge.snapshot is not None
  instance.localizer_valid = True
  instance.last_position = NS(latitude=55, longitude=37)
  instance.last_bearing = 0
  class FakeSM(dict):
    valid = {'gpsLocationExternal': True}
    alive = {'gpsLocationExternal': True}
  instance.sm = FakeSM({'liveLocationKalman': NS(gpsOK=True), 'gpsLocationExternal': NS(
    hasFix=True, latitude=55., longitude=37., bearingDeg=0., speed=10.,
    horizontalAccuracy=3., bearingAccuracyDeg=2., unixTimestampMillis=int(time.time() * 1000))})
  sent = []
  def send(service, msg):
    with schema.LiveMapDataSP.from_bytes(msg.liveMapDataSP.to_bytes()) as reader:
      sent.append((service, NS(valid=msg.valid, liveMapDataSP=reader.as_builder())))
  instance.pm = NS(send=send)

  def publish(rows):
    cameras = tuple(parse_camera_snapshot(('2|Radars|65001\n' + rows).encode()))
    save_database(str(cache), CameraDatabase('offline-selection', time.time(), cameras))  # noqa: TID251 - cache uses Unix time
    # A real candidate needs two distinct measured approaching fixes. Prime
    # the previous point five metres behind, then publish the requested point.
    # This is trajectory input, not a mocked selection or forced valid flag.
    if not instance.camera_selection.camera_id:
      gps = instance.sm[instance.camera_gps_service]
      lat, timestamp = gps.latitude, gps.unixTimestampMillis
      gps.latitude = lat - math.degrees(5 / 6_371_000)
      gps.unixTimestampMillis = timestamp - 1000
      instance.publish()
      gps.latitude, gps.unixTimestampMillis = lat, timestamp
    instance.publish()
    assert sent[-1][0] == 'liveMapDataSP' and sent[-1][1].valid
    return sent[-1][1].liveMapDataSP, cameras

  return instance, config, publish


def plan(lmd, **options):
  # This suite checks independent producer selection; production authority now
  # belongs to Resolver/SLA. A wide explicit reaction zone isolates that concern.
  output = CameraSpeedAdapter().update(lmd, enabled=True, reliable=True, engaged=True, reaction_distance=1000)
  return NS(speed_cap_mps=output.raw_limit if output.automatic else None, camera_id=output.event_id)


def test_warning_100m_does_not_mask_control_camera_500m(map_pipeline):
  instance, _, publish = map_pipeline
  lmd, cameras = publish(bkm_row(18952, 100) + bkm_row(18059, 500))
  assert len(cameras) == len(instance.camera_database.cameras) == 2
  assert lmd.speedCameraType == 'speedcamonline-cg77:18952'
  assert lmd.speedCameraAheadDistance == pytest.approx(100)
  assert lmd.speedCameraSpeedLimit == pytest.approx(60 / 3.6)
  output = plan(lmd)
  assert output.speed_cap_mps is not None
  assert output.camera_id == camera_identity(cameras[1])
  assert lmd.speedCameraControlVersion == 1 and lmd.speedCameraControlValid
  assert lmd.speedCameraControlDistance == pytest.approx(500)
  assert lmd.speedCameraControlSpeedLimit == pytest.approx(60 / 3.6)
  assert lmd.speedCameraControlEventId != lmd.speedCameraEventId


@pytest.mark.parametrize('row', [
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


@pytest.mark.parametrize('mode', ['no-eligible', 'opposite', 'control-disabled', 'no-heading'])
def test_explicit_no_candidate_never_falls_back_to_warning(map_pipeline, mode):
  instance, config, publish = map_pipeline
  rows = bkm_row(18952, 100)
  if mode == 'opposite':
    rows += bkm_row(18059, 500, azimuth=0)
  elif mode in ('control-disabled', 'no-heading'):
    rows = bkm_row(18059, 100)
  if mode == 'control-disabled':
    config['SpeedCameraControlEnabled'] = '0'
  elif mode == 'no-heading':
    instance.last_bearing = None
    instance.sm[instance.camera_gps_service].bearingDeg = float('nan')
  lmd, cameras = publish(rows)
  assert len(cameras) >= 1
  assert lmd.speedCameraControlVersion == 1 and not lmd.speedCameraControlValid
  assert lmd.speedCameraAheadValid is (mode != 'warnings-disabled')
  assert plan(lmd, camera_speed_offset_kph=30).speed_cap_mps is None


@pytest.mark.parametrize('warnings,control,warning_valid,control_valid', [
  ('0', '1', False, True), ('1', '0', True, False), ('0', '0', False, False), ('1', '1', True, True)])
def test_warning_and_control_toggles_are_independent(map_pipeline, warnings, control, warning_valid, control_valid):
  _, config, publish = map_pipeline
  config['SpeedCameraEnabled'] = warnings
  config['SpeedCameraControlEnabled'] = control
  lmd, _ = publish(bkm_row(18059, 100))
  assert lmd.speedCameraAheadValid is warning_valid
  assert lmd.speedCameraControlValid is control_valid
  if control_valid:
    assert lmd.speedCameraControlEventId and lmd.speedCameraControlState == 'ACTIVE'


@pytest.mark.parametrize('metric', [True, False])
def test_control_raw_limit_is_independent_of_metric_stock_minimum(map_pipeline, metric):
  instance, _, publish = map_pipeline
  instance.params.get_bool = lambda _key: metric
  lmd, cameras = publish(bkm_row(18059, 100, speed=30) + bkm_row(18059, 500, speed=60))
  assert len(cameras) == 2 and lmd.speedCameraSpeedLimit == pytest.approx(30 / 3.6)
  assert lmd.speedCameraControlDistance == pytest.approx(100)


def test_raw_twenty_camera_publishes_eligible_before_native_offset(map_pipeline):
  _, _, publish = map_pipeline
  lmd, _ = publish(bkm_row(18059, 100, speed=20))
  assert lmd.speedCameraControlValid
  assert lmd.speedCameraControlSpeedLimit == pytest.approx(20 / 3.6)


def test_control_disable_clears_prior_candidate_without_hiding_warning(map_pipeline):
  _, config, publish = map_pipeline
  rows = bkm_row(18952, 100) + bkm_row(18059, 500)
  lmd, _ = publish(rows)
  assert lmd.speedCameraControlValid
  config['SpeedCameraControlEnabled'] = '0'
  lmd, _ = publish(rows)
  assert not lmd.speedCameraControlValid and lmd.speedCameraAheadValid
  assert lmd.speedCameraType == 'speedcamonline-cg77:18952'


def test_index_reused_until_database_mtime_changes(map_pipeline, monkeypatch):
  instance, _, publish = map_pipeline
  publish(bkm_row(18059, 100))
  original_index = instance.camera_index
  module = sys.modules[instance.__class__.__module__]
  builds = []
  original_class = module.CameraSpatialIndex

  def build(cameras):
    builds.append(cameras)
    return original_class(cameras)

  monkeypatch.setattr(module, 'CameraSpatialIndex', build)
  for _ in range(5):
    instance.get_speed_camera()
    assert instance.camera_index is original_index
  assert not builds
  old_mtime = instance.camera_database_mtime
  cache = Path(instance.camera_database_path)
  cameras = tuple(parse_camera_snapshot(('2|Radars|65001\n' + bkm_row(18059, 500)).encode()))
  save_database(str(cache), CameraDatabase('reload', time.time(), cameras))  # noqa: TID251
  module.os.utime(cache, (old_mtime + 10, old_mtime + 10))
  assert instance.get_speed_camera().distance_m == pytest.approx(500)
  assert len(builds) == 1 and instance.camera_index is not original_index


def test_seeded_database_builds_missing_index_once_without_reloading(map_pipeline, monkeypatch):
  instance, _, publish = map_pipeline
  publish(bkm_row(18059, 100))
  module = sys.modules[instance.__class__.__module__]
  seeded = CameraDatabase('seeded', time.time(), tuple(parse_camera_snapshot(  # noqa: TID251
    ('2|Radars|65001\n' + bkm_row(18059, 500)).encode())))
  instance.camera_database = seeded
  instance.camera_index = None

  def unexpected_load(*args, **kwargs):
    pytest.fail('A seeded database with unchanged mtime must not reload')

  monkeypatch.setattr(module, 'load_database', unexpected_load)
  assert instance.get_speed_camera().distance_m == pytest.approx(500)
  index = instance.camera_index
  assert index is not None and instance.camera_database is seeded
  assert instance.get_speed_camera().distance_m == pytest.approx(500)
  assert instance.camera_index is index


@pytest.mark.parametrize('condition', ['missing', 'malformed', 'expired', 'path-change'])
def test_index_does_not_survive_missing_invalid_expired_or_replaced_database(map_pipeline, condition, monkeypatch):
  instance, config, publish = map_pipeline
  publish(bkm_row(18059, 100))
  original_index = instance.camera_index
  module = sys.modules[instance.__class__.__module__]
  cache = Path(instance.camera_database_path)
  if condition == 'missing':
    cache.unlink()
  elif condition == 'malformed':
    cache.write_text('{}')
    module.os.utime(cache, (instance.camera_database_mtime + 10, instance.camera_database_mtime + 10))
  elif condition == 'expired':
    expired_at = instance.camera_database.fetched_at + 7 * 24 * 3600 + 1
    monkeypatch.setattr(module.time, 'time', lambda: expired_at)
  else:
    replacement = cache.with_name('replacement.json')
    replacement.write_bytes(cache.read_bytes())
    config['SpeedCameraDatabasePath'] = str(replacement)
    assert instance.get_speed_camera() is not None
    assert instance.camera_index is not original_index
    return
  assert instance.get_speed_camera() is None
  assert instance.camera_index is None
  assert instance.camera_database is None
  assert instance.get_speed_camera_control() is None


def test_real_publisher_wire_pass_requires_two_distinct_raw_fixes(map_pipeline, monkeypatch):
  instance, _, publish = map_pipeline
  module = sys.modules[instance.__class__.__module__]
  epoch = time.time()
  clock = [epoch]
  monkeypatch.setattr(module.time, 'time', lambda: clock[0])
  gps = instance.sm[instance.camera_gps_service]
  def fix(north, seconds):
    clock[0] = epoch + seconds
    gps.latitude = latitude(north)
    gps.unixTimestampMillis = int(clock[0] * 1000)
    return publish(bkm_row(18059, 400))[0]
  selected = fix(380, 0)
  identity = selected.speedCameraControlEventId
  assert selected.speedCameraControlValid and identity
  first = fix(430, 1)
  assert not first.speedCameraControlPassProven
  duplicate = fix(430, 1)
  assert not duplicate.speedCameraControlPassProven
  passed = fix(440, 2)
  assert passed.speedCameraControlPassProven and passed.speedCameraControlState == 'PASSED'
  assert passed.speedCameraControlEventId == identity and not passed.speedCameraControlValid
  assert passed.speedCameraControlSpeedLimit == pytest.approx(60 / 3.6)


def test_real_publisher_uncertain_gps_retains_id_without_pass(map_pipeline):
  instance, _, publish = map_pipeline
  selected, _ = publish(bkm_row(18059, 100))
  identity = selected.speedCameraControlEventId
  gps = instance.sm[instance.camera_gps_service]
  gps.hasFix = False
  lost, _ = publish(bkm_row(18059, 100))
  assert lost.speedCameraControlEventId == identity
  assert not lost.speedCameraControlValid and not lost.speedCameraControlPassProven
  assert lost.speedCameraAheadValid
  assert lost.speedCameraControlReason == 'gps_unreliable'


def test_local_database_and_installed_maps_ignore_legacy_graph_and_never_use_network(map_pipeline, monkeypatch):
  import socket
  import urllib.request
  instance, config, publish = map_pipeline
  module = sys.modules[instance.__class__.__module__]
  config['SpeedCameraRoadGraphPath'] = str(Path(instance.camera_database_path).with_name('missing.osm'))
  config['SpeedCameraReactionDistance'] = '900'
  def forbidden(*args, **kwargs):
    raise AssertionError('Camera selection must not make a network request')
  monkeypatch.setattr(socket.socket, 'connect', forbidden)
  monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
  selected, _ = publish(bkm_row(18059, 900))
  assert selected.speedCameraControlValid
  assert selected.speedCameraControlSpeedLimit == pytest.approx(60 / 3.6)
  assert selected.speedCameraControlDistance == pytest.approx(900, abs=.1)
  assert selected.speedCameraControlState == 'ACTIVE'
  assert config['SpeedCameraRoadGraphStatus'].startswith('offline_osm:')
  assert not hasattr(module, 'RoadGraphUpdater') and not hasattr(module, 'RoadGraph')
  assert not Path(config['SpeedCameraRoadGraphPath']).exists()
  assert not Path(instance.camera_database_path).with_name('camera_road_cache').exists()
  identity = selected.speedCameraControlEventId
  # Recreate the pure source as a process restart: only local DB and GPS remain.
  instance.camera_speed_source = CameraSpeedSource()
  instance.camera_selection = instance.camera_speed_source.selection
  recovered, _ = publish(bkm_row(18059, 900))
  assert recovered.speedCameraControlValid and recovered.speedCameraControlEventId == identity


def test_offline_status_writes_only_when_changed(map_pipeline):
  instance, config, publish = map_pipeline
  writes = []
  def put(key, value):
    config[key] = value
    writes.append((key, value))
  instance.camera_params.put = put
  publish(bkm_row(18059, 700))
  writes.clear()
  instance.get_speed_camera()
  instance.get_speed_camera()
  assert not writes
  instance.sm[instance.camera_gps_service].hasFix = False
  message, _ = publish(bkm_row(18059, 700))
  assert not message.speedCameraControlValid and message.speedCameraControlReason == 'gps_unreliable'
  assert len(writes) == 1 and writes[0][0] == 'SpeedCameraRoadGraphStatus'
  assert writes[0][1].startswith('offline_osm:') and writes[0][1].endswith(':gps_unreliable')


def test_stationary_strong_fix_is_not_mislabeled_as_weak_gps(map_pipeline):
  instance, _, publish = map_pipeline
  gps = instance.sm[instance.camera_gps_service]
  gps.speed = 0
  gps.bearingAccuracyDeg = 180
  gps.bearingDeg = float('nan')
  message, _ = publish(bkm_row(18059, 700))
  assert not message.speedCameraControlValid
  assert message.speedCameraControlReason == 'waiting_for_motion'
  gps.speed = 10
  moving, _ = publish(bkm_row(18059, 700))
  assert not moving.speedCameraControlValid
  assert moving.speedCameraControlReason == 'gps_unreliable'


@pytest.mark.parametrize('condition', ['missing', 'expired'])
def test_database_loss_never_keeps_retained_event_valid(map_pipeline, monkeypatch, condition):
  instance, _, publish = map_pipeline
  selected, _ = publish(bkm_row(18059, 100))
  assert selected.speedCameraControlValid
  identity = selected.speedCameraControlEventId
  module = sys.modules[instance.__class__.__module__]
  if condition == 'missing':
    Path(instance.camera_database_path).unlink()
  else:
    expired_at = instance.camera_database.fetched_at + 7 * 86400 + 1
    monkeypatch.setattr(module.time, 'time', lambda: expired_at)
  instance.get_speed_camera()
  assert not instance.camera_selection.valid and not instance.camera_selection.pass_proven
  assert instance.camera_selection.camera_id == identity


def test_missing_installed_offline_maps_does_not_authorize_from_metadata_alone(map_pipeline, monkeypatch):
  import shutil
  import socket
  import urllib.request
  instance, _, publish = map_pipeline
  module = sys.modules[instance.__class__.__module__]
  root = Path(instance.camera_database_path).parent
  shutil.rmtree(root / 'offline')
  instance.camera_osm_bridge = module.OfflineOsmBridge(str(root))
  def forbidden(*args, **kwargs):
    raise AssertionError('Missing offline maps must not trigger a download')
  monkeypatch.setattr(socket.socket, 'connect', forbidden)
  monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
  message, _ = publish(bkm_row(18059, 700))
  worker = instance.camera_osm_bridge._worker
  if worker is not None:
    worker.join(5)
  assert not message.speedCameraControlValid
  assert instance.camera_selection.reason == 'offline_maps_unavailable'


@pytest.mark.parametrize('scenario', ['parallel', 'branch'])
def test_local_maps_veto_directionally_compatible_wrong_road_camera(map_pipeline, scenario):
  import capnp
  instance, _, publish = map_pipeline
  root = Path(instance.camera_database_path).parent
  schema = capnp.load(str(ROOT / 'sunnypilot/mapd/camera_osm_bridge.capnp'))
  tile = schema.Offline.new_message(minLat=54.9, minLon=36.9, maxLat=55.1, maxLon=37.1, overlap=.001)
  lateral_lon = 37 + math.degrees(15 / (6_371_000 * math.cos(math.radians(55))))
  if scenario == 'parallel':
    roads = [[(latitude(-500), 37), (latitude(2000), 37)],
             [(latitude(-500), lateral_lon), (latitude(2000), lateral_lon)]]
    row = bkm_row(18059, 700).replace('|37|', f'|{lateral_lon}|')
  else:
    junction = (latitude(100), 37)
    roads = [[(latitude(-500), 37), junction],
             [junction, (latitude(2000), 37)],
             [junction, (latitude(100), 37.01)]]
    row = bkm_row(18059, 700)
  ways = tile.init('ways', len(roads))
  for way, points in zip(ways, roads):
    way.oneWay = True
    way.minLat = min(point[0] for point in points)
    way.minLon = min(point[1] for point in points)
    way.maxLat = max(point[0] for point in points)
    way.maxLon = max(point[1] for point in points)
    nodes = way.init('nodes', len(points))
    for node, point in zip(nodes, points):
      node.latitude, node.longitude = point
  path = root / 'offline' / '54' / '36' / '54.900000_36.900000_55.100000_37.100000'
  path.write_bytes(tile.to_bytes_packed())
  module = sys.modules[instance.__class__.__module__]
  instance.camera_osm_bridge = module.OfflineOsmBridge(str(root))
  instance.camera_osm_bridge.tick(55, 37)
  instance.camera_osm_bridge._worker.join(5)
  assert instance.camera_osm_bridge.snapshot is not None
  message, _ = publish(row)
  assert message.speedCameraAheadValid  # provider coordinate/direction alone finds it
  assert not message.speedCameraControlValid  # local road topology authoritatively vetoes it
