"""Offline BKM contract tests; synthetic rows plus the retained SCO first row.

Run: python -B -m pytest -c tools/tests/camera_replay_pytest.ini \
  -p no:cacheprovider tools/tests/test_cityguide_cameras.py -q
"""

import csv
import hashlib
import io
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
for name, relative in (
  ('openpilot', '.'),
  ('openpilot.sunnypilot', 'sunnypilot'),
  ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd'),
):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera, find_nearest_camera, parse_camera_snapshot
from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, load_database, save_database


HEADER = '2|Radars|1251\n'
PROVIDER_ROW = '18059|RU-4|51.6482993|39.0990694|1709|60|1713|0|1705|277|1706|150|'


def snapshot(*rows):
  return HEADER + ''.join(row + '\n' for row in rows)


def test_observed_provider_row_uses_evidence_backed_travel_bearing():
  camera, = parse_cityguide_snapshot(snapshot(PROVIDER_ROW))
  assert type(camera) is SpeedCamera
  assert camera.latitude == pytest.approx(51.6482993)
  assert camera.longitude == pytest.approx(39.0990694)
  assert camera.speed_limit_kph == 60
  assert camera.camera_type == 'speedcamonline-cg77:18059'
  assert camera.direction_deg == 97 and camera.direction_type == 1
  assert camera.second_direction_deg is None
  assert camera.source_id == 'RU-4'
  assert camera.source_params == {'1709': '60', '1713': '0', '1705': '277', '1706': '150'}
  assert camera.end_latitude is None and camera.end_longitude is None
  assert camera.allowed_auto_control


@pytest.mark.parametrize('kind', [18059, 18950, 18951, 18952, 18958, 1, 2, 5, 99999])
def test_all_known_and_unknown_types_remain_warnings_only(kind):
  camera, = parse_cityguide_snapshot(snapshot(f'{kind}|synthetic|55|37|1709|60|'))
  assert camera.camera_type == f'speedcamonline-cg77:{kind}'
  assert not camera.allowed_auto_control


def test_full_file_empty_types_are_explicitly_unknown_without_dropping_records():
  # Actual malformed-type rows 60622/60623 in the supplied full file, whose
  # SHA-256 is recorded in cityguide.py. Their remaining structure is valid.
  rows = (
    '|RU-84289|50.4762586|41.3855234|1709|60|1713|0|1705|64|1706|500|',
    '|RU-84290|50.4762313|41.3856414|1709|60|1713|0|1705|244|1706|300|',
  )
  cameras = parse_cityguide_snapshot(snapshot(*rows))
  assert len(cameras) == 2
  assert all(camera.camera_type == 'speedcamonline-cg77:unknown' for camera in cameras)
  assert all(camera.speed_limit_kph == 60 for camera in cameras)
  assert all(not camera.allowed_auto_control and camera.direction_deg is None for camera in cameras)


def test_full_file_missing_azimuth_and_range_remain_unknown():
  camera, = parse_cityguide_snapshot(snapshot('18950|RU-30030|60.1448699|64.7317165|1709|60|1713|0|'))
  assert camera.speed_limit_kph == 60
  assert camera.direction_deg is None and camera.direction_type is None


@pytest.mark.parametrize('beam', [0, 1, 2, 99])
def test_beam_and_two_azimuths_do_not_become_vehicle_direction_or_section_link(beam):
  camera, = parse_cityguide_snapshot(snapshot(
    f'18951|synthetic|55|37|1713|{beam}|1705|277|1706|150|1707|97|1708|300|'))
  assert camera.direction_deg is None and camera.direction_type is None
  assert camera.end_latitude is None and camera.end_longitude is None


@pytest.mark.parametrize('encoding,bom,marker', [
  ('cp1251', False, '1251'), ('utf-8', False, '1251'),
  ('utf-8', True, '1251'), ('utf-8', False, '65001'), ('utf-8', True, '65001'),
])
def test_cyrillic_comment_encodings(encoding, bom, marker):
  text = snapshot('18059|Камера|55|37|102|Контроль скорости|1709|60|').replace('1251', marker, 1)
  payload = text.encode(encoding)
  if bom:
    payload = b'\xef\xbb\xbf' + payload
  assert parse_cityguide_snapshot(payload)[0].speed_limit_kph == 60


def test_unicode_bom_crlf_blank_lines_and_arbitrary_parameter_order():
  text = '\ufeff\r\n' + snapshot('18059||-90|180|102||1706|150|1709|60|1705|360|')
  camera, = parse_cityguide_snapshot(text.replace('\n', '\r\n').replace('\r\r\n', '\r\n'))
  assert (camera.latitude, camera.longitude, camera.speed_limit_kph) == (-90, 180, 60)


