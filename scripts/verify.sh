#!/usr/bin/env bash
# One entry point, three tiers. Before this,
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

# lib-core.sh only, not lib-stack.sh: --fast must stay usable with no .env
# and no Docker daemon, and lib-stack.sh's credential/bind checks would
# refuse both. yq_get is all this needs, to reach a running stack at its
# configured deployment.yaml network.bind instead of an assumed localhost.
. "$PACK_DIR/scripts/lib-core.sh"

SHIP_GATE="$PACK_DIR/../../ITU-Giga-KP-Plugin/skills/kp-solution-verify/scripts/check_pack.py"
PYTEST="$PACK_DIR/.venv/bin/python3"

log()  { printf '\033[1;34m[verify]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[verify WARN]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[verify FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

run_fast() {
  log "check_scenarios.py"
  python3 "$PACK_DIR/hurl/check_scenarios.py"

  # Run directly, not only through the ship gate's <pack>/<tool>/check_*.py
  # auto-discovery: the gate lives in a SIBLING repository, and this pack's
  # own prompt checks must not disappear along with it when someone runs from
  # a copy that has no sibling (the same standalone layout preflight.sh
  # refuses for the deploy path). Cheap enough to run twice when both are here.
  log "prompts/check_prompts.py"
  python3 "$PACK_DIR/prompts/check_prompts.py"

  if [ -f "$SHIP_GATE" ]; then
    log "ship gate (kp-solution-verify --ready)"
    python3 "$SHIP_GATE" "$PACK_DIR" --ready
  else
    # Skipped, loudly, rather than failed at the front door: without the
    # sibling checkout a learner could not run --fast AT ALL, including the
    # ~1000 tests that need nothing external. What is lost is real and is
    # named, not implied.
    warn "ship gate NOT RUN -- kp-solution-verify not found at $SHIP_GATE."
    warn "  This tier is weaker than the one CI runs: pack structure, README/manifest"
    warn "  conformance and cross-document claims went unchecked. Everything else below still ran."
    warn "  Clone the monorepo with its sibling ITU-Giga-KP-Plugin checkout for the full gate (runbook.md Prerequisites)."
  fi

  log "check-exposure.sh"
  "$PACK_DIR/scripts/check-exposure.sh"

  # A Python-floor lint was considered here and withdrawn (two-decisions
  # plan) when the host floor was raised to 3.9+ -- see hurl/README.md's
  # "Host Python runtime" note for what it would have enforced.
  #
  # scripts/preflight.sh's python3/PyYAML check does not belong here either:
  # --fast is meant to stay
  # ~8s, and adding a dependency check to it would need this tier's public
  # claims (tests/test_tiers.py) revisited again. Preflight runs once, in
  # the deploy path (hurl/run-linkup.sh), where the cost it guards against
  # actually lives.

  log "pytest tests/ apps/console/tests/ apps/join-api/tests/ apps/mock-registry/tests/"
  # The venv is machine-local: gitignored, and the CI rsync excludes it
  # from the droplet (a macOS venv is not a Linux one). So every machine
  # that is not the laptop it was first built on -- CI droplet, fresh
  # clone, workshop participant -- used to stop here and be told to build
  # one by hand. Build it instead. ~30s once, nothing on every run after.
  if [ ! -x "$PYTEST" ]; then
    log "no .venv -- creating it from requirements-dev.txt (once)"
    python3 -m venv "$PACK_DIR/.venv" || fail "python3 -m venv failed -- on Debian/Ubuntu the stdlib venv module ships separately: apt-get install python3-venv (infra/terraform/cloud-init.yaml installs it on the droplet)."
    "$PYTEST" -m pip install -q -r "$PACK_DIR/requirements-dev.txt"
  fi
  "$PYTEST" -m pytest "$PACK_DIR/tests" "$PACK_DIR/apps/console/tests" "$PACK_DIR/apps/join-api/tests" "$PACK_DIR/apps/mock-registry/tests" -q
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
  local bind; bind=$(yq_get "$PACK_DIR/deployment.yaml" network.bind)
  local _reachable=0 _i
  for _i in 1 2 3 4 5 6; do
    curl -sk --max-time 3 -o /dev/null "https://${bind}:4000" 2>/dev/null && { _reachable=1; break; }
    sleep 5
  done
  [ "$_reachable" = 1 ] || fail "no federation reachable at https://${bind}:4000 (waited 30s) -- deploy one first (hurl/run-linkup.sh), or run scripts/verify.sh --full. --live never deploys one itself."
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
  # console.sh up now passes --wait, so its
  # own HEALTHCHECK already blocked until the FastAPI app was
  # accepting connections. This loop stays anyway as a backstop for any other
  # caller that brings the container up without --wait -- a recorded decision,
  # not an oversight -- and costs nothing extra: it succeeds on the first
  # curl once the container is already healthy.
  local bind; bind=$(yq_get "$PACK_DIR/deployment.yaml" network.bind)
  local _i
  for _i in 1 2 3 4 5 6; do
    curl -sf --max-time 3 "http://${bind}:8090/api/health" >/dev/null 2>&1 && break
    [ "$_i" = 6 ] && fail "console health check still failing 30s after scripts/console.sh up"
    sleep 5
  done

  # Guard against silent rot: recorded fixtures nobody re-records
  # eventually describe a server that no longer exists (testing-strategy
  # plan).
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
