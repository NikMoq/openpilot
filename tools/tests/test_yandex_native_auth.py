"""Public synthetic key/vectors only; no SDK credentials or live requests."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('yandex_native_auth_test', Path(__file__).resolve().parents[2] /
                                           'sunnypilot/mapd/yandex/native_auth.py')
auth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auth)

SYNTHETIC_KEY = b'0123456789abcdef'


def sign(url='https://example.invalid/route?a=1%2C2&b=3', method='GET', body=b'', **overrides):
  return auth.request_signature(method, url, body, **dict(user_agent='synthetic-agent', signing_key=SYNTHETIC_KEY, **overrides))


def test_fixed_get_vector_checked_with_independent_crypto_implementation():
  assert sign() == '86eac17ca42a51245625198b25a49eb8c353f85d'


def test_fixed_post_vector_covers_exact_binary_body():
  assert sign('https://example.invalid/conditions?segment=1', 'POST', bytes.fromhex('18012200')) == \
    'b97945158d2180ef09cb6449359fb4fe7fec59e4'


def test_authority_excluded_but_exact_query_encoding_and_order_retained():
  assert sign('https://other.invalid:8443/route?a=1%2C2&b=3') == sign()
  assert sign('https://example.invalid/route?a=1,2&b=3') != sign()
  assert sign('https://example.invalid/route?b=3&a=1%2C2') != sign()
  assert sign('https://example.invalid/route?a=1%2c2&b=3') != sign()


def test_empty_query_marker_and_body_are_not_silently_removed():
  assert sign('https://example.invalid/route?') != sign('https://example.invalid/route')
  assert sign(body=b'\x00') != sign()


@pytest.mark.parametrize('url', ['http://example.invalid/route', 'https://example.invalid', 'https://user:password@example.invalid/r',
                                 'https://example.invalid/r#fragment', 'https://example.invalid/r#', '/relative',
                                 'https://example.invalid/a b', 'https://example.invalid/a\n', 'https://example.invalid/я'])
def test_unprepared_or_unsafe_urls_rejected(url):
  with pytest.raises(ValueError):
    sign(url)


@pytest.mark.parametrize('method', ['get', 'PUT', '', None])
def test_only_proven_methods_accepted(method):
  with pytest.raises(ValueError):
    sign(method=method)


@pytest.mark.parametrize('key', [b'', b'short', b'x' * 17, '0123456789abcdef'])
def test_wrong_key_shape_rejected_without_exposing_it(key):
  with pytest.raises(ValueError, match='key shape'):
    auth.request_signature('GET', 'https://example.invalid/r', b'', user_agent='synthetic-agent', signing_key=key)


def test_no_header_injection_and_bounded_inputs():
  for agent in ('', 'agent\r\nInjected: x', 'x' * 513):
    with pytest.raises(ValueError, match='User-Agent'):
      auth.request_signature('GET', 'https://example.invalid/r', b'', user_agent=agent, signing_key=SYNTHETIC_KEY)
  with pytest.raises(ValueError, match='body'):
    sign(body=b'x' * (1024 * 1024 + 1))
  with pytest.raises(ValueError, match='body'):
    sign(body='text')
