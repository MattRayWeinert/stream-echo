#!/usr/bin/env bash
# Loads secrets.sh from this directory and starts the mirror bot.
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f secrets.sh ]]; then
  echo "Missing secrets.sh (copy from template / create locally)." >&2
  exit 1
fi
# shellcheck disable=SC1091
source ./secrets.sh
exec python3 twitch_mirror.py "$@"
