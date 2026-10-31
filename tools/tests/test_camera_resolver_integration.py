"""Actual camera adapter/resolver/SLA/planner with explicit native IO stubs.

Params storage, cereal messaging/enums, SCC and model dependencies are boundary
stubs. The source modules under test are loaded directly from this checkout;
no generated libraries or source from another checkout are used.
"""
import importlib.util
import sys
from enum import IntEnum
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
  spec = importlib.util.spec_from_file_location(name, ROOT / relative)
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


def stub(name, **kwargs):
  module = ModuleType(name)
  module.__dict__.update(kwargs)
  sys.modules[name] = module
  return module


class EnumBase(IntEnum):
  @classmethod
  def min(cls):
    return min(cls)

  @classmethod
  def max(cls):
    return max(cls)


class Params:
  values = {"IsMetric": True, "SpeedLimitPolicy": 1, "SpeedLimitOffsetType": 1,
            "SpeedLimitValueOffset": 15, "SpeedLimitMode": 3,
            "SpeedCameraControlEnabled": "1", "SpeedCameraEnabled": "1", "SpeedCameraReactionDistance": "300"}

  def get(self, key, **_):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key, False))


class Events:
  def add(self, *_):
    pass


@pytest.fixture
def control(monkeypatch):
  # Isolate all stubs, including module imports, from other test files.
  old = dict(sys.modules)
  source = NS(none=0, car=1, map=2, schema=NS(enumerants={"none": 0, "car": 1, "map": 2}))
  state = NS(disabled=0, inactive=1, preActive=2, pending=3, adapting=4, active=5)
  class Buttons(IntEnum):
    accelCruise = 1
    resumeCruise = 2
    decelCruise = 3
    setCruise = 4
    cancel = 5
    mainCruise = 6

    @property
    def raw(self):
      return self.value

    def __str__(self):
      return self.name
  buttons = Buttons
  custom = NS(LongitudinalPlanSP=NS(SpeedLimit=NS(Source=source, AssistState=state),
                                  DynamicExperimentalControl=NS(DynamicExperimentalControlState=NS()),
                                  LongitudinalPlanSource=NS(cruise="cruise", sccVision="sccVision", sccMap="sccMap",
                                                            speedLimitAssist="speedLimitAssist")),
              OnroadEventSP=NS(EventName=NS(speedLimitChanged=1, speedLimitActive=2, speedLimitPreActive=3, speedLimitPending=4)),
              CarParamsSP=object)
  car = NS(CarParams=object, CarState=NS(ButtonEvent=NS(Type=buttons)))
  for name, path in [("openpilot", "."), ("openpilot.sunnypilot", "sunnypilot")]:
    m = stub(name)
    m.__path__ = [str(ROOT / path)]
  sys.modules['openpilot.sunnypilot'].PARAMS_UPDATE_PERIOD = 1.0
  sys.modules['openpilot.sunnypilot'].IntEnumBase = EnumBase
  sys.modules['openpilot.sunnypilot'].get_sanitize_int_param = lambda key, *_: Params().get(key)
  stub('cereal', car=car, custom=custom, messaging=NS(SubMaster=object, PubMaster=object))
  stub('cereal.messaging', SubMaster=object)
  stub('openpilot.common.params', Params=Params)
  stub('openpilot.common.gps', get_gps_location_service=lambda _: 'gps')
  stub('openpilot.common.realtime', DT_MDL=0.05)
  load('openpilot.common.constants', 'common/constants.py')
  stub('openpilot.sunnypilot.mapd.camera_updater', CameraParams=Params)
  stub('openpilot.selfdrive.controls.lib.drive_helpers', CONTROL_N=1)
  stub('openpilot.selfdrive.modeld.constants', ModelConstants=NS(T_IDXS=[0, 1]))
  stub('openpilot.sunnypilot.selfdrive.selfdrived.events', EventsSP=Events)
  stub('openpilot.selfdrive.car.cruise', V_CRUISE_MAX=145)
  stub('opendbc.car', structs=NS(CarParams=object, CarParamsSP=object))
  logs = []
  stub('openpilot.common.swaglog', cloudlog=NS(info=lambda *args: logs.append(args)))
  stub('openpilot.sunnypilot.models.helpers', get_active_bundle=lambda: None)
  stub('openpilot.sunnypilot.selfdrive.controls.lib.dec.dec', DynamicExperimentalController=object)
  stub('openpilot.sunnypilot.selfdrive.controls.lib.e2e_alerts_helper', E2EAlertsHelper=object)
  stub('openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.smart_cruise_control', SmartCruiseControl=object)
  base = 'openpilot.sunnypilot.selfdrive.controls.lib.'
  for module in ['camera_speed_adapter', 'speed_limit.common', 'speed_limit.helpers', 'speed_limit.speed_limit_resolver',
                 'speed_limit.speed_limit_assist', 'longitudinal_planner']:
    load(base + module, 'sunnypilot/selfdrive/controls/lib/' + module.replace('.', '/') + '.py')
  resolver_module = sys.modules[base + 'speed_limit.speed_limit_resolver']
  sla_module = sys.modules[base + 'speed_limit.speed_limit_assist']
  planner_module = sys.modules[base + 'longitudinal_planner']
  resolver = resolver_module.SpeedLimitResolver()
  sla = sla_module.SpeedLimitAssist(NS(openpilotLongitudinalControl=False, pcmCruise=True, brand='volkswagen'), NS(pcmCruiseSpeed=False))
  planner = planner_module.LongitudinalPlannerSP.__new__(planner_module.LongitudinalPlannerSP)
  planner.resolver, planner.sla, planner.camera = resolver, sla, resolver.camera
  planner.camera.maximum_driver_speed_mps = 145/3.6
  planner.camera_compatible, planner.events_sp, planner._diagnostic_time = True, Events(), -float('inf')
  planner.scc = NS(vision=NS(output_v_target=255., output_a_target=0.), map=NS(output_v_target=255., output_a_target=0.),
                   update=lambda *args: None)
  now = resolver_module.time.time()
  lmd = NS(locationValid=True, speedLimit=20/3.6, speedLimitValid=True, speedLimitAhead=0., speedLimitAheadValid=False,
           speedCameraControlVersion=1, speedCameraControlState='ACTIVE',
           speedLimitAheadDistance=0., speedCameraControlEventId='camera1', speedCameraControlDistance=300.,
           speedCameraControlSpeedLimit=60/3.6, speedCameraControlValid=True, speedCameraControlAutoControlAllowed=True,
           speedCameraControlPassProven=False, speedCameraControlReason='matched')
  cs = NS(vCruise=100., vCruiseCluster=100., brakePressed=False, buttonEvents=[], cruiseState=NS(enabled=True, speedCluster=100/3.6))
  class SM(dict):
    valid = {'gps': True, 'liveMapDataSP': True, 'selfdriveStateSP': True}
    alive = dict(valid)
    recv_time = {'liveMapDataSP': resolver_module.time.monotonic()}

    def all_checks(self, **_):
      return True
  sm = SM(carState=cs, carControl=NS(enabled=True, cruiseControl=NS(override=False)), liveMapDataSP=lmd,
          gps=NS(hasFix=True, unixTimestampMillis=now*1000, horizontalAccuracy=1., bearingAccuracyDeg=2.),
          carStateSP=NS(speedLimit=20/3.6),
          selfdriveStateSP=NS(intelligentCruiseButtonManagement=NS(state='holding', sendButton='none', vTarget=100.)))
  yield NS(planner=planner, resolver=resolver, sla=sla, sm=sm, lmd=lmd, cs=cs, logs=logs, states=state, buttons=buttons)
  for name in set(sys.modules) - set(old):
    if name.startswith(('openpilot', 'cereal', 'opendbc')):
      del sys.modules[name]
  sys.modules.update(old)


