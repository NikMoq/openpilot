"""Panda 10.2.0 ICBM control protocol tests against the compiled F4 ELF."""
import os
import struct
import sys
import unittest
from pathlib import Path

from arm_can import CANImage, PACKET, POPPED, TIM2_CNT, packet, raw_packet, synthetic

ELF = Path(os.environ["ICBM_TEST_ELF"])
IDLE, ACCEPTED, WAIT_RELEASE, COMPLETE, REJECTED = range(5)
ABORTED_PENDING_RELEASE, ABORTED, ABORTED_NO_PRESS = 5, 6, 7
BUTTONS = {"UP": 1, "DOWN": 2, "SET": 3, "RES": 4}


def control(image, request, request_id=0, button=0):
  image.cpu.mem_write(PACKET, bytes([request]) + struct.pack("<HHH", request_id, button, 0))
  return image.call("comms_control_handler", PACKET, POPPED)


def status(image):
  assert control(image, 0xAA) == 8
  return struct.unpack("<BBBBHH", image.read(POPPED, 8))


def tx_echo(raw, bus=2):
  result = bytearray(packet(raw, bus=bus))
  result[1] |= 0x02  # CANPacket_t.returned
  result[5] = 0
  result[5] = __import__("functools").reduce(int.__xor__, result[:10], 0)
  return bytes(result)


def tx_return(image, raw):
  image.cpu.mem_write(PACKET, tx_echo(raw))
  image.call("gra_adapter_test_tx_return", PACKET)


class Panda1020ProtocolTests(unittest.TestCase):
  def setUp(self):
    self.image = CANImage(ELF)
    self.image.guards(controls=True)

  def native(self, counter, now):
    raw = synthetic(counter)
    tx, rx, _ = self.image.receive(raw, now)
    self.assertEqual([raw_packet(frame) for frame in tx], [raw])
    self.assertEqual(rx, [packet(raw)])
    return raw

  def issue(self, request_id, button):
    self.assertEqual(control(self.image, 0xA9, request_id, BUTTONS[button]), 0)

  def test_one_outstanding_correlated_press_release_and_matching_tx_return(self):
    self.native(9, 1000)
    self.issue(1, "UP")
    self.assertEqual(status(self.image), (1, ACCEPTED, BUTTONS["UP"], 0, 1, 0))
    # Retrying the same control ID is idempotent; another ID cannot overlap.
    self.issue(1, "UP")
    self.issue(2, "DOWN")
    self.assertEqual(status(self.image), (1, ACCEPTED, BUTTONS["UP"], 0, 1, 0))

    for i in range(7):
      counter = (10 + i) & 0xF
      tx, rx, _ = self.image.receive(synthetic(counter), 21000 + i * 20000)
      self.assertEqual([raw_packet(frame) for frame in tx], [synthetic(counter, 1 << 11)])
      self.assertEqual(rx, [packet(synthetic(counter))])

    release_counter = (10 + 7) & 0xF
    release = synthetic(release_counter)
    tx, rx, _ = self.image.receive(release, 161000)
    self.assertEqual([raw_packet(frame) for frame in tx], [release])
    self.assertEqual(rx, [packet(release)])
    self.assertEqual(status(self.image), (1, WAIT_RELEASE, BUTTONS["UP"], 0, 1, 0))

    # A stale/wrong payload TX-return cannot complete the transaction.
    tx_return(self.image, synthetic((release_counter + 1) & 0xF))
    self.assertEqual(status(self.image)[1], WAIT_RELEASE)
    # Exact address/bus/DLC/payload and returned flag completes the matching ID.
    tx_return(self.image, release)
    self.assertEqual(status(self.image), (1, COMPLETE, BUTTONS["UP"], 0, 1, release_counter))

    self.image.boolean("vehicle_moving", True)
    self.issue(2, "RES")
    self.assertEqual(status(self.image), (1, ACCEPTED, BUTTONS["RES"], 0, 2, release_counter))

  def test_command_requires_fresh_native_and_active_safety_guards(self):
    self.issue(1, "DOWN")
    self.assertEqual(status(self.image), (1, REJECTED, BUTTONS["DOWN"], 2, 1, 0))

    self.image = CANImage(ELF)
    self.image.guards(controls=True)
    self.native(4, 1000)
    self.image.guards(controls=False)
    self.issue(1, "DOWN")
    self.assertEqual(status(self.image), (1, REJECTED, BUTTONS["DOWN"], 2, 1, 0))

  def test_unidentified_bulk_button_frame_is_rejected(self):
    native = synthetic(3)
    self.native(3, 1000)
    tx, rejected, _ = self.image.host(synthetic(3, 1 << 11), 1001)
    self.assertEqual(tx, [])
    self.assertEqual(len(rejected), 1)
    self.assertTrue(rejected[0][1] & 1)
    self.assertEqual(status(self.image)[0:2], (1, IDLE))

  def test_first_frame_driver_interruption_has_no_press_terminal_state(self):
    self.native(3, 1000)
    self.issue(1, "UP")
    driver = synthetic(4, 1 << 10)
    tx, _, _ = self.image.receive(driver, 21000)
    self.assertEqual([raw_packet(frame) for frame in tx], [driver])
    self.assertEqual(status(self.image)[1], ABORTED_NO_PRESS)
    tx_return(self.image, driver)
    self.assertEqual(status(self.image)[1], ABORTED_NO_PRESS)
    self.native(5, 41000)
    self.issue(2, "UP")
    self.assertEqual(status(self.image)[1], ACCEPTED)

  def test_guard_reset_before_first_press_has_no_release_claim(self):
    self.native(3, 1000)
    self.issue(1, "UP")
    self.image.guards(gas=True)
    self.image.call("gra_adapter_test_tick")
    self.assertEqual(status(self.image)[1], ABORTED_NO_PRESS)

  def test_expiry_before_first_press_has_no_release_claim(self):
    self.native(3, 1000)
    self.issue(1, "UP")
    self.image.write32(TIM2_CNT, 201001)
    self.image.call("gra_adapter_test_tick")
    self.assertEqual(status(self.image)[1], ABORTED_NO_PRESS)

  def test_guard_reset_after_press_requires_exact_returned_release(self):
    self.native(3, 1000)
    self.issue(1, "UP")
    self.image.receive(synthetic(4), 21000)
    self.image.guards(gas=True)
    self.image.call("gra_adapter_test_tick")
    self.assertEqual(status(self.image)[1], ABORTED_PENDING_RELEASE)
    release = self.native(5, 41000)
    self.assertEqual(status(self.image)[1], ABORTED_PENDING_RELEASE)
    tx_return(self.image, synthetic(6))
    self.assertEqual(status(self.image)[1], ABORTED_PENDING_RELEASE)
    tx_return(self.image, release)
    self.assertEqual(status(self.image)[1], ABORTED)


