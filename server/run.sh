#!/usr/bin/env bash
# One-command launcher. Usage:  ./run.sh        (override any YTLF_* variable by setting it first)
cd "$(dirname "$0")"
# Use this folder's own virtualenv Python directly (works even if your shell has a different/broken venv active)
if [ -x venv/bin/python ]; then
  PY="$PWD/venv/bin/python"
else
  echo "No working virtualenv in $PWD/venv (missing, or its Python was removed). Recreate it with:"
  echo "  deactivate 2>/dev/null; rm -rf venv && python3 -m venv venv && . venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
[ -z "$IP" ] && { echo "Could not detect LAN IP. Run: YTLF_PUBLIC_BASE=http://<ip>:8080 python server.py"; exit 1; }

export YTLF_PUBLIC_BASE="${YTLF_PUBLIC_BASE:-http://$IP:8080}"
# Preserve the legacy player status used by the last known working setup.
export YTLF_STATUS_OK="${YTLF_STATUS_OK-0}"
# Old-app compatibility fixes (see START_HERE.md). Set a variable to "" to turn that fix off.
export YTLF_UA="${YTLF_UA-com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)}"
export YTLF_DROP_HEADERS="${YTLF_DROP_HEADERS-X-Goog-Device-Auth}"
export YTLF_PB_DROP_MS="${YTLF_PB_DROP_MS-1}"
export YTLF_PB_VERSION="${YTLF_PB_VERSION-21.26.4}"
export YTLF_PB_OSVER="${YTLF_PB_OSVER-18.3.2.22D82}"

export YTLF_SAVE_DIR="${YTLF_SAVE_DIR-captures}"   # full request/response bodies saved here

echo "Serving on $YTLF_PUBLIC_BASE"
exec caffeinate -i "$PY" server.py
