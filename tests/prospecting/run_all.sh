#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"
PROSPECTING_PYCACHE="${TMPDIR:-/tmp}/unreal-prospecting-skills-pycache"
export PYTHONPYCACHEPREFIX="$PROSPECTING_PYCACHE"

bash -n scripts/check-upstream.sh
bash -n scripts/sync-upstream.sh
bash -n .agents/hooks/block-dangerous-commands.sh
bash -n .agents/hooks/block-sensitive-input.sh
bash -n .agents/hooks/log-task-summary.sh
node --check scripts/install.js
node --check scripts/install-unreal.js
python3 -m compileall -q shared/prospecting-core .agents/skills first-customer-finder/scripts tests/prospecting
python3 -c 'import json, pathlib; [json.loads(path.read_text(encoding="utf-8")) for path in pathlib.Path("shared/prospecting-core/schemas").glob("*.json")]'
python3 -m unittest discover -s tests/prospecting -p 'test_*.py' -v
SKILL_VALIDATOR="${SKILL_VALIDATOR:-${CODEX_HOME:-${HOME}/.codex}/skills/.system/skill-creator/scripts/quick_validate.py}"
[ -f "$SKILL_VALIDATOR" ] || { printf 'Missing skill validator: %s\n' "$SKILL_VALIDATOR" >&2; exit 1; }
python3 "$SKILL_VALIDATOR" .agents/skills/unreal-media-brand-prospector
python3 "$SKILL_VALIDATOR" .agents/skills/unreal-talent-campaign-prospector