class PQ300RegressionTests(unittest.TestCase):
  def setUp(self):
    self.image = CANImage(ELF)

  def test_legacy_standstill_resume_still_uses_safety_hook(self):
    self.image.boolean("vehicle_moving", False)
    raw = synthetic(0, 1 << 17)
    tx, rejected, events = self.image.host(raw, 1000)
    self.assertEqual(tx, [packet(raw, bus=2)])
    self.assertEqual(rejected, [])
    self.assertEqual(sum(event[0] == "safety_tx_hook" for event in events), 1)
    self.assertEqual(status(self.image)[1], IDLE)
    self.image.guards(controls=False)
    tx, rejected, events = self.image.host(raw, 2000)
    self.assertEqual(tx, [])
    self.assertEqual(len(rejected), 1)
    self.assertEqual(sum(event[0] == "safety_tx_hook" for event in events), 1)

  def test_fine_buttons_seven_frames_and_exact_release(self):
    for button, bit in (("SET", 16), ("RES", 17)):
      with self.subTest(button=button):
        self.image = CANImage(ELF)
        self.image.boolean("vehicle_moving", True)
        self.image.receive(synthetic(0), 1000)
        control(self.image, 0xA9, 1, BUTTONS[button])
        self.assertEqual(status(self.image)[1], ACCEPTED)
        for index in range(7):
          tx, _, _ = self.image.receive(synthetic(index + 1), 21000 + index * 20000)
          self.assertEqual([raw_packet(frame) for frame in tx], [synthetic(index + 1, 1 << bit)])
        release = synthetic(8)
        tx, _, _ = self.image.receive(release, 161000)
        self.assertEqual([raw_packet(frame) for frame in tx], [release])
        self.assertEqual(status(self.image)[1], WAIT_RELEASE)
        tx_return(self.image, release)
        self.assertEqual(status(self.image)[1], COMPLETE)

  def test_all_firmware_guards_reject_control_request(self):
    for guard in (dict(brake=True), dict(gas=True), dict(relay_ok=False), dict(heartbeat=False), dict(valid=False), dict(controls=False)):
      with self.subTest(guard=guard):
        self.image = CANImage(ELF)
        self.image.receive(synthetic(0), 1000)
        self.image.guards(**guard)
        control(self.image, 0xA9, 1, BUTTONS["UP"])
        self.assertEqual(status(self.image)[1], REJECTED)

  def steering(self, torque, now):
    image = self.image
    image.write32(TIM2_CNT, now)
    data = bytearray(packet(0, bus=0, addr=0xD2))
    data[0] = 0x50  # five-byte HCA_1
    value = abs(torque) * 32
    data[7] = 0x50
    data[8] = value & 255
    data[9] = ((value >> 8) & 127) | (128 if torque < 0 else 0)
    data[5] = 0
    for b in data[:11]: data[5] ^= b
    image.cpu.mem_write(PACKET, bytes(data))
    return image.call("safety_tx_hook", PACKET)

  def test_actual_safety_ramp_accepts_300_rejects_301(self):
    for torque in range(0, 301, 6):
      self.assertEqual(self.steering(torque, 1000 + torque // 6 * 20000), 1, torque)
    self.assertEqual(self.steering(301, 1021000), 0)

  def test_startup_rx_bank_and_stack_layout(self):
    s = self.image.symbols
    self.assertEqual(s["_estack"], 0x2001fffc)
    self.assertEqual((s["_sram2_bss"], s["_eram2_bss"]), (0x20040000, 0x20050000))
    self.assertEqual(self.image.read(0x20040000, 65536), bytes(65536))
    self.assertLessEqual(s["_ebss"] + s["_Min_Stack_Size"], s["_estack"])

  def test_actual_safety_rate_and_controls_still_enforced(self):
    self.assertEqual(self.steering(7, 1000), 0)
    self.image = CANImage(ELF)
    self.image.guards(controls=False)
    self.assertEqual(self.steering(6, 1000), 0)


if __name__ == "__main__":
  unittest.main(verbosity=2)
