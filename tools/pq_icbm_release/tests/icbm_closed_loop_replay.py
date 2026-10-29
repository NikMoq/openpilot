"""Production VW update/coordinator + actual ARM Panda; explicitly synthetic ACC.

No vehicle connection, private inputs or firmware changes. The modeled radar
feedback is an assumption calibrated to existing metric button observations.
"""
import argparse
import ast
import collections
import hashlib
import importlib
import importlib.util
import json
import pathlib
import struct
import sys
from types import ModuleType, SimpleNamespace as NS

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = OUT = CONTRACT = None
BRIDGE = HERE / 'icbm_arm_replay.py'
ReplayImage = PACKET = POPPED = None

BUTTON = NS(none=0, increase=1, decrease=2)
IDS = {'UP': 1, 'DOWN': 2, 'SET': 3, 'RES': 4}
BITS = {1: 11, 2: 10, 3: 16, 4: 17}


def sha(path):
  return hashlib.sha256(path.read_bytes()).hexdigest()


def prototype_factory(source, manifest_path):
  """Load the complete hash-pinned raw prototype before original Reset_Handler."""
  path = source / 'tools/exact_stable_icbm/raw_image.py'
  spec = importlib.util.spec_from_file_location('_icbm_exact_closed_loop', path)
  raw = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(raw)

  class WarmPrototype(raw.PrototypeImage):
    def __init__(self, image_path):
      super().__init__(manifest_path, image_path)
      # Original RX health and extension counter ownership require three
      # actual native observations. No policy/controls RAM is overwritten.
      self.write32(raw.TIM2_CNT, 1000)
      self.rx(0x288, 0, bytes.fromhex('0000400000000000'))
      self.rx(0x1A0, 0, bytes.fromhex('0000020000000000'))
      for counter in range(3):
        self.write32(raw.TIM2_CNT, 1000 + counter * 20000)
        queues, _ = self.rx(0x38A, 0, raw.native(counter))
        for bus, packets in queues.items():
          for packet in packets:
            self.ack_return(packet, int(bus))
      self.native_counter_start = 3

    def clock(self, ns, start):
      # GRA3 follows GRA2 by20ms; MCU time never runs backwards after warm-up.
      return super().clock(ns + 60000000, start)

  return WarmPrototype, raw.PACKET


def load_controller():
  package = ModuleType('_icbm_closed_loop_pq')
  package.__path__ = [str(SOURCE / 'opendbc_repo/opendbc/sunnypilot/car/volkswagen')]
  sys.modules[package.__name__] = package
  pq = importlib.import_module(package.__name__ + '.pq_icbm_coordinator')
  path = SOURCE / 'opendbc_repo/opendbc/car/volkswagen/carcontroller.py'
  parsed = ast.parse(path.read_text(encoding='utf-8'))
  production = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == 'CarController')
  body = [n for n in production.body if isinstance(n, ast.Assign) or isinstance(n, ast.FunctionDef) and n.name == 'update']
  module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Controller', bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[]))
  namespace = {'StockFeedback': pq.StockFeedback, 'structs': NS(IntelligentCruiseButtonManagement=NS(SendButtonState=BUTTON))}
  exec(compile(module, str(path), 'exec'), namespace)
  controller = namespace['Controller']()
  controller.frame = 1
  controller.CCP = NS(STEER_STEP=100000, LDW_STEP=100000, ACC_HUD_STEP=100000, STEER_MAX=300)
  controller.CP = NS(pcmCruise=True, openpilotLongitudinalControl=False)
  controller.apply_torque_last = 0
  controller.gra_acc_counter_last = None
  controller.icbm_coordinator = pq.PQICBMCoordinator(transaction_timeout_s=1, feedback_max_age_s=.12,
    release_max_age_s=.12, settle_s=.08, echo_max_age_s=.5, native_bus=0, returned_bus=130)
  return controller


def xor_payload(data, checksum_byte=0):
  result = bytearray(data)
  result[checksum_byte] = 0
  for index, value in enumerate(result):
    if index != checksum_byte:
      result[checksum_byte] ^= value
  return bytes(result)


