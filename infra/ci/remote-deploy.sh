#!/usr/bin/env bash
# Runs ON THE DROPLET (piped over SSH by the workflow) after the monorepo
# checkout has been rsynced to /opt/kp2/repo. Follows runbook.md's own
# order: gen-secrets (first deploy only) -> preflight -> preload-images ->
# deploy -> seed -> acceptance. Budget ~15 min cold (deploy alone is
# ~11-13 min per docs/deployment-targets.md).
set -euo pipefail

PACK="/opt/kp2/repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack"
cd "$PACK"

# This process's own default, only -- scripts/join-store-export.sh is never
# called from this (or any other CI) script; cluster destruction (runbook.md
# §6.3/§6.4) is deliberately never a CI action. Sets the convention for the
# day something here does call it. The operator-run path is what matters
# today: runbook.md's §6.3 has the operator export this same value on the
# command line before running join-store-export.sh interactively, since a
# variable exported in this process never reaches that later SSH session.
export KP2_EXPORT_DIR=/opt/kp2/exports

# deploy.sh replays the whole Hurl init sequence (deploy.sh -> hurl/
# run-linkup.sh --setup), which is NOT idempotent over an already-
# initialised federation. So: if the stack is already live, default to
# proving it (acceptance only); a purge+redeploy is an explicit ask
# (KP2_REDEPLOY=1, wired to the workflow's "redeploy" input) and follows
# the pack's own reproducibility path: teardown --purge -> cold deploy.
if docker ps --format '{{.Names}}' | grep -qx cs; then
  if [ "${KP2_REDEPLOY:-0}" = "1" ]; then
    echo "== live federation found, KP2_REDEPLOY=1 — purge and redeploy =="
    scripts/teardown.sh --purge
  else
    echo "== live federation found — running acceptance only (set redeploy=true for purge+redeploy) =="
    scripts/acceptance.sh
    echo "== federation is up and green =="
    exit 0
  fi
fi

# The pack's .env is generated on the host it deploys on and is gitignored,
# so a fresh droplet never has one. Re-runs keep the existing one (a new
# token PIN cannot be applied to an already-initialised software token —
# docs/deployment-targets.md, "Where secrets rest").
if [ ! -f .env ]; then
  echo "== no .env — generating secrets =="
  scripts/gen-secrets.sh
else
  # Appends only missing keys (e.g. the two join tokens); rotates nothing.
  scripts/gen-secrets.sh || true
fi

echo "== preflight =="
scripts/preflight.sh

echo "== preload images (digest-pinned) =="
scripts/preload-images.sh

echo "== deploy (Hurl admin-API run — the long step) =="
scripts/deploy.sh

echo "== seed =="
scripts/seed.sh

echo "== acceptance =="
scripts/acceptance.sh

echo "== federation is up and green =="
