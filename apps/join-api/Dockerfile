# KP2 member-join API -- demo only, never production (see
# docs/production-delta.md). Mirrors apps/console's Dockerfile pattern.
# Base image digest-pinned, resolved 2026-08-01 -- same digest as
# apps/console. Unlike the X-Road images, a pinned base stops receiving
# security patches until someone re-pins it -- re-pin periodically, not
# just when X-Road bumps.
#
# The Hurl binary comes from the same digest-pinned image hurl/compose.hurl.yml
# runs as a compose service -- this container never gets a Docker socket
# (design decision 8), so `docker compose run --rm hurl` is closed to it and
# the executor (job.py) shells out to a LOCAL hurl instead. Copied out of the
# pinned image rather than downloaded at build time: same bytes, same pin, no
# network in the build.
FROM ghcr.io/orange-opensource/hurl:latest@sha256:d7727dcc0166de8aea88916e73ea435ee09bfecb8ba0c281200206b6cf37cf64 AS hurl

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
# /usr/bin/hurl (verified with `command -v hurl` inside the image above) is an
# Alpine build: dynamically linked against musl and ~15 Alpine libraries, so
# the binary alone will not run here. Its libraries go to /opt/hurl-lib rather
# than /usr/lib, where they would sit beside (and could shadow) this base
# image's own glibc-built libssl/libcrypto/libz; job.py's HURL_ENV points
# musl's loader at them for the Hurl child process only. The whole directory
# is copied rather than a hand-listed set of .so files -- 18 MB, and a list
# would silently rot the next time the image digest is re-pinned.
COPY --from=hurl /usr/bin/hurl /usr/local/bin/hurl
COPY --from=hurl /lib/ld-musl-*.so.1 /lib/
COPY --from=hurl /usr/lib/ /opt/hurl-lib/
# writer.py's apply_real() shells out to `git status --porcelain` against the
# enclosing monorepo, bind-mounted whole at /repo (docker-compose.yml's
# comment on this service's volumes). python:3.12-slim carries no git binary
# -- found live, the first time this container really approved a request
# (the first time this container actually ran POST /requests/{id}/approve
# against a real checkout; apps/join-api/tests' git-dependent tests all run
# `git` on the HOST via pytest, never inside this image, so nothing in
# --fast could have caught it). safe.directory sidesteps git's
# dubious-ownership refusal (CVE-2022-24765): the repo bind mount is owned by
# the host user, this process runs as an unprivileged id that is not that
# user, and every git call here is a read (status only, never a write) -- see
# writer.py's own docstring on why nothing here ever commits.
#
# /repo, not `*` (review finding L4): `*` disarms the check for every path
# this container can see, so any repository an attacker could place inside it
# would be trusted too -- config and hooks included. One path, the one mount
# that needs it.
#
# One entry covers a git WORKTREE checkout too, which was the open question:
# git ownership-checks the WORKTREE path, not the common .git the worktree's
# `gitdir:` pointer names. Verified against this image with
# GIT_TEST_ASSUME_DIFFERENT_OWNER=1 (git's own switch for forcing the
# dubious-ownership path on a host where the ids happen to match), a worktree
# bind-mounted at /repo and its common .git mounted beside it: `git status`
# passes at /repo and refuses at any other mount point.
#
# postgresql-client (pg_dump/pg_restore) -- scripts/join-store-export.sh and
# -import.sh run these INSIDE this image via `docker compose run --rm
# join-api pg_dump/pg_restore ...` (Task 5) rather than requiring a second
# tool on the host. requirements.txt's psycopg driver does not bundle these
# CLI binaries -- confirmed absent before this line (`which pg_dump` ->
# not found). Debian's default postgresql-client version may trail
# DigitalOcean's managed cluster version; a client tolerates dumping/
# restoring against a same-or-newer server, not the reverse -- an
# operational note for the runbook, not something engineered around here.
RUN apt-get update && apt-get install -y --no-install-recommends git postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /repo
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY app.py schema.py store.py validate.py writer.py job.py join_logging.py .
COPY migrations/ migrations/
USER nobody
EXPOSE 8000
HEALTHCHECK --interval=5s --retries=12 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
