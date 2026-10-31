#!/usr/bin/env python3
"""Build disposable Linux QA dependencies exclusively from current cam source.

No hardware, generated application libraries, external checkouts, or prior objects
are inputs. Install the standard SDKs in Dockerfile.camera-runtime-deps first.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[2]


def run(command, cwd=None):
  subprocess.run([str(arg) for arg in command], cwd=cwd, check=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True, help='new or empty disposable build directory')
  args = parser.parse_args()
  output = args.output.resolve()
  if sys.platform != 'linux' or sys.version_info[:2] != (3, 12) or Path('/TICI').exists():
    parser.error('requires a disposable Linux CPython3.12 PC environment')
  if output == ROOT or ROOT in output.parents:
    parser.error('keep all generated QA objects outside the source checkout')
  if output.exists() and any(output.iterdir()):
    parser.error('output must be new or empty; prior objects are never reused')
  output.mkdir(parents=True, exist_ok=True)
  modules = output / 'modules'
  common = modules / 'openpilot/common'
  common.mkdir(parents=True)
  (common.parent / '__init__.py').write_text(f'__path__.append({str(ROOT)!r})\n')
  for source in (ROOT / 'common').iterdir():
    if source.name != '__pycache__' and source.suffix not in ('.so', '.pyc') and source.name != 'transformations':
      (common / source.name).symlink_to(source, target_is_directory=source.is_dir())
  cereal = modules / 'cereal'
  cereal.mkdir()
  for source in (ROOT / 'cereal').iterdir():
    target = cereal / source.name
    if source.suffix == '.capnp':
      original = ROOT / 'opendbc_repo/opendbc/car/car.capnp' if source.name == 'car.capnp' else source
      shutil.copyfile(original, target)
    elif source.name != '__pycache__' and source.suffix not in ('.so', '.pyc'):
      target.symlink_to(source, target_is_directory=source.is_dir())

  # Re-Cythonize actual Params and rebuild the C++ typed registry/defaults.
  params_cpp = output / 'params_pyx.cpp'
  run([sys.executable, '-m', 'cython', '--cplus', '-3', '-I', ROOT,
       ROOT / 'common/params_pyx.pyx', '-o', params_cpp])
  params_output = output / 'params'
  run([sys.executable, ROOT / 'tools/ui_build/build_params_binding.py', '--native',
       '--compiler', 'clang++', '--python-include', sysconfig.get_path('include'),
       '--generated-cpp', params_cpp, '--output', params_output])
  shutil.copyfile(params_output / 'candidate/common/params_pyx.so', common / 'params_pyx.so')

  msgq_build = output / 'msgq-build'
  msgq = msgq_build / 'msgq'
  msgq.mkdir(parents=True)
  for name in ('ipc_pyx.pyx', 'ipc.pxd', '__init__.py'):
    shutil.copyfile(ROOT / 'msgq_repo/msgq' / name, msgq / name)
  sources = [str(msgq / 'ipc_pyx.pyx')] + [str(ROOT / 'msgq_repo/msgq' / (name + '.cc'))
             for name in ('ipc', 'impl_msgq', 'impl_zmq', 'impl_fake', 'msgq', 'event')]
  (msgq_build / 'setup.py').write_text(
    'from setuptools import setup,Extension\nfrom Cython.Build import cythonize\n'
    f'extension=Extension("msgq.ipc_pyx",sources={sources!r},include_dirs={[str(ROOT / "msgq_repo")]!r},'
    'libraries=["zmq"],extra_compile_args=["-std=c++17","-O1"])\n'
    'setup(name="cam-current-msgq",ext_modules=cythonize([extension],compiler_directives={"language_level":3}))\n')
  run([sys.executable, 'setup.py', 'build_ext', '--inplace'], cwd=msgq_build)

  # Required by real calibration/events imports in the original SLA tests.
  transforms = common / 'transformations'
  transforms.mkdir()
  for source in (ROOT / 'common/transformations').iterdir():
    if source.name in ('__pycache__', 'transformations.cpp') or source.suffix in ('.so', '.pyc'):
      continue
    if source.suffix in ('.pyx', '.pxd'):
      shutil.copyfile(source, transforms / source.name)
    else:
      (transforms / source.name).symlink_to(source, target_is_directory=source.is_dir())
  transforms_build = output / 'transforms-build'
  transforms_build.mkdir()
  (transforms_build / 'setup.py').write_text(
    'from setuptools import setup,Extension\nfrom Cython.Build import cythonize\nimport numpy\n'
    f'extension=Extension("openpilot.common.transformations.transformations",{[str(transforms / "transformations.pyx")]!r},'
    f'include_dirs={[str(ROOT), str(ROOT / "common/transformations")]!r}+[numpy.get_include()],'
    'extra_compile_args=["-std=c++17","-O1"])\n'
    f'setup(name="cam-current-transforms",ext_modules=cythonize([extension],include_path={[str(modules)]!r},'
    'compiler_directives={"language_level":3}))\n')
  run([sys.executable, 'setup.py', 'build_ext', '--build-lib', modules], cwd=transforms_build)

  pythonpath = os.pathsep.join(map(str, (modules, ROOT, ROOT / 'opendbc_repo', msgq_build, ROOT / 'tools/tests')))
  runner = output / 'run_checks.py'
  runner.write_text(
    'import os,sys\n'
    f'os.environ["PYTHONPATH"]={pythonpath!r}\n'
    f'sys.path[:0]={pythonpath.split(os.pathsep)!r}\n'
    'assert os.environ.get("PARAMS_ROOT")=="/data/params"\n'
    'assert os.environ.get("PQ35_RUNTIME_SMOKE")=="1"\n'
    'assert not any(key in os.environ for key in ("OPENPILOT_PREFIX","CEREAL_FAKE","ZMQ"))\n'
    'import cereal\nimport openpilot.common.params\nimport pytest\n'
    f'raise SystemExit(pytest.main(["-c",{str(ROOT / "tools/tests/camera_replay_pytest.ini")!r},'
    '"-p","no:cacheprovider","-p","core_speed_limit_compat",'
    f'{str(ROOT / "sunnypilot/selfdrive/controls/lib/speed_limit/tests")!r},'
    f'{str(ROOT / "tools/tests/test_camera_runtime_integration.py")!r},'
    f'{str(ROOT / "tools/tests/test_camera_override_runtime.py")!r},'
    f'{str(ROOT / "tools/tests/test_camera_reaction_runtime.py")!r},"-q","--tb=short"]))\n')
  binaries = [common / 'params_pyx.so', *msgq.glob('*.so'), *transforms.glob('*.so')]
  report = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest() for path in binaries}
  (output / 'native-modules.json').write_text(json.dumps(report, indent=2) + '\n')
  print(f'Fresh native runtime ready: {runner}')


if __name__ == '__main__':
  main()