def update(c):
  return c.planner.update_targets(c.sm, 100/3.6, 0., c.cs.vCruise/3.6)[0]


def test_native_offset_autoaccept_and_four_source_arbitration(control):
  c = control
  assert update(c) == pytest.approx(75/3.6)
  assert c.sla.state == c.states.active  # below ordinary confirmation threshold
  assert c.cs.vCruise == 100
  assert c.resolver.speed_limit_offset == pytest.approx(15/3.6)
  c.planner.scc.vision.output_v_target = 70/3.6
  assert update(c) == pytest.approx(70/3.6)
  assert c.planner.source == 'sccVision'
  c.planner.scc.vision.output_v_target = 255
  c.planner.scc.map.output_v_target = 65/3.6
  assert update(c) == pytest.approx(65/3.6)
  assert c.planner.source == 'sccMap'
  assert len(c.logs) == 1  # high-rate planner calls do not flood logs


def test_threshold_pass_cache_and_identical_next_limit(control):
  c = control
  c.lmd.speedCameraControlDistance = 301
  assert update(c) == pytest.approx(100/3.6)
  c.lmd.speedCameraControlDistance = 300
  assert update(c) == pytest.approx(75/3.6)
  c.lmd.speedCameraControlValid = False
  c.lmd.speedCameraControlPassProven = True
  c.lmd.speedCameraControlState = 'PASSED'
  assert update(c) == pytest.approx(100/3.6)
  assert c.resolver.speed_limit_last == c.resolver.speed_limit_final_last == 0
  c.lmd.speedCameraControlPassProven = False
  c.lmd.speedCameraControlState = 'ACTIVE'
  c.lmd.speedCameraControlValid = True
  assert update(c) == pytest.approx(100/3.6)  # same ID suppressed
  c.lmd.speedCameraControlEventId = 'camera2'
  assert update(c) == pytest.approx(75/3.6)  # same number, new independent event


