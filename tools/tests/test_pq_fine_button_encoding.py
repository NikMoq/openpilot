"""Verify PQ button bytes using the real DBC packer; no CAN transmission."""
import importlib.util
from pathlib import Path

import pytest
from opendbc.can import CANPacker

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "opendbc_repo/opendbc/car/volkswagen/pqcan.py"
SPEC = importlib.util.spec_from_file_location("pqcan_under_test", SOURCE)
pqcan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pqcan)


@pytest.mark.parametrize("counter", range(16))
@pytest.mark.parametrize(("button", "bit"), [
  ("set_speed", 16), ("resume", 17), ("accel", 11), ("decel", 10), ("cancel", 9),
])
def test_button_encoding_preserves_configuration_and_counter(counter, button, bit):
  packer = CANPacker(str(ROOT / "opendbc_repo/opendbc/dbc/vw_pq.dbc"))
  stock = {"COUNTER": counter, "GRA_Hauptschalt": 1, "GRA_Typ_Hauptschalt": 1,
           "GRA_Kodierinfo": 1, "GRA_Sender": 2}
  before = stock.copy()
  address, data, bus = pqcan.create_acc_buttons_control(packer, 2, stock, **{button: True})
  assert address == 0x38A and bus == 2 and len(data) == 4
  raw = int.from_bytes(data, "little")
  expected = (1 << 8) | (1 << 15) | (2 << 18) | (1 << 29)
  expected |= ((counter + 1) % 16) << 20
  expected |= 1 << bit
  assert raw & 0xFFFFFF00 == expected
  assert data[0] == data[1] ^ data[2] ^ data[3]
  assert stock == before


def test_default_does_not_assert_set_or_any_button():
  packer = CANPacker(str(ROOT / "opendbc_repo/opendbc/dbc/vw_pq.dbc"))
  stock = {"COUNTER": 0, "GRA_Hauptschalt": 1, "GRA_Typ_Hauptschalt": 0,
           "GRA_Kodierinfo": 0, "GRA_Sender": 0}
  _, data, _ = pqcan.create_acc_buttons_control(packer, 2, stock)
  assert int.from_bytes(data, "little") & ((31 << 9) | (3 << 16) | (15 << 24)) == 0


@pytest.mark.parametrize("button,bit", [("UP", 11), ("DOWN", 10), ("RES", 17), ("SET", 16)])
def test_named_icbm_primitive_preserves_coarse_and_fine_semantics(button, bit):
  packer = CANPacker(str(ROOT / "opendbc_repo/opendbc/dbc/vw_pq.dbc"))
  stock = {"COUNTER": 15, "GRA_Hauptschalt": 1, "GRA_Typ_Hauptschalt": 1,
           "GRA_Kodierinfo": 1, "GRA_Sender": 2}
  address, data, bus = pqcan.create_icbm_button_control(packer, 2, stock, button)
  raw = int.from_bytes(data, "little")
  assert (address, bus, len(data)) == (0x38A, 2, 4)
  assert (raw & ((1 << 10) | (1 << 11) | (1 << 16) | (1 << 17))) == (1 << bit)
  assert (raw >> 20) & 0xF == 0
  assert data[0] == data[1] ^ data[2] ^ data[3]


def test_named_icbm_primitive_rejects_unknown_button():
  packer = CANPacker(str(ROOT / "opendbc_repo/opendbc/dbc/vw_pq.dbc"))
  with pytest.raises(ValueError, match="unsupported PQ ICBM button"):
    pqcan.create_icbm_button_control(packer, 2, {}, "CANCEL")
