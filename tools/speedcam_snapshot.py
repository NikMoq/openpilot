#!/usr/bin/env python3
"""Produce and publish the unfiltered public SpeedCamOnline Rus BKM snapshot.

  python tools/speedcam_snapshot.py build --directory /tmp/speedcam-snapshot
  python tools/speedcam_snapshot.py verify --directory /tmp/speedcam-snapshot
  python tools/speedcam_snapshot.py publish --directory /tmp/speedcam-snapshot --repository OWNER/REPO

Only build contacts SCO, through camera_database.fetch_snapshot. There is no
source override or mirror fallback. Publish uses the installed gh CLI and its
normal GH_TOKEN environment; this module never reads or stores credentials.
The raw file is preserved byte for byte, including all types and regions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType


SOURCE_URL = 'https://speedcamonline.ru/cg77/Rus/comment/'
RELEASE_TAG = 'speedcamonline-rus'
MAX_BYTES = 64 * 1024 * 1024
MIN_RECORDS = 10_000
MAX_AGE_S = 7 * 24 * 60 * 60
CLOCK_SKEW_S = 300
MANIFEST_NAME = 'manifest.json'
_HEADERS = {b'2|Radars|1251', b'2|Radars|65001'}
_ROOT = Path(__file__).resolve().parents[1]


def _helpers():
  # Standalone checkout/Windows: load only the pure mapd helpers. Do not import
  # device/native package initializers or depend on generated openpilot links.
  for name, relative in (('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'),
                         ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')):
    if name not in sys.modules:
      module = ModuleType(name)
      module.__path__ = [str(_ROOT / relative)]
      sys.modules[name] = module
  from openpilot.sunnypilot.mapd.camera_database import fetch_snapshot
  from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot
  return fetch_snapshot, parse_cityguide_snapshot


def _unix_now() -> int:
  return int(datetime.now(UTC).timestamp())


def validate_body(body: bytes) -> int:
  if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
    raise ValueError('BKM must contain 1..64 MiB of plain bytes')
  first_line = body.removeprefix(b'\xef\xbb\xbf').split(b'\n', 1)[0].removesuffix(b'\r')
  if first_line not in _HEADERS:
    raise ValueError('unrecognized CityGuide Radars header')
  _, parse = _helpers()
  cameras = parse(body)  # Strict parser: any malformed row aborts, no filtering.
  count = len(cameras)
  nonempty_rows = sum(bool(line.strip()) for line in body.split(b'\n')) - 1
  if count != nonempty_rows:
    raise ValueError('parser did not retain every BKM record')
  if count < MIN_RECORDS:
    raise ValueError(f'Rus export has {count} records; requires at least {MIN_RECORDS}')
  return count


def _read_bounded(path: Path, limit: int = MAX_BYTES) -> bytes:
  with path.open('rb') as stream:
    body = stream.read(limit + 1)
  if len(body) > limit:
    raise ValueError(f'{path.name} exceeds size limit')
  return body


def _atomic_write(path: Path, body: bytes) -> None:
  fd, temporary = tempfile.mkstemp(prefix='.speedcam-', dir=path.parent)
  try:
    with os.fdopen(fd, 'wb') as stream:
      stream.write(body)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)


def _immutable_write(path: Path, body: bytes) -> None:
  try:
    stream = path.open('xb')
  except FileExistsError:
    if _read_bounded(path) != body:
      raise ValueError('existing immutable BKM differs from its hash-named content') from None
    return
  try:
    with stream:
      stream.write(body)
      stream.flush()
      os.fsync(stream.fileno())
  except BaseException:
    path.unlink(missing_ok=True)  # Only the incomplete file created by this call.
    raise


def build_snapshot(directory: Path, timeout_s: float = 120) -> dict:
  fetch, _ = _helpers()
  body = fetch(SOURCE_URL, timeout_s=timeout_s, max_bytes=MAX_BYTES)
  fetched_at = _unix_now()
  count = validate_body(body)
  digest = hashlib.sha256(body).hexdigest()
  manifest = {
    'schema_version': 1, 'source_url': SOURCE_URL, 'fetched_at': fetched_at,
    'generated_at': max(fetched_at, _unix_now()), 'record_count': count,
    'sha256': digest, 'size_bytes': len(body), 'asset_name': f'speedcam-rus-{digest}.bkm',
  }
  # No local artifact (or release) changes until the entire source is validated.
  directory.mkdir(parents=True, exist_ok=True)
  _immutable_write(directory / manifest['asset_name'], body)
  _atomic_write(directory / MANIFEST_NAME, (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode())
  return manifest


def verify_snapshot(directory: Path) -> tuple[dict, Path]:
  manifest = json.loads(_read_bounded(directory / MANIFEST_NAME, 16 * 1024))
  if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
    raise ValueError('unsupported manifest schema')
  if manifest.get('source_url') != SOURCE_URL:
    raise ValueError('manifest source must be the fixed SCO export')
  for key in ('fetched_at', 'generated_at', 'record_count', 'size_bytes'):
    if type(manifest.get(key)) is not int or manifest[key] < 0:
      raise ValueError(f'invalid manifest {key}')
  if manifest['generated_at'] < manifest['fetched_at']:
    raise ValueError('manifest generation predates fetch')
  now = _unix_now()
  if not now - MAX_AGE_S <= manifest['fetched_at'] <= now + CLOCK_SKEW_S:
    raise ValueError('manifest fetched_at is stale or in the future')
  if manifest['generated_at'] > now + CLOCK_SKEW_S:
    raise ValueError('manifest generated_at is in the future')
  digest = manifest.get('sha256')
  if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
    raise ValueError('invalid manifest SHA-256')
  if manifest.get('asset_name') != f'speedcam-rus-{digest}.bkm':
    raise ValueError('manifest asset name must contain the full SHA-256')
  asset = directory / manifest['asset_name']
  body = _read_bounded(asset)
  if len(body) != manifest['size_bytes'] or hashlib.sha256(body).hexdigest() != digest:
    raise ValueError('BKM size or checksum mismatch')
  if validate_body(body) != manifest['record_count']:
    raise ValueError('BKM record count mismatch')
  return manifest, asset


class GitHubRelease:
  """Small gh adapter. Only the one release and explicitly named assets change."""

  def __init__(self, repository: str):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
      raise ValueError('repository must be OWNER/REPO')
    self.repository = repository
    self.base = f'repos/{repository}/releases'
    self.release_id = None

  @staticmethod
  def _run(*args: str) -> bytes:
    return subprocess.run(['gh', *args], check=True, capture_output=True, timeout=120).stdout

  def ensure(self) -> None:
    try:
      release = json.loads(self._run('api', f'{self.base}/tags/{RELEASE_TAG}'))
    except subprocess.CalledProcessError as error:
      if b'(HTTP 404)' not in (error.stderr or b''):
        raise
      self._run('release', 'create', RELEASE_TAG, '--repo', self.repository, '--target', 'dev', '--latest=false',
                '--title', 'SpeedCamOnline Rus', '--notes', f'Unfiltered public Rus export. Source: {SOURCE_URL}')
      release = json.loads(self._run('api', f'{self.base}/tags/{RELEASE_TAG}'))
    if release.get('draft') or release.get('prerelease'):
      raise ValueError('snapshot release must be an existing public stable release')
    self.release_id = release['id']
    self._run('release', 'edit', RELEASE_TAG, '--repo', self.repository, '--latest=false')

  def assets(self) -> list[dict]:
    pages = json.loads(self._run('api', '--paginate', '--slurp', f'{self.base}/{self.release_id}/assets?per_page=100'))
    return [asset for page in pages for asset in page]

  def upload(self, path: Path) -> None:
    # Never --clobber: hash-named BKM assets are immutable, staging names unique.
    self._run('release', 'upload', RELEASE_TAG, str(path), '--repo', self.repository)

  def download(self, asset: dict, directory: Path) -> Path:
    self._run('release', 'download', RELEASE_TAG, '--repo', self.repository,
              '--pattern', asset['name'], '--dir', str(directory))
    return directory / asset['name']

  def rename(self, asset_id: int, name: str) -> None:
    self._run('api', '--method', 'PATCH', f'{self.base}/assets/{asset_id}', '-f', f'name={name}')


def _asset_named(assets: list[dict], name: str) -> dict | None:
  matches = [asset for asset in assets if asset.get('name') == name]
  if len(matches) > 1:
    raise ValueError(f'duplicate release asset: {name}')
  return matches[0] if matches else None


def _verify_remote(release, asset: dict | None, expected: bytes) -> None:
  if asset is None or asset.get('state') != 'uploaded' or asset.get('size') != len(expected):
    raise ValueError('release asset missing, incomplete or wrong size')
  with tempfile.TemporaryDirectory(prefix='speedcam-verify-') as temporary:
    body = _read_bounded(release.download(asset, Path(temporary)), len(expected))
  if hashlib.sha256(body).digest() != hashlib.sha256(expected).digest():
    raise ValueError('release asset checksum mismatch')


def publish_snapshot(directory: Path, repository: str, *, release=None) -> dict:
  # Revalidate immediately before any gh call; stale/tampered outputs cannot publish.
  manifest, bkm = verify_snapshot(directory)
  raw = _read_bounded(bkm)
  manifest_body = _read_bounded(directory / MANIFEST_NAME, 16 * 1024)
  if (len(raw) != manifest['size_bytes'] or hashlib.sha256(raw).hexdigest() != manifest['sha256']
      or json.loads(manifest_body) != manifest):
    raise ValueError('snapshot changed after verification')
  release = release if release is not None else GitHubRelease(repository)
  release.ensure()
  assets = release.assets()
  bkm_asset = _asset_named(assets, bkm.name)
  if bkm_asset is None:
    release.upload(bkm)
    bkm_asset = _asset_named(release.assets(), bkm.name)
  _verify_remote(release, bkm_asset, raw)

  # Upload and verify the candidate manifest before touching the public manifest.
  # GitHub has no atomic asset replacement. Keep the previous manifest as a
  # uniquely named backup, and restore its name if the final rename fails.
  suffix = uuid.uuid4().hex
  with tempfile.TemporaryDirectory(prefix='speedcam-manifest-') as temporary:
    staged_path = Path(temporary) / f'manifest.next-{suffix}.json'
    staged_path.write_bytes(manifest_body)
    release.upload(staged_path)
    staged = _asset_named(release.assets(), staged_path.name)
    _verify_remote(release, staged, manifest_body)
  old = _asset_named(release.assets(), MANIFEST_NAME)
  try:
    if old is not None:
      release.rename(old['id'], f'manifest.previous-{suffix}.json')
    release.rename(staged['id'], MANIFEST_NAME)  # Publication is the last mutation.
  except Exception:
    # A timeout can mean the rename succeeded but its acknowledgement was lost.
    current = _asset_named(release.assets(), MANIFEST_NAME)
    if current is not None and current['id'] == staged['id']:
      return manifest
    if current is None and old is not None:
      release.rename(old['id'], MANIFEST_NAME)
    raise
  return manifest


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  build = commands.add_parser('build', help='Fetch the fixed SCO source; validate before writing')
  build.add_argument('--directory', type=Path, required=True)
  build.add_argument('--timeout', type=float, default=120)
  verify = commands.add_parser('verify', help='Offline validation of BKM and manifest')
  verify.add_argument('--directory', type=Path, required=True)
  publish = commands.add_parser('publish', help='Verify and publish into the existing repository')
  publish.add_argument('--directory', type=Path, required=True)
  publish.add_argument('--repository', required=True)
  args = parser.parse_args(argv)
  try:
    if args.command == 'build':
      result = build_snapshot(args.directory, args.timeout)
    elif args.command == 'verify':
      result, _ = verify_snapshot(args.directory)
    else:
      result = publish_snapshot(args.directory, args.repository)
  except (OSError, ValueError, subprocess.SubprocessError) as error:
    print(f'speedcam snapshot failed: {error}', file=sys.stderr)
    return 1
  print(json.dumps(result, sort_keys=True))
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
