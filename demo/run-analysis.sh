#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
OUTPUT="${1:-$SCRIPT_DIR/report.html}"

echo "==> Running kube2run against default namespace…"
cd "$ROOT"
.venv/bin/python main.py -n default -o "$OUTPUT"

echo ""
echo "==> Opening report…"
xdg-open "$OUTPUT" 2>/dev/null || open "$OUTPUT" 2>/dev/null || echo "Open manually: $OUTPUT"
