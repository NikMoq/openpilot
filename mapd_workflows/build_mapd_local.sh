#!/bin/bash
# Build mapd natively on comma device (ARM64)
# Run this on device: /data/openpilot/mapd_workflows/build_mapd_local.sh

set -e

MAPD_REPO="https://github.com/NikMoq/mapd"
GO_VERSION="1.21.6"
INSTALL_DIR="/data/media/0/osm"
BUILD_DIR="/tmp/mapd-build"

echo "=== Mapd Local Build Script ==="
echo "Target: $(uname -m)"
echo ""

# Install Go if not present
if ! command -v go &> /dev/null; then
  echo "Installing Go ${GO_VERSION}..."
  cd /tmp
  wget -q "https://go.dev/dl/go${GO_VERSION}.linux-arm64.tar.gz"
  tar -C /usr/local -xzf "go${GO_VERSION}.linux-arm64.tar.gz"
  export PATH=$PATH:/usr/local/go/bin
  echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
fi

export PATH=$PATH:/usr/local/go/bin
echo "Go version: $(go version)"

# Install dependencies
echo "Installing dependencies..."
apt-get update -qq
apt-get install -y -qq git capnproto wget

# Install capnpc-go
go install capnproto.org/go/capnp/v3/capnpc-go@v3.1.0-alpha.1
export PATH=$PATH:$(go env GOPATH)/bin

# Clone mapd repo
echo "Cloning ${MAPD_REPO}..."
rm -rf ${BUILD_DIR}
git clone --depth 1 ${MAPD_REPO} ${BUILD_DIR}
cd ${BUILD_DIR}

# Generate capnp code
echo "Generating capnp code..."
git clone -b v3.1.0-alpha.1 --depth 1 https://github.com/capnproto/go-capnp /tmp/go-capnp
export GO_CAPNP_PATH=/tmp/go-capnp/std
make capnp

# Build mapd binary
echo "Building mapd binary..."
go build -ldflags="-s -w" -o mapd .

# Install binary
mkdir -p ${INSTALL_DIR}
cp mapd ${INSTALL_DIR}/
chmod +x ${INSTALL_DIR}/mapd

# Update version in params
cd /data/openpilot
python3 -c "
from openpilot.common.params import Params
p = Params()
p.put('MapdVersion', 'v1.0.0-local')
print('Version updated in params')
"

echo ""
echo "=== Build Complete ==="
echo "Binary: ${INSTALL_DIR}/mapd"
${INSTALL_DIR}/mapd version || true
echo ""
echo "Next step: Generate tiles with:"
echo "  /data/openpilot/mapd_workflows/generate_tiles_local.sh"
