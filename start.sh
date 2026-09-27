#!/usr/bin/env bash
# Start the agent: creates the Python environment on first run, loads .env, runs the server.
set -e
cd "$(dirname "$0")"

PY=$(command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3.10 || command -v python3)
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or newer is needed. On a Mac: brew install python@3.12"
  exit 1
fi
if [ ! -f .env ]; then
  echo "No .env file yet. Run: cp .env.example .env  and fill it in."
  exit 1
fi
if [ ! -d .venv ]; then
  echo "First run: setting up (takes a minute)..."
  "$PY" -m venv .venv
fi
. .venv/bin/activate
pip install -q --disable-pip-version-check -r requirements.txt
exec uvicorn agent.main:app --port 8000 --env-file .env
