"""Original PQ parser rejects generated-bus and TX-return button observations.

This verifies software source segregation, not installed harness routing or ECU
acceptance. Original CAN parser, packer and CarState event conversion execute.
"""
from types import SimpleNamespace

from opendbc.can import CANParser, CANPacker
from opendbc.car import structs
from opendbc.car.volkswagen.carstate import CarState


def test_own_destination_and_tx_return_cannot_become_pt_button_events():
  button_type = structs.CarState.ButtonEvent.Type.accelCruise
  observer = CarState.__new__(CarState)
  observer.button_states = {button_type: False}
  buttons = [SimpleNamespace(event_type=button_type, can_addr='GRA_Neu', can_msg='GRA_Up_kurz', values=[1])]
  parser = CANParser('vw_pq', [], 0)
  parser.vl['GRA_Neu']  # Register the original lazy message before observations.
  packer = CANPacker('vw_pq')

  def frame(bus, pressed, counter):
    return packer.make_can_msg('GRA_Neu', bus, {'GRA_Up_kurz': int(pressed), 'COUNTER': counter,
                                              'GRA_Hauptschalt': 1})

  parser.update([(1_000_000_000, [frame(0, False, 0)])])
  assert not observer.create_button_events(parser, buttons)
  for index, bus in enumerate((2, 130, 194), start=1):
    parser.update([(1_000_000_000 + index * 20_000_000, [frame(bus, True, index)])])
    assert not observer.create_button_events(parser, buttons)
  parser.update([(1_100_000_000, [frame(0, True, 1)])])
  events = observer.create_button_events(parser, buttons)
  assert len(events) == 1 and events[0].pressed and events[0].type == button_type
  parser.update([(1_120_000_000, [frame(0, False, 2)])])
  events = observer.create_button_events(parser, buttons)
  assert len(events) == 1 and not events[0].pressed
