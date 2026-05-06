#!/bin/bash
# Install P1 server pipeline integration into NikMoq/mapd fork
# Run this in the mapd fork directory

set -e

P1_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo "  P1 Integration into NikMoq/mapd"
echo "=========================================="
echo ""

# Check we're in mapd fork
if [ ! -f "go.mod" ] || ! grep -q "pfeifer.dev/mapd" go.mod 2>/dev/null; then
    echo "ERROR: This doesn't look like a mapd Go project"
    exit 1
fi

echo "[1/3] Adding generate command..."

# Create cmd/generate.go if cmd/ exists, otherwise suggest main.go integration
if [ -d "cmd" ]; then
    cp "$P1_DIR/cmd/generate.go" cmd/
    echo "  Added cmd/generate.go"
else
    echo "  No cmd/ directory found. Integrate generate command into main.go:"
    echo ""
    cat "$P1_DIR/cmd/generate.go"
    echo ""
fi

echo "[2/3] Verifying P0 files are present..."
required_files=(
    "maps/speedcam_parser.go"
    "maps/way_index.go"
    "maps/morton.go"
    "maps/camera_runtime.go"
    "maps/generate_cameras.go"
    "maps/offline_camera.go"
)

for f in "${required_files[@]}"; do
    if [ ! -f "$f" ]; then
        echo "  ERROR: Missing P0 file: $f"
        echo "  Run P0 install first!"
        exit 1
    fi
    echo "  ✓ $f"
done

echo "[3/3] Building..."
if go build ./...; then
    echo ""
    echo "=========================================="
    echo "  P1 INTEGRATION SUCCESSFUL"
    echo "=========================================="
    echo ""
    echo "Test generation:"
    echo "  ./mapd generate --osm russia.osm.pbf --cameras Rus.radar.txt --out ./test-tiles"
    echo ""
    echo "Then use server scripts:"
    echo "  python tools/mapd-russia/server/generate_tiles.py --out ./tiles --mapd-bin ./mapd"
else
    echo ""
    echo "=========================================="
    echo "  BUILD FAILED"
    echo "=========================================="
    exit 1
fi
