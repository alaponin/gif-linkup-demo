#!/usr/bin/env bash
# Build a distributable archive of this pack from git, never from the working
# tree. A folder copy or a Finder zip of this directory carries whatever
# happens to be on disk: a real .env with the live token PIN and admin
# password, ~25 MB of darwin-only Terraform provider binary under
# infra/terraform/.terraform/, a local tfstate, out/, the .venv, and any
# Finder "<name> 2" duplicate. Every one of those is gitignored -- which keeps
# them out of git and does nothing whatever about a zip. git archive packages
# the committed tree, so the archive holds exactly what a fresh clone would.
#
#   scripts/package.sh                 # -> ../kp2-build-pack-<sha>.zip
#   scripts/package.sh /tmp/kp2.zip    # explicit destination (.zip or .tar.gz)
#
# The archive is the PACK, not the monorepo. That is enough for the whole
# runbook -- preflight, gen-secrets, deploy, seed, acceptance -- but not for
# the join demo: apps/join-api bind-mounts the monorepo root with its .git and
# runs `git status --porcelain` there before approving a join
# (infra/DO-DEPLOYMENT.md). Hand out a clone, not an archive, when the join
# demo is part of the session.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

cd "$PACK_DIR"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "not a git checkout -- there is nothing to archive from. Clone the repository first."

SUBTREE=$(git rev-parse --show-prefix)
SUBTREE=${SUBTREE%/}
SHA=$(git rev-parse --short HEAD)
OUT=${1:-$PACK_DIR/../kp2-build-pack-$SHA.zip}
# Absolute before the cd below, or a relative destination would land next to
# the repo root instead of where it was typed.
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac

case "$OUT" in
  *.zip)    FORMAT=zip ;;
  *.tar.gz) FORMAT=tar.gz ;;
  *) fail "unknown archive format for $OUT -- use .zip or .tar.gz" ;;
esac

# Committed state is what gets packaged, so say so when it differs from what
# the person running this is looking at. A warning, not a refusal: packaging
# the last commit while mid-edit is a legitimate thing to want.
if [ -n "$(git status --porcelain -- .)" ]; then
  log "WARNING: uncommitted changes in the pack -- the archive holds commit $SHA, not your working tree"
fi

# From the repo TOPLEVEL, not from here: git archive applies the current
# directory as an implicit pathspec, so running it inside the pack filters the
# pack's own subtree away and writes an archive containing nothing but the
# prefix directory (found the hard way -- it exits 0).
cd "$(git rev-parse --show-toplevel)"
git archive --format="$FORMAT" --prefix=kp2-build-pack/ -o "$OUT" "HEAD${SUBTREE:+:$SUBTREE}"
log "wrote $OUT (from commit $SHA)"
