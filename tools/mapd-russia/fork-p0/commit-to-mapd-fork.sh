#!/bin/bash
# Этот скрипт нужно выполнить в КЛОНЕ форка NikMoq/mapd
# cd ~/mapd && bash /path/to/this/script.sh

set -e

P0_DIR="$(cd "$(dirname "$0")/commit-ready" && pwd)"

echo "=========================================="
echo "  Commit P0 to NikMoq/mapd fork"
echo "=========================================="
echo ""

# Проверяем что мы в правильном репозитории
if ! git remote -v | grep -q "NikMoq/mapd"; then
    echo "WARNING: This doesn't look like NikMoq/mapd fork"
    echo "Remotes:"
    git remote -v
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "[1/5] Creating branch p0-russian-cameras..."
git checkout -b p0-russian-cameras

echo "[2/5] Copying capnp schemas..."
cp "$P0_DIR/cereal/offline/offline.capnp" cereal/offline/
cp "$P0_DIR/cereal/custom/custom.capnp" cereal/custom/

echo "[3/5] Regenerating capnp Go code..."
make capnp

echo "[4/5] Copying Go source files..."
cp "$P0_DIR/maps/"*.go maps/

echo "[5/5] Committing..."
git add cereal/offline/offline.capnp cereal/offline/offline.capnp.go
git add cereal/custom/custom.capnp cereal/custom/custom.capnp.go
git add maps/*.go
git commit -m "P0: Russian speed camera support (offline, bearing-aware)

Add speed camera layer with:
- Capnp schema: Camera, CameraTile, CameraInfo
- Parser: speedcamonline.ru Rus.radar.txt (cp1251→UTF-8)
- Map matching: grid spatial index over OSM ways
- Runtime: FindCameraAhead <5ms with bearing filter
- Generator: adaptive tile split (≤1000 cameras, ≤64KB)
- Groups: avtodoria cascade detection (500m radius)
- TTL: mobile cameras degrade after 3h, hidden after 24h

Closes: mapd-russia P0"

echo ""
echo "=========================================="
echo "  P0 COMMITTED to branch p0-russian-cameras"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Push branch: git push origin p0-russian-cameras"
echo "  2. Create PR or merge to main"
echo "  3. Tag release: git tag v1.12.1-rus.1"
echo "  4. Push tag: git push origin v1.12.1-rus.1"
echo "  5. GitHub Actions builds release automatically"