def gra(counter, manual=False):
  raw = (1 << 8) | (1 << 15) | (1 << 18) | ((counter & 15) << 20)
  if manual:
    raw |= 1 << 16
  return xor_payload(raw.to_bytes(4, 'little'))


def unpack(packet):
  packed = struct.unpack_from('<I', packet, 1)[0]
  return packed >> 3, (packet[0] >> 1) & 7, packet[6:6 + (packet[0] >> 4)], bool(packed & 1), bool(packed & 2)


def usb(image, command, request_id=0, button=0):
  image.cpu.mem_write(PACKET, bytes([command]) + struct.pack('<HHH', request_id, button, 0))
  return image.call('comms_control_handler', PACKET, POPPED)


def status(image):
  if usb(image, 0xAA) != 8:
    raise AssertionError('actual AA response length')
  version, state, button, reason, request_id, tx_counter = struct.unpack('<BBBBHH', image.read(POPPED, 8))
  return {'version': version, 'state': state, 'button': button, 'reason': reason,
          'request_id': request_id, 'tx_counter': tx_counter}


class MetricACCFixture:
  """Separate assumed stock ECU; never returns a request as its own ACK."""
  def __init__(self, initial, delay_ms=80, ignore=False, floor=30, down_model='lower_decade'):
    self.speed = initial
    self.delay_ns = delay_ms * 1000000
    self.ignore = ignore
    self.floor = floor
    self.down_model = down_model
    self.seen = set()
    self.pending = []
    self.effects = []

  def consume(self, stamp, raw, firmware):
    if firmware['state'] not in (1, 2) or len(raw) != 4:
      return
    request_id, button = firmware['request_id'], firmware['button']
    if button not in BITS or not (int.from_bytes(raw, 'little') & (1 << BITS[button])):
      return
    if request_id in self.seen:
      return
    self.seen.add(request_id)
    if not self.ignore:
      self.pending.append((stamp + self.delay_ns, request_id, button))

  def advance(self, stamp):
    while self.pending and self.pending[0][0] <= stamp:
      due, request_id, button = self.pending.pop(0)
      before = self.speed
      if button == 1:
        self.speed = (self.speed // 10 + 1) * 10
      elif button == 2:
        self.speed = self.speed - 10 if self.down_model == 'minus10' else ((self.speed - 1) // 10) * 10
      elif button == 3:
        self.speed -= 1
      elif button == 4:
        self.speed += 1
      self.speed = max(self.floor, self.speed)
      self.effects.append({'stamp': due, 'requestId': request_id, 'button': button,
                           'before': before, 'after': self.speed})


def run_scenario(config, elf):
  image, controller = ReplayImage(elf), load_controller()
  acc = MetricACCFixture(config['initial'], delay_ms=config.get('delayMs', 80), ignore=config.get('ignore', False), down_model=config.get('downModel', 'lower_decade'))
  cc = NS(actuators=NS(as_builder=lambda: NS()), hudControl=NS(), cruiseControl=NS(cancel=False, resume=False))
  sp = NS(intelligentCruiseButtonManagement=NS(vTarget=config['target'], speedUnit='kph', sendButton=BUTTON.none),
          icbm_status_valid=True, icbm_signature_matched=True, icbm_protocol_valid=True,
          icbm_panda_count=1, icbm_input_valid=True)
  cs = NS(gra_stock_values={'COUNTER': 0}, out=NS(cruiseState=NS(enabled=True, available=True),
          accFaulted=False, gasPressed=False, brakePressed=False, buttonEvents=[]), icbm_feedback_snapshot={})
  origin = 10000000000
  commands, a9_calls, counts, states = [], [], collections.Counter(), []
  last_state, counter, feedback_stamp, first_request_stamp, completed_at = None, getattr(image, 'native_counter_start', 0), origin, None, None
  pressed_by_id = collections.Counter()
  returned_release = collections.Counter()
  blocked_history = []
  interrupted = False
  expected_stock = config.get('expectedStock', config['target'])

  def note_returns(packets, stamp):
    for packet in packets:
      address, bus, raw, rejected, returned = unpack(packet)
      if address == 0x38A:
        counts['graRxRejected' if rejected else 'graTxReturned' if returned else 'graNativeRx'] += 1
        src = bus + (192 if rejected else 128 if returned else 0)
        if not (config.get('loseRelease') and returned and int.from_bytes(raw, 'little') & sum(1 << x for x in BITS.values()) == 0 and first_request_stamp is not None):
          controller.icbm_coordinator.observe(stamp, address, raw, src)

  def receive(address, raw, stamp):
    queues, rx = image.rx(address, 0, raw)
    note_returns(rx, stamp)
    for bus, packets in queues.items():
      for packet in packets:
        queued_address, _, queued_raw, _, _ = unpack(packet)
        firmware = status(image)
        pressed = queued_address == 0x38A and int.from_bytes(queued_raw, 'little') & sum(1 << x for x in BITS.values()) != 0
        if pressed and firmware['request_id'] and firmware['state'] in (1, 2):
          pressed_by_id[firmware['request_id']] += 1
          if cs.out.gasPressed or cs.out.brakePressed or cs.out.buttonEvents:
            counts['ownedPressWhileDriverInterrupts'] += 1
          acc.consume(stamp, queued_raw, firmware)
        release = queued_address == 0x38A and not pressed and first_request_stamp is not None
        if config.get('loseRelease') and release:
          counts['syntheticDroppedReleaseTxAck'] += 1
          continue
        returned = image.ack_return(packet, int(bus))
        note_returns(returned, stamp)
        if release:
          returned_release[firmware['request_id']] += 1

  for index in range(config.get('steps', 1000)):
    stamp = origin + index * 10000000
    image.clock(stamp, origin)
    elapsed_from_request = (stamp - first_request_stamp) / 1e9 if first_request_stamp is not None else -1
    interruption = config.get('interrupt') if .05 <= elapsed_from_request < .25 else None
    if interruption:
      interrupted = True
    gas, brake, manual = interruption == 'gas', interruption == 'brake', interruption == 'manual'
    # Pedal recovery fixture explicitly reenables stock cruise after release;
    # it does not write controls_allowed or fabricate a retained ACC state.
    cruise = not (gas or brake)
    cs.out.cruiseState.enabled = cruise
    cs.out.gasPressed, cs.out.brakePressed = gas, brake
    cs.out.buttonEvents = [NS(pressed=True)] if manual else []
    # Native safety inputs at their declared frequencies; checksums/counters
    # and RX hooks are run by the production ARM image.
    driver = bytearray(6)
    driver[1] = (index & 15) << 4
    receive(0xD0, xor_payload(driver), stamp)
    speed = bytearray(8)
    speed[2:4] = (2000 << 1).to_bytes(2, 'little')
    receive(0x1A0, bytes(speed), stamp)
    throttle = bytearray(8)
    throttle[2] = 50 if gas else 0
    receive(0x380, bytes(throttle), stamp)
    if index % 2 == 0:
      motor2 = bytearray(8)
      motor2[2] = (0x40 if cruise else 0) | int(brake)
      receive(0x288, bytes(motor2), stamp)
      motor5 = bytearray(8)
      motor5[6] = 4
      receive(0x480, xor_payload(motor5, 7), stamp)
      receive(0x38A, gra(counter, manual), stamp)
      cs.gra_stock_values['COUNTER'] = counter
      counter = (counter + 1) & 15
    acc.advance(stamp)
    firmware = status(image)
    if firmware['version'] != 1:
      raise AssertionError('actual protocol version')
    sp.icbm_firmware_request_id, sp.icbm_firmware_state = firmware['request_id'], firmware['state']
    sp.icbm_firmware_button, sp.icbm_firmware_reason = firmware['button'], firmware['reason']
    sp.icbm_firmware_tx_counter, sp.icbm_firmware_status_mono_time_ns = firmware['tx_counter'], stamp
    sp.icbm_input_timestamp_ns = stamp
    if not (config.get('loseFeedback') and first_request_stamp is not None):
      feedback_stamp = stamp
    cs.icbm_feedback_snapshot = {
      'ACC_GRA_Anzeige': {'ACA_V_Wunsch': acc.speed, 'ACA_kmh_mph': 0, 'timestamp_nanos': feedback_stamp},
      'ACC_System': {'ACS_Sta_ADR': 1, 'ACS_FreigSollB': 1, 'timestamp_nanos': stamp},
      'readiness_timestamps': {key: stamp for key in ('GRA_Neu', 'Motor_2', 'Motor_3', 'Motor_5')}}
    sp.intelligentCruiseButtonManagement.sendButton = BUTTON.increase if config['target'] > acc.speed else BUTTON.decrease if config['target'] < acc.speed else BUTTON.none
    controller.update(cc, sp, cs, stamp)
    command = controller.icbm_command
    if command is not None:
      if first_request_stamp is None:
        first_request_stamp = stamp
      request_id, button = command
      usb(image, 0xA9, request_id, button)
      a9_calls.append({'stamp': stamp, 'requestId': request_id, 'button': button, 'retry': False})
      counts['A9Calls'] += 1
      if not any(x['requestId'] == request_id for x in commands):
        commands.append({'stamp': stamp, 'requestId': request_id, 'button': button})
      if config.get('retry'):
        before = status(image)
        usb(image, 0xA9, request_id, button)
        a9_calls.append({'stamp': stamp, 'requestId': request_id, 'button': button, 'retry': True})
        if status(image) != before:
          raise AssertionError('same request retry changed status')
        counts['duplicateA9Calls'] += 1
    coordinator = controller.icbm_coordinator
    blocked = coordinator.blocked_reason
    selected = {'hostState': str(coordinator.state), 'blocked': blocked, 'firmware': firmware,
                'stockSpeed': acc.speed, 'panda': image.state(), 'interruption': interruption}
    if selected != last_state:
      if len(states) < 300:
        states.append({'stamp': stamp, **selected})
      last_state = selected
    if blocked and (not blocked_history or blocked_history[-1]['reason'] != blocked):
      blocked_history.append({'stamp': stamp, 'reason': blocked})
    if acc.speed == expected_stock and coordinator.transaction.pending is None and not blocked and commands:
      if completed_at is None:
        completed_at = stamp
      if stamp - completed_at >= 200000000:
        break
    else:
      completed_at = None
    if config.get('expectBlocked') and blocked and elapsed_from_request > 1.5:
      break
  target_reached = acc.speed == expected_stock
  idle = controller.icbm_coordinator.transaction.pending is None and not controller.icbm_coordinator.blocked_reason
  maximum_press = max(pressed_by_id.values(), default=0)
  checks = {'singleEffectPerRequest': len({x['requestId'] for x in acc.effects}) == len(acc.effects),
            'atMostSevenInjectedFramesPerRequest': maximum_press <= 7,
            'actualRequestsObserved': bool(commands), 'targetReached': target_reached,
            'hostCleanIdle': idle, 'interruptionExercised': interrupted if config.get('interrupt') else True,
            'noA9WhileInterrupted': not any(first_request_stamp + 50000000 <= x['stamp'] < first_request_stamp + 250000000 for x in a9_calls) if interrupted else True,
            'noOwnedPressWhileInterrupted': counts['ownedPressWhileDriverInterrupts'] == 0}
  if config.get('expectBlocked'):
    passed = bool(blocked_history) and checks['singleEffectPerRequest'] and checks['atMostSevenInjectedFramesPerRequest']
  else:
    passed = all(checks.values())
  return {'name': config['name'], 'configuration': config, 'status': 'PASS' if passed else 'PARITY_GAP' if config.get('parityGap') else 'FAIL',
          'checks': checks, 'finalStockSpeed': acc.speed, 'expectedStockSpeed': expected_stock,
          'requiredStockMinimumForZeroTarget': 30 if config.get('parityGap') else None,
          'durationSeconds': (stamp - origin) / 1e9, 'commands': commands, 'a9Calls': a9_calls, 'counts': dict(counts),
          'pressedFramesByRequest': dict(pressed_by_id), 'releaseReturnsByRequest': dict(returned_release),
          'syntheticECUEffects': acc.effects, 'blockedHistory': blocked_history, 'stateChanges': states,
          'stateChangesCapped': len(states) == 300, 'finalFirmware': status(image), 'finalPanda': image.state()}


def input_label(path):
  return 'source/' + str(path.relative_to(SOURCE)).replace('\\', '/') if path.is_relative_to(SOURCE) else 'tool/' + path.name


def main():
  global SOURCE, OUT, CONTRACT, BRIDGE, ReplayImage, PACKET, POPPED
  parser = argparse.ArgumentParser()
  artifacts = parser.add_mutually_exclusive_group(required=True)
  artifacts.add_argument('--prototype-manifest', type=pathlib.Path, help='current exact-stable+ICBM raw prototype manifest')
  artifacts.add_argument('--elf', type=pathlib.Path, help='legacy source-rebuilt ELF reference only; never a raw-prototype fallback')
  parser.add_argument('--source', type=pathlib.Path, required=True, help='openpilot repository root')
  parser.add_argument('--contract', type=pathlib.Path, default=HERE / 'icbm_metric_button_contract.json')
  parser.add_argument('--output', type=pathlib.Path, default=pathlib.Path('synthetic_closed_loop.json'))
  parser.add_argument('--only', default='')
  args = parser.parse_args()
  SOURCE, CONTRACT = args.source.resolve(), args.contract.resolve()
  args.output = args.output.resolve()
  OUT = args.output.parent
  sys.path.insert(0, str(SOURCE / 'tools/pq_icbm_release/tests'))
  artifact_inputs = []
  if args.prototype_manifest is not None:
    args.prototype_manifest = args.prototype_manifest.resolve()
    manifest = json.loads(args.prototype_manifest.read_text())
    artifact = args.prototype_manifest.parent / manifest['prototypeFile']
    BRIDGE = SOURCE / 'tools/exact_stable_icbm/raw_image.py'
    artifact_inputs = [args.prototype_manifest, artifact, SOURCE / 'tools/exact_stable_icbm/original_abi.json']
    artifact_kind = 'exact_stable_raw_icbm_prototype'
  else:
    artifact = args.elf.resolve()
    BRIDGE = HERE / 'icbm_arm_replay.py'
    artifact_inputs = [artifact, HERE / 'arm_can.py', HERE / 'arm_memory.py']
    artifact_kind = 'legacy_source_rebuilt_elf_reference'
  tracked = [SOURCE / 'opendbc_repo/opendbc/car/volkswagen/carcontroller.py', BRIDGE,
             CONTRACT, pathlib.Path(__file__)]
  tracked += artifact_inputs
  tracked += list((SOURCE / 'opendbc_repo/opendbc/sunnypilot/car/volkswagen').glob('pq_*.py'))
  input_hashes = {input_label(path): sha(path) for path in tracked}
  if args.prototype_manifest is not None:
    ReplayImage, PACKET = prototype_factory(SOURCE, args.prototype_manifest)
  else:
    from icbm_arm_replay import ReplayImage as ArmImage, PACKET as ArmPacket
    ReplayImage, PACKET = ArmImage, ArmPacket
  POPPED = PACKET + 32
  scenarios = [
    {'name': 'coarse_up_then_fine', 'initial': 56, 'target': 73},
    {'name': 'coarse_down_then_fine', 'initial': 100, 'target': 79},
    {'name': 'offgrid_down_lower_decade', 'initial': 43, 'target': 30, 'downModel': 'lower_decade'},
    {'name': 'offgrid_down_minus10', 'initial': 43, 'target': 30, 'downModel': 'minus10'},
    {'name': 'fine_res_exact', 'initial': 80, 'target': 81},
    {'name': 'fine_set_exact', 'initial': 80, 'target': 79},
    {'name': 'idempotent_A9_retry', 'initial': 80, 'target': 91, 'retry': True},
    {'name': 'manual_priority_recovery', 'initial': 56, 'target': 73, 'interrupt': 'manual'},
    {'name': 'gas_priority_recovery', 'initial': 56, 'target': 73, 'interrupt': 'gas'},
    {'name': 'brake_priority_recovery', 'initial': 56, 'target': 73, 'interrupt': 'brake'},
    {'name': 'lost_release_blocks', 'initial': 80, 'target': 81, 'loseRelease': True, 'expectBlocked': True},
    {'name': 'lost_feedback_blocks', 'initial': 80, 'target': 81, 'loseFeedback': True, 'expectBlocked': True},
    {'name': 'ignored_press_timeout_confirmed_rearm', 'initial': 80, 'target': 81, 'ignore': True, 'expectBlocked': True},
    {'name': 'zero_target_factory_minimum_parity', 'initial': 80, 'target': 0, 'expectedStock': 30, 'parityGap': True, 'steps': 600},
    {'name': 'below_minimum_factory_minimum_parity', 'initial': 80, 'target': 15, 'expectedStock': 30, 'parityGap': True, 'steps': 600}]
  if args.only:
    requested = set(args.only.split(','))
    unknown = requested - {x['name'] for x in scenarios}
    if unknown:
      parser.error('unknown scenario names: ' + ','.join(sorted(unknown)))
    scenarios = [x for x in scenarios if x['name'] in requested]
  results = []
  for config in scenarios:
    result = run_scenario(config, artifact)
    results.append(result)
    print(json.dumps({'name': result['name'], 'status': result['status'], 'speed': result['finalStockSpeed'],
                      'commands': len(result['commands']), 'checks': result['checks']}), flush=True)
  if input_hashes != {input_label(path): sha(path) for path in tracked}:
    raise RuntimeError('source inputs changed during scenario matrix; rerun immutable inputs')
  report = {'scope': 'Production VW update/PQ coordinator/selection/transaction/echo + production ARM CAN RX/adapter/A9/AA/queue/TX-return; explicit synthetic stock ACC feedback',
            'status': 'PASS' if all(x['status'] == 'PASS' for x in results) else 'INCOMPLETE',
            'artifactKind': artifact_kind, 'artifactSha256': sha(artifact),
            'prototypeManifestSha256': sha(args.prototype_manifest) if args.prototype_manifest is not None else None,
            'warmupFixture': {'nativeCounters': [0, 1, 2], 'periodMicroseconds': 20000,
                              'firstScenarioCounter': 3, 'clockOffsetMicroseconds': 60000,
                              'policyRAMOverrides': False} if args.prototype_manifest is not None else None,
            'controllerSha256': sha(SOURCE / 'opendbc_repo/opendbc/car/volkswagen/carcontroller.py'),
            'inputHashes': input_hashes,
            'contractSha256': sha(CONTRACT), 'harnessSha256': sha(pathlib.Path(__file__)),
            'armBridgeSha256': sha(BRIDGE),
            'coordinatorInputs': {path.name: sha(path) for path in (SOURCE / 'opendbc_repo/opendbc/sunnypilot/car/volkswagen').glob('pq_*.py')},
            'assumptions': ['Metric stockACC model accepts first valid held-button frame per request; publishes independent feedback after80ms.',
                            'UP next higher decade; DOWN two explicitly alternative models (lower decade vsminus10), off-grid DOWN behavior unobserved; SET-1/RES+1; stock minimum30 fixture.',
                            'Actual hardware transmission modeled successful via TXOK mailbox fixture; this is not ECU acknowledgement.',
                            ('Complete prototype is loaded before original Reset_Handler and extension initialization; three native GRA frames0/1/2 at20ms warm counters, nextcounter3/clockoffset60ms. No policy RAM overrides.' if args.prototype_manifest is not None else 'Legacy ELF reference initializes healthy guard fixtures once; it is not the current raw exact-stable release.'),
                            'Full production update body loaded by AST; unrelated steering/HUD cadence kept off; transport packer not executed.',
                            'High-level planner/ICBM publisher, Params and board main-loop heartbeat/thermal timing are outside this harness; healthy link and valid target/direction are supplied fixtures.',
                            'Pedal recovery explicitly reenables native cruise; manual source pauses and releases. Driver base-memory semantics remain outside scope.',
                            'Ignored-response timeout can rearm after independently fresh terminal firmware/release/stock evidence; this test does not claim a permanent rejection latch.',
                            'Matching source/compiledARM behavior does not establish new injected press acceptance on physical radar or PQ46/mph parity.'],
            'results': results}
  OUT.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
  if report['status'] != 'PASS':
    raise SystemExit(1)


if __name__ == '__main__':
  main()
