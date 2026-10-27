"""Read-only observer for PQ GRA forwarding TX-return echoes.

This module does not send CAN or alter vehicle controls. The caller supplies
ordered, timestamped raw records. Timestamps and ``max_age`` use the same
caller-defined monotonic unit; timestamps represent delivery time, not a
hardware TX timestamp. A returned CAN TXOK echo is not proof of ECU acceptance.
"""
from collections import deque
from dataclasses import dataclass
from enum import Enum
import math


class EchoState(str, Enum):
  DISARMED = "disarmed"
  WAIT_NATIVE_IDLE = "wait_native_idle"
  WAIT_RETURNED_PRESS = "wait_returned_press"
  PRESSED = "pressed"
  RELEASED = "released"
  INVALID = "invalid"


@dataclass(frozen=True)
class EchoProvenance:
  kind: str
  timestamp: int | float
  src: int
  counter: int | None = None
  payload: bytes | None = None


@dataclass(frozen=True)
class EchoSnapshot:
  state: EchoState
  expected_button: str | None
  issued_timestamp: int | float | None
  native_counter: int | None
  pressed_return_timestamp: int | float | None
  release_return_timestamp: int | float | None
  last_idle_return_timestamp: int | float | None
  reason: str | None
  provenance: tuple[EchoProvenance, ...]


