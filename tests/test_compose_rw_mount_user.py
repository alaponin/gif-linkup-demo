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

KP2_HOST_UID is now `${KP2_CONTAINER_UID:-$(id -u)}`, not a bare `id -u`
(docs/security-review-2026-08-23.md, finding H1: on the droplet `id -u` was 0,
so join-api parsed applicant payloads as root). infra/ci/remote-deploy.sh
exports KP2_CONTAINER_UID=10001 -- the dedicated `kp2` identity that owns
exactly the paths those containers may write -- and nothing else does, so a
laptop still gets the developer's own id and the mounts it writes are still
its own. What this test asserts is unchanged either way: a service that
bind-mounts host files read-write must follow that owner, never pin a fixed
foreign id of its own.
"""
from __future__ import annotations

import pathlib
import re

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
    -- and with KP2_CONTAINER_UID unset (every laptop, --fast included) it must
    still resolve to `id -u`, unchanged. The indirection exists for exactly one
    caller, infra/ci/remote-deploy.sh."""
    lib = (PACK / "scripts/lib-stack.sh").read_text()
    assert "export KP2_HOST_UID=${KP2_CONTAINER_UID:-$(id -u)}" in lib
    assert "export KP2_HOST_GID=${KP2_CONTAINER_GID:-$(id -g)}" in lib

    deploy = (PACK / "infra/ci/remote-deploy.sh").read_text()
    assert "export KP2_CONTAINER_UID=10001" in deploy
    assert "export KP2_CONTAINER_GID=10001" in deploy

    # remote-deploy.sh's export covers CI only -- an operator running
    # `scripts/join.sh up` by hand on the droplet is a later SSH session that
    # never saw it, and would put the containers back on UID 0. Warned about,
    # deliberately not refused (`join.sh down` is a documented kill switch).
    assert '[ "$POSTURE" = "production" ] && [ -z "${KP2_CONTAINER_UID:-}" ]' in lib


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


if __name__ == "__main__":
    test_rw_bind_mount_services_do_not_run_as_a_fixed_foreign_user()
    test_lib_stack_exports_the_ids_compose_interpolates()
    test_the_droplet_identity_exists_and_owns_what_the_containers_write()
    print("ok")
