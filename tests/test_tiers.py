"""Mechanically enforce the fast tier's contract -- lib-split-and-tier-honesty
plan Task 4 (T1). Three documents (README.md's "Verify a change", the CI
workflow header, testing-strategy's Global Constraints) used to claim
`scripts/verify.sh --fast` "needs no Docker" -- false: `check-exposure.sh`
runs `docker compose ... config` to read the *rendered* Compose config
(profiles and `${VAR}` interpolation resolved), which is the entire point of
that check. What's actually true is narrower and was measured, not assumed:

  - no running containers, no federation, no network
  - the Docker CLI IS required
  - but a running Docker DAEMON is not: `docker compose config` (and so
    `check-exposure.sh`, and so `--fast`) is a client-side operation --
    parses the compose files, resolves `${VAR}` interpolation and profiles,
    does not call the daemon at all.

That last point was confirmed for real on 2026-07-31 by stopping the local
Docker daemon outright (`colima stop`, not just `docker compose down`) and
running `scripts/verify.sh --fast` against it: green, ~5s, 27 pytest passes
included. This file is what stops that claim drifting back to false the next
time someone adds a check to `--fast` that assumes a daemon is there.

RECURSION GUARD: `scripts/verify.sh --fast`'s own last step is
`pytest tests/ apps/console/tests/`, which collects this very file. A test
here that shells out to `scripts/verify.sh --fast` would therefore spawn a
second `--fast` run whose own pytest step collects this file again --
unbounded recursion (confirmed the hard way: an earlier draft of this file
forked 168+ nested pytest/verify.sh processes before being killed). Every
subprocess call below sets _GUARD_ENV in the child's environment and checks
it first -- present means "already inside a `--fast` invoked by this file",
so the test skips instead of shelling out again. This caps recursion at
exactly one extra level.

test_fast_succeeds_with_docker_daemon_unreachable does NOT itself stop the
system Docker daemon -- doing that from inside a test would mean either
fighting for root on Linux or touching machine state a CI runner's other
jobs may depend on, which is a worse trade than the thing it is testing.
Instead it points DOCKER_HOST at a socket path that does not exist. `docker
info` against that path fails with the identical error family observed
against the truly stopped daemon ("connect: no such file or directory"),
which is what `docker compose config` would see in both cases: no daemon to
dial. Confirmed equivalent by hand against the real daemon-down case above
before relying on it here.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

PACK = pathlib.Path(__file__).resolve().parent.parent
_GUARD_ENV = "KP2_TEST_TIERS_NESTED"


def _run_fast(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    if os.environ.get(_GUARD_ENV):
        pytest.skip(
            "nested inside scripts/verify.sh --fast's own pytest step -- "
            "see this module's RECURSION GUARD docstring section"
        )
    env = {**os.environ, _GUARD_ENV: "1", **(extra_env or {})}
    return subprocess.run(
        ["scripts/verify.sh", "--fast"],
        cwd=PACK,
        capture_output=True,
        text=True,
        env=env,
    )


def test_fast_succeeds_with_no_federation():
    """scripts/verify.sh --fast must succeed with no containers running --
    it never touches one. This is the contract; it does not depend on
    whether a federation happens to be up on the machine running the test."""
    result = _run_fast()
    assert result.returncode == 0, (
        f"scripts/verify.sh --fast failed:\n{result.stdout}\n{result.stderr}"
    )


def test_fast_succeeds_with_docker_daemon_unreachable():
    """--fast must also succeed with no Docker DAEMON reachable -- a
    stronger claim than "no containers", since check-exposure.sh still
    shells out to `docker compose config`. See this module's docstring for
    why DOCKER_HOST is redirected here instead of stopping the real daemon,
    and for the manual confirmation that the two are equivalent."""
    fake_docker_host = f"unix://{PACK}/.nonexistent-docker-for-test.sock"
    result = _run_fast({"DOCKER_HOST": fake_docker_host})
    assert result.returncode == 0, (
        f"scripts/verify.sh --fast failed with DOCKER_HOST unreachable:\n"
        f"{result.stdout}\n{result.stderr}"
    )
