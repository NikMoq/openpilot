"""Connectivity to the actual Yandex host, independent of SDK authentication.

TCP plus certificate-verified TLS establishes reachability only. It does not
establish API authorization, routing availability or event inventory coverage.
Run the blocking probe inside the killable child; DNS has no socket deadline.
"""
from __future__ import annotations

from contextlib import suppress
import socket
import ssl
from urllib.parse import urlsplit

from .native_endpoint import DEFAULT_BASE_URL
from .native_errors import AcquisitionCancelled, NativeTransportError


def probe_yandex_endpoint():
  """Child-only TCP/TLS probe: no HTTP, SDK headers, credentials or ICMP."""
  host = urlsplit(DEFAULT_BASE_URL).hostname
  connection = secured = None
  try:
    connection = socket.create_connection((host, 443), timeout=2)
    context = ssl.create_default_context()  # CERT_REQUIRED and hostname checks.
    secured = context.wrap_socket(connection, server_hostname=host)
    return True
  except OSError:
    return False
  finally:
    for stream in (secured, connection):
      if stream is not None:
        with suppress(OSError):
          stream.close()


def _run_probe(should_continue):
  # Lazy import keeps the worker's probe entry path free of process-runner cycles.
  from .isolated import run_isolated
  return run_isolated('probe', timeout_s=5, should_continue=should_continue)


class ConnectivityProbe:
  def __call__(self, *, should_continue=lambda: True):
    if not should_continue():
      raise AcquisitionCancelled('connectivity probe cancelled')
    try:
      result = _run_probe(should_continue)
    except NativeTransportError as error:
      if not should_continue():
        raise AcquisitionCancelled('connectivity probe cancelled') from None
      if error.category in ('network_error', 'timeout', 'worker_timeout', 'worker_failed', 'invalid_response'):
        return False
      raise
    if not should_continue():
      raise AcquisitionCancelled('connectivity probe cancelled')
    return isinstance(result, dict) and result.get('ok') is True and result.get('online') is True
