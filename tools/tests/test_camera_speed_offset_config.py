"""Portable camera-offset wiring tests; no native extensions or root conftest.

Execute the actual LongitudinalPlannerSP class with dependency-boundary doubles,
and real file-backed CameraParams. This is not a native messaging/runtime test.
"""

import ast
import math
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
KEY = "SpeedCameraSpeedOffsetKph"
if "openpilot" not in sys.modules:
  namespace = ModuleType("openpilot")
  namespace.__path__ = [str(ROOT)]
  sys.modules["openpilot"] = namespace

from openpilot.sunnypilot.mapd.camera_updater import CameraParams
from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_planner import SpeedCameraPlanner


class Snapshot(dict):
  """SubMaster's consumed interface, without sockets or cereal."""


class CallRecorder:
  def __init__(self, return_value=None):
    self.return_value = return_value
    self.calls = []
    self.call_args = None

  def __call__(self, *args, **kwargs):
    self.calls.append((args, kwargs))
    self.call_args = SimpleNamespace(args=args, kwargs=kwargs)
    return self.return_value


@pytest.fixture
def wired_planner(tmp_path, monkeypatch):
  monkeypatch.setenv("PARAMS_ROOT", str(tmp_path))
  monkeypatch.setenv("OPENPILOT_PREFIX", "offset-test")
  camera_params = CameraParams()
  camera_params.put("SpeedCameraEnabled", "1")
  camera_params.put("SpeedCameraControlEnabled", "1")
  resolver = SimpleNamespace(
    params=SimpleNamespace(get=CallRecorder(return_value=3), get_bool=CallRecorder(return_value=True)),
    update=CallRecorder(), speed_limit_valid=True, speed_limit_last_valid=False,
    speed_limit=80 / 3.6, speed_limit_final_last=70 / 3.6, distance=250,
    _gps_location_service="gpsLocation")
  scc = SimpleNamespace(
    update=CallRecorder(), vision=SimpleNamespace(output_v_target=100.0, output_a_target=0.1),
    map=SimpleNamespace(output_v_target=100.0, output_a_target=0.2))
  sla = SimpleNamespace(update=CallRecorder(), output_v_target=100.0, output_a_target=0.3)
  camera = SimpleNamespace(update=CallRecorder(return_value=SimpleNamespace(speed_cap_mps=None, manual_takeover=False)))
  source = SimpleNamespace(cruise="cruise", sccVision="vision", sccMap="map", speedLimitAssist="sla")
  dependencies = {
    "math": math,
    "time": SimpleNamespace(monotonic=lambda: 1000.1, time=lambda: 1_800_000_000.0),
    "CV": SimpleNamespace(KPH_TO_MS=1 / 3.6, MPH_TO_MS=0.44704), "V_CRUISE_MAX": 145,
    "EventsSP": SimpleNamespace, "SpeedLimitResolver": lambda: resolver,
    "DynamicExperimentalController": lambda *_: SimpleNamespace(), "SmartCruiseControl": lambda: scc,
    "SpeedLimitAssist": lambda *_: sla, "get_active_bundle": lambda: None,
    "LongitudinalPlanSource": source, "E2EAlertsHelper": SimpleNamespace,
    "SpeedCameraPlanner": lambda: camera, "CameraParams": CameraParams,
    "get_minimum_set_speed": lambda is_metric: 30 if is_metric else 20,
    "LIMIT_ADAPT_ACC": -1.0, "Mode": SimpleNamespace(assist=3),
  }
  path = ROOT / "sunnypilot/selfdrive/controls/lib/longitudinal_planner.py"
  parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  planner_class = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == "LongitudinalPlannerSP")
  future_annotations = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
  module = ast.fix_missing_locations(ast.Module(body=[future_annotations, planner_class], type_ignores=[]))
  exec(compile(module, str(path), "exec", dont_inherit=True), dependencies)
  planner = dependencies["LongitudinalPlannerSP"](
    SimpleNamespace(openpilotLongitudinalControl=False),
    SimpleNamespace(intelligentCruiseButtonManagementAvailable=True, pcmCruiseSpeed=False), None)
  sm = Snapshot({
    "carState": SimpleNamespace(vCruiseCluster=110.0, cruiseState=SimpleNamespace(speedCluster=20.0)),
    "carControl": SimpleNamespace(enabled=True, cruiseControl=SimpleNamespace(override=False)),
    "gpsLocation": SimpleNamespace(), "liveMapDataSP": SimpleNamespace(),
  })
  sm.valid = dict.fromkeys(sm, True)
  sm.alive = dict.fromkeys(sm, True)
  sm.recv_time = {"liveMapDataSP": 1000.0}
  return SimpleNamespace(planner=planner, sm=sm, params=camera_params, camera=camera,
                         resolver=resolver, scc=scc, sla=sla)


