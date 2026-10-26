#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import hashlib
import logging
import os
import stat
import time
import traceback
from pathlib import Path
from urllib.request import urlopen

import requests

from cereal import messaging
from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths
from openpilot.common.spinner import Spinner
from openpilot.system.version import is_prebuilt
from openpilot.sunnypilot.mapd import MAPD_PATH, MAPD_BIN_DIR
import openpilot.system.sentry as sentry

VERSION = "v1.12.0"
URL = f"https://github.com/pfeiferj/mapd/releases/download/{VERSION}/mapd"
EXPECTED_SHA256 = "fdb3b49ee19956e6ce09fdc3373cbba557f1263b2180e9f344c1d4053852284b"
DOWNLOAD_CONNECT_TIMEOUT = 10
DOWNLOAD_READ_TIMEOUT = 45
DOWNLOAD_ATTEMPTS = 5


def sha256_file(file_path: Path) -> str:
  digest = hashlib.sha256()
  with open(file_path, 'rb') as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b''):
      digest.update(chunk)
  return digest.hexdigest()


def is_verified_binary(file_path: Path | None = None) -> bool:
  file_path = Path(MAPD_PATH) if file_path is None else Path(file_path)
  try:
    return sha256_file(file_path) == EXPECTED_SHA256
  except OSError:
    return False


def update_installed_version(version: str, params: Params = None) -> bool:
  if params is None:
    params = Params()
  # mapd_manager calls this at startup; never let that stamp a version onto
  # a missing or different executable.
  if not is_verified_binary():
    params.remove("MapdVersion")
    logging.warning("Not recording MapdVersion: installed mapd does not match %s", VERSION)
    return False
  if version != VERSION:
    logging.warning("Not recording unexpected MapdVersion %s; expected %s", version, VERSION)
    return False

  params.put("MapdVersion", version)
  return True


class MapdInstallManager:
  def __init__(self, spinner_ref: Spinner):
    self._spinner = spinner_ref
    self._params = Params()

  def download(self) -> None:
    if not is_verified_binary():
      self._params.remove("MapdVersion")
    self.ensure_directories_exist()
    self._download_file()
    if not update_installed_version(VERSION, self._params):
      raise RuntimeError("Verified mapd binary was not installed; MapdVersion was not updated")

  def check_and_download(self) -> None:
    if self.download_needed():
      self.download()

  def download_needed(self) -> bool:
    if not is_verified_binary():
      return True
    if self.get_installed_version() != VERSION:
      update_installed_version(VERSION, self._params)
    return False

  @staticmethod
  def ensure_directories_exist() -> None:
    if not os.path.exists(Paths.mapd_root()):
      os.makedirs(Paths.mapd_root())
    if not os.path.exists(MAPD_BIN_DIR):
      os.makedirs(MAPD_BIN_DIR)

  def _download_file(self, num_retries: int | None = None) -> None:
    if num_retries is None:
      num_retries = DOWNLOAD_ATTEMPTS
    temp_file = Path(MAPD_PATH + ".tmp")
    last_error = None
    for cnt in range(num_retries):
      try:
        temp_file.unlink(missing_ok=True)
        digest = hashlib.sha256()
        with requests.get(URL, stream=True,
                          timeout=(DOWNLOAD_CONNECT_TIMEOUT, DOWNLOAD_READ_TIMEOUT)) as response:
          response.raise_for_status()
          with temp_file.open('wb') as output:
            for chunk in response.iter_content(chunk_size=8192):
              if chunk:
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != EXPECTED_SHA256:
          raise ValueError(f"mapd release SHA-256 mismatch: {actual_sha256}")
        current_permissions = stat.S_IMODE(os.lstat(temp_file).st_mode)
        os.chmod(temp_file, current_permissions | stat.S_IEXEC)
        temp_file.replace(MAPD_PATH)
        return
      except (requests.exceptions.RequestException, TimeoutError) as error:
        last_error = error
        self._spinner.update(f"mapd binary download attempt {cnt + 1}/{num_retries} failed: {error}")
        if cnt + 1 < num_retries:
          time.sleep(0.5)
      finally:
        temp_file.unlink(missing_ok=True)

    logging.error("Failed to install mapd binary after %s attempts", num_retries)
    raise RuntimeError(f"Failed to install verified mapd binary after {num_retries} attempts") from last_error

  def get_installed_version(self) -> str:
    return str(self._params.get("MapdVersion") or "")

  def wait_for_internet_connection(self, return_on_failure: bool = False) -> bool:
    max_retries = 10
    for retries in range(max_retries + 1):
      self._spinner.update(f"Waiting for internet connection... [{retries}/{max_retries}]")
      time.sleep(2)
      try:
        _ = urlopen('https://sentry.io', timeout=10)
        return True
      except Exception as e:
        print(f'Wait for internet failed: {e}')
        if return_on_failure and retries == max_retries:
          return False

    return False

  def non_prebuilt_install(self) -> None:
    sm = messaging.SubMaster(['deviceState'])
    metered = sm['deviceState'].networkMetered

    if metered:
      self._spinner.update("Can't proceed with mapd install since network is metered!")
      time.sleep(5)
      return

    try:
      self.ensure_directories_exist()
      if not self.download_needed():
        self._spinner.update("Mapd is good!")
        time.sleep(0.1)
        return

      if self.wait_for_internet_connection(return_on_failure=True):
        self._spinner.update(f"Downloading pfeiferj's mapd [{self.get_installed_version()}] => [{VERSION}].")
        time.sleep(0.1)
        self.check_and_download()
      self._spinner.close()

    except Exception:
      for i in range(6):
        self._spinner.update("Failed to install the official mapd executable; offline map downloads remain unavailable. " +
                             f"Boot will continue in {5 - i}s...")
        time.sleep(1)

      sentry.init(sentry.SentryProject.SELFDRIVE)
      traceback.print_exc()
      sentry.capture_exception()


if __name__ == "__main__":
  spinner = Spinner()
  install_manager = MapdInstallManager(spinner)
  install_manager.ensure_directories_exist()
  if is_prebuilt():
    debug_msg = f"[DEBUG] This is prebuilt, no mapd install required. VERSION: [{VERSION}], Param [{install_manager.get_installed_version()}]"
    spinner.update(debug_msg)
    update_installed_version(VERSION)
  else:
    spinner.update(f"Checking if mapd is installed and valid. Prebuilt [{is_prebuilt()}]")
    install_manager.non_prebuilt_install()
