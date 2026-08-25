"""A service that bind-mounts host files READ-WRITE must run as their owner.

Found live on the droplet: join-api's image ends in `USER nobody`, its whole
purpose is writing the bind-mounted checkout (out/join records, configs/
member-*/, manifest.yaml, generate.py's output), and on the droplet that
checkout is root-owned -- the workflow rsyncs it --chown=root:root so git does
not refuse it as dubious ownership. So the container died on its first
mkdir("out/join") with EACCES. The console had the identical bug one revoke
away: same `USER nobody`, ./out mounted read-write, journal never yet written.

Neither failed locally because Docker Desktop's macOS bind mounts virtualise
ownership -- the class of bug that only appears on a real Linux host, i.e. only
in the deploy that matters. The fix is compose's `user:` (KP2_HOST_UID, exported
by scripts/lib-stack.sh); this test stops the next read-write service from being
added without it.

KP2_HOST_UID is no longer a bare `id -u`: on the droplet `id -u` was 0, so
join-api parsed applicant payloads as root. lib-stack.sh resolves it from the
`kp2` account when the host has one, with KP2_CONTAINER_UID as an override --
resolved from the HOST rather than from an exported variable because the
first version of this fix relied on remote-deploy.sh's export and
remote-deploy.sh is the one CI script that never starts these containers. A
laptop has no kp2 account, so it still
gets the developer's own id. What this test asserts is unchanged either way:
a service that bind-mounts host files read-write must follow that owner,
never pin a fixed foreign id of its own.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import tempfile

import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((PACK / "docker-compose.yml").read_text())

_USER = re.compile(r"^\s*USER\s+(\S+)", re.M)


def _rw_bind_mounts(svc: dict) -> list[str]:
    """Host-path mounts (./x, ../x) that are not :ro. Named volumes are not
    host files and carry no host ownership, so they are not at issue."""
    return [
        v for v in (svc.get("volumes") or [])
        if isinstance(v, str) and v.split(":")[0].startswith((".", "/")) and not v.endswith(":ro")
    ]


def _image_user(svc: dict) -> str | None:
    build = svc.get("build")
    if not isinstance(build, str):
        return None
    dockerfile = PACK / build / "Dockerfile"
    if not dockerfile.is_file():
        return None
    found = _USER.findall(dockerfile.read_text())
    return found[-1] if found else None


def test_rw_bind_mount_services_do_not_run_as_a_fixed_foreign_user():
    for name, svc in COMPOSE["services"].items():
        mounts = _rw_bind_mounts(svc)
        image_user = _image_user(svc)
        if not mounts or image_user in (None, "root", "0"):
            continue
        assert svc.get("user"), (
            f"{name}: image runs as {image_user!r} and bind-mounts {mounts} "
            f"read-write -- on a Linux host it cannot write them. Add "
            f'user: "${{KP2_HOST_UID:-0}}:${{KP2_HOST_GID:-0}}".'
        )
        assert "KP2_HOST_UID" in svc["user"], (
            f"{name}: user: {svc['user']!r} pins an id instead of following the "
            f"host checkout's owner (KP2_HOST_UID, exported by lib-stack.sh)."
        )


def test_lib_stack_exports_the_ids_compose_interpolates():
    """The default in compose is 0:0; unexported, a laptop would write root-owned
    files into the developer's own checkout. lib-stack.sh must actually set them
    -- and with neither KP2_CONTAINER_UID nor a kp2 account (every laptop,
    --fast included) it must still resolve to `id -u`, unchanged."""
    lib = (PACK / "scripts/lib-stack.sh").read_text()
    assert "export KP2_HOST_UID=${KP2_CONTAINER_UID:-$(id -u)}" in lib
    assert "export KP2_HOST_GID=${KP2_CONTAINER_GID:-$(id -g)}" in lib

    deploy = (PACK / "infra/ci/remote-deploy.sh").read_text()
    assert "export KP2_CONTAINER_UID=10001" in deploy
    assert "export KP2_CONTAINER_GID=10001" in deploy


def test_the_droplet_identity_exists_and_owns_what_the_containers_write():
    """The uid remote-deploy.sh pins has to be a real, unprivileged account on
    the droplet -- cloud-init creates it, and remote-deploy.sh recreates it,
    because `terraform apply` refreshes an existing droplet without re-running
    cloud-init. And it has to own the writable set, or join-api dies on its
    first mkdir exactly as it did as `nobody`."""
    cloud_init = (PACK / "infra/terraform/cloud-init.yaml").read_text()
    assert "useradd -u 10001 -g kp2 -M -s /usr/sbin/nologin kp2" in cloud_init

    deploy = (PACK / "infra/ci/remote-deploy.sh").read_text()
    assert "useradd -u 10001 -g kp2 -M -s /usr/sbin/nologin kp2" in deploy
    assert "chown -R kp2:kp2 configs manifest.yaml onboarding out" in deploy
    # hurl/ holds root-owned code (generate.py, steps.py) beside container-
    # written output. Sticky, so a non-owner cannot unlink or rename the code.
    assert "chmod 3775 hurl" in deploy
    # .env: readable by the containers (generate.py's read_env), writable by
    # neither.
    assert "chmod 640 .env" in deploy

    # ...and the generated files INSIDE that sticky directory must end up
    # kp2's, not root's. They are gitignored, so `rsync --delete` removes them
    # and lib-stack.sh regenerates them by running generate.py as root during
    # the deploy -- left root-owned 644 under the sticky bit, the container
    # could neither rewrite nor unlink hurl/topology.json and the next join
    # would fail. Which is also why the handover runs again on the way out.
    assert 'chown -R kp2:kp2 "hurl/$generated"' in deploy
    assert "trap 'harden_container_paths || true' EXIT" in deploy
    for generated in ("scenarios", "vars.env", "local.ini", "topology.json",
                      "topology.sh", "compose.members.yml"):
        assert generated in deploy, (
            f"hurl/{generated} is one of hurl/generate.py's outputs (see "
            f".gitignore) but remote-deploy.sh does not hand it to kp2"
        )


def test_every_ci_script_that_starts_these_containers_sets_the_identity():
    """The test that would have caught it.

    The first version of this work asserted only that remote-deploy.sh
    exports KP2_CONTAINER_UID -- and remote-deploy.sh is the one CI script
    that never starts the console or join-api. infra/ci/console-publish.sh
    does, in its own ssh session, so both came up as UID 0 on every normal
    deploy and stayed there (`restart: unless-stopped`), with the whole
    ownership/sticky-bit backstop bypassed by CAP_DAC_OVERRIDE. Assert
    against what the scripts DO, not against the one we happened to edit.

    lib-stack.sh resolving the identity from the `kp2` account is the fix
    that cannot be forgotten; this keeps the explicit statement at the call
    sites, and catches a new CI script that starts a container by a route
    lib-stack.sh is not on -- which is exactly what db-sync-remote.sh is.
    """
    starts = re.compile(r"(console|join)\.sh\"?\s+up|docker compose run\b")
    for script in sorted((PACK / "infra" / "ci").glob("*.sh")):
        text = script.read_text()
        # Code, not prose: infra/ci/db-sync.sh runs on the CI RUNNER and only
        # MENTIONS `docker compose run` in a comment about the script it pipes
        # over ssh. Matching comments would demand an export from a script
        # that starts nothing.
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        if not starts.search(code):
            continue
        # Either name works: KP2_CONTAINER_UID for a script that goes through
        # lib-stack.sh, KP2_HOST_UID for one (db-sync-remote.sh) that reaches
        # docker compose directly and so is not on lib-stack.sh's path.
        assert re.search(r"^export KP2_(CONTAINER|HOST)_UID=", text, re.M), (
            f"infra/ci/{script.name} starts a container that bind-mounts this "
            f"checkout, but exports neither KP2_CONTAINER_UID nor "
            f"KP2_HOST_UID -- so docker-compose.yml's `${{KP2_HOST_UID:-0}}` "
            f"default runs it as UID 0 against those bind-mounted, "
            f"applicant-writable paths."
        )
        assert re.search(r"^export KP2_(CONTAINER|HOST)_GID=", text, re.M), (
            f"infra/ci/{script.name} sets a uid but not a gid"
        )


def test_lib_stack_resolves_the_identity_from_the_host_not_from_an_export():
    """`id -u kp2` on a host that has the account, whoever is running.

    The export in remote-deploy.sh covers one process; this covers every
    caller, including console-publish.sh's separate ssh session and an
    operator running `scripts/join.sh up` by hand.
    """
    lib = (PACK / "scripts/lib-stack.sh").read_text()
    assert "_kp2_uid=$(id -u kp2 2>/dev/null)" in lib

    # ...and prove it, rather than only reading it: a stub `id` that answers
    # for a kp2 account must move KP2_HOST_UID off this process's own uid.
    stub = pathlib.Path(tempfile.mkdtemp()) / "bin"
    stub.mkdir()
    (stub / "id").write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in *kp2*) [ "$1" = -u ] && echo 10001 || echo 10002;; '
        '*) exec /usr/bin/id "$@";; esac\n'
    )
    (stub / "id").chmod(0o755)
    script = f'. "{PACK}/scripts/lib-stack.sh" >/dev/null 2>&1; echo "$KP2_HOST_UID:$KP2_HOST_GID"'
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}
    env.pop("KP2_CONTAINER_UID", None)
    env.pop("KP2_CONTAINER_GID", None)
    got = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert got.stdout.strip() == "10001:10002", (got.stdout, got.stderr)

    # No kp2 account (every laptop): the developer's own id, unchanged. This
    # is the invariant lib-stack.sh must preserve no matter which branch it
    # resolves through.
    plain = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env | {"PATH": os.environ["PATH"]})
    assert plain.stdout.strip() == f"{os.getuid()}:{os.getgid()}", (plain.stdout, plain.stderr)


if __name__ == "__main__":
    test_rw_bind_mount_services_do_not_run_as_a_fixed_foreign_user()
    test_lib_stack_exports_the_ids_compose_interpolates()
    test_the_droplet_identity_exists_and_owns_what_the_containers_write()
    test_every_ci_script_that_starts_these_containers_sets_the_identity()
    test_lib_stack_resolves_the_identity_from_the_host_not_from_an_export()
    print("ok")
