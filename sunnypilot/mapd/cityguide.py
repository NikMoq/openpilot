"""Strict, offline SpeedCamOnline CityGuide 7.7+ BKM import.

Evidence (reviewed 2026-10-26):
* https://speedcamonline.ru/ documents the CityGuide text export.
* Provider prefix: .native-build/source-probe/ru_cg77_metadata.json,
  https://speedcamonline.ru/cg77/Rus/comment/ ; partial payload SHA-256
  eb9838514c3530071194634c1c91a39e17afb96565156c502f1406519d2a2867.
  Observed header: 2|Radars|1251; rows: type|label|latitude|longitude|key|value...
* CityGuide 7.8 User Manual, copyright 2013 MIT, PDF page 106 (index 105),
  third-party mirror: https://gpsmobile.com.ua/files/Manual/CityGuide7.pdf .
  Documents 1709=speed; 1705/1707=first/second azimuth;
  1706/1708=corresponding range; 1713=beam shape (1 sector, 2 straight).
  Types: 18059 stationary, 18950 mobile, 18951 advanced speed radar,
  18952 traffic surveillance, 18958 tripod. These are NOT iGo type IDs.
* Full user-supplied SpeedCam.bkm SHA-256:
  0ccaba30044729081e36cc006817d81501ad52dbe9eaf2ddbf35aa5751482c6c
  (5,305,735 bytes, 74,016 records). Rows 60622/60623 have empty type IDs;
  retain these as explicitly unknown, never substitute a numeric camera type.

HIGH CONFIDENCE, not a universal exporter specification: 290 exact ID AND
coordinate matches against the independent SCO iGo prefix all have
travel bearing = (1705 + 180) % 360, identical speed, DIRTYPE=1, no conflicts.
The iGo prefix SHA-256 is
37ad25447299f181a271f2cbac8ba8e108fd4e3602f2c5d2bc8286110101364e.
Matches: 18059->iGo 1 (185), 18059->2 (9), 18950->5 (65), 18958->5 (31).
1713=0/1/2 all occur with DIRTYPE=1: beam is NOT direction_type.
https://www.rd-forum.ru/threads/izmenenie-suschestvujuschix-tochek-speedcamonline-ru.6046/post-318260
defines SCO course as vehicle travel bearing, not physical camera facing.

Selected policy: apply this inference ONLY to 18059/18950/18958 with recognized
beam 0/1/2 and valid azimuth metadata. A second documented azimuth gets the
same transformation (manual symmetry; not directly observed in the sample).
Retain one record, source label (NOT globally unique) and raw parameter tokens.
UNKNOWN: beam 0's geometric meaning, physical camera facing, section endpoints
or linkage. Ranges are retained, not used as road/route detection geometry.
Other types/beams and missing azimuths remain warning-only. No CG-to-iGo type
conversion is made; import eligibility uses a separate CG point-type policy.
This parser checks syntax, NOT HTTP completeness: a prefix ending on a complete
record cannot be detected without independent transport/size/hash evidence.
"""

from __future__ import annotations

import math
import re

from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera, cityguide_point_directions


_UINT = re.compile(r"[0-9]+\Z")
_HEADERS = {"2|Radars|1251", "2|Radars|65001"}


def _decode(payload: str | bytes) -> str:
  if isinstance(payload, str):
    return payload.removeprefix("\ufeff")
  if not isinstance(payload, bytes):
    raise TypeError("CityGuide payload must be str or bytes")
  if payload.startswith(b"\xef\xbb\xbf"):
    return payload.decode("utf-8-sig")
  # SCO declares Windows-1251; ASCII and UTF-8 exports are also accepted.
  try:
    return payload.decode("utf-8")
  except UnicodeDecodeError:
    if payload.split(b"\n", 1)[0].rstrip(b"\r") == b"2|Radars|65001":
      raise  # Never reinterpret a declared UTF-8 file as Windows-1251.
    return payload.decode("cp1251")


