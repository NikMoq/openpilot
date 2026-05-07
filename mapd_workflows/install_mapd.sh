#!/bin/bash
# One-click mapd installer for comma device
# Run: /data/openpilot/mapd_workflows/install_mapd.sh

set -e

OPENPILOT_DIR="/data/openpilot"
MAPD_DIR="/data/media/0/osm"

echo "======================================"
echo "  Mapd Installer for openpilot"
echo "======================================"
echo ""

# Step 1: Build mapd
echo "[1/3] Building mapd binary..."
bash ${OPENPILOT_DIR}/mapd_workflows/build_mapd_local.sh

# Step 2: Generate tiles for Moscow (default)
echo ""
echo "[2/3] Generating tiles for Moscow region..."
bash ${OPENPILOT_DIR}/mapd_workflows/generate_tiles_local.sh moscow

# Step 3: Enable in params
echo ""
echo "[3/3] Enabling OSM in openpilot..."
cd ${OPENPILOT_DIR}
python3 -c "
from openpilot.common.params import Params
p = Params()
p.put_bool('OsmLocal', True)
p.put('OsmLocationName', 'Russia')
p.put('OsmStateName', 'Moscow')
print('OSM enabled in params')
"

echo ""
echo "======================================"
echo "  Installation Complete!"
echo "======================================"
echo ""
echo "Next steps:"
echo "  1. Reboot device: sudo reboot"
echo "  2. Check OSM settings in openpilot"
echo "  3. For more regions run:"
echo "     /data/openpilot/mapd_workflows/generate_tiles_local.sh spb"
echo ""
