#!/bin/bash
# Generate OSM tiles locally on comma device
# Run this on device after build_mapd_local.sh

set -e

MAPD_BIN="/data/media/0/osm/mapd"
OSM_DIR="/data/media/0/osm"
TEMP_DIR="/tmp/osm-tiles"
REGION="${1:-moscow}"

echo "=== Mapd Tile Generation ==="
echo "Region: ${REGION}"
echo ""

# Check mapd binary
if [ ! -f "${MAPD_BIN}" ]; then
  echo "Error: mapd binary not found at ${MAPD_BIN}"
  echo "Run build_mapd_local.sh first"
  exit 1
fi

# Create directories
mkdir -p ${OSM_DIR}/db
mkdir -p ${TEMP_DIR}

# Download OSM data based on region
case ${REGION} in
  moscow)
    OSM_URL="https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
    BBOX="36.8,54.6,38.2,56.0"
    ;;
  spb)
    OSM_URL="https://download.geofabrik.de/russia/northwestern-fed-district-latest.osm.pbf"
    BBOX="29.5,59.5,31.5,60.5"
    ;;
  all|russia)
    OSM_URL="https://download.geofabrik.de/russia-latest.osm.pbf"
    BBOX=""
    ;;
  *)
    echo "Unknown region: ${REGION}"
    echo "Usage: $0 [moscow|spb|all]"
    exit 1
    ;;
esac

# Download OSM PBF
OSM_FILE="${TEMP_DIR}/${REGION}.osm.pbf"
if [ ! -f "${OSM_FILE}" ]; then
  echo "Downloading OSM data..."
  wget -q --show-progress -O "${OSM_FILE}" "${OSM_URL}"
fi

# Download speedcam database (optional)
CAMERA_FILE="${TEMP_DIR}/cameras.txt"
echo "Downloading speedcam database..."
wget -q --show-progress -O "${CAMERA_FILE}" \
  "https://speedcamonline.ru/map/Rus/Rus.radar.txt" || \
  echo "" > "${CAMERA_FILE}"

# Generate tiles
echo "Generating tiles..."
mkdir -p ${TEMP_DIR}/output
if [ -n "${BBOX}" ]; then
  ${MAPD_BIN} generate \
    --osm "${OSM_FILE}" \
    --cameras "${CAMERA_FILE}" \
    --out "${TEMP_DIR}/output" \
    --bbox "${BBOX}"
else
  ${MAPD_BIN} generate \
    --osm "${OSM_FILE}" \
    --cameras "${CAMERA_FILE}" \
    --out "${TEMP_DIR}/output"
fi

# Move to final location
echo "Installing tiles..."
mkdir -p ${OSM_DIR}/tiles
cp -r ${TEMP_DIR}/output/offline/* ${OSM_DIR}/tiles/ 2>/dev/null || true

# Create manifest
cd /data/openpilot
python3 << 'PYEOF'
import hashlib
import json
import os
from pathlib import Path

tiles_dir = Path("/data/media/0/osm/tiles")
if not tiles_dir.exists():
    print("No tiles generated")
    exit(0)

manifest = {
    "version": "local-" + os.popen("date +%Y%m%d").read().strip(),
    "tiles": {},
}

for tile_file in sorted(tiles_dir.rglob("*.tar.gz")):
    rel = tile_file.relative_to(tiles_dir).as_posix()
    h = hashlib.sha256()
    with open(tile_file, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    manifest["tiles"][rel] = {
        "hash": h.hexdigest(),
        "size": tile_file.stat().st_size,
    }

with open("/data/media/0/osm/manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)

total_size = sum(t["size"] for t in manifest["tiles"].values())
print(f"Tiles: {len(manifest['tiles'])}, Total: {total_size / 1024 / 1024:.1f} MB")
PYEOF

echo ""
echo "=== Generation Complete ==="
echo "Tiles: ${OSM_DIR}/tiles/"
echo "Manifest: ${OSM_DIR}/manifest.json"
echo ""
echo "Enable OSM in openpilot settings"
