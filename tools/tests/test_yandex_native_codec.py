"""Fixed synthetic wire vectors, no network or live credentials."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('native_codec_test', ROOT / 'sunnypilot/mapd/yandex/native_codec.py')
codec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codec)


def test_conditions_fixed_vector():
  body = bytes.fromhex('180118960122080a02080112027879')
  decoded = codec.decode_conditions_request(body)
  assert decoded['field3_values'] == [1, 150]
  assert decoded['representation'] == {'polyline_wire': b'\x08\x01', 'field2_bytes': b'xy', 'unknown_field_numbers': []}
  assert codec.encode_conditions_request([1, 150], polyline_wire=b'\x08\x01', field2_bytes=b'xy') == body


def test_packed_mixed_and_uint32_max():
  assert codec.decode_conditions_request(bytes.fromhex('1a0301960118ffffffff0f'))['field3_values'] == [1, 150, 0xffffffff]


def test_empty_presence():
  assert codec.encode_conditions_request() == b''
  assert codec.encode_conditions_request(representation_present=True) == b'\x22\x00'
  assert codec.decode_conditions_request(b'\x22\x00')['representation']['polyline_wire'] is None


def test_speed_known_fixed_vectors():
  raw = bytes.fromhex('0d0000803f10ffffffffffffffffff01')
  assert codec.decode_speed_limit(raw) == {'field1_float32': 1.0, 'field2_int32': -1, 'unknown_field_numbers': []}
  assert codec.decode_speed_limits(b'\x0a\x10' + raw)['field1_limits'][0]['field2_int32'] == -1
  assert codec.decode_speed_limit(bytes.fromhex('1080808080f8ffffffff01'))['field2_int32'] == -2147483648
  assert codec.decode_speed_limit(bytes.fromhex('10ffffffff07'))['field2_int32'] == 2147483647


def test_unknown_numbers_each_level():
  assert codec.decode_conditions_request(bytes.fromhex('0801'))['unknown_field_numbers'] == [1]
  assert codec.decode_conditions_request(bytes.fromhex('22021803'))['representation']['unknown_field_numbers'] == [3]
  assert codec.decode_speed_limit(bytes.fromhex('1801'))['unknown_field_numbers'] == [3]
  assert codec.decode_speed_limits(bytes.fromhex('1001'))['unknown_field_numbers'] == [2]


@pytest.mark.parametrize('body', [
  b'\x00',
  b'\x18\x80',
  b'\x18\x81\x00',
  b'\x18' + b'\xff' * 10,
  b'\x22\x02\x08',
  b'\x1d1234',
  b'\x23',
  b'\x18\x80\x80\x80\x80\x10',
  b'\x22\x00\x22\x00',
  b'\x22\x02\x08\x00'])
def test_malformed_conditions(body):
  with pytest.raises(codec.WireError):
    codec.decode_conditions_request(body)


@pytest.mark.parametrize('body', [
  bytes.fromhex('0d0000807f'),
  bytes.fromhex('0d0000c07f'),
  bytes.fromhex('108080808008'),
  bytes.fromhex('10ffffffff0f'),
  bytes.fromhex('0801'),
  bytes.fromhex('10011002')])
def test_malformed_speed(body):
  with pytest.raises(codec.WireError):
    codec.decode_speed_limit(body)


def test_aggregate_budget(monkeypatch):
  monkeypatch.setattr(codec, 'MAX_FIELDS', 2)
  with pytest.raises(codec.WireError):
    codec.decode_speed_limits(bytes.fromhex('0a0410011801'))
  with pytest.raises(codec.WireError):
    codec.decode_conditions_request(b'\x08\x01' * 3)


def test_value_and_size_budget(monkeypatch):
  monkeypatch.setattr(codec, 'MAX_VALUES', 2)
  with pytest.raises(codec.WireError):
    codec.decode_conditions_request(bytes.fromhex('1a03010203'))
  with pytest.raises(codec.WireError):
    codec.encode_conditions_request(range(3))
  monkeypatch.setattr(codec, 'MAX_BYTES', 2)
  with pytest.raises(codec.WireError):
    codec.decode_speed_limit(b'abc')


@pytest.mark.parametrize('value', [-1, True, 1.5, 1 << 32])
def test_encode_rejects_invalid_raw_values(value):
  with pytest.raises(codec.WireError):
    codec.encode_conditions_request([value])
