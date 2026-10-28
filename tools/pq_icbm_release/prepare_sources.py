"""Create fresh unsigned build input from pinned source and stable safety."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

PANDA_BASE = "1020d355584265391eb3acb556e4353b581fa9c0"
STABLE_BASE = "3b9179406c4a43319af3383157fc83e1098cba4d"


def git(repository, *arguments):
  return subprocess.check_output(["git", "-c", "safe.directory=" + str(repository.resolve()), "-C", str(repository), *arguments])


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--panda", required=True, type=Path)
  parser.add_argument("--port", required=True, type=Path)
  parser.add_argument("--output", required=True, type=Path)
  args = parser.parse_args()
  if args.output.exists():
    raise SystemExit("Output must be NEW, to exclude stale sources/artifacts")
  package = Path(__file__).resolve().parent
  safety_root = args.port / "opendbc_repo"
  safety = []
  for path in (safety_root / "opendbc/safety").rglob("*"):
    if not path.is_file() or path.suffix not in (".h", ".c"):
      continue
    relative = path.relative_to(safety_root)
    data = path.read_bytes()
    baseline = git(args.port, "show", STABLE_BASE + ":opendbc_repo/" + relative.as_posix())
    if data.replace(b"\r\n", b"\n") != baseline.replace(b"\r\n", b"\n"):
      raise SystemExit("Stable safety changed: " + str(relative))
    safety.append((relative, data.replace(b"\r\n", b"\n")))
  # Inspect names only; never archive/open vendor certificate or key material.
  candidates = git(args.panda, "ls-tree", "-r", "--name-only", PANDA_BASE,
                   "--", "board", "python/__init__.py", "LICENSE").decode("utf-8").splitlines()
  allowed = []
  for name in candidates:
    path = Path(name)
    source_file = (name.startswith("board/") and path.suffix in (".c", ".h", ".s", ".S", ".ld", ".inc"))
    if source_file or name in ("python/__init__.py", "LICENSE"):
      # Defensive denylist applies before Git reads any selected file content.
      if any(part.lower() in ("certs", "keys", "secrets", "obj") for part in path.parts):
        continue
      if any(word in path.name.lower() for word in ("private", ".pem", ".key", ".bin", ".signed")):
        continue
      allowed.append(name)
  if not allowed:
    raise SystemExit("No allowed pinned source files")
  archive = git(args.panda, "archive", PANDA_BASE, "--", *allowed)
  source = args.output / "panda"
  source.mkdir(parents=True)
  with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    tar.extractall(source, filter="data")
  # A new Git root prevents apply from resolving the outer project repository.
  git(source, "init", "-q")
  git(source, "apply", str(package / "panda-10.2.0.patch"))
  for name in ("gra_reference_adapter.h", "gra_transform.c", "gra_transform.h"):
    (source / "board" / name).write_bytes((package / name).read_bytes().replace(b"\r\n", b"\n"))
  obj = source / "board/obj"
  obj.mkdir(exist_ok=True)
  (obj / "gitversion.h").write_bytes(b'const uint8_t gitversion[] = "OFFLINE-1020-ICBM300";\n')
  for relative, data in safety:
    target = args.output / "safety" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
  inputs = {}
  for folder in (source, args.output / "safety"):
    for path in folder.rglob("*"):
      if path.is_file() and ".git" not in path.relative_to(folder).parts:
        inputs[path.relative_to(args.output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
  (args.output / "inputs.json").write_text(json.dumps({"panda_base": PANDA_BASE,
    "safety_base": STABLE_BASE, "sha256": inputs}, indent=2))


if __name__ == "__main__":
  main()
