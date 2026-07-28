#!/bin/bash
# Install the dovetail skill into ~/.claude/skills/ as a live symlink install.
#
# SKILL.md references scripts via ${CLAUDE_SKILL_DIR}, which Claude Code
# substitutes to the skill's own directory for personal, project, and plugin
# installs alike. So this script symlinks the whole skill directory into
# ~/.claude/skills/ - every edit (scripts AND SKILL.md) is immediately live,
# with no per-file rewrite. Re-run only when you add a new skill directory.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_ROOT="$HOME/.claude/skills"

echo "=== dovetail skill installer (Claude Code) ==="
echo

# --- Dependencies ---
# Standard library only, so python3 and git are the entire requirement. The
# 3.11 floor is the tested one.
MISSING=""
command -v python3 >/dev/null 2>&1 || MISSING="$MISSING python3"
command -v git     >/dev/null 2>&1 || MISSING="$MISSING git"
if [ -n "$MISSING" ]; then
  echo "Missing required dependencies:$MISSING"
  echo "  macOS:  brew install$MISSING"
  echo "  Ubuntu: sudo apt install$MISSING"
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "dovetail needs Python 3.11 or newer; found $(python3 -V 2>&1)"
  exit 1
fi
echo "Dependencies OK ($(python3 -V 2>&1), standard library only)."
echo

# --- Install each skill in this repo as a full-directory symlink ---
mkdir -p "$SKILLS_ROOT"
for src in "$SCRIPT_DIR"/skills/*/; do
  src="${src%/}"
  name="$(basename "$src")"
  target="$SKILLS_ROOT/$name"
  echo "Installing '$name' -> $target"
  rm -rf "$target"            # replace any prior copy or partial-symlink install
  ln -sfn "$src" "$target"    # whole-directory symlink; ${CLAUDE_SKILL_DIR} resolves it
  chmod +x "$src"/scripts/*.py 2>/dev/null || true
done

echo
echo "Installed as directory symlinks - all edits (scripts and SKILL.md) are live. Re-run only when adding a new skill."
echo
echo "Done. Try: 'run dovetail on this repo'"
echo
echo "To gate pull requests on it, copy skills/dovetail/ci/dovetail-pr.yml"
echo "into your repository's .github/workflows/."
