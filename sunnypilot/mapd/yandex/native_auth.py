"""SDK-compatible request signature primitive; no embedded credentials/network.

The input formula was checked with the current SDK's ARM64 builder in a Linux
instruction emulator using synthetic strings and standard-string helper models.
This is not proof of server authorization. Callers supply their private signing
material; the owner's MapKit API key is a separate HTTP credential.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit


def request_signature(method: str, prepared_url: str, body: bytes, *, user_agent: str, signing_key: bytes | bytearray) -> str:
  """Sign the exact prepared wire URL/body, preserving percent escapes and order.

  No query sorting or URL re-encoding occurs here. The SDK excludes authority
  from its signed message. Require a slash in the prepared URL, avoiding its
  ambiguous pathless URL handling. No credentials or signatures are logged.
  """
  if method not in ('GET', 'POST'):
    raise ValueError('unsupported request method')
  if not isinstance(signing_key, (bytes, bytearray)) or len(signing_key) != 16:
    raise ValueError('invalid signing key shape')
  if not isinstance(body, bytes) or len(body) > 1024 * 1024:
    raise ValueError('invalid request body')
  if not isinstance(user_agent, str) or not 1 <= len(user_agent) <= 512 or any(ord(c) < 32 or ord(c) == 127 for c in user_agent):
    raise ValueError('invalid User-Agent')
  if not isinstance(prepared_url, str) or not 1 <= len(prepared_url) <= 8192 or not prepared_url.isascii():
    raise ValueError('invalid prepared URL')
  if any(ord(c) <= 32 or ord(c) == 127 for c in prepared_url):
    raise ValueError('invalid prepared URL')
  parsed = urlsplit(prepared_url)
  if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None
      or not parsed.path.startswith('/') or '#' in prepared_url):
    raise ValueError('invalid prepared URL')
  path_query = parsed.path + ('?' + parsed.query if parsed.query or prepared_url.endswith('?') else '')
  message = user_agent.encode('utf-8') + method.encode('ascii') + b' ' + path_query.encode('ascii') + body
  return hmac.new(signing_key, message, hashlib.sha1).hexdigest()
