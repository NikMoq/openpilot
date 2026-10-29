"""Assemble an offline runtime overlay; never install or contact a device.

Both native artifacts must match their build manifests and this source tree.
No Panda image, provider database, SDK library or private material is included.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
BASE = '5e049f491ac36a9e6efb9ee31e4871b0f1408a32'
NATIVE_INPUTS = {
  'selfdrive/ui/ui': 'c7e674a43cb07fda5f0de20fe72fae2027645bee2d0fe95a999f2fc5e7ff0ba6',
  'common/params_pyx.so': '667cb07f118989b8d09a0699008b071a79530bbaca8348ee73f08aed9c0e5ef7',
}


def sha(path):
  return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
  return subprocess.check_output(['git', '-c', 'core.quotePath=false', *args], cwd=ROOT, text=True).strip()


def check_artifact(path, manifest):
  header = path.read_bytes()[:20]
  if len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 183:
    raise ValueError(f'expected an AArch64 ELF: {path}')
  if sha(path) != manifest['sha256']:
    raise ValueError(f'artifact hash differs from build manifest: {path}')


def check_current_source(relative, expected):
  path = (ROOT / relative).resolve()
  if not path.is_relative_to(ROOT) or not path.is_file() or sha(path) != expected:
    raise ValueError(f'source changed since native build: {relative}')


def verify_build_inputs(ui, params):
  if ui.get('target') != 'device' or params.get('architecture') != 'AArch64' or params.get('python_abi') != 'CPython 3.12':
    raise ValueError('device UI and CPython 3.12 AArch64 Params manifests are required')
  for category in ('compiled_source_sha256', 'project_header_sha256'):
    for relative, digest in ui[category].items():
      if not relative.startswith('.native-build/'):
        check_current_source(relative, digest)
  for generated, digest in ui['schema_sha256'].items():
    schema = ROOT / 'cereal' / Path(generated).name
    content = schema.read_text()
    if len(content.strip().splitlines()) == 1 and content.strip().endswith('.capnp'):
      schema = (schema.parent / content.strip()).resolve()
    check_current_source(str(schema.relative_to(ROOT)), digest)
  for source, digest in params['sources'].items():
    if source.startswith('/cam/'):
      check_current_source(source[len('/cam/'):], digest)


def check_ui_abi(report, manifest):
  if (report.get('candidate_sha256') != manifest['sha256'] or
      report.get('sdk_dependency_closure_complete') is not True or report.get('missing') != []):
    raise ValueError('matching complete UI SDK dependency report is required')


def runtime_files():
  changed = git('diff', '--name-only', BASE).splitlines()
  added = git('ls-files', '--others', '--exclude-standard').splitlines()
  return sorted({p for p in changed + added if p.startswith(('cereal/', 'common/', 'selfdrive/', 'sunnypilot/', 'system/'))
                 and p.endswith(('.py', '.capnp')) and '/tests/' not in p and not p.startswith('selfdrive/test/')})


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--ui', type=Path, required=True)
  parser.add_argument('--ui-manifest', type=Path, required=True)
  parser.add_argument('--ui-abi', type=Path, required=True, help='Matching SDK dependency report; not device acceptance evidence')
  parser.add_argument('--params', type=Path, required=True)
  parser.add_argument('--params-manifest', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True, help='New output directory; must not exist')
  args = parser.parse_args()
  if git('branch', '--show-current') != 'cam':
    raise ValueError('assemble only from the cam branch')
  subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=ROOT, check=True)
  ui = json.loads(args.ui_manifest.read_text())
  params = json.loads(args.params_manifest.read_text())
  check_ui_abi(json.loads(args.ui_abi.read_text()), ui)
  check_artifact(args.ui, ui)
  check_artifact(args.params, params)
  verify_build_inputs(ui, params)
  for relative, digest in NATIVE_INPUTS.items():
    check_current_source(relative, digest)  # Original tracked artifacts remain the rollback base.
  inputs = {p: ROOT / p for p in runtime_files()}
  inputs.update({'selfdrive/ui/ui': args.ui, 'common/params_pyx.so': args.params})
  input_hashes = {relative: sha(source) for relative, source in inputs.items()}
  if args.output.exists():
    raise ValueError('output already exists; choose a new directory')
  args.output.mkdir(parents=True)
  records = {}
  for relative, source in inputs.items():
    target = args.output / 'overlay' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha(target) != input_hashes[relative]:
      raise ValueError(f'input changed while copying: {relative}')
    records[relative] = {'sha256': sha(target), 'bytes': target.stat().st_size,
                         'mode': '0755' if relative == 'selfdrive/ui/ui' else '0644'}
    if relative in NATIVE_INPUTS:
      records[relative]['expected_original_sha256'] = NATIVE_INPUTS[relative]
  if any(sha(source) != input_hashes[relative] for relative, source in inputs.items()):
    raise ValueError('source snapshot changed while assembling the candidate')
  provenance = args.output / 'provenance'
  provenance.mkdir()
  shutil.copyfile(args.ui_manifest, provenance / 'ui.json')
  shutil.copyfile(args.params_manifest, provenance / 'params.json')
  shutil.copyfile(args.ui_abi, provenance / 'ui-sdk-abi.json')
  manifest = {'status': 'OFFLINE_CANDIDATE_NOT_INSTALLED', 'base_commit': BASE,
              'source_head': git('rev-parse', 'HEAD'), 'source_worktree': git('status', '--porcelain=v1'),
              'files': records, 'requires': ['matching base checkout', 'local camera database', 'local road graph and acquisition manifest'],
              'hardware_validation': 'not performed', 'contains_panda_firmware': False}
  (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
  archive = args.output / 'camera-runtime-overlay.tar.gz'
  with tarfile.open(archive, 'w:gz') as out:
    for relative, record in records.items():
      target = args.output / 'overlay' / relative
      info = out.gettarinfo(str(target), arcname=relative)
      info.uid = info.gid = 0
      info.uname = info.gname = ''
      info.mode = int(record['mode'], 8)
      with target.open('rb') as data:
        out.addfile(info, data)
    for metadata in [args.output / 'manifest.json', *sorted(provenance.iterdir())]:
      name = 'manifest.json' if metadata.parent == args.output else 'provenance/' + metadata.name
      info = out.gettarinfo(str(metadata), arcname='__camera_candidate__/' + name)
      info.uid = info.gid = 0
      info.uname = info.gname = ''
      info.mode = 0o644
      with metadata.open('rb') as data:
        out.addfile(info, data)
  print(json.dumps({'output': str(args.output.resolve()), 'files': len(records), 'archive_sha256': sha(archive)}))


if __name__ == '__main__':
  main()
