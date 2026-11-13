#!/usr/bin/env bash
# Environment-build install step. Idempotent; never loads NAV history (the snapshot carries it).
set -euo pipefail

python3 -m venv .venv
if [ -f pyproject.toml ]; then
  .venv/bin/pip install -q -e ".[dev]"
fi
