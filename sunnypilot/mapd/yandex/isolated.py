"""Killable native acquisition: DNS and headers share the wall-clock budget."""
from __future__ import annotations

import json
import math
from pathlib import Path
import select
import subprocess
import sys
import time

from .native_client import AcquisitionCancelled
from .native_provider import NativeWindowResult
from .native_transport import NativeTransportError

WINDOW_TIMEOUT_S = 60.
PROBE_TIMEOUT_S = 5.
MAX_WORKER_BYTES = 8 * 1024 * 1024 + 4096
_CATEGORIES = frozenset(('worker_timeout', 'network_error', 'timeout', 'credentials_unavailable',
                         'http_error', 'unexpected_content_type', 'response_over_budget',
                         'invalid_request', 'invalid_response', 'worker_failed', 'closed'))


def run_isolated(mode, *, credentials_path=None, request=None, timeout_s=None,
                 should_continue=lambda: True, _command=None):
  """Read bounded JSON from a child and reap it on cancellation or timeout.

  No credential value is passed in arguments or environment. Only the child
  performing acquisition reads the existing owner-private credential file.
  """
  maximum = {'fetch': WINDOW_TIMEOUT_S, 'probe': PROBE_TIMEOUT_S}.get(mode)
  timeout_s = maximum if timeout_s is None else timeout_s
  if (maximum is None or type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
      or not 0 < timeout_s <= maximum or (mode == 'fetch' and credentials_path is None)):
    raise NativeTransportError('invalid_request')
  if not should_continue():
    raise AcquisitionCancelled('native acquisition cancelled')
  try:
    payload = json.dumps(request or {}, separators=(',', ':'), allow_nan=False).encode()
  except (TypeError, ValueError):
    raise NativeTransportError('invalid_request') from None
  if len(payload) > 4096:
    raise NativeTransportError('invalid_request')
  command = [sys.executable, str(Path(__file__).with_name('native_worker.py')), '--mode', mode]
  if mode == 'fetch':
    command.extend(('--credentials', str(credentials_path)))
  if _command is not None:  # Deterministic child fixtures, never a persisted setting.
    command = list(_command)
  process = None
  deadline = time.monotonic() + timeout_s
  data = bytearray()
  try:
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    process.stdin.write(payload)
    process.stdin.close()
    descriptor = process.stdout.fileno()
    while True:
      if not should_continue():
        raise AcquisitionCancelled('native acquisition cancelled')
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise NativeTransportError('worker_timeout')
      ready, _, _ = select.select([descriptor], [], [], min(.1, remaining))
      if not ready:
        continue
      chunk = process.stdout.read1(65536)
      if not chunk:
        break
      if len(data) + len(chunk) > MAX_WORKER_BYTES:
        raise NativeTransportError('response_over_budget')
      data.extend(chunk)
    while process.poll() is None:
      if not should_continue():
        raise AcquisitionCancelled('native acquisition cancelled')
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise NativeTransportError('worker_timeout')
      try:
        process.wait(timeout=min(.1, remaining))
      except subprocess.TimeoutExpired:
        pass
    if process.returncode != 0:
      raise NativeTransportError('worker_failed')
    try:
      result = json.loads(data)
    except (ValueError, UnicodeError):
      raise NativeTransportError('invalid_response') from None
    if not isinstance(result, dict) or type(result.get('ok')) is not bool:
      raise NativeTransportError('invalid_response')
    if not result['ok']:
      category, status = result.get('category'), result.get('status')
      category = category if isinstance(category, str) and category in _CATEGORIES else 'worker_failed'
      status = status if type(status) is int and 100 <= status <= 599 else None
      raise NativeTransportError(category, status)
    return result
  except (OSError, subprocess.SubprocessError):
    raise NativeTransportError('worker_failed') from None
  finally:
    if process is not None:
      if process.poll() is None:
        # This process owns only network acquisition, never manager or hardware.
        process.kill()
      process.wait()
      if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
      if process.stdout is not None:
        process.stdout.close()


class IsolatedYandexProvider:
  def __init__(self, credentials_path):
    self.credentials_path = Path(credentials_path)

  def fetch_window(self, latitude, longitude, heading, *, should_continue=lambda: True):
    result = run_isolated('fetch', credentials_path=self.credentials_path,
                          request={'latitude': latitude, 'longitude': longitude, 'heading': heading},
                          should_continue=should_continue)
    if (set(result) != {'ok', 'events', 'complete', 'errors', 'network_failure'}
        or type(result['complete']) is not bool or type(result['network_failure']) is not bool
        or not isinstance(result['events'], list) or len(result['events']) > 5000
        or any(not isinstance(event, dict) for event in result['events'])
        or not isinstance(result['errors'], list) or len(result['errors']) > 2
        or any(not isinstance(error, str) or len(error) > 128 for error in result['errors'])):
      raise NativeTransportError('invalid_response')
    return NativeWindowResult(tuple(result['events']), result['complete'], tuple(result['errors']), result['network_failure'])
