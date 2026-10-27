"""Check the bounded Cereal transport services used by card and pandad."""
import importlib.util
from pathlib import Path


SERVICES = Path(__file__).resolve().parents[2] / "cereal" / "services.py"
SPEC = importlib.util.spec_from_file_location("pq_icbm_services", SERVICES)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_icbm_request_service_is_runtime_only_and_card_rate():
  request = MODULE.SERVICE_LIST["customReserved10"]
  assert request.should_log is False
  assert request.frequency == 100.0


def test_icbm_status_service_is_logged_at_pandad_poll_rate():
  status = MODULE.SERVICE_LIST["customReserved11"]
  assert status.should_log is True
  assert status.frequency == 10.0
  assert status.decimation == 1
