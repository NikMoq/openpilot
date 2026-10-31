"""Bounded distance accounting for an already observed camera, never acquisition."""
import math

MAX_SAMPLE_AGE_S = .25
MAX_SAMPLE_GAP_S = 2.
MAX_PLAUSIBLE_SPEED_MPS = 60.  # Input sanity, matching existing GPS jump plausibility.


def camera_can_speed_acceptable(state):
  return bool(state.canValid and str(state.gearShifter) != 'reverse' and
              math.isfinite(state.vEgo) and 0 <= state.vEgo <= MAX_PLAUSIBLE_SPEED_MPS)


def car_speed_sample(sm, now_s):
  """Return actual CAN speed and its monotonic envelope timestamp, or no sample."""
  try:
    state = sm['carState']
    stamp = sm.logMonoTime['carState'] / 1_000_000_000
    speed = state.vEgo
    if (sm.valid['carState'] and sm.alive['carState'] and camera_can_speed_acceptable(state) and
        all(math.isfinite(v) for v in (speed,stamp,now_s)) and
        0 <= now_s-stamp <= MAX_SAMPLE_AGE_S):
      return stamp,speed
  except (KeyError,AttributeError,TypeError,ValueError):
    pass
  return None


class CameraOdometry:
  def __init__(self):
    self.identity = ''
    self.remaining = None
    self.previous = None
    self.last_stamp = None
    self.ready = False

  def anchor(self, identity, distance, sample):
    if not identity or not math.isfinite(distance) or distance < 0:
      return
    self.identity,self.remaining = identity,distance
    self.previous = sample if sample is None or (all(math.isfinite(v) for v in sample) and
                                                0 <= sample[1] <= MAX_PLAUSIBLE_SPEED_MPS) else None
    self.last_stamp = self.previous[0] if self.previous is not None else None
    self.ready = False

  def advance(self, identity, sample):
    self.ready = False
    if not identity or identity != self.identity or self.remaining is None:
      return None
    if sample is None:
      self.previous = None  # Unknown distance is never interpolated later.
      return None
    stamp,speed = sample
    if not all(math.isfinite(v) for v in sample) or not 0 <= speed <= MAX_PLAUSIBLE_SPEED_MPS:
      self.previous = None
      return None
    if self.last_stamp is not None and stamp <= self.last_stamp:
      if stamp < self.last_stamp:
        self.previous = None
      return None  # Duplicate/reversed samples cannot spend distance.
    self.last_stamp = stamp
    previous,self.previous = self.previous,sample
    if previous is None or not 0 < stamp-previous[0] <= MAX_SAMPLE_GAP_S:
      return None
    self.remaining = max(0.,self.remaining-(stamp-previous[0])*(previous[1]+speed)*.5)
    self.ready = True
    return self.remaining
