#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import logging
import os
import json
import stat
import time
import traceback
import requests
from pathlib import Path
from urllib.request import urlopen

from cereal import messaging
from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths
from openpilot.common.spinner import Spinner
from openpilot.system.version import is_prebuilt
from openpilot.sunnypilot.mapd import MAPD_PATH, MAPD_BIN_DIR
import openpilot.system.sentry as sentry

VERSION = "v1.0.0"
URL = f"https://github.com/NikMoq/mapd/releases/download/{VERSION}/mapd-linux-arm64.tar.gz"


def update_installed_version(version: str, params: Params = None) -> None:
  if params is None:
    params = Params()

  params.put("MapdVersion", version)


class MapdInstallManager:
  def __init__(self, spinner_ref: Spinner):
    self._spinner = spinner_ref
    self._params = Params()

  def download(self) -> None:
    self.ensure_directories_exist()
    self._download_file()
    update_installed_version(VERSION, self._params)

  def check_and_download(self) -> None:
    if self.download_needed():
      self.download()

  def download_needed(self) -> bool:
    return not os.path.exists(MAPD_PATH) or self.get_installed_version() != VERSION

  @staticmethod
  def ensure_directories_exist() -> None:
    if not os.path.exists(Paths.mapd_root()):
      os.makedirs(Paths.mapd_root())
    if not os.path.exists(MAPD_BIN_DIR):
      os.makedirs(MAPD_BIN_DIR)
    # Ensure camera database directory exists
    camera_dir = os.path.join(Paths.mapd_root(), 'radars')
    if not os.path.exists(camera_dir):
      os.makedirs(camera_dir)

  @staticmethod
  def download_radar_database(region_id: str = None) -> bool:
    """Download production radar database for selected region or default (Krasnodar Krai)."""
    import urllib.request
    import urllib.error

    # Load regions metadata
    regions_file = os.path.join(os.path.dirname(__file__), 'regions.json')
    radar_url = None

    if region_id and os.path.exists(regions_file):
      try:
        with open(regions_file, 'r') as f:
          regions_data = json.load(f)
        for region in regions_data.get('regions', []):
          if region.get('id') == region_id:
            radar_url = region.get('radar_url')
            break
      except Exception as e:
        print(f"Failed to load regions metadata: {e}")

    # Default radar URL if no region specified or not found
    if not radar_url:
      radar_url = "https://raw.githubusercontent.com/NikMoq/mapd/main/data/Rus.radar.txt"

    radar_path = os.path.join(Paths.mapd_root(), 'radars', 'Rus.radar.txt')

    try:
      urllib.request.urlretrieve(radar_url, radar_path)
      if os.path.exists(radar_path):
        print(f"Radar database downloaded successfully: {radar_path}")
        return True
    except Exception as e:
      print(f"Failed to download radar database: {e}")

    return False

  @staticmethod
  def _safe_write_and_set_executable(file_path: Path, content: bytes) -> None:
    with open(file_path, 'wb') as output:
      output.write(content)
      output.flush()
      os.fsync(output.fileno())
    current_permissions = stat.S_IMODE(os.lstat(file_path).st_mode)
    os.chmod(file_path, current_permissions | stat.S_IEXEC)

  def _download_file(self, num_retries=5) -> None:
    import tarfile
    temp_archive = Path(MAPD_PATH + ".tar.gz.tmp")
    download_timeout = 120
    for cnt in range(num_retries):
      try:
        response = requests.get(URL, stream=True, timeout=download_timeout)
        response.raise_for_status()
        # Save archive
        with open(temp_archive, 'wb') as f:
          for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
        # Extract tar.gz
        with tarfile.open(temp_archive, "r:gz") as tar:
          for member in tar.getmembers():
            if member.name.endswith('mapd-linux-arm64') or member.name == 'mapd':
              member.name = os.path.basename(member.name)
              tar.extract(member, path=MAPD_BIN_DIR)
              extracted_path = os.path.join(MAPD_BIN_DIR, member.name)
              os.rename(extracted_path, MAPD_PATH)
              self._safe_write_and_set_executable(Path(MAPD_PATH), open(MAPD_PATH, 'rb').read())
              break
        temp_archive.unlink(missing_ok=True)
        return
      except requests.exceptions.ReadTimeout:
        self._spinner.update(f"ReadTimeout caught. Timeout is [{download_timeout}]. Retrying download... [{cnt}]")
        time.sleep(0.5)
      except requests.exceptions.RequestException as e:
        self._spinner.update(f"RequestException caught: {e}. Retrying download... [{cnt}]")
        time.sleep(0.5)

    # Delete temp file if the process was not successful.
    if temp_archive.exists():
      temp_archive.unlink()
    logging.error("Failed to download file after all retries")

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
      else:
        if self.wait_for_internet_connection(return_on_failure=True):
          self._spinner.update(f"Downloading pfeiferj's mapd [{self.get_installed_version()}] => [{VERSION}].")
          time.sleep(0.1)
          self.check_and_download()

      # Get selected region and download radar database
      region_id = self._params.get("OsmRegionId", return_default=True) or "russia_krasnodar"
      self._spinner.update("Downloading radar database...")
      if self.download_radar_database(region_id):
        self._spinner.update("Radar database installed.")
      else:
        self._spinner.update("Radar database download failed, continuing without cameras.")

      time.sleep(0.1)
      self._spinner.close()

    except Exception:
      for i in range(6):
        self._spinner.update("Failed to download OSM maps won't work until properly downloaded!" +
                             "Try again manually rebooting. " +
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
