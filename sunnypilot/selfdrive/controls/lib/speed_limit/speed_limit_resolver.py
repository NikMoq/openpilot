"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import math
import time
from dataclasses import replace

import cereal.messaging as messaging
from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.gps import get_gps_location_service
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD, get_sanitize_int_param
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import LIMIT_MAX_MAP_DATA_AGE, LIMIT_ADAPT_ACC
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Policy, OffsetType
from openpilot.sunnypilot.mapd.camera_updater import CameraParams
from openpilot.sunnypilot.mapd.camera_gps_quality import camera_accuracy_acceptable
from openpilot.sunnypilot.mapd.camera_odometry import car_speed_sample, MAX_SAMPLE_AGE_S
from openpilot.sunnypilot.selfdrive.controls.lib.camera_speed_adapter import CameraSpeedAdapter
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.helpers import get_minimum_set_speed

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

ALL_SOURCES = tuple(SpeedLimitSource.schema.enumerants.values())


class SpeedLimitResolver:
  limit_solutions: dict[custom.LongitudinalPlanSP.SpeedLimit.Source, float]
  distance_solutions: dict[custom.LongitudinalPlanSP.SpeedLimit.Source, float]
  v_ego: float
  speed_limit: float
  speed_limit_last: float
  speed_limit_final: float
  speed_limit_final_last: float
  distance: float
  source: custom.LongitudinalPlanSP.SpeedLimit.Source
  speed_limit_offset: float

  def __init__(self):
    self.params = Params()
    self.frame = -1

    self._gps_location_service = get_gps_location_service(self.params)
    self.limit_solutions = {}  # Store for speed limit solutions from different sources
    self.distance_solutions = {}  # Store for distance to current speed limit start for different sources

    self.policy = self.params.get("SpeedLimitPolicy", return_default=True)
    self.policy = get_sanitize_int_param(
      "SpeedLimitPolicy",
      Policy.min().value,
      Policy.max().value,
      self.params
    )
    self._policy_to_sources_map = {
      Policy.car_state_only: [SpeedLimitSource.car],
      Policy.map_data_only: [SpeedLimitSource.map],
      Policy.car_state_priority: [SpeedLimitSource.car, SpeedLimitSource.map],
      Policy.map_data_priority: [SpeedLimitSource.map, SpeedLimitSource.car],
      Policy.combined: [SpeedLimitSource.car, SpeedLimitSource.map],
    }
    self.source = SpeedLimitSource.none
    for source in ALL_SOURCES:
      self._reset_limit_sources(source)

    self.is_metric = self.params.get_bool("IsMetric")
    self.offset_type = get_sanitize_int_param(
      "SpeedLimitOffsetType",
      OffsetType.min().value,
      OffsetType.max().value,
      self.params
    )
    self.offset_value = self.params.get("SpeedLimitValueOffset", return_default=True)

    self.speed_limit = 0.
    self.speed_limit_last = 0.
    self.speed_limit_final = 0.
    self.speed_limit_final_last = 0.
    self.speed_limit_offset = 0.
    self.camera_params = CameraParams()
    self.camera = CameraSpeedAdapter()
    self.camera_mode = False
    self._camera_requested = False
    self.camera_reaction_distance = 300
    self._camera_final_hold = None
    self.camera_display_raw_limit = 0.0
    self.camera_display_offset = 0.0
    self.camera_target_below_minimum = False
    self._last_odometry_car_receipt = None
    self._last_odometry_map_receipt = None
    self._last_odometry_car_stamp = None

  def update_speed_limit_states(self) -> None:
    self.speed_limit_final = self.speed_limit + self.speed_limit_offset

    if self.speed_limit > 0.:
      self.speed_limit_last = self.speed_limit
      self.speed_limit_final_last = self.speed_limit_final

  @property
  def speed_limit_valid(self) -> bool:
    return self.speed_limit > 0.

  @property
  def speed_limit_last_valid(self) -> bool:
    return self.speed_limit_last > 0.

  def update_params(self):
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.policy = self.params.get("SpeedLimitPolicy", return_default=True)
      self.is_metric = self.params.get_bool("IsMetric")
      self.offset_type = self.params.get("SpeedLimitOffsetType", return_default=True)
      self.offset_value = self.params.get("SpeedLimitValueOffset", return_default=True)

  def _get_speed_limit_offset(self, raw_limit: float | None = None) -> float:
    if self.offset_type == OffsetType.off:
      return 0
    elif self.offset_type == OffsetType.fixed:
      return float(self.offset_value * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS))
    elif self.offset_type == OffsetType.percentage:
      return float(self.offset_value * 0.01 * (self.speed_limit if raw_limit is None else raw_limit))
    else:
      raise NotImplementedError("Offset not supported")

  def _reset_limit_sources(self, source: custom.LongitudinalPlanSP.SpeedLimit.Source) -> None:
    self.limit_solutions[source] = 0.
    self.distance_solutions[source] = 0.

  def _get_from_car_state(self, sm: messaging.SubMaster) -> None:
    self._reset_limit_sources(SpeedLimitSource.car)
    self.limit_solutions[SpeedLimitSource.car] = sm['carStateSP'].speedLimit
    self.distance_solutions[SpeedLimitSource.car] = 0.

  def _get_from_map_data(self, sm: messaging.SubMaster) -> None:
    self._reset_limit_sources(SpeedLimitSource.map)
    self._process_map_data(sm)

  def _process_map_data(self, sm: messaging.SubMaster) -> None:
    gps_data = sm[self._gps_location_service]
    map_data = sm['liveMapDataSP']

    gps_fix_age = time.time() - gps_data.unixTimestampMillis * 1e-3  # noqa: TID251 - GPS timestamps are Unix
    if not 0 <= gps_fix_age <= LIMIT_MAX_MAP_DATA_AGE:
      return

    speed_limit = map_data.speedLimit if map_data.speedLimitValid else 0.
    next_speed_limit = map_data.speedLimitAhead if map_data.speedLimitAheadValid else 0.

    self._calculate_map_data_limits(sm, speed_limit, next_speed_limit)

  def _calculate_map_data_limits(self, sm: messaging.SubMaster, speed_limit: float, next_speed_limit: float) -> None:
    gps_data = sm[self._gps_location_service]
    map_data = sm['liveMapDataSP']

    distance_since_fix = self.v_ego * (time.time() - gps_data.unixTimestampMillis * 1e-3)  # noqa: TID251
    distance_to_speed_limit_ahead = max(0., map_data.speedLimitAheadDistance - distance_since_fix)

    self.limit_solutions[SpeedLimitSource.map] = speed_limit
    self.distance_solutions[SpeedLimitSource.map] = 0.

    # FIXME-SP: this is not working as expected
    if 0. < next_speed_limit < self.v_ego:
      adapt_time = (next_speed_limit - self.v_ego) / LIMIT_ADAPT_ACC
      adapt_distance = self.v_ego * adapt_time + 0.5 * LIMIT_ADAPT_ACC * adapt_time ** 2

      if distance_to_speed_limit_ahead <= adapt_distance:
        self.limit_solutions[SpeedLimitSource.map] = next_speed_limit
        self.distance_solutions[SpeedLimitSource.map] = distance_to_speed_limit_ahead

  def _get_source_solution_according_to_policy(self) -> custom.LongitudinalPlanSP.SpeedLimit.Source:
    sources_for_policy = self._policy_to_sources_map[self.policy]

    if self.policy != Policy.combined:
      # They are ordered in the order of preference, so we pick the first that's non-zero
      for source in sources_for_policy:
        if self.limit_solutions[source] > 0.:
          return source
      return SpeedLimitSource.none

    sources_with_limits = [(s, limit) for s, limit in [(s, self.limit_solutions[s]) for s in sources_for_policy] if limit > 0.]
    if sources_with_limits:
      return min(sources_with_limits, key=lambda x: x[1])[0]

    return SpeedLimitSource.none

  def _resolve_limit_sources(self, sm: messaging.SubMaster) -> tuple[float, float, custom.LongitudinalPlanSP.SpeedLimit.Source]:
    """Get limit solutions from each data source"""
    self._get_from_car_state(sm)
    self._get_from_map_data(sm)

    source = self._get_source_solution_according_to_policy()
    speed_limit = self.limit_solutions[source] if source else 0.
    distance = self.distance_solutions[source] if source else 0.

    return speed_limit, distance, source

  def update(self, v_ego: float, sm: messaging.SubMaster, camera_compatible: bool = False) -> None:
    self.v_ego = v_ego
    self.update_params()

    previous_mode = self.camera_mode
    if self.frame < 0 or self.frame % 20 == 0:
      self._camera_requested = self.camera_params.get("SpeedCameraControlEnabled") == "1"
      try:
        reaction = int(self.camera_params.get("SpeedCameraReactionDistance"))
      except (TypeError, ValueError):
        reaction = 300
      self.camera_reaction_distance = reaction if 50 <= reaction <= 1000 and reaction % 50 == 0 else 300
    self.camera_mode = bool(camera_compatible and self._camera_requested)
    if previous_mode != self.camera_mode:
      # Never carry a road limit into camera mode, or a camera into road mode.
      self.speed_limit_last = self.speed_limit_final_last = 0.0
      self._camera_final_hold = None
    if self.camera_mode:
      self.camera_target_below_minimum = False
      gps = sm[self._gps_location_service]
      lmd = sm['liveMapDataSP']
      unix_age = time.time() - gps.unixTimestampMillis * 1e-3  # noqa: TID251
      map_age = time.monotonic() - sm.recv_time['liveMapDataSP']
      gps_reliable = bool(sm.valid[self._gps_location_service] and sm.alive[self._gps_location_service] and gps.hasFix and
                          0 <= unix_age <= 2.5 and camera_accuracy_acceptable(gps))
      lmd_transport_reliable = bool(sm.alive['liveMapDataSP'] and 0 <= map_age <= 2.5)
      lmd_reliable = bool(sm.valid['liveMapDataSP'] and lmd_transport_reliable and lmd.locationValid)
      cs = sm['carState']
      now = time.monotonic()
      car_receipt = sm.recv_time.get('carState', 0.)
      map_receipt = sm.recv_time['liveMapDataSP']
      car_ordered = bool(isinstance(car_receipt, (float, int)) and math.isfinite(car_receipt) and car_receipt > 0 and
                         0 <= now - car_receipt <= MAX_SAMPLE_AGE_S and
                         (self._last_odometry_car_receipt is None or car_receipt >= self._last_odometry_car_receipt))
      map_ordered = bool(math.isfinite(map_receipt) and map_receipt > 0 and
                         (self._last_odometry_map_receipt is None or map_receipt >= self._last_odometry_map_receipt))
      if car_ordered:
        self._last_odometry_car_receipt = car_receipt
      if map_ordered and lmd_transport_reliable:
        self._last_odometry_map_receipt = map_receipt
      sample = car_speed_sample(sm, now)
      can_ordered = bool(sample is not None and
                         (self._last_odometry_car_stamp is None or sample[0] >= self._last_odometry_car_stamp))
      can_reliable = car_ordered and can_ordered
      if can_reliable:
        self._last_odometry_car_stamp = sample[0]
      unreliable_reason = ("gps_unreliable" if not gps_reliable else "localizer_unreliable"
                           if lmd_transport_reliable and not lmd.locationValid else "lmd_unreliable")
      source_reason = str(getattr(lmd, 'speedCameraControlReason', ''))
      odometry_context = source_reason in ('odometry_tracking', 'odometry_passed') or (
        self.camera.output.reason in ('odometry_tracking', 'odometry_unavailable') and self.camera.output.event_id and
        self.camera.output.event_id == getattr(lmd, 'speedCameraControlEventId', ''))
      if odometry_context:
        if not lmd_transport_reliable or not map_ordered:
          unreliable_reason = 'lmd_unreliable'
        elif not can_reliable and source_reason != 'odometry_passed':
          unreliable_reason = 'odometry_unavailable'
        elif not sm.valid['liveMapDataSP']:
          unreliable_reason = 'lmd_unreliable'
      self.camera.observe_car_state(cs)
      output = self.camera.update(lmd, enabled=True, reliable=gps_reliable and lmd_reliable,
                                  engaged=sm['carControl'].enabled and sm['carState'].cruiseState.enabled,
                                  reaction_distance=self.camera_reaction_distance,
                                  unreliable_reason=unreliable_reason,
                                  odometry_reliable=can_reliable and map_ordered and lmd_transport_reliable and sm.valid['liveMapDataSP'],
                                  odometry_pass_reliable=map_ordered and lmd_transport_reliable and sm.valid['liveMapDataSP'])
      self.camera_display_raw_limit = output.raw_limit if output.event_id else 0.0
      self.camera_display_offset = (self._get_speed_limit_offset(self.camera_display_raw_limit)
                                    if self.camera_display_raw_limit > 0 else 0.0)
      self.speed_limit = output.raw_limit if output.automatic else 0.0
      self.distance = output.distance if output.automatic else 0.0
      self.source = SpeedLimitSource.map if output.automatic else SpeedLimitSource.none
      self.speed_limit_offset = self._get_speed_limit_offset() if output.automatic else 0.0
      self.update_speed_limit_states()
      if output.automatic:
        final = self.speed_limit_final
        self._camera_final_hold = final if self._camera_final_hold is None else min(self._camera_final_hold, final)
        self.speed_limit_final = self.speed_limit_final_last = self._camera_final_hold
        # Publish the APPLIED offset so raw + displayed offset always equals
        # the effective ceiling, including while a larger setting is held.
        self.speed_limit_offset = self.speed_limit_final - self.speed_limit
        self.camera_display_offset = self.speed_limit_offset
        minimum = get_minimum_set_speed(self.is_metric) * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS)
        self.camera_target_below_minimum = self.speed_limit_final < minimum - 1e-5
      else:
        # Camera cache belongs only to this active event. PASSED/override/idle
        # must not leave a permanent road limit in SLA's last-positive fallback.
        self.speed_limit_last = self.speed_limit_final_last = 0.0
        self._camera_final_hold = None
      self.frame += 1
      return

    self.camera.update(sm['liveMapDataSP'], enabled=False, reliable=False, engaged=False,
                       reaction_distance=self.camera_reaction_distance)
    self.camera_display_raw_limit = self.camera_display_offset = 0.0
    self.camera_target_below_minimum = False

    if self._camera_requested:
      self.camera.output = replace(self.camera.output, reason="camera_control_unavailable")
      # Camera-only authority was requested but is unavailable for these
      # CarParams. Do not silently substitute road limits or cached road data.
      self.speed_limit = self.speed_limit_last = 0.0
      self.speed_limit_final = self.speed_limit_final_last = 0.0
      self.speed_limit_offset = self.distance = 0.0
      self.source = SpeedLimitSource.none
      self._camera_final_hold = None
      for source in ALL_SOURCES:
        self._reset_limit_sources(source)
      self.frame += 1
      return

    self.speed_limit, self.distance, self.source = self._resolve_limit_sources(sm)
    self.speed_limit_offset = self._get_speed_limit_offset()

    self.update_speed_limit_states()

    self.frame += 1
