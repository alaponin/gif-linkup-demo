#!/usr/bin/env bash
# Renders onboarding/{pnia,plr,pnea}/ for the three canonical members
# through the same writer.py code path a real join uses -- see
# scripts/render_onboarding.py's own docstring for how. Not
# hand-authored, and not run automatically by hurl/generate.py: this reads
# each canonical member's member_requirements/sla fields off
# configs/member-<key>/<key>.yaml (added by hand, prompts/register-
# member.md) and re-renders on every run, so it is safe to re-run after
# editing one of those configs.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

PY="$PACK_DIR/.venv/bin/python3"
[ -x "$PY" ] || fail "$PY not found -- set up the dev venv (see apps/console/tests/ and tests/test_golden.py for what it needs); rendering onboarding/ needs apps/join-api's own pydantic-based schema.py and writer.py."

for key in pnia plr pnea; do
  "$PY" "$PACK_DIR/scripts/render_onboarding.py" "$PACK_DIR" "$key"
done

# Last, after every member's own record exists: the instance-wide catalogue
# is derived from configs/member-*/ rather than from the trees above, and is
# regenerated wholesale -- re-running this script is how it is kept current.
"$PY" "$PACK_DIR/scripts/render_catalogue.py" "$PACK_DIR"
