"""Shared camera GPS uncertainty policy; freshness/fix/lifecycle stay at callers."""
import math

MAX_HORIZONTAL_ACCURACY_M = 30.
MAX_BEARING_ACCURACY_DEG = 45.


def camera_accuracy_acceptable(gps):
  return bool(math.isfinite(gps.horizontalAccuracy) and 0 < gps.horizontalAccuracy <= MAX_HORIZONTAL_ACCURACY_M and
              math.isfinite(gps.bearingAccuracyDeg) and 0 <= gps.bearingAccuracyDeg <= MAX_BEARING_ACCURACY_DEG)


