#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${TCL_HMI_PYTHON:-}" ]]; then
  PYTHON="$TCL_HMI_PYTHON"
elif [[ -x "$HERE/../rotary-controller-python/venv/bin/python" ]]; then
  PYTHON="$HERE/../rotary-controller-python/venv/bin/python"
else
  PYTHON="python3"
fi

export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
export KIVY_HOME="${KIVY_HOME:-/tmp/tcl-lathe-hmi-kivy}"

exec "$PYTHON" -m tcl_lathe_hmi "$@"