def test_gps_loss_missing_candidate_does_not_release_or_accept_pass(control):
  c = control
  assert update(c) == pytest.approx(75/3.6)
  c.sm.valid['gps'] = False
  c.lmd.speedCameraControlPassProven = True
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlValid = False
  assert update(c) == pytest.approx(75/3.6)
  assert c.resolver.camera.output.reason == 'unreliable_hold'


def camera_wire_cycle(c, monkeypatch):
  runtime = real_wire_controls(c, monkeypatch)
  cs, helper = runtime.cs, runtime.helper
  update(c)
  runtime.publish()
  helper.update_v_cruise(cs, True, True)
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  def frame(speed=75, events=(), *, gas=False, brake=False, enabled=True, acc=True):
    cs.cruiseState.speed = cs.cruiseState.speedCluster = speed/3.6
    cs.cruiseState.enabled = acc
    cs.gasPressed, cs.brakePressed = gas, brake
    cs.buttonEvents = list(events)
    cc.enabled = c.sm['carControl'].enabled = enabled
    c.planner.camera.observe_car_state(cs, automatic_target=c.planner.output_v_target)
    helper.update_v_cruise(cs, enabled, True)
    cs.vCruise = cs.vCruiseCluster = helper.v_cruise_kph
    update(c)
    plan = runtime.publish()
    runtime.icbm.run(cs, cc, plan, True)
    assert not plan.speedCameraControl.manualTakeover
    assert plan.speedCameraControl.state not in ('manual', 'driverOverride')
    return plan
  return runtime, frame


def test_camera_buttons_do_not_cancel_camera_or_other_scc_authority(control):
  c = control
  assert update(c) == pytest.approx(75/3.6)
  c.cs.buttonEvents = [NS(type='accelCruise', pressed=True)]
  c.planner.camera.observe_car_state(c.cs)
  c.planner.scc.map.output_v_target = 70/3.6
  assert update(c) == pytest.approx(70/3.6)
  assert c.resolver.camera.output.automatic and not c.resolver.camera.output.manual
  c.cs.buttonEvents = [NS(type='accelCruise', pressed=False)]
  c.planner.camera.observe_car_state(c.cs)
  c.planner.scc.map.output_v_target = 255
  assert update(c) == pytest.approx(75/3.6)
  c.lmd.speedCameraControlPassProven = True
  c.lmd.speedCameraControlState = 'PASSED'
  assert update(c) == pytest.approx(100/3.6)
  c.lmd.speedCameraControlPassProven = False
  c.lmd.speedCameraControlState = 'ACTIVE'
  c.lmd.speedCameraControlEventId = 'next'
  assert update(c) == pytest.approx(75/3.6)


