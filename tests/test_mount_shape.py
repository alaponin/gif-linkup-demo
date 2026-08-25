"""join-api's monorepo mount is read-only, and its writable set stays small.

join-api parses applicant-controlled payloads, and it used to bind-mount the
WHOLE monorepo read-write -- scripts/, apps/, infra/, .git and .env included
-- while host scripts running as root sourced .env and hurl/topology.sh out
of that same tree. Code execution inside the container was therefore root on
the host at the next `scripts/console.sh status`.

The mount half of the fix is `../../..:/repo:ro` plus a named read-write
child per path a join actually writes. Docker mounts each path
independently, so the boundary is the kernel's, not file ownership's. This
test stops the next read-write mount from being added outside that set --
which is the shape the regression would take: someone needs one more
directory writable, adds `- ./scripts:...` without the `:ro`, and the whole
escalation is back with every other part of the fix still in place.
"""
from __future__ import annotations

import pathlib

import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((PACK / "docker-compose.yml").read_text())

_PACK_IN_REPO = "/repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack"

# Exactly apps/join-api/writer.py's _written_paths (manifest.yaml,
# onboarding/ for the catalogue and onboarding/<key>/, configs/ for
# member-<key>/), plus out/ for the join store and hurl/ for hurl/
# generate.py's own outputs. Adding to this set is a deliberate act with a
# security question attached; that is the point of writing it down here.
_WRITABLE = {
    "join-api": {
        f"{_PACK_IN_REPO}/configs",
        f"{_PACK_IN_REPO}/manifest.yaml",
        f"{_PACK_IN_REPO}/onboarding",
        f"{_PACK_IN_REPO}/out",
        f"{_PACK_IN_REPO}/hurl",
    },
    # The console reconstructs a curated read-only /pack and writes only its
    # ACL journal, under /out.
    "console": {"/out"},
}

# The four X-Road sidecars mount hurl/local.ini (one of generate.py's own
# outputs, so join-api-writable) read-write, into vendor images this repo
# does not build or control. Nothing here reads it back on the host, so it
# is not a container-to-host-root escalation -- but it IS the same "a file
# join-api can write is consumed by something else" shape as
# hurl/compose.members.yml, and both are recorded as open residuals in
# docs/production-delta.md rather than closed here. Listed so a genuinely
# NEW service still fails the test below.
_KNOWN_UNCHANGED = {"/etc/xroad/conf.d/local.ini"}


def _bind_mounts(service: str) -> list[str]:
    """Host-path mounts (./x, ../x, /x, or a ${VAR} that expands to one).
    Named volumes carry no host path and are not what this test is about."""
    return [
        v for v in (COMPOSE["services"][service].get("volumes") or [])
        if isinstance(v, str) and v.split(":")[0].startswith((".", "/", "$"))
    ]


def _target_of(mount: str) -> str:
    """The container-side path of a `host:container[:mode]` mount. Split from
    the right, so a `${VAR:-/default}` host side containing no colon is safe
    and a trailing :ro is not mistaken for the target."""
    parts = mount.rsplit(":", 2)
    if len(parts) == 3 and parts[2] in ("ro", "rw", "z", "Z"):
        return parts[1]
    return mount.rsplit(":", 1)[-1] if mount.count(":") else mount


def test_join_api_mounts_the_monorepo_read_only():
    assert "../../..:/repo:ro" in _bind_mounts("join-api"), (
        "join-api must mount the monorepo read-only. Without the :ro every "
        "other safeguard here is decoration: the container can rewrite "
        "scripts/lib-stack.sh, apps/join-api/writer.py or .env directly."
    )


def test_join_api_mounts_dot_git_read_only():
    """A writable .git is a writable .git/hooks -- code the HOST's next git
    command runs, as root on the droplet. Every git call this container makes
    is a read (writer.py's own docstring)."""
    git_mounts = [m for m in _bind_mounts("join-api") if "GIT_COMMON_DIR" in m]
    assert git_mounts, "the KP2_GIT_COMMON_DIR mount vanished -- see docker-compose.yml"
    for mount in git_mounts:
        assert mount.endswith(":ro"), f"{mount} must be read-only"


def test_no_read_write_mount_outside_the_known_writable_set():
    # EVERY service, not just the two in _WRITABLE: a service absent from that
    # table is allowed no read-write host mount at all, which is the honest
    # default for one nobody has thought about yet.
    for service in COMPOSE["services"]:
        allowed = _WRITABLE.get(service, set())
        for mount in _bind_mounts(service):
            if mount.endswith(":ro"):
                continue
            target = _target_of(mount)
            if target in _KNOWN_UNCHANGED:
                continue
            assert target in allowed, (
                f"{service} mounts {mount!r} read-write, and {target} is not "
                f"in the writable set {sorted(allowed)}. join-api parses "
                f"applicant-controlled payloads: a new read-write path is a "
                f"new way for one to reach the host. Either add `:ro`, or "
                f"add the path here with the reason it has to be writable."
            )


if __name__ == "__main__":
    test_join_api_mounts_the_monorepo_read_only()
    test_join_api_mounts_dot_git_read_only()
    test_no_read_write_mount_outside_the_known_writable_set()
    print("ok")
