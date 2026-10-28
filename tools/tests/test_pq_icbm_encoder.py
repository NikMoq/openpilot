"""Pure named-button mapping test without Cap'n Proto or a CAN runtime."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "opendbc_repo/opendbc/car/volkswagen/pqcan.py"
SPEC = importlib.util.spec_from_file_location("pqcan_icbm_encoder", SOURCE)
pqcan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pqcan)


class CapturePacker:
  def make_can_msg(self, name, bus, values):
    return name, bus, values


@pytest.mark.parametrize(("button", "signal"), [
  ("UP", "GRA_Up_kurz"), ("DOWN", "GRA_Down_kurz"),
  ("RES", "GRA_Recall"), ("SET", "GRA_Neu_Setzen"),
])
def test_named_button_preserves_coarse_and_fine_stock_signals(button, signal):
  stock = {"COUNTER": 15, "GRA_Hauptschalt": 1, "GRA_Typ_Hauptschalt": 1,
           "GRA_Kodierinfo": 7, "GRA_Sender": 2}
  before = stock.copy()
  name, bus, values = pqcan.create_icbm_button_control(CapturePacker(), 2, stock, button)
  assert name == "GRA_Neu" and bus == 2
  assert values[signal] is True
  assert sum(bool(values[key]) for key in ("GRA_Up_kurz", "GRA_Down_kurz", "GRA_Recall", "GRA_Neu_Setzen")) == 1
  assert values["COUNTER"] == 0
  assert values["GRA_Kodierinfo"] == stock["GRA_Kodierinfo"]
  assert stock == before


def test_named_button_rejects_unknown_action():
  with pytest.raises(ValueError, match="unsupported PQ ICBM button"):
    pqcan.create_icbm_button_control(CapturePacker(), 2, {}, "CANCEL")
