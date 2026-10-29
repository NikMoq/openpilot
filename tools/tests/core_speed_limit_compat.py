"""Explicit test-only portability fixes for the original core SLA/resolver suite.

Load with -p core_speed_limit_compat in the disposable Linux runtime container.
Production modules, native Params, msgq, schemas and state machines are unchanged.
"""
import os
from pathlib import Path
import sys
import time

import pytest


def pytest_configure(config):
  if (sys.platform != 'linux' or os.getenv('PQ35_RUNTIME_SMOKE') != '1' or
      os.getenv('PARAMS_ROOT') != '/data/params' or Path('/TICI').exists()):
    raise pytest.UsageError('core compatibility runner requires explicit disposable Linux runtime opt-in')


def pytest_collection_modifyitems(items):
  # Windows may materialize the tracked opendbc symlink as a plain text file.
  # Resolve only its original source-data target; no interface behavior is stubbed.
  helpers = sys.modules.get('openpilot.sunnypilot.selfdrive.controls.lib.nnlc.helpers')
  if helpers is not None and not Path(helpers.TORQUE_NN_MODEL_SUBSTITUTE_PATH).is_file():
    source = Path(__file__).resolve().parents[2] / 'opendbc_repo/opendbc/car/torque_data/substitute.toml'
    if source.is_file():
      helpers.TORQUE_NN_MODEL_SUBSTITUTE_PATH = str(source)
  seen = set()
  for item in items:
    module = item.module
    if module in seen or not hasattr(module, 'setup_sm_mock'):
      continue
    seen.add(module)
    original = module.setup_sm_mock

    def compatible(mocker, _original=original):
      sm = _original(mocker)
      # The original mock wrote monotonic seconds into a Unix timestamp field.
      # Correct that fresh-fixture epoch after the production Unix-age fix.
      sm['gpsLocation'].unixTimestampMillis = time.time() * 1000
      return sm

    module.setup_sm_mock = compatible