def _uint(value: str) -> int:
  if not _UINT.fullmatch(value):
    raise ValueError("expected unsigned integer")
  return int(value)


def _number(value: str, minimum: float, maximum: float = math.inf) -> float:
  result = float(value)
  if not math.isfinite(result) or not minimum <= result <= maximum:
    raise ValueError("number out of range")
  return result


def _row(line: str) -> SpeedCamera:
  raw_values = line.split("|")
  values = [value.strip() for value in raw_values]
  if values[-1] == "":
    values.pop()  # Optional final delimiter, not an additional parameter.
  if len(values) < 4 or (len(values) - 4) % 2:
    raise ValueError("expected type, label, coordinates and key/value pairs")
  kind = _uint(values[0]) if values[0] else "unknown"
  if kind == 0:
    raise ValueError("invalid type ID")
  latitude = _number(values[2], -90, 90)
  longitude = _number(values[3], -180, 180)
  parameters: dict[int, str] = {}
  source_params: dict[str, str] = {}
  for index in range(4, len(values), 2):
    key = _uint(values[index])
    if key == 0 or key in parameters:
      raise ValueError("invalid or duplicate parameter key")
    parameters[key] = values[index + 1]
    source_params[raw_values[index]] = raw_values[index + 1]

  speed = parameters.get(1709)
  # 0, blank and absent mean no usable positive limit. 300 is an import
  # sanity bound shared with the local iGo parser, not a BKM specification.
  limit = _number(speed, 0, 300) if speed else None
  for key in (1705, 1707):
    if key in parameters:
      _number(parameters[key], 0, 360)
  for key in (1706, 1708):
    if key in parameters:
      _number(parameters[key], 0)
  # Unknown beam tokens remain preserved warning records, including nonnumeric
  # values; do not interpret them as direction_type or discard the snapshot.
  first, second = cityguide_point_directions(str(kind), source_params)

  return SpeedCamera(
    latitude=latitude,
    longitude=longitude,
    speed_limit_kph=limit if limit else None,
    camera_type=f"speedcamonline-cg77:{kind}",
    direction_deg=first,
    second_direction_deg=second,
    direction_type=1 if first is not None else None,
    source_id=raw_values[1],
    source_params=source_params,
  )


def parse_cityguide_snapshot(payload: str | bytes) -> list[SpeedCamera]:
  """Parse atomically or raise ValueError; never return a valid partial prefix.

  Accept UTF-8 (optional BOM) and Windows-1251, LF or CRLF, and numeric unknown
  types/keys. Require the exact Radars version/code-page header, at least one
  record, a final newline, finite coordinates and valid numeric known fields.
  Empty provider type IDs are retained as speedcamonline-cg77:unknown.
  Unknown keys are structurally validated and preserved. Blank lines are ignored.
  No final newline is treated as truncation even for a structurally valid row.
  """
  try:
    text = _decode(payload)
  except UnicodeDecodeError as error:
    raise ValueError("invalid CityGuide encoding") from error
  # Do not let splitlines silently turn embedded control bytes into records.
  if any(ord(character) < 32 and character not in "\r\n\t" for character in text):
    raise ValueError("invalid CityGuide control character")
  if not text.endswith("\n") or "\r" in text.replace("\r\n", ""):
    raise ValueError("CityGuide snapshot is truncated or has invalid line endings")
  lines = [(number, line) for number, line in enumerate(text.split("\n"), 1) if line.strip()]
  if not lines or lines[0][1].rstrip("\r") not in _HEADERS:
    raise ValueError("invalid CityGuide Radars header")
  if len(lines) == 1:
    raise ValueError("CityGuide snapshot contains no records")
  cameras = []
  for number, line in lines[1:]:
    try:
      cameras.append(_row(line.rstrip("\r")))
    except ValueError as error:
      raise ValueError(f"invalid CityGuide row {number}: {error}") from error
  return cameras
