"""Validate the shipped ICBM pair before the normal Panda update wrapper runs."""
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile

KEY = "PQ35IcbmExpectedPandaSignature"
FIRMWARE = "panda/board/obj/panda.bin.signed"
DAEMON = "selfdrive/pandad/pandad"


def verified_signature(root):
  root = Path(root)
  manifest = json.loads((root / "selfdrive/pandad/pq_icbm_release.json").read_text())
  if manifest["schema"] != 1 or set(manifest["files"]) != {FIRMWARE, DAEMON}:
    raise ValueError("Invalid ICBM release manifest")
  for name in (FIRMWARE, DAEMON):
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != manifest["files"][name]:
      raise ValueError(f"ICBM release mismatch: {name}")
  firmware = (root / FIRMWARE).read_bytes()
  if len(firmware) < 140 or struct.unpack_from("<I", firmware)[0] + 128 != len(firmware):
    raise ValueError("Invalid ICBM firmware length")
  signature = firmware[-128:].hex()
  if signature != manifest["panda_signature_hex"]:
    raise ValueError("ICBM signature identity mismatch")
  return signature


def prepare_pairing(root, params):
  # The shipped Python params extension may predate this key. Use its actual
  # namespace directory and atomic replacement, without changing its key table.
  # Called after manager startup/clearAll and before native pandad reads the key.
  directory = Path(params.get_param_path())
  target = directory / KEY
  try:
    signature = verified_signature(root)
  except Exception:
    target.unlink(missing_ok=True)
    raise
  fd, temporary = tempfile.mkstemp(prefix=".icbm-", dir=directory)
  try:
    with os.fdopen(fd, "w") as stream:
      stream.write(signature)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, target)
    if os.name == "posix":
      directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
      try:
        os.fsync(directory_fd)
      finally:
        os.close(directory_fd)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)
