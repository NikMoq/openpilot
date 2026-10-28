from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus
from opendbc.car.volkswagen.carstate import CarState
from opendbc.car.volkswagen.values import CAR, DBC


def test_pq_icbm_feedback_snapshot_uses_raw_values_and_parser_timestamps():
  dbc_name = DBC[CAR.VOLKSWAGEN_PASSAT_NMS][Bus.pt]
  parser = CANParser(dbc_name, [
    ("ACC_GRA_Anzeige", float("nan")),
    ("ACC_System", float("nan")),
    ("GRA_Neu", float("nan")),
    ("Motor_2", float("nan")),
    ("Motor_3", float("nan")),
    ("Motor_5", float("nan")),
  ], 0)
  packer = CANPacker(dbc_name)
  state = CarState.__new__(CarState)

  state.update_icbm_feedback_snapshot(parser, parser)
  assert state.icbm_feedback_snapshot == {
    "ACC_GRA_Anzeige": {"ACA_V_Wunsch": 0, "ACA_kmh_mph": 0, "timestamp_nanos": 0},
    "ACC_System": {"ACS_Sta_ADR": 0, "ACS_FreigSollB": 0, "timestamp_nanos": 0},
    "readiness_timestamps": {"GRA_Neu": 0, "Motor_2": 0, "Motor_3": 0, "Motor_5": 0},
  }

  gra = packer.make_can_msg("ACC_GRA_Anzeige", 0, {"ACA_V_Wunsch": 87, "ACA_kmh_mph": 1})
  system = packer.make_can_msg("ACC_System", 0, {"ACS_Sta_ADR": 1, "ACS_FreigSollB": 1})
  native = packer.make_can_msg("GRA_Neu", 0, {"COUNTER": 1})
  motor_2 = packer.make_can_msg("Motor_2", 0, {"MO2_Sta_GRA": 1, "MO2_BLS": 0})
  motor_3 = packer.make_can_msg("Motor_3", 0, {"Fahrpedal_Rohsignal": 0})
  motor_5 = packer.make_can_msg("Motor_5", 0, {"GRA_Hauptschalter": 1})
  parser.update([
    (1_234_567_890, [gra]),
    (1_345_678_901, [system]),
    (1_456_789_012, [native]),
    (1_567_890_123, [motor_2]),
    (1_678_901_234, [motor_3]),
    (1_789_012_345, [motor_5]),
  ])
  state.update_icbm_feedback_snapshot(parser, parser)

  assert state.icbm_feedback_snapshot == {
    "ACC_GRA_Anzeige": {
      "ACA_V_Wunsch": 87,
      "ACA_kmh_mph": 1,
      "timestamp_nanos": 1_234_567_890,
    },
    "ACC_System": {
      "ACS_Sta_ADR": 1,
      "ACS_FreigSollB": 1,
      "timestamp_nanos": 1_345_678_901,
    },
    "readiness_timestamps": {
      "GRA_Neu": 1_456_789_012,
      "Motor_2": 1_567_890_123,
      "Motor_3": 1_678_901_234,
      "Motor_5": 1_789_012_345,
    },
  }
