"""Mechanically enforce the fast tier's contract -- lib-split-and-tier-honesty
plan. Three documents (README.md's "Verify a change", the CI
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

That last point was confirmed for real by stopping the local
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
    daemon, and for the manual confirmation that the two are equivalent.

    The fake socket lives under /tmp, not under PACK: PACK's absolute path
    varies by checkout location, and on a deeply nested CI checkout it pushes
    the socket path past the kernel's ~108-byte sun_path limit, making
    `docker` fail with "unix socket path ... too long" instead of the
    intended "no such file or directory" -- a different error that breaks
    the equivalence this test relies on."""
    fake_docker_host = f"unix:///tmp/.nonexistent-docker-for-test-{os.getpid()}.sock"
    result = _run_check_exposure({"DOCKER_HOST": fake_docker_host})
    assert result.returncode == 0, (
        f"scripts/check-exposure.sh failed with DOCKER_HOST unreachable:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_check_exposure_fails_on_unacknowledged_public_bind(tmp_path):
    """Regression test: check-exposure.sh duplicates lib-stack.sh's
    XROAD_BIND/XROAD_VERSION/XROAD_CS_TAG/TESTCA_TAG exports (deliberately --
    see check-exposure.sh's own comment for why it can't just source
    lib-stack.sh). If a new `${VAR}` substitution were ever added to
    docker-compose.yml's `ports:` and exported from lib-stack.sh only,
    check-exposure.sh would silently render it via Compose's own fallback
    default and pass a config that is actually publicly exposed -- a class
    of bug that previously had no automated guard against it recurring,
    caught only once, by hand. This exercises the check end-to-end (via
    check-exposure.sh's KP2_DEPLOY_SPEC override, added for this test)
    against a deployment.yaml
    with network.bind: 0.0.0.0 and no acknowledge_public_exposure: it must
    fail, non-zero, and name the exposed ports."""
    text = (PACK / "deployment.yaml").read_text()
    assert "bind: 127.0.0.1" in text, "deployment.yaml no longer binds loopback by default -- update this fixture"
    tmp_deploy = tmp_path / "deployment.yaml"
    tmp_deploy.write_text(text.replace("bind: 127.0.0.1", "bind: 0.0.0.0"))

    result = _run_check_exposure({"KP2_DEPLOY_SPEC": str(tmp_deploy)})
    assert result.returncode != 0, (
        f"scripts/check-exposure.sh should fail on network.bind: 0.0.0.0 with "
        f"no acknowledge_public_exposure, but passed:\n{result.stdout}\n{result.stderr}"
    )
    assert "0.0.0.0" in result.stdout, f"expected exposed ports listed in output:\n{result.stdout}"


def test_lib_stack_refuses_non_loopback_bind_under_production_posture(tmp_path):
    """security-review-remediation-plan.md Phase A (H3): scripts/lib-stack.sh
    refuses posture: production with a non-loopback network.bind outright --
    no acknowledge_public_exposure setting can override it, mirroring the
    Test CA rule immediately below it in that file. Sourced (via
    KP2_DEPLOY_SPEC, the same test-only override check-exposure.sh already
    has) rather than run as its own script -- lib-stack.sh's own header says
    it is meant to be sourced, not executed. The real .env's credentials are
    used as-is (this repo's own dev .env, real values -- lib-stack.sh's
    credential refusal would otherwise fire first and mask the check this
    test targets); the refusal this test targets is reached, and the process
    exits, before anything Docker-related runs."""
    text = (PACK / "deployment.yaml").read_text()
    assert "posture: demo" in text, "deployment.yaml no longer states posture: demo by default -- update this fixture"
    assert "bind: 127.0.0.1" in text, "deployment.yaml no longer binds loopback by default -- update this fixture"
    tmp_deploy = tmp_path / "deployment.yaml"
    tmp_deploy.write_text(
        text.replace("posture: demo", "posture: production").replace("bind: 127.0.0.1", "bind: 0.0.0.0")
    )

    result = subprocess.run(
        ["bash", "-c", ". scripts/lib-stack.sh"],
        cwd=PACK,
        capture_output=True,
        text=True,
        env={**os.environ, "KP2_DEPLOY_SPEC": str(tmp_deploy)},
    )
    assert result.returncode != 0, (
        f"scripts/lib-stack.sh should refuse posture: production with a "
        f"non-loopback network.bind, but passed:\n{result.stdout}\n{result.stderr}"
    )
    assert "posture: production" in result.stderr, f"expected the refusal to name posture: production:\n{result.stderr}"
    assert "no acknowledge_public_exposure setting" in result.stderr, result.stderr