@pytest.mark.parametrize('tail', ['', '|1709|0|', '|1709||', '|102|Нет ограничения|'])
def test_unknown_limit_is_none(tail):
  camera, = parse_cityguide_snapshot(snapshot('18059|synthetic|55|37' + tail))
  assert camera.speed_limit_kph is None


def test_unknown_keys_are_not_inferred_as_speed_or_linkage():
  camera, = parse_cityguide_snapshot(snapshot('99999|synthetic|55|37|999|60|1000|next-id|'))
  assert camera.speed_limit_kph is None and camera.end_latitude is None


@pytest.mark.parametrize('row', [
  '18059|x|55', '18059|x|55|37|1709', '18059|x|55|37|1706|',
  '18059|x|55|37|1709|60|1709|80|', '18059|x|55|37|1709|60|01709|80|',
  '18059|x|55|37|bad|60|', '18059|x|55|37|0|60|',
  'unknown|x|55|37|', '0|x|55|37|', '-1|x|55|37|', '1.0|x|55|37|',
  '18059|x|nan|37|', '18059|x|55|inf|', '18059|x|90.1|37|',
  '18059|x|-90.1|37|', '18059|x|55|180.1|', '18059|x|55|-180.1|',
  '18059|x||37|', '18059|x|55|37|1709|-1|', '18059|x|55|37|1709|301|',
  '18059|x|55|37|1709|nan|', '18059|x|55|37|1709|fast|',
  '18059|x|55|37|1705|361|', '18059|x|55|37|1707|-1|',
  '18059|x|55|37|1706|-1|', '18059|x|55|37|1708|inf|',
])
def test_malformed_row_rejects_whole_snapshot_including_valid_prefix(row):
  with pytest.raises(ValueError, match='CityGuide row 3'):
    parse_cityguide_snapshot(snapshot(PROVIDER_ROW, row))


@pytest.mark.parametrize('payload', [
  '', HEADER, HEADER + '\n', '2|Other|1251\n' + PROVIDER_ROW + '\n',
  '1|Radars|1251\n' + PROVIDER_ROW + '\n',
  '2|Radars|unknown\n' + PROVIDER_ROW + '\n',
  '<html>upstream error</html>\n',
  snapshot(PROVIDER_ROW)[:-1], snapshot(PROVIDER_ROW) + '18059|x|55|37|1706|',
  snapshot(PROVIDER_ROW) + '18059|x|55|37|1706|\n',
  snapshot(PROVIDER_ROW).replace('\n', '\r'),
  snapshot(PROVIDER_ROW).replace('RU-4', 'bad\x00text'),
  snapshot(PROVIDER_ROW).replace('RU-4', 'bad\x1etext'),
  b'\xef\xbb\xbf2|Radars|1251\n18059|\xff|55|37|\n',
  b'2|Radars|65001\n18059|\xff|55|37|\n',
  b'2|Radars|1251\n18059|\x98|55|37|\n',
])
def test_bad_header_encoding_or_truncation_is_never_a_snapshot(payload):
  with pytest.raises(ValueError):
    parse_cityguide_snapshot(payload)


def test_complete_row_boundary_cannot_prove_transport_completeness():
  # Deliberately explicit limitation: header has no declared record count.
  assert len(parse_cityguide_snapshot(snapshot(PROVIDER_ROW))) == 1


def test_complete_final_pair_without_optional_pipe_is_accepted():
  assert parse_cityguide_snapshot(snapshot('18059|x|55|37|1709|60'))[0].speed_limit_kph == 60


@pytest.mark.parametrize('kind', [18059, 18950, 18958])
@pytest.mark.parametrize('beam', [0, 1, 2])
def test_known_point_types_and_beams_normalize_as_explicit_directions(kind, beam):
  camera, = parse_cityguide_snapshot(snapshot(
    f'{kind}|synthetic|55|37|1709|60|1713|{beam}|1705|180|1706|500|1707|270|1708|150|'))
  assert camera.direction_deg == 0
  assert camera.second_direction_deg == 90
  assert camera.direction_type == 1  # Two explicit bearings, not an inferred opposite bearing.
  assert camera.allowed_auto_control


@pytest.mark.parametrize('beam', ['99', '-1', '1.5', '', 'unknown'])
def test_unknown_beams_keep_record_and_raw_metadata_but_remain_warning_only(beam):
  camera, = parse_cityguide_snapshot(snapshot(f'18059|synthetic|55|37|1709|60|1713|{beam}|1705|180|'))
  assert camera.source_params['1713'] == beam
  assert camera.direction_deg is None and camera.second_direction_deg is None
  assert camera.direction_type is None and not camera.allowed_auto_control


