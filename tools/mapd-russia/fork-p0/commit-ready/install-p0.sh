#!/bin/bash
set -e

MAPD_FORK_DIR="${1:-$HOME/mapd}"
P0_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo "  P0 Installer for mapd-russia"
echo "=========================================="
echo ""
echo "Target fork: $MAPD_FORK_DIR"
echo "P0 source:   $P0_DIR"
echo ""

if [ ! -d "$MAPD_FORK_DIR" ]; then
    echo "ERROR: Fork directory not found: $MAPD_FORK_DIR"
    echo "Usage: $0 [path-to-mapd-fork]"
    exit 1
fi

cd "$MAPD_FORK_DIR"

echo "[1/7] Backing up original capnp schemas..."
cp cereal/offline/offline.capnp cereal/offline/offline.capnp.bak 2>/dev/null || true
cp cereal/custom/custom.capnp cereal/custom/custom.capnp.bak 2>/dev/null || true

echo "[2/7] Copying new capnp schemas..."
cp "$P0_DIR/cereal/offline/offline.capnp" cereal/offline/
cp "$P0_DIR/cereal/custom/custom.capnp" cereal/custom/

echo "[3/7] Regenerating capnp Go code..."
if ! make capnp; then
    echo "ERROR: make capnp failed"
    echo "Trying manual compilation..."
    GO_CAPNP_PATH="${GO_CAPNP_PATH:-../go-capnp/std}"
    capnp compile -I "$GO_CAPNP_PATH" -ogo cereal/offline/offline.capnp
    capnp compile -I "$GO_CAPNP_PATH" -ogo cereal/custom/custom.capnp
fi

echo "[4/7] Verifying generated code..."
if ! grep -q "type Camera capnp.Struct" cereal/offline/offline.capnp.go; then
    echo "ERROR: Camera struct not found in generated code"
    exit 1
fi
if ! grep -q "type CameraTile capnp.Struct" cereal/offline/offline.capnp.go; then
    echo "ERROR: CameraTile struct not found in generated code"
    exit 1
fi
if ! grep -q "type CameraInfo capnp.Struct" cereal/custom/custom.capnp.go; then
    echo "ERROR: CameraInfo struct not found in generated code"
    exit 1
fi
echo "  ✓ All structs present"

echo "[5/7] Copying Go source files..."
cp "$P0_DIR/maps/speedcam_parser.go" maps/
cp "$P0_DIR/maps/way_index.go" maps/
cp "$P0_DIR/maps/morton.go" maps/
cp "$P0_DIR/maps/camera_runtime.go" maps/
cp "$P0_DIR/maps/generate_cameras.go" maps/
cp "$P0_DIR/maps/offline_camera.go" maps/

echo "[6/7] Adding golang.org/x/text dependency..."
go get golang.org/x/text/encoding/charmap golang.org/x/text/transform 2>/dev/null || true

echo "[7/7] Building..."
if go build ./...; then
    echo ""
    echo "=========================================="
    echo "  P0 BUILD SUCCESSFUL"
    echo "=========================================="
    echo ""
    echo "Next steps:"
    echo "  1. Apply state_camera.patch:"
    echo "     patch -p1 < $P0_DIR/../state_camera.patch"
    echo "  2. Add state.InitCameraIndex() call in main.go"
    echo "  3. Commit and tag: git tag v1.12.1-rus.1"
else
    echo ""
    echo "=========================================="
    echo "  BUILD FAILED — CHECK ERRORS ABOVE"
    echo "=========================================="
    exit 1
fi
