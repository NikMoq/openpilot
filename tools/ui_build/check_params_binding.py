#!/usr/bin/env python3
"""Exercise a freshly built native Params registry and lifecycle persistence.

Only Python cloud logging is stubbed; storage, typed binding and clearAll are
the real Cython/C++ implementation. No device paths or persisted data are used.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('binding', type=Path)
  args = parser.parse_args()
  logger = ModuleType('openpilot.common.swaglog')
  logger.cloudlog = SimpleNamespace(warning=lambda *_: None)
  sys.modules['openpilot.common.swaglog'] = logger
  spec = importlib.util.spec_from_file_location('params_pyx', args.binding)
  binding = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(binding)
  values = {'SpeedCameraReactionDistance': 650,
            'SpeedCameraRoadGraphPath': '/offline/synthetic-road-graph.osm',
            'SpeedCameraRoadGraphStatus': 'ready:synthetic'}
  with tempfile.TemporaryDirectory(prefix='cam-params-check-') as temporary:
    os.environ['OPENPILOT_PREFIX'] = 'camera-params-qa'
    params = binding.Params(temporary)
    assert params.get('SpeedCameraReactionDistance', return_default=True) == 300
    assert params.get('SpeedCameraRoadGraphStatus', return_default=True) == 'missing'
    assert params.get_type('SpeedCameraReactionDistance') == binding.ParamKeyType.INT
    assert params.get_type('SpeedCameraRoadGraphPath') == binding.ParamKeyType.STRING
    assert params.get_type('SpeedCameraRoadGraphStatus') == binding.ParamKeyType.STRING
    yandex_values = {'SpeedCameraYandexEnabled': True,
                     'SpeedCameraYandexStatus': 'ready:2 warnings;0 verified limits',
                     'SpeedCameraYandexCount': '2', 'SpeedCameraYandexError': '',
                     'SpeedCameraYandexUpdatedAt': '123'}
    defaults = {'SpeedCameraYandexEnabled': False, 'SpeedCameraYandexStatus': 'disabled',
                'SpeedCameraYandexCount': '0', 'SpeedCameraYandexUpdatedAt': '0'}
    for key, default in defaults.items():
      assert params.get(key, return_default=True) == default
    for key in yandex_values:
      expected_type = binding.ParamKeyType.BOOL if key.endswith('Enabled') else binding.ParamKeyType.STRING
      assert params.check_key(key) and params.get_type(key) == expected_type
    backup = set(params.all_keys(binding.ParamKeyFlag.BACKUP))
    persistent = set(params.all_keys(binding.ParamKeyFlag.PERSISTENT))
    for key, value in values.items():
      assert params.check_key(key)
      assert key.encode() in backup and key.encode() in persistent
      params.put(key, value)
      assert params.get(key) == (None if value == '' else value)
    for key, value in yandex_values.items():
      assert key.encode() in persistent
      assert (key.encode() in backup) == key.endswith('Enabled')
      params.put(key, value)
      assert params.get(key) == (None if value == '' else value)
    values.update(yandex_values)
    # Unknown files are still removed: there is no cleanup bypass or whitelist.
    unknown = Path(params.get_param_path()) / 'UnknownCameraQaKey'
    unknown.write_text('remove-me')
    for flag in (binding.ParamKeyFlag.CLEAR_ON_MANAGER_START, binding.ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION,
                 binding.ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION, binding.ParamKeyFlag.CLEAR_ON_IGNITION_ON):
      params.clear_all(flag)
      for key, value in values.items():
        assert params.get(key) == (None if value == '' else value)
    assert not unknown.exists()
    del params
    reopened = binding.Params(temporary)
    for key, value in values.items():
      assert reopened.get(key) == (None if value == '' else value)
  print(json.dumps(dict(status='PASS', checks='typed registry, defaults, backup, four cleanup transitions, reopen, unknown removal',
                        binding_sha256=hashlib.sha256(args.binding.read_bytes()).hexdigest()), sort_keys=True))


if __name__ == '__main__':
  main()