def test_missing_beam_with_present_azimuth_remains_warning_only():
  camera, = parse_cityguide_snapshot(snapshot('18059|x|55|37|1709|60|1705|180|'))
  assert camera.source_params['1705'] == '180'
  assert camera.direction_deg is None and not camera.allowed_auto_control


@pytest.mark.parametrize('kind', [18951, 18952, 18925, 99999])
def test_unverified_type_with_otherwise_valid_metadata_does_not_gain_eligibility(kind):
  camera, = parse_cityguide_snapshot(snapshot(f'{kind}|x|55|37|1709|60|1713|1|1705|180|'))
  assert camera.direction_deg is None and not camera.allowed_auto_control


def test_opposite_direction_is_rejected_even_when_camera_is_ahead_of_vehicle():
  camera, = parse_cityguide_snapshot(snapshot('18059|x|55|37|1709|60|1713|0|1705|180|'))
  match = find_nearest_camera([camera], 54.999, 37, heading_deg=0)
  assert match is not None and match.camera is camera
  assert find_nearest_camera([camera], 55.001, 37, heading_deg=180) is None


def test_second_azimuth_matches_without_duplicating_record_or_inventing_reverse_bearing():
  cameras = parse_cityguide_snapshot(snapshot('18059|x|55|37|1709|60|1713|2|1705|180|1707|270|'))
  assert len(cameras) == 1
  assert find_nearest_camera(cameras, 54.999, 37, heading_deg=0) is not None
  match = find_nearest_camera(cameras, 55, 36.999, heading_deg=90)
  assert match is not None and match.camera is cameras[0]
  assert find_nearest_camera(cameras, 55, 37.001, heading_deg=270) is None
  assert find_nearest_camera(cameras, 55.001, 37, heading_deg=180) is None


@pytest.mark.parametrize('azimuth,expected', [(0, 180), (180, 0), (360, 180), (359.5, 179.5)])
def test_normalization_wraps_bearings(azimuth, expected):
  camera, = parse_cityguide_snapshot(snapshot(f'18958|x|55|37|1709|60|1713|1|1705|{azimuth}|'))
  assert camera.direction_deg == expected and camera.allowed_auto_control


@pytest.mark.parametrize('speed', [None, 0, -1, float('nan'), float('inf'), 301])
def test_eligibility_requires_usable_positive_limit_for_both_providers(speed):
  camera, = parse_cityguide_snapshot(snapshot(PROVIDER_ROW))
  assert not replace(camera, speed_limit_kph=speed).allowed_auto_control
  assert not SpeedCamera(55, 37, speed, 0, 'speedcamonline:1').allowed_auto_control


@pytest.mark.parametrize('changes', [
  {'source_params': {}}, {'direction_deg': 277}, {'direction_type': 2},
  {'second_direction_deg': 10}, {'end_latitude': 55, 'end_longitude': 37},
  {'source_params': {'1709': '40', '1713': '0', '1705': '277'}},
  {'source_params': {'1709': '60', '1713': '0', '1705': '277', '1706': '-1'}},
  {'source_params': {'1709': '60', '1713': '0', '1705': '277', '1707': 'nan'}},
  {'source_params': {'1709': '60', '1713': '0', '1705': '277', '01705': '277'}},
])
def test_cached_type_alone_or_inconsistent_metadata_cannot_enable_control(changes):
  camera, = parse_cityguide_snapshot(snapshot(PROVIDER_ROW))
  assert not replace(camera, **changes).allowed_auto_control


def test_old_cache_without_raw_metadata_is_still_readable_but_cg_warning_only():
  camera, = parse_camera_snapshot([{'latitude': 55, 'longitude': 37, 'speed_limit_kph': 60,
                                   'camera_type': 'speedcamonline-cg77:18059',
                                   'direction_deg': 0, 'direction_type': 1}])
  assert camera.source_params == {} and camera.source_id is None
  assert not camera.allowed_auto_control


