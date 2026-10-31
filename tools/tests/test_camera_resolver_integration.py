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
  assert c.resolver.camera.output.reason == 'gps_unreliable'


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
    cs.vCruise = float(helper.v_cruise_kph)
    cs.vCruiseCluster = float(helper.v_cruise_cluster_kph)
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


def outside_camera_wire_cycle(c, monkeypatch, driver=38, stock=38, requested=True):
  monkeypatch.setitem(Params.values, 'SpeedCameraControlEnabled', '1' if requested else '0')
  if not requested:
    monkeypatch.setitem(Params.values, 'SpeedLimitMode', 0)
  c.lmd.speedCameraControlValid = False
  c.lmd.speedCameraControlEventId = ''
  c.lmd.speedCameraControlState = 'IDLE'
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  runtime.helper.v_cruise_kph = runtime.helper.v_cruise_cluster_kph = driver
  runtime.cs.vCruise = runtime.cs.vCruiseCluster = driver
  frame(stock)
  assert runtime.helper.v_cruise_kph == driver  # Native changes alone have no ownership.
  return runtime, frame


@pytest.mark.parametrize('button,before,press,held,final', [
  ('resumeCruise', 38, 38, (60, 90, 110), 110),
  ('setCruise', 38, 110, (), 110), ('setCruise', 110, 109, (), 109),
  ('accelCruise', 38, 40, (), 40), ('decelCruise', 40, 40, (30,), 30),
])
def test_outside_camera_physical_native_gesture_owns_actual_feedback(control, monkeypatch, button, before, press, held, final):
  runtime, frame = outside_camera_wire_cycle(control, monkeypatch, stock=before)
  frame(press, [dict(type=button, pressed=True)])
  assert runtime.helper.v_cruise_kph == pytest.approx(press)
  for stock in held:
    frame(stock)
    assert runtime.helper.v_cruise_kph == pytest.approx(stock)
  plan = frame(final, [dict(type=button, pressed=False)])
  assert runtime.helper.v_cruise_kph == pytest.approx(final)
  assert plan.vTarget == pytest.approx(final/3.6)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none


@pytest.mark.parametrize('button', ['resumeCruise', 'setCruise'])
@pytest.mark.parametrize('requested', [False, True])
def test_outside_camera_native_gesture_accepts_delayed_ack_without_camera_dependency(control, monkeypatch, button, requested):
  runtime, frame = outside_camera_wire_cycle(control, monkeypatch, requested=requested)
  frame(38, [dict(type=button, pressed=True)])
  frame(38, [dict(type=button, pressed=False)])
  for _ in range(30):
    frame(38)
  plan = frame(110)
  assert runtime.helper.v_cruise_kph == pytest.approx(110)
  assert plan.vTarget == pytest.approx(110/3.6)


def test_reengaging_set_accepts_later_native_feedback(control, monkeypatch):
  runtime, frame = outside_camera_wire_cycle(control, monkeypatch, requested=False)
  frame(38, enabled=False, acc=False)
  frame(38, [dict(type='setCruise', pressed=True)], enabled=False, acc=False)
  frame(38, [dict(type='setCruise', pressed=False)])
  plan = frame(110)
  assert runtime.helper.v_cruise_kph == pytest.approx(110)
  assert plan.vTarget == pytest.approx(110/3.6)


def test_custom_virtual_increment_stays_distinct_from_native_feedback(control, monkeypatch):
  runtime, frame = outside_camera_wire_cycle(control, monkeypatch, driver=40, stock=40)
  runtime.helper.custom_acc_enabled = True
  runtime.helper.short_increment, runtime.helper.long_increment = 5, 10
  frame(40, [dict(type='accelCruise', pressed=True)])
  plan = frame(41, [dict(type='accelCruise', pressed=False)])
  assert runtime.helper.v_cruise_kph == 45  # Configured +5, not native +1.
  assert plan.vTarget == pytest.approx(45/3.6)
  frame(44)  # Automatic stock changes do not replace the custom target.
  assert runtime.helper.v_cruise_kph == 45
  frame(44, [dict(type='resumeCruise', pressed=True)])
  frame(46, [dict(type='resumeCruise', pressed=False)])
  assert runtime.helper.v_cruise_kph == pytest.approx(46)  # Native RES remains native.


