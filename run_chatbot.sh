#!/usr/bin/env bash
# Loads secrets.sh and starts the LLM mention chatbot.
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f secrets.sh ]]; then
  echo "Missing secrets.sh (copy from template / create locally)." >&2
  exit 1
fi
# shellcheck disable=SC1091
source ./secrets.sh
exec python3 twitch_chatbot.py "$@"