@pytest.mark.parametrize('button,opposite,resolved', [('accelCruise', 70, 85), ('decelCruise', 80, 65)])
def test_delayed_and_opposite_stock_feedback_never_changes_pre_camera_base(control, monkeypatch, button, opposite, resolved):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  frame(75, [dict(type=button, pressed=True)])
  frame(75, [dict(type=button, pressed=False)])
  for i in range(100):
    plan = frame(opposite if i >= 30 else 75)
    assert runtime.helper.v_cruise_kph == 100
    assert plan.speedCameraControl.state == 'active'
    assert plan.speedCameraControl.speedCap == pytest.approx(75/3.6)
  plan = frame(resolved)
  assert runtime.helper.v_cruise_kph == 100
  assert plan.vTarget == pytest.approx(75/3.6)
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  plan = frame(resolved)
  assert plan.speedCameraControl.state == 'passed'
  assert plan.speedCameraControl.rawLimit == 0 and not plan.speedCameraControl.countdownValid
  assert runtime.helper.v_cruise_kph == 100 and plan.vTarget == pytest.approx(100/3.6)


def test_no_change_button_and_cancel_create_no_persistent_camera_override(control, monkeypatch):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  runtime.helper.v_cruise_kph = runtime.helper.v_cruise_cluster_kph = 145
  runtime.cs.vCruise = runtime.cs.vCruiseCluster = 145
  frame(145, [dict(type='accelCruise', pressed=True)])
  frame(145, [dict(type='accelCruise', pressed=False)])
  for _ in range(50):
    plan = frame(145)
  assert plan.speedCameraControl.state == 'active' and runtime.helper.v_cruise_kph == 145
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.decrease
  plan = frame(145, [dict(type='cancel', pressed=True)], enabled=False, acc=False)
  assert not runtime.icbm.is_ready and runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  assert runtime.helper.v_cruise_kph == 145
  plan = frame(145, [dict(type='cancel', pressed=False)], enabled=False, acc=False)
  assert plan.speedCameraControl.state == 'active'
  for _ in range(50):
    plan = frame(145)
  assert plan.vTarget == pytest.approx(75/3.6)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.decrease


@pytest.mark.parametrize('button,resolved,opposite', [('resumeCruise', 76, 74), ('setCruise', 74, 76)])
def test_active_fine_buttons_pause_transmission_but_keep_camera_and_original_base(control, monkeypatch, button, resolved, opposite):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  frame(75, [dict(type=button, pressed=True)])
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  for _ in range(5):
    plan = frame(opposite)
    assert runtime.helper.v_cruise_kph == 100
    assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  frame(resolved, [dict(type=button, pressed=False)])
  for _ in range(50):
    plan = frame(resolved)
  assert plan.speedCameraControl.state == 'active' and plan.vTarget == pytest.approx(75/3.6)
  assert runtime.helper.v_cruise_kph == 100
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  plan = frame(resolved)
  assert plan.speedCameraControl.state == 'passed' and plan.vTarget == pytest.approx(100/3.6)
  assert runtime.helper.v_cruise_kph == 100


def test_brake_resume_session_preserves_original_driver_target(control, monkeypatch):
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  cs, helper = runtime.cs, runtime.helper
  update(c)
  runtime.publish()
  helper.update_v_cruise(cs, True, True)
  cs.brakePressed = True
  cs.cruiseState.enabled = False
  c.planner.camera.observe_car_state(cs)
  helper.update_v_cruise(cs, False, True)
  update(c)
  runtime.publish()
  cs.brakePressed = False
  # A RES edge that reengages ACC is a session action, not fine-speed '+'.
  cs.cruiseState.enabled = True
  cs.buttonEvents = [dict(type='resumeCruise', pressed=True)]
  c.planner.camera.observe_car_state(cs)
  helper.update_v_cruise(cs, True, True)
  update(c)
  assert runtime.publish().speedCameraControl.state == 'active'
  cs.buttonEvents = [dict(type='resumeCruise', pressed=False)]
  c.planner.camera.observe_car_state(cs)
  helper.update_v_cruise(cs, True, True)
  cs.buttonEvents = []
  assert helper.v_cruise_kph == 100
  assert update(c) == pytest.approx(75/3.6)
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  assert update(c) == pytest.approx(100/3.6)