def test_scc_stock_movement_does_not_replace_native_driver_memory(control, monkeypatch):
  c = control
  runtime, frame = outside_camera_wire_cycle(c, monkeypatch, driver=110, stock=110)
  # An explicit SET may establish its direction from either positive or
  # negative native feedback; an automatic goal must close residual follow.
  frame(110, [dict(type='setCruise', pressed=True)])
  frame(100, [dict(type='setCruise', pressed=False)])
  assert runtime.helper.v_cruise_kph == pytest.approx(100)
  c.planner.scc.map.output_v_target = 70/3.6
  frame(100)
  frame(70)
  assert runtime.helper.v_cruise_kph == pytest.approx(100)
  c.planner.scc.map.output_v_target = 255
  plan = frame(90)
  assert runtime.helper.v_cruise_kph == pytest.approx(100)
  assert plan.vTarget == pytest.approx(100/3.6)


def test_native110_camera90_then_pass_restores110_and_pedals_keep_priority(control, monkeypatch):
  c = control
  runtime, frame = outside_camera_wire_cycle(c, monkeypatch)
  frame(38, [dict(type='resumeCruise', pressed=True)])
  frame(110)
  frame(110, [dict(type='resumeCruise', pressed=False)])
  assert runtime.helper.v_cruise_kph == pytest.approx(110)
  monkeypatch.setitem(Params.values, 'SpeedCameraReactionDistance', '500')
  monkeypatch.setitem(Params.values, 'SpeedLimitValueOffset', 0)
  c.resolver.frame = 0
  c.lmd.speedCameraControlValid = True
  c.lmd.speedCameraControlEventId = 'photo-camera90'
  c.lmd.speedCameraControlState = 'ACTIVE'
  c.lmd.speedCameraControlSpeedLimit = 90/3.6
  c.lmd.speedCameraControlDistance = 408.
  for _ in range(60):
    plan = frame(110)
  assert plan.vTarget == pytest.approx(90/3.6)
  assert plan.speedCameraControl.driverSetSpeed == pytest.approx(110/3.6)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.decrease
  frame(90)
  assert runtime.helper.v_cruise_kph == pytest.approx(110)
  frame(90, gas=True)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  frame(90, brake=True, enabled=False, acc=False)
  assert runtime.helper.v_cruise_kph == pytest.approx(110)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  frame(90, [dict(type='resumeCruise', pressed=True)])
  frame(90, [dict(type='resumeCruise', pressed=False)])
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  for _ in range(60):
    plan = frame(90)
  assert runtime.helper.v_cruise_kph == pytest.approx(110)
  assert plan.vTarget == pytest.approx(110/3.6)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.increase


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
    assert c.resolver.camera.output.reason == 'camera_control_unavailable'
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


