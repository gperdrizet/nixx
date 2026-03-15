#!/usr/bin/env bash
# Install nixx systemd units by symlinking from the repo into /etc/systemd/system.
# Must be run as root (or with sudo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

UNITS=(
    "nixx.target"
    "nixx-embed.service"
    "nixx-server.service"
    "nixx-pgweb.service"
)

echo "Installing nixx systemd units..."

for unit in "${UNITS[@]}"; do
    src="${SCRIPT_DIR}/${unit}"
    dest="${SYSTEMD_DIR}/${unit}"

    if [[ ! -f "$src" ]]; then
        echo "  SKIP  ${unit} (not found in ${SCRIPT_DIR})"
        continue
    fi

    if [[ -L "$dest" ]]; then
        echo "  OK    ${unit} (symlink already exists)"
    elif [[ -f "$dest" ]]; then
        echo "  REPLACE ${unit} (removing old copy, creating symlink)"
        rm "$dest"
        ln -s "$src" "$dest"
    else
        echo "  LINK  ${unit}"
        ln -s "$src" "$dest"
    fi
done

echo ""
echo "Reloading systemd..."
systemctl daemon-reload

echo ""
echo "Done. Usage:"
echo "  sudo systemctl start nixx.target   # start all nixx services"
echo "  sudo systemctl stop nixx.target    # stop all nixx services"
echo "  sudo systemctl status nixx.target  # check status"
echo "  sudo systemctl enable nixx.target  # enable auto-start on boot"
