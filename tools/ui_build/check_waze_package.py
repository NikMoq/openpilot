#!/usr/bin/env python3
"""Check that the prebuilt Waze package matches its native source inputs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'tools/ui_build/waze_package.json'
ARTIFACTS = ('selfdrive/ui/ui', 'common/params_pyx.so', 'selfdrive/ui/translations/main_ru.qm')
KEYS = tuple('SpeedCamera' + provider + suffix for provider in ('Waze', 'Yandex')
             for suffix in ('Enabled', 'Status', 'Count', 'Error', 'UpdatedAt'))
SOURCES = ('common/params_keys.h', 'common/params.cc', 'common/params_pyx.pyx', 'common/params_pyx.cpp',
           'selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/speed_limit_settings.cc',
           'selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/speed_limit_settings.h',
           'selfdrive/ui/translations/main_ru.ts')
ICBM_FILES = ('selfdrive/pandad/pq_icbm_release.py', 'selfdrive/pandad/pq_icbm_release.json',
              'selfdrive/pandad/pandad', 'panda/board/obj/panda.bin.signed')


def digest(data):
  return hashlib.sha256(data).hexdigest()


def validate(root=ROOT):
  root = Path(root)
  manifest = json.loads((root / MANIFEST).read_text(encoding='utf-8'))
  if manifest.get('schema_version') != 1 or set(manifest.get('artifacts', {})) != set(ARTIFACTS):
    raise ValueError('Incomplete prebuilt Waze package manifest')
  if (manifest.get('architecture') != 'AArch64' or manifest.get('python_abi') != 'CPython 3.12' or
      not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_commit', ''))):
    raise ValueError('Invalid native build architecture, Python ABI or source revision')
  if set(manifest.get('source_sha256', {})) != set(SOURCES):
    raise ValueError('Incomplete native source provenance')
  for path, expected in manifest['source_sha256'].items():
    # Git can check text out as CRLF on Windows; the Linux build uses LF blobs.
    if digest((root / path).read_bytes().replace(b'\r\n', b'\n')) != expected:
      raise ValueError('Native source changed without rebuilding the Waze package: ' + path)
  for path, expected in manifest['artifacts'].items():
    data = (root / path).read_bytes()
    if digest(data) != expected:
      raise ValueError('Packaged artifact does not match its build record: ' + path)
    if path.endswith('.qm'):
      if data[:16].hex() != '3cb86418caef9c95cd211cbf60a1bddd':
        raise ValueError('Invalid compiled Qt translation')
      continue
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
      raise ValueError('Packaged native artifact is not ARM64: ' + path)
    if any(key.encode() not in data for key in KEYS):
      raise ValueError('Packaged native artifact lacks the Waze parameter registry: ' + path)
    if path == 'selfdrive/ui/ui' and any(text not in data for text in (b'Custom database URL', b'Yandex Navigator', b'Yandex Status', b'Speed cameras, mobile cameras, and police posts.')):
      raise ValueError('Packaged UI lacks the provider controls')
  # Use the real read-only preflight, not a duplicate implementation or a
  # relaxed hash check. A Params rebuild must remain a matched ICBM release.
  icbm_manifest = json.loads((root / ICBM_FILES[1]).read_text(encoding='utf-8'))
  if not isinstance(icbm_manifest.get('params_pyx_sha256'), str):
    raise ValueError('Waze ICBM release must retain the Params extension pin')
  spec = importlib.util.spec_from_file_location('waze_icbm_release_check', root / ICBM_FILES[0])
  release = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(release)
  release.verified_signature(root)
  return dict(status='PASS', architecture='AArch64', artifacts=len(ARTIFACTS), native_sources=len(SOURCES), icbm_preflight='PASS')


if __name__ == '__main__':
  print(json.dumps(validate(), sort_keys=True))