@pytest.mark.parametrize('acquired', [False, True])
@pytest.mark.parametrize('failure,reason', [
  ('gps_invalid', 'gps_unreliable'), ('gps_stale', 'gps_unreliable'), ('gps_accuracy', 'gps_unreliable'),
  ('lmd_invalid', 'lmd_unreliable'), ('lmd_dead', 'lmd_unreliable'), ('lmd_stale', 'lmd_unreliable'),
  ('lmd_location', 'localizer_unreliable'), ('localizer_envelope', 'localizer_unreliable'), ('contract', 'camera_contract_invalid'),
  ('source', 'road_graph_unavailable'), ('incompatible', 'camera_control_unavailable'),
])
def test_published_camera_failure_reason_preserves_existing_authority(control, monkeypatch, acquired, failure, reason):
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  if acquired:
    assert update(c) == pytest.approx(75/3.6)
  if failure == 'gps_invalid':
    c.sm.valid['gps'] = False
  elif failure == 'gps_stale':
    c.sm['gps'].unixTimestampMillis -= 10000
  elif failure == 'gps_accuracy':
    c.sm['gps'].horizontalAccuracy = 100.
  elif failure == 'lmd_invalid':
    c.sm.valid['liveMapDataSP'] = False
  elif failure == 'lmd_dead':
    c.sm.alive['liveMapDataSP'] = False
  elif failure == 'lmd_stale':
    c.sm.recv_time['liveMapDataSP'] -= 10.
  elif failure == 'lmd_location':
    c.lmd.locationValid = False
  elif failure == 'localizer_envelope':
    c.lmd.locationValid = False
    c.sm.valid['liveMapDataSP'] = False
  elif failure == 'contract':
    c.lmd.speedCameraControlVersion = 9
  elif failure == 'source':
    c.lmd.speedCameraControlValid = False
    c.lmd.speedCameraControlReason = reason
  elif failure == 'incompatible':
    c.planner.camera_compatible = False
  held = acquired and failure != 'incompatible'
  assert update(c) == pytest.approx((75 if held else 100)/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.reason == reason
  assert plan.speedCameraControl.active == held
  assert not plan.speedCameraControl.countdownValid
  assert plan.speedCameraControl.speedCap == pytest.approx(75/3.6 if held else 0.)
  assert plan.speedCameraControl.enabled == (failure != 'incompatible')


@pytest.mark.parametrize('acquired', [False, True])
@pytest.mark.parametrize('horizontal,bearing,fix,alive,age,accepted', [
  (25., 35., True, True, 0., True), (30., 60., True, True, 3., True),
  (30.01, 35., True, True, 0., False), (25., 60.01, True, True, 0., False),
  (25., 35., False, True, 0., False), (25., 35., True, False, 0., False),
  (25., 35., True, True, 3.001, False),
])
def test_shared_gps_accuracy_gate_preserves_fix_freshness_and_hold(
    control, monkeypatch, acquired, horizontal, bearing, fix, alive, age, accepted):
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  if acquired:
    assert update(c) == pytest.approx(75/3.6)
  gps = c.sm['gps']
  gps.horizontalAccuracy, gps.bearingAccuracyDeg, gps.hasFix = horizontal, bearing, fix
  # Exact age boundaries need a fixed Unix observation; real test execution
  # otherwise adds a few milliseconds and turns age3 into age>3.
  resolver_module = sys.modules[type(c.resolver).__module__]
  clock = resolver_module.time
  monkeypatch.setattr(resolver_module,'time',NS(time=lambda: gps.unixTimestampMillis*.001+age,
                                              monotonic=clock.monotonic))
  gps.unixTimestampMillis -= age * 1000
  c.sm.alive['gps'] = alive
  held = acquired or accepted
  assert update(c) == pytest.approx((75 if held else 100)/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.reason == ('camera_limit' if accepted else 'gps_unreliable')
  assert plan.speedCameraControl.active == held
  assert plan.speedCameraControl.countdownValid == accepted
  assert plan.speedCameraControl.speedCap == pytest.approx(75/3.6 if held else 0.)


def odometry_wire_cycle(c, monkeypatch, acquire=True):
  import time
  clock = [500.]
  monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
  runtime = real_wire_controls(c, monkeypatch)
  c.cs.canValid, c.cs.vEgo, c.cs.gearShifter = True, 10., 'drive'
  c.sm.valid['carState'] = c.sm.alive['carState'] = True
  c.sm.logMonoTime = {'carState': int(clock[0] * 1e9)}
  c.sm.recv_time['carState'] = c.sm.recv_time['liveMapDataSP'] = clock[0]
  c.lmd.speedCameraControlDistance = 100.
  if acquire:
    assert update(c) == pytest.approx(75/3.6)
    runtime.publish()
  c.sm['gps'].hasFix = False
  c.lmd.locationValid = False
  def message(distance, passed=False, identity='camera1'):
    c.lmd.speedCameraControlEventId = identity
    c.lmd.speedCameraControlState = 'PASSED' if passed else 'ACTIVE'
    c.lmd.speedCameraControlReason = 'odometry_passed' if passed else 'odometry_tracking'
    c.lmd.speedCameraControlValid = not passed
    c.lmd.speedCameraControlPassProven = passed
    c.lmd.speedCameraControlDistance = distance
  def fresh(t):
    clock[0] = t
    c.sm.recv_time['carState'] = c.sm.recv_time['liveMapDataSP'] = t
    c.sm.logMonoTime['carState'] = int(t * 1e9)
  return runtime, clock, message, fresh


def test_existing_camera_odometry_advances_countdown_and_restores_base(control, monkeypatch):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch)
  for t, distance in ((501., 90.), (502., 50.), (503., 0.)):
    fresh(t)
    message(distance, passed=distance == 0.)
    # Neither raw GPS nor fused position is valid during this fallback.
    assert not c.sm['gps'].hasFix and not c.lmd.locationValid
    assert update(c) == pytest.approx((100 if distance == 0 else 75)/3.6)
    plan = runtime.publish()
    assert plan.speedCameraControl.reason == ('odometry_passed' if distance == 0 else 'odometry_tracking')
    assert plan.speedCameraControl.countdownValid == (distance > 0)
    if distance:
      assert plan.speedCameraControl.distance == pytest.approx(distance)
      assert plan.speedCameraControl.driverSetSpeed == pytest.approx(100/3.6)
    else:
      assert plan.speedCameraControl.state == 'passed'
      assert not plan.speedLimit.resolver.speedLimitLastValid
      assert plan.speedCameraControl.rawLimit == 0


@pytest.mark.parametrize('fault', ['stale_can', 'can_invalid', 'can_dead', 'envelope_invalid', 'reverse',
                                  'negative', 'nan', 'huge', 'reversed_can_stamp', 'reversed_car_receipt',
                                  'reversed_map_receipt', 'stale_map', 'invalid_map'])
def test_unreliable_odometry_freezes_tracking_and_stale_source_cannot_release_camera(control, monkeypatch, fault):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch)
  fresh(500.1)
  message(90.)
  update(c)
  fresh(500.2)
  pass_fault = fault in ('reversed_map_receipt', 'stale_map', 'invalid_map')
  message(0. if pass_fault else 80., passed=pass_fault)
  if fault == 'stale_can':
    c.sm.logMonoTime['carState'] = int(499. * 1e9)
  elif fault == 'can_invalid':
    c.cs.canValid = False
  elif fault == 'can_dead':
    c.sm.alive['carState'] = False
  elif fault == 'envelope_invalid':
    c.sm.valid['carState'] = False
  elif fault == 'reverse':
    c.cs.gearShifter = 'reverse'
  elif fault in ('negative', 'nan', 'huge'):
    c.cs.vEgo = {'negative': -1., 'nan': float('nan'), 'huge': 1000.}[fault]
  elif fault == 'reversed_can_stamp':
    c.sm.logMonoTime['carState'] = int(500.05 * 1e9)
  elif fault == 'reversed_car_receipt':
    c.sm.recv_time['carState'] = 500.05
  elif fault == 'reversed_map_receipt':
    c.sm.recv_time['liveMapDataSP'] = 500.05
  elif fault == 'stale_map':
    c.sm.recv_time['liveMapDataSP'] = 496.
  elif fault == 'invalid_map':
    c.sm.valid['liveMapDataSP'] = False
  assert update(c) == pytest.approx(75/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.state == 'active'
  assert not plan.speedCameraControl.countdownValid
  assert plan.speedLimit.resolver.speedLimitLastValid
  assert plan.speedCameraControl.reason == ('lmd_unreliable' if pass_fault else 'odometry_unavailable')


def test_speed_gap_in_existing_odometry_context_has_explicit_reason(control, monkeypatch):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch)
  fresh(501.)
  message(90.)
  update(c)
  fresh(502.)
  c.cs.canValid = False
  c.sm.valid['liveMapDataSP'] = False
  c.lmd.speedCameraControlReason = 'gps_unreliable'
  c.lmd.speedCameraControlValid = False
  assert update(c) == pytest.approx(75/3.6)
  assert runtime.publish().speedCameraControl.reason == 'odometry_unavailable'


