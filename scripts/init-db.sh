#!/usr/bin/env bash
# Nixx database initialisation
# Creates the nixx PostgreSQL role and database in an existing instance.
#
# Usage (from repo root):
#   bash scripts/init-db.sh
#
# Reads NIXX_POSTGRES_PASSWORD from .env.
# Connects as the admin superuser — supply credentials via standard psql env vars:
#   PGHOST, PGPORT, PGUSER, PGPASSWORD
# e.g.
#   PGHOST=localhost PGPORT=5432 PGUSER=admin PGPASSWORD=... bash scripts/init-db.sh

set -euo pipefail

ENV_FILE="$(dirname "$0")/../.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env not found at $ENV_FILE" >&2
  exit 1
fi

# Extract NIXX_POSTGRES_PASSWORD from .env (ignores comments and blank lines)
NIXX_POSTGRES_PASSWORD=$(grep -E '^NIXX_POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"'"'" | head -1)

if [[ -z "$NIXX_POSTGRES_PASSWORD" ]]; then
  echo "Error: NIXX_POSTGRES_PASSWORD not set in .env" >&2
  exit 1
fi

psql "${PGDATABASE:-postgres}" <<SQL
CREATE USER nixx WITH PASSWORD '$NIXX_POSTGRES_PASSWORD';
CREATE DATABASE nixx OWNER nixx;
REVOKE ALL ON DATABASE nixx FROM PUBLIC;
\connect nixx
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO nixx;
SQL

echo "Done — database 'nixx' and role 'nixx' created."
