"""Private child entry point. Stdout is a bounded, sanitized JSON protocol."""
import argparse
import json
from pathlib import Path
import sys

# This file also runs directly under the device's Python interpreter. Native
# modules use relative imports and do not require importing manager or cereal.
if not __package__:
  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
  from yandex.native_errors import NativeTransportError
else:
  from .native_errors import NativeTransportError

MAX_BYTES = 8 * 1024 * 1024 + 4096


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--mode', choices=('fetch', 'probe'), required=True)
  parser.add_argument('--credentials')
  args = parser.parse_args()
  try:
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
      raise NativeTransportError('invalid_request')
    request = json.loads(raw)
    if args.mode == 'probe':
      if request != {}:
        raise NativeTransportError('invalid_request')
      if not __package__:
        from yandex.connectivity import probe_yandex_endpoint
      else:
        from .connectivity import probe_yandex_endpoint
      result = {'ok': True, 'online': bool(probe_yandex_endpoint())}
    else:
      if not isinstance(request, dict) or set(request) != {'latitude', 'longitude', 'heading'} or not args.credentials:
        raise NativeTransportError('invalid_request')
      if not __package__:
        from yandex.native_provider import NativeYandexProvider
      else:
        from .native_provider import NativeYandexProvider
      window = NativeYandexProvider(args.credentials).fetch_window(request['latitude'], request['longitude'], request['heading'])
      result = {'ok': True, 'events': window.events, 'complete': window.complete,
                'errors': window.errors, 'network_failure': window.network_failure}
    data = json.dumps(result, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > MAX_BYTES:
      raise NativeTransportError('response_over_budget')
  except NativeTransportError as error:
    data = json.dumps({'ok': False, 'category': error.category, 'status': error.status}).encode()
  except TimeoutError:
    data = b'{"ok":false,"category":"timeout"}'
  except OSError:
    data = b'{"ok":false,"category":"network_error"}'
  except Exception:
    data = b'{"ok":false,"category":"invalid_response"}'
  sys.stdout.buffer.write(data)
  sys.stdout.buffer.flush()


if __name__ == '__main__':
  main()
