"""Yandex native evidence admission and freshness; no network or control effects."""
import hashlib
import json
import math

from openpilot.sunnypilot.mapd.speed_cameras import CameraSpatialIndex, SpeedCamera
from openpilot.sunnypilot.mapd.yandex.native_snapshot import _bearing, _distance

MAX_EVENT_BYTES = 16 * 1024


def _number(value):
  return type(value) in (int, float) and math.isfinite(value)


def _point(value):
  return (isinstance(value, (tuple, list)) and len(value) == 2 and
          all(_number(v) for v in value) and abs(value[0]) <= 180 and abs(value[1]) <= 90)


def _type(event):
  tags = event.get('tags')
  if not isinstance(tags, (list, tuple)) or not all(isinstance(t, str) for t in tags):
    return None
  kind = event.get('source_kind')
  if kind == 'mobile':
    for tag in ('mobile_control', 'police_patrol'):
      if tag in tags:
        return 'yandex:mobile:' + tag
  if kind == 'fixed' and 'speed_control' in tags:
    return 'yandex:fixed:speed_camera'
  if kind == 'warning' and 'police' in tags:
    return 'yandex:warning:police'
  return None


def _valid(event):
  if not isinstance(event, dict) or event.get('source') != 'yandex' or _type(event) is None:
    return False
  identity = event.get('source_id')
  observed, expires = event.get('observed_at'), event.get('expires_at')
  ttl = 180 if event.get('source_kind') == 'mobile' else 300
  return (isinstance(identity, str) and identity.startswith('yandex:native:') and len(identity) <= 512 and
          _point((event.get('longitude'), event.get('latitude'))) and
          _number(observed) and observed > 0 and _number(expires) and 0 < expires - observed <= ttl and
          event.get('ttl_basis') == 'local_policy_cap')


def _fresh(event, now):
  return _valid(event) and _number(now) and event['observed_at'] <= now + 1 and now < event['expires_at']


def _event(camera):
  try:
    raw = camera.source_params['yandex_event']
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_EVENT_BYTES:
      return None
    event = json.loads(raw)
    return event if _valid(event) else None
  except (KeyError, TypeError, ValueError, AttributeError):
    return None


def _control(event):
  if not _valid(event) or event['source_kind'] not in ('fixed', 'mobile'):
    return None
  if (event.get('source_sdk') != '4.45.0-full' or
      event.get('speed_limit_confidence') != 'native_rle_verified_against_sdk_edges' or
      event.get('road_limit_raw_unit') != 'm/s' or event.get('road_limit_unit') != 'km/h' or
      event.get('road_limit_direction_basis') not in ('acquired_route_forward_edge', 'matched_mobile_parallel_route')):
    return None
  raw, kph = event.get('road_limit_raw_mps'), event.get('road_limit_kph')
  if not _number(raw) or not _number(kph) or not 0 < kph <= 300 or abs(raw * 3.6 - kph) > 1e-4 or kph != round(kph):
    return None
  geometry = event.get('road_geometry')
  if not isinstance(geometry, (list, tuple)) or len(geometry) != 2 or not all(_point(p) for p in geometry):
    return None
  a, b = geometry
  if _distance(a, b) <= 0 or abs(a[0] - b[0]) > 180:
    return None
  fraction, index = event.get('position_fraction'), event.get('position_index')
  if not _number(fraction) or not 0 <= fraction <= 1 or type(index) is not int or index < 0:
    return None
  projected = (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)
  supplied = (event.get('route_position_longitude'), event.get('route_position_latitude'))
  if not _point(supplied) or _distance(projected, supplied) > .01:
    return None
  separation = _distance((event['longitude'], event['latitude']), supplied)
  reported = event.get('report_route_distance_m')
  heading = event.get('road_heading_deg')
  parallel = event.get('road_limit_direction_basis') == 'matched_mobile_parallel_route'
  if (separation > (40 if parallel else 30) or not _number(reported) or reported < 0 or abs(reported - separation) > .01 or
      not _number(heading) or not 0 <= heading < 360 or abs((heading - _bearing(a, b) + 180) % 360 - 180) > .01):
    return None
  if parallel:
    if event['source_kind'] != 'mobile' or event.get('match_unique') is not True:
      return None
    original = event.get('original_road_geometry')
    if not isinstance(original, (list, tuple)) or len(original) != 2 or not all(_point(p) for p in original):
      return None
    oa, ob = original
    if _distance(oa, ob) <= 0 or abs(oa[0] - ob[0]) > 180:
      return None
    ofraction = event.get('original_position_fraction')
    oheading = event.get('original_road_heading_deg')
    opoint = (event.get('original_route_position_longitude'), event.get('original_route_position_latitude'))
    if not _number(ofraction) or not 0 <= ofraction <= 1 or not _point(opoint):
      return None
    oprojected = (oa[0] + (ob[0] - oa[0]) * ofraction, oa[1] + (ob[1] - oa[1]) * ofraction)
    if (_distance(oprojected, opoint) > .01 or not _number(oheading) or not 0 <= oheading < 360 or
        abs((oheading - _bearing(oa, ob) + 180) % 360 - 180) > .01 or
        abs((heading - oheading + 180) % 360 - 180) < 150):
      return None
    oseparation = _distance((event['longitude'], event['latitude']), opoint)
    oreported = event.get('original_report_route_distance_m')
    if oseparation > 30 or not _number(oreported) or oreported < 0 or abs(oreported - oseparation) > .01:
      return None
  suffix = hashlib.sha256(json.dumps(geometry, separators=(',', ':')).encode()).hexdigest()[:24]
  return supplied[1], supplied[0], kph, heading, event['source_id'] + ':road:' + suffix


