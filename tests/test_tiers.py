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
included.

These tests exercise `scripts/check-exposure.sh` directly rather than the
whole `scripts/verify.sh --fast` pipeline. check-exposure.sh is the *only*
step `--fast` runs that touches Docker at all -- `check_scenarios.py`, the
kp-solution-verify ship gate, and the rest of `pytest tests/
apps/console/tests/` are plain Python with no `docker` calls in them, so
they need no coverage here. (An earlier draft of this file called
`scripts/verify.sh --fast` directly instead. `--fast`'s own last step is
`pytest tests/ apps/console/tests/`, which collects this file -- so that
call recursed into a second, nested `--fast` per test, tripling every real,
non-nested `--fast` run's cost forever and needing a recursion guard to
avoid forking without bound. Caught in review; calling check-exposure.sh
directly removes both problems and is a closer test of the actual claim.)

test_check_exposure_succeeds_with_docker_daemon_unreachable does NOT itself
stop the system Docker daemon -- doing that from inside a test would mean
either fighting for root on Linux or touching machine state a CI runner's
other jobs may depend on, which is a worse trade than the thing it is
testing. Instead it points DOCKER_HOST at a socket path that does not
exist. `docker info` against that path fails with the identical error
family observed against the truly stopped daemon ("connect: no such file or
directory"), which is what `docker compose config` would see in both cases:
no daemon to dial. Confirmed equivalent by hand against the real
daemon-down case above before relying on it here.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

PACK = pathlib.Path(__file__).resolve().parent.parent


def _run_check_exposure(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        ["scripts/check-exposure.sh"],
        cwd=PACK,
        capture_output=True,
        text=True,
        env=env,
    )


def test_check_exposure_succeeds_with_no_federation():
    """check-exposure.sh -- the one --fast step that shells out to Docker --
    must succeed with no containers running. It renders Compose config and
    never queries container state, so this holds regardless of whether a
    federation happens to be up on the machine running the test; this
    assertion is what stops that from being just an assumption."""
    result = _run_check_exposure()
    assert result.returncode == 0, (
        f"scripts/check-exposure.sh failed:\n{result.stdout}\n{result.stderr}"
    )


def test_check_exposure_succeeds_with_docker_daemon_unreachable():
    """check-exposure.sh must also succeed with no Docker DAEMON reachable
    -- a stronger claim than "no containers". See this module's docstring
    for why DOCKER_HOST is redirected here instead of stopping the real
    daemon, and for the manual confirmation that the two are equivalent."""
    fake_docker_host = f"unix://{PACK}/.nonexistent-docker-for-test.sock"
    result = _run_check_exposure({"DOCKER_HOST": fake_docker_host})
    assert result.returncode == 0, (
        f"scripts/check-exposure.sh failed with DOCKER_HOST unreachable:\n"
        f"{result.stdout}\n{result.stderr}"
    )