def test_real_cache_roundtrip_preserves_raw_parameters_id_and_second_bearing(tmp_path):
  cameras = parse_cityguide_snapshot(snapshot(
    '18059|RU-1|55|37|1709|60|1713|1|1705 | 180 |1706|500|1707|270|1708|150|102| Комментарий |999|opaque|',
    '18952|RU-1|56|38|1709|0|1713|99|1705|20|1706|100|',
  ))
  original = CameraDatabase('synthetic', 1_700_000_000, tuple(cameras), 'speedcamonline.ru', 'synthetic-hash')
  path = tmp_path / 'cameras.json'
  save_database(str(path), original)
  restored = load_database(str(path))
  assert restored == original
  assert restored.cameras[0].source_params['1705 '] == ' 180 '
  assert restored.cameras[0].source_params['102'] == ' Комментарий '
  assert restored.cameras[0].source_params['999'] == 'opaque'
  assert restored.cameras[0].second_direction_deg == 90
  assert restored.cameras[0].allowed_auto_control and not restored.cameras[1].allowed_auto_control
  assert len(restored.cameras) == 2  # Repeated labels never merge records.
  assert json.loads(path.read_text())['data'][0]['source_id'] == 'RU-1'
  assert parse_camera_snapshot(json.dumps([asdict(camera) for camera in cameras])) == cameras
  assert parse_camera_snapshot({'type': 'FeatureCollection', 'features': [
    {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [37, 55]},
     'properties': asdict(cameras[0])}]}) == [cameras[0]]


def test_full_user_file_counts_and_source_hash_unchanged():
  path = Path(os.environ.get('CITYGUIDE_TEST_FILE', Path.home() / 'Downloads/Telegram Desktop/SpeedCam.bkm'))
  if not path.is_file():
    pytest.skip('external user BKM fixture unavailable; set CITYGUIDE_TEST_FILE')
  raw = path.read_bytes()
  digest = '0ccaba30044729081e36cc006817d81501ad52dbe9eaf2ddbf35aa5751482c6c'
  assert hashlib.sha256(raw).hexdigest() == digest
  cameras = parse_cityguide_snapshot(raw)
  assert len(cameras) == 74016
  assert Counter(camera.camera_type.removeprefix('speedcamonline-cg77:') for camera in cameras) == {
    '18059': 56553, '18950': 932, '18958': 2667, '18925': 192,
    '18952': 13496, '18951': 174, 'unknown': 2,
  }
  assert sum(camera.direction_deg is not None for camera in cameras) == 60149
  assert sum(camera.allowed_auto_control for camera in cameras) == 59669
  assert Counter(camera.camera_type.removeprefix('speedcamonline-cg77:') for camera in cameras
                 if camera.allowed_auto_control) == {'18059': 56104, '18950': 907, '18958': 2658}
  assert all(camera.second_direction_deg is None for camera in cameras)
  assert sum(camera.speed_limit_kph is None for camera in cameras) == 3852
  for row, camera in zip(raw.decode('ascii').splitlines()[1:], cameras):
    fields = row.split('|')
    assert camera.source_id == fields[1]
    assert camera.source_params == dict(zip(fields[4:-1:2], fields[5:-1:2]))
    assert camera.end_latitude is None and camera.end_longitude is None
  assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_retained_independent_igo_prefix_agrees_with_290_exact_coordinate_and_id_matches():
  bkm_path = Path(os.environ.get('CITYGUIDE_TEST_FILE', Path.home() / 'Downloads/Telegram Desktop/SpeedCam.bkm'))
  igo_path = ROOT.parent / 'results/speedcamonline_research/speedcamonline_igo_Rus.raw'
  if not bkm_path.is_file() or not igo_path.is_file():
    pytest.skip('external independent BKM/iGo evidence fixtures unavailable')
  raw = igo_path.read_bytes()
  assert hashlib.sha256(raw).hexdigest() == '37ad25447299f181a271f2cbac8ba8e108fd4e3602f2c5d2bc8286110101364e'
  rows = list(csv.DictReader(io.StringIO(raw[:raw.rfind(b'\n') + 1].decode('ascii'))))
  assert len(rows) == 422
  cameras = parse_cityguide_snapshot(bkm_path.read_bytes())
  index = {(camera.source_id, camera.latitude, camera.longitude): camera for camera in cameras}
  matched = Counter()
  for row in rows:
    camera = index.get(('RU-' + row['IDX'], float(row['Y']), float(row['X'])))
    if camera is None:
      continue
    assert camera.direction_deg == float(row['DIRECTION'])
    assert camera.direction_type == int(row['DIRTYPE']) == 1
    assert camera.speed_limit_kph == (float(row['SPEED']) or None)
    matched[(camera.camera_type.removeprefix('speedcamonline-cg77:'), row['TYPE'])] += 1
  assert matched == {('18059', '1'): 185, ('18059', '2'): 9, ('18950', '5'): 65, ('18958', '5'): 31}
