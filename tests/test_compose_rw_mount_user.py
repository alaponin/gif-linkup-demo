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
    files into the developer's own checkout. lib-stack.sh must actually set them."""
    lib = (PACK / "scripts/lib-stack.sh").read_text()
    assert "export KP2_HOST_UID=$(id -u)" in lib
    assert "export KP2_HOST_GID=$(id -g)" in lib


if __name__ == "__main__":
    test_rw_bind_mount_services_do_not_run_as_a_fixed_foreign_user()
    test_lib_stack_exports_the_ids_compose_interpolates()
    print("ok")
