#!/bin/bash
# Start pgweb for nixx database management
set -e

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example and configure it."
    exit 1
fi

source .env

if [ -z "$NIXX_DATABASE_URL" ]; then
    echo "Error: NIXX_DATABASE_URL not set in .env"
    exit 1
fi

PORT="${1:-8081}"

if ! command -v pgweb &> /dev/null; then
    echo "pgweb not found. Installing..."
    TEMP_DIR=$(mktemp -d)
    cd "$TEMP_DIR"
    curl -L https://github.com/sosedoff/pgweb/releases/download/v0.17.0/pgweb_linux_amd64.zip -o pgweb.zip
    unzip -o pgweb.zip
    sudo mv pgweb_linux_amd64 /usr/local/bin/pgweb
    sudo chmod +x /usr/local/bin/pgweb
    rm pgweb.zip
    cd -
    rm -rf "$TEMP_DIR"
    echo "pgweb installed successfully"
fi

echo "Starting pgweb on http://localhost:$PORT"
echo "Press Ctrl+C to stop"
pgweb --url="$NIXX_DATABASE_URL" --bind=0.0.0.0 --listen="$PORT"
