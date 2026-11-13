#!/usr/bin/env bash
# One-time local Postgres role + database for the screener on this host.
set -euo pipefail
sudo apt-get install -y postgresql postgresql-contrib 2>/dev/null || true
sudo pg_ctlcluster 16 main start 2>/dev/null || sudo service postgresql start 2>/dev/null || true
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE USER mfscreener WITH PASSWORD 'mfscreener' CREATEDB;"
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE DATABASE mfscreener OWNER mfscreener;"
sudo -u postgres psql -c "ALTER USER mfscreener WITH SUPERUSER;"
echo "DATABASE_URL=postgresql://mfscreener:mfscreener@localhost:5432/mfscreener"
