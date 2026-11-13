#!/usr/bin/env bash
# Runs on every Cloud Agent boot: bring up the Postgres cluster baked into the environment snapshot.
set -euo pipefail

if ! pg_isready -q -h localhost; then
  sudo pg_ctlcluster 16 main start
fi
until pg_isready -q -h localhost; do sleep 1; done

psql "postgresql://mfscreener:mfscreener@localhost:5432/mfscreener" -Atc \
  "SELECT 'nav_daily rows: ' || reltuples::bigint FROM pg_class WHERE relname = 'nav_daily'"