@pytest.mark.parametrize('button,held,final', [('accelCruise', (85, 95), 105), ('decelCruise', (65, 55), 45)])
@pytest.mark.parametrize('delayed_final', [False, True])
def test_held_camera_gesture_and_delayed_release_never_rewrite_restored_base(control, monkeypatch, button, held, final, delayed_final):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  frame(75, [dict(type=button, pressed=True)])
  for speed in held:
    plan = frame(speed)
    assert runtime.helper.v_cruise_kph == 100
    assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  plan = frame(held[-1])
  assert plan.speedCameraControl.state == 'passed'
  assert plan.speedCameraControl.rawLimit == 0 and not plan.speedCameraControl.countdownValid
  assert runtime.helper.v_cruise_kph == 100 and plan.vTarget == pytest.approx(100/3.6)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  release_speed = held[-1] if delayed_final else final
  frame(release_speed, [dict(type=button, pressed=False)])
  for _ in range(50):
    plan = frame(release_speed)
    assert runtime.helper.v_cruise_kph == 100
  plan = frame(final)
  assert runtime.helper.v_cruise_kph == 100 and plan.vTarget == pytest.approx(100/3.6)
  # A delayed native increment is feedback, not a new physical outside-zone action.
  plan = frame(held[-1])
  assert runtime.helper.v_cruise_kph == 100


@pytest.mark.parametrize('button,held,auto_goal', [('accelCruise', (85, 95), 70), ('decelCruise', (65, 55), 45)])
def test_scc_feedback_after_camera_button_release_cannot_rewrite_pre_camera_base(control, monkeypatch, button, held, auto_goal):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  frame(75, [dict(type=button, pressed=True)])
  for speed in held:
    frame(speed)
  frame(held[-1], [dict(type=button, pressed=False)])
  c.planner.scc.map.output_v_target = auto_goal/3.6
  for _ in range(50):
    plan = frame(auto_goal)
    assert runtime.helper.v_cruise_kph == 100
  assert plan.vTarget == pytest.approx(auto_goal/3.6)
  assert plan.speedCameraControl.state == 'active'
  c.planner.scc.map.output_v_target = 255
  assert frame(auto_goal).vTarget == pytest.approx(75/3.6)
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  assert frame(auto_goal).vTarget == pytest.approx(100/3.6)
  assert runtime.helper.v_cruise_kph == 100


def test_unrequested_camera_preserves_ordinary_sla_confirmation(control, monkeypatch):
  c = control
  monkeypatch.setitem(Params.values, 'SpeedCameraControlEnabled', '0')
  c.planner.camera_compatible = False
  assert update(c) == pytest.approx(100/3.6)
  assert c.resolver.speed_limit == pytest.approx(20/3.6)  # ordinary OSM restored, not cameras
  assert c.sla.state != c.states.active


def test_incompatible_requested_camera_cannot_fall_back_to_road_or_cached_limit(control):
  c = control
  c.planner.camera_compatible = False
  # Simulate positive road state already retained before compatibility failed.
  c.resolver.speed_limit = c.resolver.speed_limit_last = 60/3.6
  c.resolver.speed_limit_final = c.resolver.speed_limit_final_last = 75/3.6
  c.resolver.speed_limit_offset = 15/3.6
  c.resolver.distance = 120.
  c.resolver._camera_final_hold = 75/3.6
  for source in c.resolver.limit_solutions:
    c.resolver.limit_solutions[source] = 60/3.6
    c.resolver.distance_solutions[source] = 120.
  assert Params.values['SpeedLimitMode'] == 3
  assert c.lmd.speedLimitValid and c.sm['carStateSP'].speedLimit > 0
  for _ in range(3):
    assert update(c) == pytest.approx(100/3.6)
    assert c.planner.source == 'cruise'
    assert c.sla.output_v_target == 255.
    assert not c.resolver.camera_mode
    assert not c.resolver.camera.output.automatic
    assert c.resolver.camera.output.reason == 'disabled'
    assert not c.resolver.speed_limit_valid and not c.resolver.speed_limit_last_valid
    assert c.resolver.speed_limit_final == c.resolver.speed_limit_final_last == 0.
    assert c.resolver.speed_limit_offset == c.resolver.distance == 0.
    assert c.resolver.source == 0
    assert all(limit == 0. for limit in c.resolver.limit_solutions.values())
    assert all(distance == 0. for distance in c.resolver.distance_solutions.values())
    assert c.resolver._camera_final_hold is None
  c.planner.camera_compatible = True
  assert update(c) == pytest.approx(75/3.6)