def test_fresh_completed_source_odometry_pass_survives_receiver_can_loss(control, monkeypatch):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch)
  fresh(501.)
  c.cs.canValid = False
  c.cs.brakePressed = True
  message(0., passed=True)
  assert update(c) == pytest.approx(100/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.state == 'passed'
  assert plan.speedCameraControl.reason == 'odometry_passed'
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  for _ in range(60):
    runtime.icbm.run(c.cs, cc, plan, True)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none


def test_gps_preselected_approach_can_enter_zone_by_odometry_without_raw_limit_change(control, monkeypatch):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch, acquire=False)
  c.sm['gps'].hasFix = c.lmd.locationValid = True
  c.lmd.speedCameraControlDistance = 310.
  assert update(c) == pytest.approx(100/3.6)
  assert c.resolver.camera.output.state == 'APPROACHING'
  c.sm['gps'].hasFix = c.lmd.locationValid = False
  fresh(501.)
  message(300.)
  c.lmd.speedCameraControlSpeedLimit = 30/3.6
  assert update(c) == pytest.approx(75/3.6)
  assert runtime.publish().speedCameraControl.rawLimit == pytest.approx(60/3.6)


@pytest.mark.parametrize('acquired', [False, True])
def test_odometry_cannot_acquire_unknown_event_or_replace_existing_one(control, monkeypatch, acquired):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch, acquire=acquired)
  fresh(501.)
  message(10., identity='unknown-camera')
  assert update(c) == pytest.approx((75 if acquired else 100)/3.6)
  assert c.resolver.camera.output.event_id != 'unknown-camera'
  fresh(502.)
  message(0., passed=True, identity='unknown-camera')
  assert update(c) == pytest.approx((75 if acquired else 100)/3.6)
  assert runtime.publish().speedCameraControl.state != 'passed'