def update(wired):
  return wired.planner.update_targets(wired.sm, v_ego=25.0, a_ego=0.4, v_cruise=110 / 3.6)


def test_native_registry_persistent_backup_string_zero():
  registry = (ROOT / "common/params_keys.h").read_text(encoding="utf-8")
  entries = re.findall(r'\{"SpeedCameraSpeedOffsetKph",\s*\{([^}]+)\}\}', registry)
  assert len(entries) == 1
  flags, kind, default = [field.strip() for field in entries[0].split(",")]
  assert {flag.strip() for flag in flags.split("|")} == {"PERSISTENT", "BACKUP"}
  assert (kind, default) == ("STRING", '"0"')


def test_camera_params_whitelist_roundtrip(tmp_path):
  params = CameraParams(str(tmp_path))
  params.put(KEY, "17")
  assert (tmp_path / KEY).read_text(encoding="utf-8") == "17"
  assert CameraParams(str(tmp_path)).get(KEY) == "17"
  with pytest.raises(KeyError):
    params.put("SpeedLimitValueOffset", "17")
  params.remove(KEY)
  assert params.get(KEY) is None


@pytest.mark.parametrize("offset", range(31))
def test_each_ui_step_reaches_camera_in_kph(wired_planner, offset):
  wired = wired_planner
  wired.params.put(KEY, str(offset))
  update(wired)
  kwargs = wired.camera.update.call_args.kwargs
  assert kwargs["camera_speed_offset_kph"] == offset
  assert kwargs["user_cruise_mps"] == pytest.approx(110 / 3.6)
  assert kwargs["minimum_speed_mps"] == pytest.approx(30 / 3.6)
  assert kwargs["enabled"] is True


@pytest.mark.parametrize("raw", [None, "", "bad", "nan", "NaN", "inf", "-inf", "1e309", "-1", "30.01", "1.5", "true", "\ufffd"])
def test_invalid_or_missing_value_falls_back_to_zero(wired_planner, raw):
  if raw is not None:
    wired_planner.params.put(KEY, raw)
  update(wired_planner)
  assert wired_planner.camera.update.call_args.kwargs["camera_speed_offset_kph"] == 0.0


def test_live_refresh_and_corruption_do_not_retain_old_offset(wired_planner):
  wired = wired_planner
  assert wired.planner.camera_speed_offset_kph == 0.0
  update(wired)
  wired.params.put(KEY, "18")
  for _ in range(19):
    update(wired)
    assert wired.camera.update.call_args.kwargs["camera_speed_offset_kph"] == 0.0
  update(wired)
  assert wired.camera.update.call_args.kwargs["camera_speed_offset_kph"] == 18.0
  wired.params.put(KEY, "nan")
  for _ in range(20):
    update(wired)
  assert wired.camera.update.call_args.kwargs["camera_speed_offset_kph"] == 0.0


def test_offset_remains_kph_in_imperial_mode(wired_planner):
  wired = wired_planner
  wired.resolver.params.get_bool.return_value = False
  wired.params.put(KEY, "30")
  update(wired)
  kwargs = wired.camera.update.call_args.kwargs
  assert kwargs["camera_speed_offset_kph"] == 30.0
  assert kwargs["minimum_speed_mps"] == pytest.approx(20 * 0.44704)


