#!/usr/bin/env bash
# P3: Deploy mapd-russia to Comma device
# Usage: ./deploy-to-comma.sh [comma-ip]

set -euo pipefail

COMMA_IP="${1:-comma.local}"
COMMA_USER="comma"
OPENPILOT_DIR="/data/openpilot"

echo "=== P3: Deploy mapd-russia to Comma ==="
echo "Target: $COMMA_USER@$COMMA_IP"
echo ""

# Check connection
echo "[1/5] Checking connection..."
if ! ssh -o ConnectTimeout=5 "$COMMA_USER@$COMMA_IP" "echo OK" >/dev/null 2>&1; then
    echo "ERROR: Cannot connect to $COMMA_IP"
    echo "Make sure Comma is on the same network and SSH is enabled."
    exit 1
fi
echo "  ✓ Connected"

# Push code changes
echo ""
echo "[2/5] Pushing code changes..."
rsync -av --delete \
    sunnypilot/mapd/ \
    "$COMMA_USER@$COMMA_IP:$OPENPILOT_DIR/sunnypilot/mapd/"
echo "  ✓ Code pushed"

# Rebuild
echo ""
echo "[3/5] Rebuilding sunnypilot..."
ssh "$COMMA_USER@$COMMA_IP" "cd $OPENPILOT_DIR && scons -j\$(nproc)" </dev/null
echo "  ✓ Build complete"

# Verify mapd binary
echo ""
echo "[4/5] Checking mapd binary..."
ssh "$COMMA_USER@$COMMA_IP" "
    cd $OPENPILOT_DIR
    python3 sunnypilot/mapd/mapd_installer.py --check || true
" </dev/null
echo "  ✓ Mapd check done"

# Reboot prompt
echo ""
echo "[5/5] Done!"
echo ""
read -p "Reboot Comma now? [Y/n] " -n 1 -r
echo
if [[ ! \$REPLY =~ ^[Nn]$ ]]; then
    echo "Rebooting..."
    ssh "$COMMA_USER@$COMMA_IP" "sudo reboot" </dev/null || true
else
    echo "Manual reboot required for changes to take effect."
fi
