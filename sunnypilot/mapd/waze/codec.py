"""Minimal fetch-only Waze RT protobuf wire codec, with no generated/runtime dependency.

Field evidence: MIT highway-radar-sabre-plus Waze fetch schema, exercised by
the project's November 2026 Python and Java live probes. Reporting messages
are intentionally not encoded. No server-supplied descriptions/credentials
are included in errors or event exports.
"""
from __future__ import annotations

import base64
import uuid

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_FIELDS = 100_000
TYPES = {0: "UNKNOWN", 1: "CHIT_CHAT", 2: "POLICE", 3: "ACCIDENT", 4: "JAM", 5: "TRAFFIC_INFO",
         6: "HAZARD", 7: "MISC", 8: "CONSTRUCTION", 9: "PARKING", 10: "DYNAMIC", 11: "CAMERA",
         13: "ROAD_CLOSED", 14: "SYSTEM_ROAD_CLOSED", 16: "SOS", 17: "CRASH_PRONE", 19: "TURN_CLOSED",
         100: "NEW_BAD_WEATHER", 101: "NEW_LANE_CLOSED", 103: "PERMANENT_HAZARD", 104: "PERSONAL_SAFETY"}
SUBTYPES = {0: "NO_SUBTYPE", 201: "POLICE_VISIBLE", 202: "POLICE_HIDING", 203: "POLICE_WITH_MOBILE_CAMERA",
            301: "ACCIDENT_MINOR", 302: "ACCIDENT_MAJOR", 401: "JAM_MODERATE_TRAFFIC", 402: "JAM_HEAVY_TRAFFIC",
            403: "JAM_STAND_STILL_TRAFFIC", 404: "JAM_LIGHT_TRAFFIC", 623: "HAZARD_ON_ROAD_CONSTRUCTION",
            1301: "ROAD_CLOSED_HAZARD", 1302: "ROAD_CLOSED_CONSTRUCTION", 1303: "ROAD_CLOSED_EVENT", 5001: "DEFAULT_CAMERA"}


class ProtocolError(ValueError):
  pass


def varint(value: int) -> bytes:
  value &= (1 << 64) - 1
  out = bytearray()
  while value > 127:
    out.append((value & 127) | 128)
    value >>= 7
  out.append(value)
  return bytes(out)


def integer(field: int, value: int) -> bytes:
  return varint(field << 3) + varint(value)


def binary(field: int, value: bytes | str) -> bytes:
  body = value.encode("utf-8") if isinstance(value, str) else value
  return varint((field << 3) | 2) + varint(len(body)) + body


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
  value = 0
  for shift in range(0, 70, 7):
    if offset >= len(data):
      raise ProtocolError("Truncated Waze protobuf varint")
    byte = data[offset]
    offset += 1
    if shift == 63 and byte > 1:
      raise ProtocolError("Oversized Waze protobuf varint")
    value |= (byte & 127) << shift
    if byte < 128:
      return value, offset
  raise ProtocolError("Oversized Waze protobuf varint")


def message(data: bytes) -> dict[int, list[int | bytes]]:
  if not isinstance(data, bytes) or len(data) > MAX_MESSAGE_BYTES:
    raise ProtocolError("Invalid Waze protobuf size")
  out: dict[int, list[int | bytes]] = {}
  offset, fields = 0, 0
  while offset < len(data):
    key, offset = _read_varint(data, offset)
    field, wire = key >> 3, key & 7
    if field == 0 or field >= 1 << 29:
      raise ProtocolError("Invalid Waze protobuf field")
    fields += 1
    if fields > MAX_FIELDS:
      raise ProtocolError("Too many Waze protobuf fields")
    if wire == 0:
      value, offset = _read_varint(data, offset)
    elif wire in (1, 2, 5):
      if wire == 2:
        size, offset = _read_varint(data, offset)
      else:
        size = 8 if wire == 1 else 4
      if size > len(data) - offset:
        raise ProtocolError("Truncated Waze protobuf field")
      value = data[offset:offset + size]
      offset += size
    else:
      raise ProtocolError("Unsupported Waze protobuf wire type")
    out.setdefault(field, []).append(value)
  return out


def first(fields: dict, number: int, default=None):
  values = fields.get(number, [])
  return values[-1] if values else default


def nested(fields: dict, number: int) -> dict:
  value = first(fields, number, b"")
  if not isinstance(value, bytes):
    raise ProtocolError("Wrong Waze protobuf nested type")
  return message(value)


