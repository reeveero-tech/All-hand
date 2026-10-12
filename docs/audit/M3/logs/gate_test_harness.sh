#!/bin/bash
# Simulate the gate locally.
set -uo pipefail
CHANGED="$1"
MANDATORY=(docs/governance/ROADMAP.md docs/governance/PROJECT_STATE.md docs/governance/CHANGELOG.md)
MISSING=()
for f in "${MANDATORY[@]}"; do
  if ! grep -qxF "$f" <<< "$CHANGED"; then MISSING+=("$f"); fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "FAIL: missing ${MISSING[*]}"
  exit 1
fi
CODE_RE='^(server\.py|run\.py|agent_civilization/|agos_kernel/|src/|web/|tests/)'
if grep -Eq "$CODE_RE" <<< "$CHANGED"; then
  grep -qxE 'docs/audit/.+' <<< "$CHANGED" || { echo "FAIL: code changed without audit evidence"; exit 1; }
fi
if grep -Eq '^server\.py|agent_civilization/civilization_routes\.py' <<< "$CHANGED"; then
  grep -qxF 'docs/governance/API.md' <<< "$CHANGED" || { echo "FAIL: API changed without API.md"; exit 1; }
fi
if grep -Eq 'agent_civilization/storage/' <<< "$CHANGED"; then
  grep -qxF 'docs/governance/DATA_MODEL.md' <<< "$CHANGED" || { echo "FAIL: storage changed without DATA_MODEL.md"; exit 1; }
fi
if grep -Eq '^\.github/workflows/' <<< "$CHANGED"; then
  grep -qxF 'docs/governance/QUALITY.md' <<< "$CHANGED" || { echo "FAIL: CI changed without QUALITY.md"; exit 1; }
fi
echo "PASS"