@pytest.mark.parametrize("limiter,cap,expected_speed,expected_accel", [
  ("cruise", 40.0, 110 / 3.6, 0.4),
  ("vision", 30.0, 15.0, 0.1),
  ("map", 30.0, 15.0, 0.2),
  ("sla", 30.0, 15.0, 0.3),
  ("cruise", 20.0, 20.0, 0.4),
  ("sla", None, 15.0, 0.3),
])
def test_offset_does_not_raise_existing_minimum_or_modify_road_inputs(wired_planner, limiter, cap, expected_speed, expected_accel):
  wired = wired_planner
  if limiter != "cruise":
    target = wired.sla if limiter == "sla" else getattr(wired.scc, limiter)
    target.output_v_target = 15.0
  wired.params.put(KEY, "30")
  wired.camera.update.return_value.speed_cap_mps = cap
  speed, accel = update(wired)
  assert (speed, accel) == pytest.approx((expected_speed, expected_accel))
  assert wired.scc.update.calls == [((wired.sm, True, False, 25.0, 0.4, 110 / 3.6), {})]
  assert wired.sla.update.calls == [((True, False, 25.0, 0.4, pytest.approx(110 / 3.6),
                                     80 / 3.6, 70 / 3.6, True, 250, wired.planner.events_sp), {})]
  assert wired.sm["carState"].vCruiseCluster == 110.0
  assert wired.camera.update.call_args.kwargs["user_cruise_mps"] == pytest.approx(110 / 3.6)


@pytest.mark.parametrize("gate", ["control", "warning", "mode", "compatibility"])
def test_offset_cannot_enable_camera_control(wired_planner, gate):
  wired = wired_planner
  wired.params.put(KEY, "30")
  if gate == "control":
    wired.params.put("SpeedCameraControlEnabled", "0")
  elif gate == "warning":
    wired.params.put("SpeedCameraEnabled", "0")
  elif gate == "mode":
    wired.resolver.params.get.return_value = 2
  else:
    wired.planner.camera_compatible = False
  update(wired)
  assert wired.camera.update.call_args.kwargs["enabled"] is False


def test_manual_handover_still_uses_stock_speed(wired_planner):
  wired = wired_planner
  wired.params.put(KEY, "30")
  wired.camera.update.return_value = SimpleNamespace(speed_cap_mps=35.0, manual_takeover=True)
  speed, _ = update(wired)
  assert speed == 20.0


@pytest.mark.parametrize("raw,expected_kph", [("0", 60), ("10", 70), ("30", 90), ("nan", 60), ("1.5", 60)])
def test_file_param_through_longitudinal_into_real_camera_adapter(wired_planner, raw, expected_kph):
  wired = wired_planner
  wired.params.put(KEY, raw)
  wired.planner.camera = SpeedCameraPlanner()
  cs = wired.sm["carState"]
  cs.cruiseState.enabled = True
  cs.cruiseState.speed = 110 / 3.6
  cs.gasPressed = cs.brakePressed = False
  cs.buttonEvents = []
  cc = wired.sm["carControl"]
  cc.cruiseControl.cancel = cc.cruiseControl.resume = False
  wired.sm["gpsLocation"] = SimpleNamespace(
    hasFix=True, horizontalAccuracy=3.0, bearingAccuracyDeg=2.0, unixTimestampMillis=1_800_000_000_000,
    latitude=55.0 - math.degrees(100 / 6_371_000.0), longitude=37.0, bearingDeg=0.0)
  wired.sm["liveMapDataSP"] = SimpleNamespace(
    locationValid=True, speedCameraControlVersion=1, speedCameraControlValid=True,
    speedCameraControlAutoControlAllowed=True, speedCameraControlType="speedcamonline:1",
    speedCameraControlSpeedLimit=60 / 3.6, speedCameraControlLatitude=55.0, speedCameraControlLongitude=37.0)
  speed, _ = update(wired)
  # 100 m at the production -1 m/s^2 adaptation acceleration.
  assert speed == pytest.approx(math.sqrt((expected_kph / 3.6) ** 2 + 200), abs=1e-6)
  assert wired.planner.camera.output.reason == "kinematic_ceiling"