def test_camera_authority_independent_ordinary_sla_mode_and_warning_toggle(control, monkeypatch):
  c = control
  monkeypatch.setitem(Params.values, 'SpeedLimitMode', 0)
  monkeypatch.setitem(Params.values, 'SpeedCameraEnabled', '0')
  c.sla.frame = 0
  assert update(c) == pytest.approx(75/3.6)
  assert c.resolver.camera_mode and c.sla.is_active
  assert Params.values['SpeedLimitMode'] == 0  # No global mode mutation.
  c.cs.cruiseState.enabled = False
  assert update(c) == pytest.approx(100/3.6)
  assert not c.sla.is_active
  c.cs.cruiseState.enabled = True
  assert update(c) == pytest.approx(75/3.6)
  monkeypatch.setitem(Params.values, 'SpeedCameraControlEnabled', '0')
  c.resolver.frame = c.sla.frame = 0
  assert update(c) == pytest.approx(100/3.6)
  assert not c.resolver.camera_mode and not c.sla.is_active
  assert Params.values['SpeedLimitMode'] == 0


def test_real_cruise_driver_memory_ignores_camera_button_feedback_and_preserves_pass_base(control):
  c = control
  load('openpilot.sunnypilot.selfdrive.car.cruise_ext', 'sunnypilot/selfdrive/car/cruise_ext.py')
  cruise_mod = load('openpilot.selfdrive.car.cruise', 'selfdrive/car/cruise.py')
  helper = cruise_mod.VCruiseHelper(NS(pcmCruise=True, openpilotLongitudinalControl=False),
                                  NS(pcmCruiseSpeed=False, intelligentCruiseButtonManagementAvailable=True))
  helper.v_cruise_kph = helper.v_cruise_cluster_kph = 100.
  helper._stock_virtual_cruise_initialized = helper.enabled_prev = True
  helper.camera_mode = helper.camera_episode_active = True
  helper.sla_state = c.states.active
  helper.speed_limit_final_last_kph = 75.
  c.cs.cruiseState.available = True
  c.cs.cruiseState.standstill = False
  c.cs.cruiseState.speed = c.cs.cruiseState.speedCluster = 75/3.6
  helper.update_v_cruise(c.cs, True, True)
  assert helper.v_cruise_kph == helper.driver_set_speed_kph == 100
  c.cs.buttonEvents = [NS(type=c.buttons.accelCruise, pressed=True)]
  helper.update_v_cruise(c.cs, True, True)
  c.cs.buttonEvents = [NS(type=c.buttons.accelCruise, pressed=False)]
  c.cs.cruiseState.speed = c.cs.cruiseState.speedCluster = 85/3.6
  helper.update_v_cruise(c.cs, True, True)
  helper.camera_episode_active = False
  c.cs.buttonEvents = []
  for _ in range(25):
    helper.update_v_cruise(c.cs, True, True)
  assert helper.v_cruise_kph == helper.driver_set_speed_kph == 100
  helper.camera_episode_active = True
  c.cs.cruiseState.speed = c.cs.cruiseState.speedCluster = 70/3.6
  for _ in range(30):
    helper.update_v_cruise(c.cs, True, True)
  assert helper.v_cruise_kph == helper.driver_set_speed_kph == 100


def test_version_state_pass_proof_and_applied_offset_hold(control, monkeypatch):
  c = control
  c.lmd.speedCameraControlVersion = 2
  assert update(c) == pytest.approx(100/3.6)
  c.lmd.speedCameraControlVersion = 1
  assert update(c) == pytest.approx(75/3.6)
  c.lmd.speedCameraControlPassProven = True  # ACTIVE is not a passage proof
  assert update(c) == pytest.approx(75/3.6)
  c.sm.valid['gps'] = False
  monkeypatch.setitem(Params.values, 'SpeedLimitValueOffset', 30)
  c.resolver.frame = 0
  assert update(c) == pytest.approx(75/3.6)
  assert c.resolver.speed_limit_offset == pytest.approx(15/3.6)
  assert c.resolver.speed_limit + c.resolver.speed_limit_offset == pytest.approx(c.resolver.speed_limit_final)
  monkeypatch.setitem(Params.values, 'SpeedLimitValueOffset', 5)
  c.resolver.frame = 0
  assert update(c) == pytest.approx(65/3.6)
  assert c.resolver.speed_limit_offset == pytest.approx(5/3.6)


