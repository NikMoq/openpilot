"""Bounded Linux HTTPS transport. Credentials live in an owner-private file."""
from __future__ import annotations

import base64
import json
import math
import os
import re
import stat
import time
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError, ReadTimeoutError

from .native_auth import request_signature

SDK_BUILD = '4.45.0-full'
DEFAULT_BASE_URL = 'https://proxy.mob.maps.yandex.net:443/mapkit2/driving/2.x/'
DEFAULT_USER_AGENT = 'org.sunnypilot.yandexprobe/0.1-diagnostic maps.mobile/2026081315.14-d31a76c2e2204db-210.5-full-public linux/4.9.103 (comma; Comma; ru_RU)'
MAX_RESPONSE_BYTES = 1024 * 1024
TOTAL_TIMEOUT_S = 45
_HOSTS = frozenset(('proxy.mob.maps.yandex.net', 'proxy.mob.maps.yandex-net.ru', 'proxy.mob.maps.yandexcom.net'))
_API_KEY = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')


class NativeTransportError(RuntimeError):
  def __init__(self, category, status=None):
    self.category, self.status = category, status
    super().__init__(category)  # Never export URL, server text, headers or credentials.


def load_private_credentials(path):
  """Read a regular owner-only Linux file without following symlinks or FIFOs."""
  try:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags)
    try:
      info = os.fstat(fd)
      if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
          or not 0 < info.st_size <= 4096):
        raise ValueError('private file required')
      raw = os.read(fd, 4097)
    finally:
      os.close(fd)
    if len(raw) > 4096:
      raise ValueError('credential size')
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or set(payload) != {'schema_version', 'sdk_build', 'api_key', 'signing_key_b64'}
        or type(payload['schema_version']) is not int or payload['schema_version'] != 1 or payload['sdk_build'] != SDK_BUILD):
      raise ValueError('credential schema')
    api_key = payload['api_key']
    encoded = payload['signing_key_b64']
    if not isinstance(api_key, str) or not _API_KEY.fullmatch(api_key) or not isinstance(encoded, str) or len(encoded) > 64:
      raise ValueError('credential shape')
    signing_key = base64.b64decode(encoded, validate=True)
    if len(signing_key) != 16:
      raise ValueError('signing key shape')
    return api_key, signing_key
  except (OSError, ValueError, TypeError, AttributeError):
    raise NativeTransportError('credentials_unavailable') from None


class NativeHTTPTransport:
  def __init__(self, api_key, signing_key, *, session=None, base_url=DEFAULT_BASE_URL,
               user_agent=DEFAULT_USER_AGENT, timeout=(6, 15), monotonic_clock=time.monotonic):
    if not isinstance(api_key, str) or not _API_KEY.fullmatch(api_key):
      raise NativeTransportError('credentials_unavailable')
    if not isinstance(signing_key, (bytes, bytearray)) or len(signing_key) != 16:
      raise NativeTransportError('credentials_unavailable')
    if (not isinstance(timeout, tuple) or len(timeout) != 2
        or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum
               for value, maximum in zip(timeout, (6, 15), strict=True))):
      raise NativeTransportError('invalid_timeout')
    try:
      parsed = urlsplit(base_url)
      if (parsed.scheme != 'https' or parsed.hostname not in _HOSTS or parsed.port not in (None, 443)
          or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
          or parsed.path != '/mapkit2/driving/2.x/'):
        raise ValueError('untrusted service')
      request_signature('GET', base_url, b'', user_agent=user_agent, signing_key=signing_key)
    except (TypeError, ValueError):
      raise NativeTransportError('invalid_service_configuration') from None
    self._api_key, self._signing_key = api_key, bytearray(signing_key)
    self._user_agent, self.base_url, self.timeout = user_agent, base_url, timeout
    self._monotonic = monotonic_clock
    self.session = session if session is not None else requests.Session()
    self.session.trust_env = False
    self._closed = False

  def __call__(self, method, resource, params, body):
    if self._closed:
      raise NativeTransportError('closed')
    expected = {'route': ('GET', {'rll', 'results', 'lang'}), 'conditions': ('POST', {'segment', 'lang'})}
    if resource not in expected or method != expected[resource][0] or not isinstance(params, dict):
      raise NativeTransportError('invalid_request')
    if (set(params) != expected[resource][1] or any(not isinstance(v, str) or len(v) > 512 for v in params.values())
        or not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES or (method == 'GET' and body)):
      raise NativeTransportError('invalid_request')
    headers = {'User-Agent': self._user_agent, 'X-YMapKit-Api-Key': self._api_key, 'Accept': '*/*'}
    if method == 'POST':
      headers['Content-Type'] = 'application/x-protobuf'
    request = self.session.prepare_request(requests.Request(method, self.base_url + resource,
                                                          params=sorted(params.items()), data=body, headers=headers))
    request.headers['X-YRuntime-Signature'] = request_signature(method, request.url, body,
                                                              user_agent=self._user_agent, signing_key=self._signing_key)
    response = None
    deadline = self._monotonic() + TOTAL_TIMEOUT_S

    def check_deadline():
      if self._monotonic() >= deadline:
        raise NativeTransportError('timeout')

    try:
      response = self.session.send(request, timeout=self.timeout, allow_redirects=False, stream=True, verify=True)
      check_deadline()
      if response.status_code != 200:
        raise NativeTransportError('http_error', response.status_code)
      content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
      if content_type != 'application/x-protobuf':
        raise NativeTransportError('unexpected_content_type')
      data = bytearray()
      raw = getattr(response, 'raw', None)
      read1 = getattr(raw, 'read1', None)

      def chunks():
        if callable(read1):
          while True:
            check_deadline()
            chunk = read1(16384, decode_content=True)
            check_deadline()
            if not chunk:
              return
            yield chunk
        else:
          # A buffered 16KiB read can wait indefinitely on a slow byte drip.
          yield from response.iter_content(1)

      for chunk in chunks():
        check_deadline()
        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
          raise NativeTransportError('response_over_budget')
        data.extend(chunk)
      return bytes(data)
    except (requests.RequestException, HTTPError) as error:
      category = 'timeout' if isinstance(error, (requests.Timeout, ReadTimeoutError)) else 'network_error'
      raise NativeTransportError(category) from None
    finally:
      request.headers.clear()
      headers.clear()
      if response is not None:
        response.close()

  def close(self):
    self._closed = True
    for index in range(len(self._signing_key)):
      self._signing_key[index] = 0
    self._api_key = ''
    self.session.close()