@pytest.mark.parametrize('fault', ['positive_pass_distance', 'missing_pass_proof', 'wrong_version', 'tracking_without_valid'])
def test_incoherent_odometry_contract_cannot_release_or_advance_camera(control, monkeypatch, fault):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch)
  fresh(501.)
  message(0., passed=True)
  if fault == 'positive_pass_distance':
    c.lmd.speedCameraControlDistance = 1.
  elif fault == 'missing_pass_proof':
    c.lmd.speedCameraControlPassProven = False
  elif fault == 'wrong_version':
    c.lmd.speedCameraControlVersion = 2
  elif fault == 'tracking_without_valid':
    message(50.)
    c.lmd.speedCameraControlValid = False
  assert update(c) == pytest.approx(75/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.state == 'active'
  assert not plan.speedCameraControl.countdownValid


def test_odometry_never_changes_retained_camera_raw_cap(control, monkeypatch):
  c = control
  runtime, clock, message, fresh = odometry_wire_cycle(c, monkeypatch)
  fresh(501.)
  message(90.)
  c.lmd.speedCameraControlSpeedLimit = 30/3.6
  assert update(c) == pytest.approx(75/3.6)
  assert runtime.publish().speedCameraControl.rawLimit == pytest.approx(60/3.6)


def test_healthy_no_camera_reason_is_not_a_failure(control, monkeypatch):
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  c.lmd.speedCameraControlValid = False
  c.lmd.speedCameraControlEventId = ''
  c.lmd.speedCameraControlState = 'IDLE'
  c.lmd.speedCameraControlReason = 'no_camera'
  assert update(c) == pytest.approx(100/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.reason == 'no_camera'
  assert not plan.speedCameraControl.active


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
