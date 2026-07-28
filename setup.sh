#!/usr/bin/env bash
# dovetail has no dependencies to install. This only marks scripts executable.
set -e

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

chmod +x "$SKILL_DIR"/scripts/*.py 2>/dev/null || true

echo "dovetail: ready (stdlib only, nothing to install)"