def yandex_control_evidence(camera):
  """Validate serialized evidence and every camera field without wall-clock reads."""
  event = _event(camera)
  evidence = _control(event) if event else None
  if evidence is None:
    return False
  lat, lon, speed, heading, identity = evidence
  return bool(camera.latitude == lat and camera.longitude == lon and camera.speed_limit_kph == speed and
              type(camera.direction_type) is int and camera.direction_type == 1 and camera.direction_deg == heading and
              camera.source_id == identity and camera.camera_type == _type(event) and
              camera.end_latitude is None and camera.end_longitude is None and camera.second_direction_deg is None)


def events_to_cameras(events, now):
  cameras = {}
  for event in events:
    if not _fresh(event, now):
      continue
    try:
      raw = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
      continue
    if len(raw.encode()) > MAX_EVENT_BYTES:
      continue
    params = {'yandex_event': raw, 'source_id': event['source_id']}
    evidence = _control(event)
    if evidence is None or event['source_kind'] == 'mobile':
      camera = SpeedCamera(event['latitude'], event['longitude'], camera_type=_type(event),
                           source_id=event['source_id'], source_params=params)
      cameras[camera.source_id] = camera
    if evidence:
      lat, lon, speed, heading, identity = evidence
      camera = SpeedCamera(lat, lon, speed, heading, _type(event), 1, source_id=identity, source_params=params)
      cameras[identity] = camera
  return list(cameras.values())


def filter_fresh_yandex(cameras, now):
  result = []
  for camera in cameras:
    event = _event(camera)
    if event is None or not _fresh(event, now):
      continue
    # Reconstruct admission so tampered cached warning/control fields cannot survive.
    if camera in events_to_cameras((event,), now):
      result.append(camera)
  return result


def merge_yandex_sources(base, yandex, now):
  """Preserve all existing authority and separate directed provider identities."""
  result = list(base)
  identities = {c.source_id for c in result}
  index = CameraSpatialIndex(result)
  for camera in filter_fresh_yandex(yandex, now):
    if camera.source_id in identities:
      continue
    if not yandex_control_evidence(camera):
      hidden = False
      for original in index.query(camera.latitude, camera.longitude, 50):
        if (not original.allowed_auto_control or
            _distance((camera.longitude, camera.latitude), (original.longitude, original.latitude)) > 50):
          continue
        opposite = (camera.direction_deg is not None and original.direction_deg is not None and
                    abs((camera.direction_deg - original.direction_deg + 180) % 360 - 180) > 90)
        if not opposite:
          hidden = True
          break
      if hidden:
        continue
    result.append(camera)
    identities.add(camera.source_id)
  return result