class PQButtonEchoObserver:
  """Correlate source-bus native GRA with its physical TX-return echo.

  ``max_age`` is deliberately mandatory and has no ECU-timing default. The
  observer keeps only ``history_size`` native frames and provenance entries.
  Native and returned buses default to the observed PQ setup (0 and 130), but
  the caller can supply the actual bus mapping.
  """

  ADDRESS = 0x38A
  CONFIG_MASK = (1 << 8) | (1 << 15) | (3 << 18) | (3 << 29)
  DRIVER_MASK = (31 << 9) | (3 << 16) | (15 << 24)
  INVALID_NATIVE_MASK = (1 << 14) | (1 << 31)
  BUTTON_BITS = {"DOWN": 1 << 10, "UP": 1 << 11,
                 "SET": 1 << 16, "RES": 1 << 17}

  def __init__(self, *, max_age: int | float, native_bus: int = 0,
               returned_bus: int = 130, history_size: int = 32):
    if (not self._finite_number(max_age) or max_age <= 0
        or isinstance(native_bus, bool) or not isinstance(native_bus, int) or native_bus < 0
        or isinstance(returned_bus, bool) or not isinstance(returned_bus, int) or returned_bus < 128
        or returned_bus == native_bus
        or isinstance(history_size, bool) or not isinstance(history_size, int) or history_size < 2):
      raise ValueError("invalid observer configuration")
    self.max_age = max_age
    self.native_bus = native_bus
    self.returned_bus = returned_bus
    self.history_size = history_size
    self.state = EchoState.DISARMED
    self.expected_button = None
    self.issued_timestamp = None
    self.reason = None
    self._last_timestamp = None
    self._last_native_counter = None
    self._last_return_counter = None
    self._config = None
    self._native_history = deque(maxlen=history_size)
    self._provenance = deque(maxlen=history_size)
    self._pressed_return_timestamp = None
    self._release_return_timestamp = None
    self._last_idle_return_timestamp = None

  @staticmethod
  def _finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))

  @staticmethod
  def _payload_bytes(payload):
    if isinstance(payload, bytes):
      return payload if len(payload) == 4 else None
    if isinstance(payload, (bytearray, memoryview)):
      data = bytes(payload)
      return data if len(data) == 4 else None
    return None

  @staticmethod
  def _raw(data):
    return int.from_bytes(data, "little")

  @staticmethod
  def _counter(raw):
    return (raw >> 20) & 0xF

  @classmethod
  def _valid_payload(cls, data):
    if data is None:
      return False
    return data[0] == (data[1] ^ data[2] ^ data[3])

  def _invalidate(self, reason, timestamp=None, src=-1, counter=None, payload=None):
    self.state = EchoState.INVALID
    self.reason = reason
    self._provenance.append(EchoProvenance("invalid:" + reason, timestamp, src, counter, payload))

  def arm(self, expectedbutton, issuedtimestamp):
    """Begin observing one explicitly issued native GRA button intent."""
    if expectedbutton not in self.BUTTON_BITS or not self._finite_number(issuedtimestamp) or issuedtimestamp < 0:
      raise ValueError("expectedbutton must be UP/DOWN/SET/RES and issuedtimestamp finite/nonnegative")
    if self.state == EchoState.INVALID:
      raise RuntimeError("invalid ownership requires explicit disarm before rearming")
    if self.state not in (EchoState.DISARMED, EchoState.RELEASED):
      raise RuntimeError("an owned button transaction is already active")
    if self._last_timestamp is not None and issuedtimestamp < self._last_timestamp:
      raise ValueError("issuedtimestamp must not precede the latest observed record")
    self.state = EchoState.WAIT_NATIVE_IDLE
    self.expected_button = expectedbutton
    self.issued_timestamp = issuedtimestamp
    self.reason = None
    self._last_timestamp = None
    self._last_native_counter = None
    self._last_return_counter = None
    self._config = None
    self._native_history.clear()
    self._provenance.clear()
    self._pressed_return_timestamp = None
    self._release_return_timestamp = None
    self._last_idle_return_timestamp = None
    self._provenance.append(EchoProvenance("armed:" + expectedbutton, issuedtimestamp, -1))
    return self.snapshot()

  def disarm(self):
    """Explicitly abandon observation and clear ownership/bootstrap evidence."""
    self.state = EchoState.DISARMED
    self.expected_button = None
    self.issued_timestamp = None
    self.reason = None
    self._last_timestamp = None
    self._last_native_counter = None
    self._last_return_counter = None
    self._config = None
    self._native_history.clear()
    self._provenance.clear()
    self._pressed_return_timestamp = None
    self._release_return_timestamp = None
    self._last_idle_return_timestamp = None
    return self.snapshot()

  def observe(self, timestamp, address, payload, src):
    """Consume one record in stream order; return the current immutable snapshot."""
    if self.state == EchoState.INVALID:
      return self.snapshot()
    # Other traffic is outside this observer's ownership and cannot invalidate
    # its timestamp stream or establish/release the armed GRA transaction.
    if address != self.ADDRESS or src not in (self.native_bus, self.returned_bus, self.returned_bus + 64):
      return self.snapshot()
    if (not self._finite_number(timestamp) or timestamp < 0
        or isinstance(address, bool) or not isinstance(address, int)
        or isinstance(src, bool) or not isinstance(src, int)):
      if self.state == EchoState.DISARMED:
        self._reset_bootstrap("invalid_record_metadata", timestamp, src if isinstance(src, int) else -1)
      else:
        self._invalidate("invalid_record_metadata", timestamp, src if isinstance(src, int) else -1)
      return self.snapshot()
    # Records timestamped before arm may be delayed in the host delivery path;
    # they are outside this transaction and must neither ack nor poison it.
    if self.state != EchoState.DISARMED and timestamp < self.issued_timestamp:
      return self.snapshot()
    if self._last_timestamp is not None and timestamp < self._last_timestamp:
      if self.state == EchoState.DISARMED:
        self._reset_bootstrap("out_of_order_timestamp", timestamp, src)
        self._last_timestamp = timestamp
      else:
        self._invalidate("out_of_order_timestamp", timestamp, src)
      return self.snapshot()
    self._last_timestamp = timestamp
    if (self.state not in (EchoState.DISARMED, EchoState.RELEASED)
        and timestamp - self.issued_timestamp > self.max_age):
      self._invalidate("stale_record", timestamp, src)
      return self.snapshot()
    data = self._payload_bytes(payload)
    if not self._valid_payload(data):
      if self.state == EchoState.DISARMED:
        self._reset_bootstrap("bad_dlc_or_xor", timestamp, src, payload=data)
      else:
        self._invalidate("bad_dlc_or_xor", timestamp, src, payload=data)
      return self.snapshot()
    raw = self._raw(data)
    counter = self._counter(raw)
    if src == self.native_bus:
      self._observe_native(timestamp, src, data, raw, counter)
    elif src == self.returned_bus + 64:
      if self.state == EchoState.DISARMED:
        self._reset_bootstrap("rejected_return", timestamp, src, counter, data)
      else:
        self._invalidate("rejected_return", timestamp, src, counter, data)
    else:
      self._observe_returned(timestamp, src, data, raw, counter)
    return self.snapshot()

  def _observe_native(self, timestamp, src, data, raw, counter):
    if raw & (self.DRIVER_MASK | self.INVALID_NATIVE_MASK) or not (raw & (1 << 8)):
      if self.state == EchoState.DISARMED:
        self._reset_bootstrap("native_not_idle_or_ineligible", timestamp, src, counter, data)
      else:
        self._invalidate("native_not_idle_or_ineligible", timestamp, src, counter, data)
      return
    config = raw & self.CONFIG_MASK
    if self.state == EchoState.DISARMED and (
        (self._config is not None and config != self._config)
        or (self._last_native_counter is not None
            and counter != ((self._last_native_counter + 1) & 0xF))):
      self._reset_bootstrap("native_stream_restart", timestamp, src, counter, data)
    if self._config is None:
      self._config = config
    elif config != self._config:
      self._invalidate("native_config_changed", timestamp, src, counter, data)
      return
    if (self._last_native_counter is not None
        and counter != ((self._last_native_counter + 1) & 0xF)):
      self._invalidate("native_counter_gap", timestamp, src, counter, data)
      return
    self._last_native_counter = counter
    self._native_history.append((timestamp, counter, raw, data))
    self._provenance.append(EchoProvenance("native_idle", timestamp, src, counter, data))
    if self.state == EchoState.WAIT_NATIVE_IDLE:
      self.state = EchoState.WAIT_RETURNED_PRESS

  def _matching_native(self, timestamp, counter, returned_raw, returned_data):
    expected_bit = self.BUTTON_BITS.get(self.expected_button)
    for native_at, native_counter, native_raw, native_data in reversed(self._native_history):
      if timestamp - native_at > self.max_age:
        break
      if native_counter != counter:
        continue
      press_matches = False
      if expected_bit is not None:
        expected_raw = native_raw | expected_bit
        expected_data = bytearray(expected_raw.to_bytes(4, "little"))
        expected_data[0] = expected_data[1] ^ expected_data[2] ^ expected_data[3]
        press_matches = bytes(expected_data) == returned_data
      if ((native_data == returned_data or press_matches)
          and self._raw(returned_data) == returned_raw):
        return native_at
    return None

  def _observe_returned(self, timestamp, src, data, raw, counter):
    returned_buttons = raw & self.DRIVER_MASK
    if self.state == EchoState.DISARMED:
      if returned_buttons != 0:
        self._reset_bootstrap("unowned_nonidle_return", timestamp, src, counter, data)
        return
      native_at = self._matching_native(timestamp, counter, raw, data)
      if native_at is None:
        self._provenance.append(EchoProvenance("unmatched_idle_return_ignored", timestamp, src, counter, data))
        return
      if (self._last_return_counter is not None
          and counter != ((self._last_return_counter + 1) & 0xF)):
        self._reset_bootstrap("returned_stream_restart", timestamp, src, counter, data)
      self._last_return_counter = counter
      self._last_idle_return_timestamp = timestamp
      self._provenance.append(EchoProvenance("bootstrap_native_idle_txok", timestamp, src, counter, data))
      return

    expected_bit = self.BUTTON_BITS[self.expected_button]
    # Before an owned press has been seen, a neutral TX echo may belong to a
    # native frame queued before arm. It is not an acknowledgement or release.
    if returned_buttons == 0 and self.state not in (EchoState.PRESSED, EchoState.RELEASED):
      self._provenance.append(EchoProvenance("unowned_idle_return_ignored", timestamp, src, counter, data))
      return
    native_at = self._matching_native(timestamp, counter, raw, data)
    if native_at is None:
      self._invalidate("returned_without_matching_native", timestamp, src, counter, data)
      return
    if (self._last_return_counter is not None
        and counter != ((self._last_return_counter + 1) & 0xF)):
      self._invalidate("returned_counter_gap", timestamp, src, counter, data)
      return
    self._last_return_counter = counter
    if returned_buttons == expected_bit:
      if self.state not in (EchoState.WAIT_RETURNED_PRESS, EchoState.PRESSED):
        self._invalidate("pressed_return_before_native_idle", timestamp, src, counter, data)
        return
      self.state = EchoState.PRESSED
      self._pressed_return_timestamp = timestamp
      self._provenance.append(EchoProvenance("returned_pressed_txok", timestamp, src, counter, data))
      return
    if returned_buttons != 0:
      self._invalidate("returned_wrong_or_driver_button", timestamp, src, counter, data)
      return
    self._provenance.append(EchoProvenance("returned_native_idle_txok", timestamp, src, counter, data))
    self._last_idle_return_timestamp = timestamp
    if self.state == EchoState.PRESSED:
      self.state = EchoState.RELEASED
      self._release_return_timestamp = timestamp
      self._provenance.append(EchoProvenance("returned_native_release_txok", timestamp, src, counter, data))

  def _reset_bootstrap(self, reason, timestamp, src, counter=None, payload=None):
    self._last_native_counter = None
    self._last_return_counter = None
    self._config = None
    self._native_history.clear()
    self._last_idle_return_timestamp = None
    self._provenance.append(EchoProvenance("bootstrap_reset:" + reason, timestamp, src, counter, payload))

  def snapshot(self):
    return EchoSnapshot(self.state, self.expected_button, self.issued_timestamp,
                        self._last_native_counter, self._pressed_return_timestamp,
                        self._release_return_timestamp, self._last_idle_return_timestamp,
                        self.reason, tuple(self._provenance))
