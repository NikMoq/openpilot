"""Verified integer-only common2.geometry.Polyline wire adapter."""

MAX_BYTES = 1_048_576
MAX_POINTS = 100_000
_I32_MIN = -(1 << 31)
_I32_MAX = (1 << 31) - 1


def _varint(data, offset):
  value = 0
  for shift in range(0, 35, 7):
    if offset >= len(data):
      raise ValueError("truncated varint")
    byte = data[offset]
    offset += 1
    value |= (byte & 127) << shift
    if not byte & 128:
      if value > 0xffffffff or (shift and byte == 0):
        raise ValueError("invalid uint32 varint")
      return value, offset
  raise ValueError("oversized varint")


def _put(value):
  out = bytearray()
  while value >= 128:
    out.append((value & 127) | 128)
    value >>= 7
  out.append(value)
  return bytes(out)


def _signed(value):
  return (value >> 1) ^ -(value & 1)


def _zigzag(value):
  if not _I32_MIN <= value <= _I32_MAX:
    raise ValueError("coordinate delta exceeds int32")
  return ((value << 1) ^ (value >> 31)) & 0xffffffff


def _payload(data, offset):
  size, offset = _varint(data, offset)
  end = offset + size
  if end > len(data):
    raise ValueError("truncated message")
  return data[offset:end], end


def _sequence(data, limit):
  initial = None
  deltas = []
  offset = 0
  while offset < len(data):
    tag, offset = _varint(data, offset)
    if tag == 8:
      if initial is not None:
        raise ValueError("duplicate initial coordinate")
      value, offset = _varint(data, offset)
      initial = _signed(value)
    elif tag in (16, 18):
      if tag == 18:
        packed, offset = _payload(data, offset)
        position = 0
        while position < len(packed):
          value, position = _varint(packed, position)
          deltas.append(_signed(value))
          if len(deltas) >= MAX_POINTS:
            raise ValueError("point budget exceeded")
      else:
        value, offset = _varint(data, offset)
        deltas.append(_signed(value))
      if len(deltas) >= MAX_POINTS:
        raise ValueError("point budget exceeded")
    else:
      raise ValueError("unknown coordinate field")
  if initial is None:
    raise ValueError("missing initial coordinate")
  values = [initial]
  for delta in deltas:
    values.append(values[-1] + delta)
  if any(not -limit <= value <= limit for value in values):
    raise ValueError("coordinate outside geographic range")
  return values


def decode_polyline_scaled(data: bytes) -> tuple[tuple[int, int], ...]:
  """Decode longitude/latitude in integer millionths of a degree."""
  if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
    raise ValueError("invalid polyline byte budget")
  axes = {}
  offset = 0
  while offset < len(data):
    tag, offset = _varint(data, offset)
    if tag not in (10, 18) or tag in axes:
      raise ValueError("unknown or duplicate polyline field")
    payload, offset = _payload(data, offset)
    axes[tag] = _sequence(payload, 180_000_000 if tag == 10 else 90_000_000)
  if set(axes) != {10, 18} or len(axes[10]) != len(axes[18]):
    raise ValueError("missing or unequal coordinate sequences")
  return tuple(zip(axes[10], axes[18], strict=True))


def encode_polyline_scaled(points) -> bytes:
  """Encode exact scaled integers; never requantize floating coordinates."""
  axes = ([], [])
  for point in points:
    if len(axes[0]) >= MAX_POINTS:
      raise ValueError("point budget exceeded")
    if not isinstance(point, (tuple, list)) or len(point) != 2:
      raise ValueError("expected longitude/latitude pair")
    for axis, value, limit in zip(axes, point, (180_000_000, 90_000_000), strict=True):
      if type(value) is not int or not -limit <= value <= limit:
        raise ValueError("expected scaled geographic integer")
      axis.append(value)
  if not axes[0]:
    raise ValueError("empty polyline")
  result = bytearray()
  for tag, values in zip((10, 18), axes, strict=True):
    payload = b"\x08" + _put(_zigzag(values[0]))
    packed = b"".join(_put(_zigzag(current - previous)) for previous, current in zip(values, values[1:], strict=False))
    if packed:
      payload += b"\x12" + _put(len(packed)) + packed
    result.extend(bytes((tag,)) + _put(len(payload)) + payload)
  if len(result) > MAX_BYTES:
    raise ValueError("polyline byte budget exceeded")
  return bytes(result)


def join_polylines(polylines) -> bytes:
  """Join connected sections, dropping only the identical boundary point."""
  points = []
  total_bytes = 0
  for data in polylines:
    total_bytes += len(data)
    if total_bytes > MAX_BYTES:
      raise ValueError("input byte budget exceeded")
    section = decode_polyline_scaled(data)
    if points and points[-1] != section[0]:
      raise ValueError("disconnected polyline sections")
    points.extend(section[1:] if points else section)
    if len(points) > MAX_POINTS:
      raise ValueError("point budget exceeded")
  return encode_polyline_scaled(points)
