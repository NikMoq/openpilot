"""Linux/Cereal integration: serialized pandad status consumed by real Card code."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cereal import log
from test_pq_icbm_card_runtime import SM, control_method


def test_serialized_status_reaches_controller_with_no_event_wrapper():
  message = log.Event.new_message(logMonoTime=1_000_000_000, valid=True)
  status = message.init("customReserved11")
  status.signatureMatched = True
  status.protocolValid = True
  status.pandaCount = 1
  status.state = 7
  status.button = 3
  status.requestId = 65535
  status.statusMonoTimeNs = 900_000_000
  with log.Event.from_bytes(message.to_bytes()) as event:
    sm = SM(customReserved11=event.customReserved11)
    sm.alive = True
    sm.valid = {"customReserved11": event.valid}
    sm.logMonoTime = {"customReserved11": event.logMonoTime, "carControlSP": event.logMonoTime}
    ci = NS(apply=Mock(return_value=(None, [])), CC=NS(icbm_command=None))
    card = NS(initialized_prev=True, sm=sm, can_log_mono_time=event.logMonoTime,
              CI=ci, pm=Mock(), publish_icbm_command=Mock())
    control = NS()
    control_method()(card, NS(canValid=True), NS(), control)
    assert control.icbm_status_valid
    assert control.icbm_firmware_state == 7
    assert control.icbm_firmware_request_id == 65535
    assert control.icbm_firmware_status_mono_time_ns == 900_000_000
