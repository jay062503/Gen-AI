#!/usr/bin/env bash
# Double-click launcher for macOS (Finder). Same as ./start.sh
cd "$(dirname "$0")" && bash ./start.sh "$@"
status=$?
[ $status -ne 0 ] && { echo; read -n 1 -s -r -p "Press any key to close..."; }
exit $status
