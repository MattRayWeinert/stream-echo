#!/usr/bin/env bash
# Registers native host manifests (Perl only — no Python).
set -euo pipefail
cd "$(dirname "$0")"
if [[ -n "${1:-}" ]]; then
  exec perl install_manifest.pl "$1"
fi
echo "Paste extension ID (chrome://extensions → Developer mode → ID):"
read -r ID
exec perl install_manifest.pl "$ID"
