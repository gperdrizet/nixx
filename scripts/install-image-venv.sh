#!/usr/bin/env bash
# Install nixx-image venv dependencies.
# Run after torch is installed: bash scripts/install-image-venv.sh

set -euo pipefail

VENV=~/.local/share/pipx/venvs/nixx-image
PIP="$VENV/bin/pip"
NIXX_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Installing diffusers + transformers + accelerate..."
"$PIP" install --quiet \
    diffusers \
    "transformers<4.52" \
    accelerate \
    sentencepiece \
    protobuf \
    pillow \
    fastapi \
    "uvicorn[standard]" \
    httpx

echo "==> Installing nixx package (editable) into image venv..."
"$PIP" install --quiet -e "$NIXX_DIR"

echo "==> Verifying entry point..."
"$VENV/bin/nixx-image" --help 2>&1 | head -3 || true

echo "==> Done. Run: sudo systemctl start nixx-image"
