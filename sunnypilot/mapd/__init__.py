import os
from openpilot.common.basedir import BASEDIR
from openpilot.system.hardware.hw import Paths

# mapd binary location - uses the binary installed by mapd_installer
MAPD_BIN_DIR = Paths.mapd_root()
MAPD_PATH = os.path.join(MAPD_BIN_DIR, 'mapd-arm64')
