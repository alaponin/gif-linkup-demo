#!/usr/bin/env bash
# One entry point, three tiers -- testing-strategy plan Task 2. Before this,
# "verify" meant either a handful of static scripts or a full 880-900s
# teardown.sh --purge -> hurl/run-linkup.sh cycle, so anything that felt
# like real verification cost a quarter of an hour.
#
#   scripts/verify.sh --fast    # static + golden + pytest. No running containers,
#                                # no network, no federation -- but the Docker CLI
#                                # is required (check-exposure.sh reads the rendered
#                                # Compose config). Measured: see README.md.
#   scripts/verify.sh --live    # --fast, then acceptance.sh against a RUNNING stack.
#                                # Refuses if nothing is deployed -- never deploys one.
#   scripts/verify.sh --full    # purge, deploy, seed, acceptance, console smoke.
set -euo pipefail
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PACK_DIR"

SHIP_GATE="$PACK_DIR/../../ITU-Giga-KP-Plugin/skills/kp-solution-verify/scripts/check_pack.py"
PYTEST="$PACK_DIR/.venv/bin/python3"

log()  { printf '\033[1;34m[verify]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[verify FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

run_fast() {
  log "check_scenarios.py"
  python3 "$PACK_DIR/hurl/check_scenarios.py"

  log "ship gate (kp-solution-verify --ready)"
  [ -f "$SHIP_GATE" ] || fail "ship gate not found at $SHIP_GATE -- this pack's --fast tier depends on the sibling ITU-Giga-KP-Plugin checkout being present, not an external dependency to skip quietly."
  python3 "$SHIP_GATE" "$PACK_DIR" --ready

  log "check-exposure.sh"
  "$PACK_DIR/scripts/check-exposure.sh"

  # Landed by the simplification plan, not this one -- run it if and only
  # if it exists, per this plan's own Task 2 Step 2. Its absence is not a
  # failure of THIS plan.
  if [ -x "$PACK_DIR/scripts/check-python-floor.sh" ]; then
    log "check-python-floor.sh"
    "$PACK_DIR/scripts/check-python-floor.sh"
  fi

  log "pytest tests/ apps/console/tests/"
  [ -x "$PYTEST" ] || fail "$PYTEST not found -- set up a venv with pytest/httpx/fastapi/pyyaml (see apps/console/tests/ and tests/test_golden.py for what they need)."
  "$PYTEST" -m pytest "$PACK_DIR/tests" "$PACK_DIR/apps/console/tests" -q
}

run_live() {
  # Distinguishing "the checks failed" from "there was nothing to check" is
  # the whole point of this tier -- it must never silently deploy a
  # federation just because none was reachable. A short, bounded retry
  # (not unbounded) tolerates a federation that was JUST started (e.g. a
  # bare `docker compose ... up -d`, runbook.md's "resume a
  # stopped-but-not-purged federation" path) without blurring that
  # distinction -- found live, a single-shot probe right after containers
  # come up failed on ones that were reachable seconds later.
  local _reachable=0 _i
  for _i in 1 2 3 4 5 6; do
    curl -sk --max-time 3 -o /dev/null https://localhost:4000 2>/dev/null && { _reachable=1; break; }
    sleep 5
  done
  [ "$_reachable" = 1 ] || fail "no federation reachable at https://localhost:4000 (waited 30s) -- deploy one first (hurl/run-linkup.sh), or run scripts/verify.sh --full. --live never deploys one itself."
  run_fast
  log "acceptance.sh"
  "$PACK_DIR/scripts/acceptance.sh"
}

run_full() {
  log "purging"
  "$PACK_DIR/scripts/teardown.sh" --purge
  log "deploying (hurl/run-linkup.sh)"
  bash "$PACK_DIR/hurl/run-linkup.sh"
  log "seeding"
  "$PACK_DIR/scripts/seed.sh"
  log "acceptance"
  "$PACK_DIR/scripts/acceptance.sh"
  log "console smoke"
  "$PACK_DIR/scripts/console.sh" up
  # console.sh up returning does not mean the FastAPI app inside is already
  # accepting connections yet -- found live, a bare curl right after up
  # failed on a container that was healthy two seconds later.
  local _i
  for _i in 1 2 3 4 5 6; do
    curl -sf --max-time 3 http://localhost:8090/api/health >/dev/null 2>&1 && break
    [ "$_i" = 6 ] && fail "console health check still failing 30s after scripts/console.sh up"
    sleep 5
  done

  # Guard against silent rot: recorded fixtures nobody re-records
  # eventually describe a server that no longer exists (testing-strategy
  # plan Task 6).
  log "xroad fixture drift check"
  "$PACK_DIR/scripts/capture-xroad-fixtures.sh" --check
}

case "${1:-}" in
  --fast) run_fast ;;
  --live) run_live ;;
  --full) run_full ;;
  *) echo "usage: scripts/verify.sh --fast|--live|--full" >&2; exit 1 ;;
esac

log "${1} OK"
