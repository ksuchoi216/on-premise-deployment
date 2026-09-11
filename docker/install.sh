#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] Verifying files..."
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c checksums.sha256
else
    echo "Warning: sha256sum not found, skipping verification."
fi

echo "[2/4] Loading Docker images..."
docker load -i images.tar

echo "[3/4] Starting services..."
docker compose up -d

echo "[4/4] Checking services status..."
docker compose ps
