#!/usr/bin/env sh
# Snapshot every organisation's database into data/backups/<date-time>/ (safe while the app runs).
# Usage: sh backup.sh   (or ./backup.sh)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run sh run.sh once first, so the app's environment exists."
  exit 1
fi
exec .venv/bin/python -m app.backup
