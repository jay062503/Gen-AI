#!/usr/bin/env bash
# Media Studio launcher for macOS and Linux.
#   ./start.sh                 first run installs everything, then starts and opens the browser
#   ./start.sh --ml            force the AI model runtime      (--no-ml = demo mode only)
#   ./start.sh --reinstall     repair / refresh the installation
#   ./start.sh --no-open       do not open the browser
#   ./start.sh --port 8095 --host 0.0.0.0   (anything else is passed to run.py)
set -euo pipefail
cd "$(dirname "$0")"

SETUP_ARGS=(); RUN_ARGS=(); OPEN=(--open)
for a in "$@"; do
  case "$a" in
    --ml|--no-ml|--reinstall) SETUP_ARGS+=("$a") ;;
    --no-open) OPEN=() ;;
    *) RUN_ARGS+=("$a") ;;
  esac
done

find_python() {
  for c in python3.12 python3.13 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  PY=$(find_python) || {
    echo "Python 3.10-3.13 was not found."
    echo "  macOS : brew install python@3.12      (or download it from https://www.python.org/downloads/)"
    echo "  Ubuntu: sudo apt install python3.12 python3.12-venv"
    exit 1
  }
  echo "Creating virtual environment with $($PY --version)..."
  "$PY" -m venv .venv
fi

# ${arr[@]+"${arr[@]}"} keeps empty arrays safe on macOS's bash 3.2
.venv/bin/python scripts/setup.py ${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"}
exec .venv/bin/python run.py ${OPEN[@]+"${OPEN[@]}"} ${RUN_ARGS[@]+"${RUN_ARGS[@]}"}
