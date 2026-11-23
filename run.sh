#!/usr/bin/env sh
# Start Personal Finance Insights on macOS or Linux.  Usage: sh run.sh   (or ./run.sh)
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3 was not found. Install Python 3.11 or newer (see README: Run it), then try again."
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "This app needs Python 3.10 or newer; $("$PY" --version 2>&1) was found."
  echo "Install a newer Python (see README: Run it) or point to it:  PYTHON=python3.12 sh run.sh"
  exit 1
fi

if [ ! -x .venv/bin/python ] || [ ! -f .venv/.installed ]; then
  echo "First run: creating a private Python environment (about a minute)..."
  rm -rf .venv
  if ! "$PY" -m venv .venv; then
    echo "Could not create the environment. On Debian/Ubuntu run:  sudo apt install python3-venv"
    rm -rf .venv
    exit 1
  fi
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
  touch .venv/.installed
fi

exec .venv/bin/python -m app
