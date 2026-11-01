"""Pure, inactive prerequisite for PQ stock-ACC button selection.

Inputs are displayed integer speeds, not m/s. Only the verified kph model is
supported: RES/SET change the setpoint by +1/-1; UP moves to the next strictly
higher decade. Captured DOWN responses establish -10 only on decade-aligned
setpoints; off-decade DOWN is unverified, so selection uses fine SET there.
A coarse action is selected only if its supported endpoint lies between the
current speed and target. Valid inputs are 1..250;
255 is the unset sentinel, and all other invalid inputs produce no selection.

This module neither sends buttons nor enables an ICBM/Panda capability. The
caller must establish active ACC, units, freshness, manual/pedal priority,
transaction ownership, feedback, and release. A selection is not a lease or
proof that a physical ECU will respond according to this model. Existing user
1/5 virtual-target increments are not changed or rounded to decades here.
"""
import math
from typing import Literal


PQButton = Literal["RES", "SET", "UP", "DOWN"]


def _valid_display_speed(value: int | float) -> bool:
  return (isinstance(value, (int, float)) and not isinstance(value, bool)
          and 1 <= value <= 250 and math.isfinite(value) and value == int(value))


def select_pq_button(target_speed: int | float, current_speed: int | float, speed_unit: str) -> PQButton | None:
  """Return a named action, or None for equal/invalid/unsupported inputs.

  Integral floats such as 80.0 are accepted because transported speeds may be
  Float32 values. Strings, booleans, fractional speeds, mph and unknown units
  are rejected without conversion or guessing. Names denote independent
  primitives: RES is fine increase, SET fine decrease, UP/DOWN coarse steps.
  """
  if speed_unit != "kph" or not _valid_display_speed(target_speed) or not _valid_display_speed(current_speed):
    return None

  target, current = int(target_speed), int(current_speed)
  if target == current:
    return None
  if target > current:
    next_decade = (current // 10 + 1) * 10
    return "UP" if next_decade <= target else "RES"
  next_decade = ((current - 1) // 10) * 10
  return "DOWN" if current % 10 == 0 and next_decade >= target else "SET"
