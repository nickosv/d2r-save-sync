#!/bin/sh
# Launch d2rsync from the repo root.
# With no arguments this syncs (the useful default for a Steam Deck Game Mode shortcut).
# Set D2R_SYNC_NO_PAUSE=1 to skip the trailing "Press Enter".
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required (3.9 or newer)." >&2
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "python3 3.9 or newer is required." >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    set -- sync
fi

set +e
python3 -m d2rsync "$@"
status=$?
set -e

if [ -t 0 ] && [ "${D2R_SYNC_NO_PAUSE:-}" != "1" ]; then
    printf '\nPress Enter to close...\n'
    read -r _ || true
fi
exit "$status"