def real_wire_controls(control, monkeypatch):
  import capnp
  real_custom = capnp.load(str(ROOT / 'cereal/custom.capnp'))
  car_parser = capnp.SchemaParser()
  real_car = car_parser.load(str(ROOT / 'opendbc_repo/opendbc/car/car.capnp'))
  c = control
  sys.modules['cereal'].car = real_car
  sys.modules['cereal'].custom = real_custom
  sys.modules['openpilot.common.realtime'].DT_CTRL = .01
  sys.modules['opendbc.car'].apply_hysteresis = lambda value, *_: value  # HYST_GAP is exactly zero
  load('openpilot.sunnypilot.selfdrive.car.cruise_ext', 'sunnypilot/selfdrive/car/cruise_ext.py')
  cruise = load('openpilot.selfdrive.car.cruise', 'selfdrive/car/cruise.py')
  icbm_mod = load('openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller',
                  'sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py')
  CP = NS(pcmCruise=True, openpilotLongitudinalControl=False)
  CP_SP = NS(pcmCruiseSpeed=False, intelligentCruiseButtonManagementAvailable=True)
  helper = cruise.VCruiseHelper(CP, CP_SP)
  helper.v_cruise_kph = helper.v_cruise_cluster_kph = 100.
  helper._stock_virtual_cruise_initialized = helper.enabled_prev = True
  icbm = icbm_mod.IntelligentCruiseButtonManagement(CP, CP_SP)
  cs = real_car.CarState.new_message(vCruise=100, vCruiseCluster=100,
                                    cruiseState=dict(enabled=True, available=True, speed=75/3.6, speedCluster=75/3.6))
  c.sm['carState'] = cs
  # Real new schema message crosses the source/controls boundary.
  c.sm['liveMapDataSP'] = real_custom.LiveMapDataSP.new_message(**vars(c.lmd))
  c.cs, c.lmd = cs, c.sm['liveMapDataSP']
  c.planner.events_sp.to_msg = lambda: []
  c.planner.dec = NS(mode=lambda: 'acc', enabled=lambda: False, active=lambda: False)
  c.planner.e2e_alerts_helper = NS(green_light_alert=False, lead_depart_alert=False)
  for branch in (c.planner.scc.vision, c.planner.scc.map):
    branch.state, branch.is_enabled, branch.is_active = 'disabled', False, False
    branch.current_lat_acc = branch.max_pred_lat_acc = 0.
  module = sys.modules['openpilot.sunnypilot.selfdrive.controls.lib.longitudinal_planner']
  monkeypatch.setattr(module, 'DecState', real_custom.LongitudinalPlanSP.DynamicExperimentalControl.DynamicExperimentalControlState)
  messages = []
  monkeypatch.setattr(module.messaging, 'new_message', lambda _: NS(valid=True, longitudinalPlanSP=real_custom.LongitudinalPlanSP.new_message()), raising=False)
  def publish():
    c.planner.publish_longitudinal_plan_sp(c.sm, NS(send=lambda _, msg: messages.append(msg)))
    wire = messages[-1].longitudinalPlanSP.to_bytes()
    with real_custom.LongitudinalPlanSP.from_bytes(wire) as plan:
      helper.update_speed_limit_assist(True, plan)
      return real_custom.LongitudinalPlanSP.new_message(**plan.to_dict())
  return NS(publish=publish, helper=helper, icbm=icbm, icbm_mod=icbm_mod, cs=cs,
            custom=real_custom, car=real_car)


def test_real_wire_quick_camera_tap_resumes_automatic_authority_and_preserves_base(control, monkeypatch):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  frame(75, [dict(type='accelCruise', pressed=True)])
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  frame(85, [dict(type='accelCruise', pressed=False)])
  for _ in range(50):
    plan = frame(85)
  assert plan.speedCameraControl.state == 'active'
  assert runtime.helper.camera_episode_active and not runtime.helper.camera_manual_takeover
  assert runtime.helper.v_cruise_kph == 100
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.decrease
  c.planner.scc.map.output_v_target = 70/3.6
  assert frame(85).vTarget == pytest.approx(70/3.6)
  c.planner.scc.map.output_v_target = 255
  assert frame(85).vTarget == pytest.approx(75/3.6)
