import json

from openpilot.sunnypilot.mapd.speed_cameras import find_nearest_camera, parse_camera_snapshot


def test_parse_json_and_geojson():
  payload = json.dumps({"type": "FeatureCollection", "features": [{
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [37.62, 55.75]},
    "properties": {"speedLimit": 60, "type": "fixed"},
  }]})
  cameras = parse_camera_snapshot(payload)
  assert len(cameras) == 1
  assert cameras[0].speed_limit_kph == 60
  assert cameras[0].camera_type == "fixed"


def test_parse_radarbase_export_shape():
  cameras = parse_camera_snapshot({
    "meta": {"ver": "3.0", "build": 4237},
    "objects": [{"lat": 55.75, "lng": 37.61, "type": 1, "dir": 180,
                 "speedControls": [{"speed": 60, "type": "current"}]}],
  })
  assert len(cameras) == 1
  assert cameras[0].direction_deg == 180
  assert cameras[0].speed_limit_kph == 60


def test_nearest_camera_filters_behind_and_direction():
  cameras = parse_camera_snapshot({"cameras": [
    {"lat": 55.751, "lon": 37.620, "speed": 60, "direction": 0},
    {"lat": 55.749, "lon": 37.620, "speed": 40, "direction": 180},
  ]})
  match = find_nearest_camera(cameras, 55.750, 37.620, heading_deg=0, max_distance_m=500)
  assert match is not None
  assert match.camera.speed_limit_kph == 60


def test_no_match_outside_radius():
  cameras = parse_camera_snapshot([{"lat": 55.9, "lon": 37.620}])
  assert find_nearest_camera(cameras, 55.750, 37.620, max_distance_m=100) is None
