"""Stage hash-pinned text sources, then current port sources. No binaries."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".c++", ".h", ".hpp", ".s", ".sh", ".py", ".pyx", ".pxd",
                   ".capnp", ".md", ".yaml", ".yml", ".toml", ".json"}
SOURCE_NAMES = {"Dockerfile", "SConscript", "SConstruct", ".gitignore", ".dockerignore"}
CACHE_DIRS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache", "node_modules", "arm_obj"}


def source_path(relative):
  path = PurePosixPath(relative)
  if (not relative or "\\" in relative or ":" in relative or path.is_absolute()
      or any(part in {".", ".."} or part in CACHE_DIRS or part.startswith("arm_obj.") for part in relative.split("/"))
      or (path.suffix.lower() not in SOURCE_SUFFIXES and path.name not in SOURCE_NAMES)):
    raise ValueError(f"Not a permitted source path: {relative}")
  return Path(*path.parts)


def text_source(data, relative):
  if b"\0" in data:
    raise ValueError(f"Binary content in source: {relative}")
  try:
    data.decode("utf-8")
  except UnicodeDecodeError as error:
    raise ValueError(f"Non-text content in source: {relative}") from error
  return data


def stage_sources(foundation, port, output, manifest):
  foundation, port, output, manifest = map(Path, (foundation, port, output, manifest))
  if output.exists():
    raise ValueError("Output must be a new directory; stale objects are forbidden")
  inputs, pending = {}, []
  for line in manifest.read_text().splitlines():
    expected, relative = line.split(None, 1)
    relative = relative.strip()
    path = source_path(relative)
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or relative in inputs:
      raise ValueError(f"Invalid or duplicate foundation entry: {relative}")
    data = (foundation / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
      raise ValueError(f"Foundation hash mismatch: {relative}")
    text_source(data, relative)
    origin = "hash-pinned-foundation"
    if (port / path).is_file():
      data = (port / path).read_bytes()
      origin = "current-port"
    if relative == "cereal/car.capnp":
      data = (port / "opendbc_repo/opendbc/car/car.capnp").read_bytes()
      origin = "current-port-resolved-car-schema"
    text_source(data, relative)
    inputs[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "origin": origin,
                        "foundation_sha256": expected}
    pending.append((path, data))
  # Verify all sources, overlays and helper scripts before creating any output.
  for name in ("build_aarch64_pandad.sh", "test_native_icbm.sh"):
    pending.append((Path(name), text_source(Path(__file__).with_name(name).read_bytes(), name)))
  output.mkdir(parents=True)
  for path, data in pending:
    target = output / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
  (output / "staged_inputs.json").write_text(json.dumps(inputs, indent=2))


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--foundation", required=True, type=Path)
  parser.add_argument("--port", required=True, type=Path)
  parser.add_argument("--output", required=True, type=Path)
  args = parser.parse_args()
  stage_sources(args.foundation, args.port, args.output, Path(__file__).with_name("source_inputs.sha256"))


if __name__ == "__main__":
  main()