def text(fields: dict, number: int, default: str = "") -> str:
  value = first(fields, number)
  if value is None:
    return default
  if not isinstance(value, bytes):
    raise ProtocolError("Wrong Waze protobuf string type")
  try:
    return value.decode("utf-8")
  except UnicodeDecodeError:
    raise ProtocolError("Invalid Waze protobuf text") from None


def scalar(fields: dict, number: int, default: int = 0) -> int:
  value = first(fields, number, default)
  if not isinstance(value, int):
    raise ProtocolError("Wrong Waze protobuf scalar type")
  return value


def signed32(value: int) -> int:
  value &= 0xffffffff
  return value - (1 << 32) if value & (1 << 31) else value


def elements(data: bytes) -> list[dict]:
  values = message(data).get(1001, [])
  if any(not isinstance(value, bytes) for value in values):
    raise ProtocolError("Invalid Waze RT batch element")
  return [message(value) for value in values]


def line(element_number: int, payload: bytes) -> str:
  return "ProtoBase64," + base64.b64encode(binary(1001, binary(element_number, payload))).decode("ascii")


def client_info(lat: float, lon: float, installation: str, now_ms: int) -> str:
  position = integer(101, round(lon * 1e6)) + integer(102, round(lat * 1e6))
  display = integer(1, 2) + integer(2, 1080) + integer(3, 2400)
  info = (integer(1, 234) + binary(3, "5.17.1.0") + binary(4, position) + binary(5, "Google") + binary(6, "Pixel 8")
          + binary(11, "15-SDK35") + binary(16, "en") + binary(17, installation) + integer(18, 50) + integer(19, 1)
          + binary(24, display) + binary(25, "en") + binary(26, str(uuid.uuid4())) + integer(28, now_ms) + integer(31, 5))
  return line(2184, info)


def login_line(username: str, password: str) -> str:
  return line(2744, binary(1, binary(1, username) + binary(2, password)) + integer(2, 0) + integer(4, 0))


def uid_header(session_id: int, secret_key: str) -> str:
  return base64.b64encode(integer(1, session_id) + binary(2, secret_key)).decode("ascii")


def map_command(west: float, south: float, east: float, north: float) -> str:
  coordinates = [west, north, east, north, east, south, west, south]
  center = [(west + east) / 2, (south + north) / 2]
  return "MapDisplayed," + ",".join(f"{value:.6f}" for value in coordinates + center) + ",67186," + ",".join(f"{value:.6f}" for value in coordinates)


def parse_events(batch: list[dict], observed_at_ms: int, ttl_ms: int) -> tuple[list[dict], list[str]]:
  events, removed = [], []
  for element in batch:
    command = text(element, 2001).strip()
    if command.startswith("RmAlert,"):
      identifier = command.split(",", 1)[1].strip()
      if identifier:
        removed.append(identifier)
    if 2708 not in element:
      continue
    realtime = nested(nested(element, 2708), 1)
    info = nested(realtime, 2)
    coordinate = nested(info, 3)
    if 101 not in coordinate or 102 not in coordinate:
      continue
    lon, lat = signed32(scalar(coordinate, 101)) / 1e6, signed32(scalar(coordinate, 102)) / 1e6
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
      raise ProtocolError("Waze RT event has invalid coordinates")
    reporting = nested(realtime, 3)
    identifier = text(realtime, 6)
    if not identifier:
      numeric_id = scalar(realtime, 1)
      if numeric_id <= 0:
        continue
      identifier = str(numeric_id)
    if scalar(reporting, 13):
      removed.append(identifier)
      continue
    report_seconds = scalar(reporting, 4)
    heading = scalar(info, 6, -1)
    address = nested(reporting, 8)
    raw_type, raw_subtype = scalar(info, 1), scalar(info, 2)
    events.append({"source": "waze", "source_kind": "mobile", "source_id": f"waze:rt:{identifier}", "raw_id": identifier,
                   "raw_type": TYPES.get(raw_type, str(raw_type)), "raw_subtype": SUBTYPES.get(raw_subtype, str(raw_subtype)),
                   "raw_type_code": raw_type, "raw_subtype_code": raw_subtype, "latitude": lat, "longitude": lon,
                   "heading_raw": heading if 0 <= heading < 360 else None,
                   "published_at_ms": report_seconds * 1000 if report_seconds > 0 else None,
                   "observed_at_ms": observed_at_ms, "expires_at_ms": observed_at_ms + ttl_ms,
                   "speed_raw": None, "speed_unit": None, "speed_limit_kph": None,
                   "street": text(address, 2) or None, "city": text(address, 3) or None,
                   "thumbs_up": scalar(reporting, 9) if 9 in reporting else None})
  return events, [f"waze:rt:{identifier}" for identifier in removed]
