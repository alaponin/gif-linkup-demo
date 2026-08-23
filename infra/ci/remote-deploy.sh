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

# -- the container identity, and what it may write ----------------------------
#
# join-api parses applicant-controlled payloads and bind-mounts this
# checkout; it used to do that as UID 0, because this script runs as root and
# scripts/lib-stack.sh took the containers' uid from `id -u`
# (docs/security-review-2026-08-23.md, finding H1). Here -- and only here; a
# laptop still gets the developer's own id -- it runs as the dedicated
# unprivileged `kp2` identity instead.
export KP2_CONTAINER_UID=10001
export KP2_CONTAINER_GID=10001

# infra/terraform/cloud-init.yaml creates this at first boot. Repeated here
# because `terraform apply` refreshes an EXISTING droplet without re-running
# cloud-init, so a droplet provisioned before that block existed would
# otherwise chown to a uid with no name and no group.
getent group kp2 >/dev/null || groupadd -g 10001 kp2
id -u kp2 >/dev/null 2>&1 || useradd -u 10001 -g kp2 -M -s /usr/sbin/nologin kp2

# The backstop under docker-compose.yml's read-only /repo mount: if the mount
# flags were ever misconfigured, ownership still says no. Re-applied on every
# deploy because the workflow's `rsync --chown=root:root` resets it, including
# on the acceptance-only path below -- which is why this runs before that
# early exit rather than after it.
harden_container_paths() {
  # The tree stays root:root: git's dubious-ownership check is satisfied by
  # that, and `git status` on the host is part of every deploy.
  mkdir -p out   # gitignored, so a fresh droplet has no out/ to chown yet
  chown -R kp2:kp2 configs manifest.yaml onboarding out

  # hurl/ holds BOTH generate.py/steps.py/templates/run-linkup.sh (root-owned
  # code the host executes) and generate.py's own OUTPUT, which the container
  # has to be able to replace. So: everything root's, the directory setgid so
  # the container's new files land in the kp2 group and STICKY so a non-owner
  # cannot unlink or rename the code beside its own files -- and then the
  # generated set handed to kp2 outright.
  #
  # That second step is not cosmetic. The generated files are gitignored, so
  # the workflow's `rsync --delete` removes them and lib-stack.sh regenerates
  # them by running generate.py AS ROOT during deploy.sh below. Left
  # root-owned 644 inside a sticky directory, the container could neither
  # rewrite nor unlink them, and the next join would fail on
  # `hurl/topology.json`. Hence also the EXIT trap: everything root wrote
  # during the deploy is handed over on the way out.
  chown -R root:kp2 hurl
  chmod 3775 hurl
  # Exactly generate.py's outputs -- the same set .gitignore lists.
  for generated in scenarios vars.env local.ini topology.json topology.sh \
                   compose.members.yml; do
    if [ -e "hurl/$generated" ]; then chown -R kp2:kp2 "hurl/$generated"; fi
  done

  # 640 root:kp2, not 600 root:root: hurl/generate.py's read_env() and
  # writer.py's _COPY_ITEMS both need to READ .env inside the container;
  # neither ever writes it, and now cannot. Still unreadable to everyone else
  # on the host.
  if [ -f .env ]; then
    chown root:kp2 .env
    chmod 640 .env
  fi
}
harden_container_paths
# ...and again on the way out, whichever way this script ends. Every step
# below runs as root and writes into out/ and hurl/ (generate.py, seed.sh,
# acceptance.sh), so the ownership that matters is the ownership the
# containers are left with, not the one they started with. `|| true` because
# an EXIT trap must never change the exit status this script is reporting.
trap 'harden_container_paths || true' EXIT

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
# gen-secrets.sh writes .env as root:root 600; put it back to root:kp2 640 so
# the containers can still read it. Whole function rather than two lines: it
# is idempotent and takes a moment.
harden_container_paths

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
