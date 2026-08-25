"""scripts/member.sh drift's reporting, offline.

`refresh` is what remedies drift, and it needs a live Security Server admin
API -- so it is proved by running exercise 3 end to end, not here. What IS
testable without a federation is the half a reader actually acts on: whether
drift can tell "moved since join" from "moved since the operator last
remediated". That distinction is the whole reason `refresh` amends the record
instead of rewriting endpoint_baseline, and getting it backwards produces a
warning that either never clears or clears when it should not.

No Docker, no admin API, no join-api: a temp pack, a synthetic ACTIVE join
record seeded straight into the SQLite join store (the same technique
tests/test_migrate_join_store.py uses -- import store.py and call into it),
and a local http.server serving the "current" spec -- which is a real
fetch, because that is what drift does.

drift's fetch now runs origin.py's origin_error before it and
no_redirect_opener for it, which unconditionally refuses an IP-literal
spec_url -- so the fixture spec_url below is a host NAME, not the local
http.server's raw 127.0.0.1,
and a `sitecustomize.py` dropped onto the drift subprocess's PYTHONPATH
resolves that name back to 127.0.0.1 (no real DNS record, no /etc/hosts
edit, no root needed -- the module the `site` module auto-imports at
interpreter startup, patching socket.getaddrinfo before member.sh's own
python3 heredoc ever runs).
"""
from __future__ import annotations

import http.server
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

import pytest
import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
PORT = 18766
SERVICE = "awards-api"
SPEC_HOST = "member-drift-test-host"

# apps/join-api/ is not a package -- same reason
# scripts/migrate-join-store.py inserts this directory onto sys.path
# (that script's own comment). Only store.py is needed here.
sys.path.insert(0, str(PACK / "apps" / "join-api"))
import store  # noqa: E402


class _SpecHandler(http.server.BaseHTTPRequestHandler):
    paths: list[str] = []

    def do_GET(self):  # noqa: N802 -- stdlib method name
        body = yaml.safe_dump({
            "openapi": "3.0.0",
            "info": {"title": "awards", "version": "1"},
            "servers": [{"url": f"http://127.0.0.1:{PORT}"}],
            "paths": {p: {"get": {"responses": {"200": {}}}} for p in self.paths},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/yaml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest -q output clean
        pass


@pytest.fixture(scope="module", autouse=True)
def _spec_server():
    server = http.server.HTTPServer(("127.0.0.1", PORT), _SpecHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield
    server.shutdown()
    thread.join()


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """Stands in for a host that would walk a followed redirect straight
    off join.spec_url_hosts -- no_redirect_opener refuses the redirect
    outright, so where it points never actually matters, only that it is
    never followed."""

    def do_GET(self):  # noqa: N802 -- stdlib method name
        self.send_response(302)
        self.send_header("Location", "http://evil.example.com/spec.yaml")
        self.end_headers()

    def log_message(self, *args):  # keep pytest -q output clean
        pass


@pytest.fixture(scope="module")
def _redirect_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    thread.join()


# A sitecustomize.py every drift subprocess picks up via PYTHONPATH (below),
# resolving SPEC_HOST to the local http.server's real 127.0.0.1 -- see the
# module docstring for why a hostname is needed at all now that
# origin_error refuses IP literals unconditionally. Built once, at import
# time, not per-test: its content is fixed, so a pytest fixture would only
# add indirection every test function would have to name.
_DRIFT_SITE_DIR = pathlib.Path(tempfile.mkdtemp(prefix="kp2-drift-sitecustomize-"))
(_DRIFT_SITE_DIR / "sitecustomize.py").write_text(
    "import socket\n"
    "_orig_getaddrinfo = socket.getaddrinfo\n"
    f"_MAP = {{{SPEC_HOST!r}: '127.0.0.1'}}\n"
    "def _patched(host, *a, **kw):\n"
    "    return _orig_getaddrinfo(_MAP.get(host, host), *a, **kw)\n"
    "socket.getaddrinfo = _patched\n"
)


@pytest.fixture
def pack(tmp_path):
    """Just enough pack for scripts/member.sh drift: lib-core.sh resolves
    PACK_DIR from its own location, so the script has to run from a copy.
    Now also needs configs/x-road-bus/join-policy.yaml (drift's origin
    check reads join.spec_url_hosts from it) and apps/join-api/origin.py
    (drift imports origin_error/no_redirect_opener from it, stdlib-only so
    a plain copy is enough -- no venv, no other apps/join-api module)."""
    pack = tmp_path / "pack"
    (pack / "scripts").mkdir(parents=True)
    for name in ("member.sh", "lib-core.sh"):
        shutil.copy(PACK / "scripts" / name, pack / "scripts" / name)
    (pack / "apps" / "join-api").mkdir(parents=True)
    shutil.copy(PACK / "apps" / "join-api" / "origin.py", pack / "apps" / "join-api" / "origin.py")
    (pack / "configs" / "x-road-bus").mkdir(parents=True)
    (pack / "configs" / "x-road-bus" / "join-policy.yaml").write_text(yaml.safe_dump({
        "join": {"spec_url_hosts": [SPEC_HOST]},
    }))
    member_dir = pack / "configs" / "member-ptsb"
    member_dir.mkdir(parents=True)
    (member_dir / "ptsb.yaml").write_text(yaml.safe_dump({
        "module": "member-ptsb",
        "services": [{"code": SERVICE, "spec_url": f"http://{SPEC_HOST}:{PORT}/spec.yaml"}],
    }))
    return pack


def _record(pack, *, baseline, refreshes=None):
    record = {
        "id": "abc123",
        "state": "ACTIVE",
        "submitted_at": "2026-08-01T00:00:00+00:00",
        "payload": {"code": "PTSB"},
        "endpoint_baseline": {SERVICE: baseline},
    }
    if refreshes is not None:
        record["refreshes"] = refreshes
    conn = store.connect(store.init(pack / "out"))
    store.save_request(conn, record, actor="system", event="test-seed")
    conn.close()


def _drift(pack):
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{_DRIFT_SITE_DIR}{os.pathsep}{existing}" if existing else str(_DRIFT_SITE_DIR)
    )
    return subprocess.run(
        ["bash", str(pack / "scripts" / "member.sh"), "drift", "ptsb"],
        capture_output=True, text=True, env=env,
    )


def test_no_drift_when_the_spec_still_serves_the_join_time_endpoints(pack):
    _SpecHandler.paths = ["/awards/{nin}"]
    _record(pack, baseline=["/awards/{nin}"])
    result = _drift(pack)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no drift" in result.stdout


def test_drift_since_join_with_no_refresh_names_the_remedy(pack):
    """The gap this item closes: before `refresh` existed, drift reported and
    the operator had nothing to do about it."""
    _SpecHandler.paths = ["/awards/{nin}", "/awards"]
    _record(pack, baseline=["/awards/{nin}"])
    result = _drift(pack)
    assert result.returncode == 1
    assert "DRIFT since join" in result.stdout
    assert "+ /awards" in result.stdout
    assert "scripts/member.sh refresh ptsb" in result.stdout


def test_a_recorded_refresh_clears_the_warning_without_touching_the_baseline(pack):
    """The federation now publishes what the spec serves. The contract still
    differs from the one this member was ADMITTED on, and drift still says
    so -- endpoint_baseline is evidence, not a moving target -- but there is
    nothing left for the operator to do, so this exits 0."""
    _SpecHandler.paths = ["/awards/{nin}", "/awards"]
    _record(pack, baseline=["/awards/{nin}"], refreshes=[
        {"at": "2026-08-10T00:00:00+00:00", "endpoints": {SERVICE: ["/awards/{nin}", "/awards"]}},
    ])
    result = _drift(pack)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRIFT since join" in result.stdout
    assert "clean since the last refresh" in result.stdout


def test_drift_after_the_last_refresh_is_reported_again(pack):
    """A member that moves twice must not stay silent because it was
    refreshed once."""
    _SpecHandler.paths = ["/awards/{nin}", "/awards", "/awards/summary"]
    _record(pack, baseline=["/awards/{nin}"], refreshes=[
        {"at": "2026-08-10T00:00:00+00:00", "endpoints": {SERVICE: ["/awards/{nin}", "/awards"]}},
    ])
    result = _drift(pack)
    assert result.returncode == 1
    assert "DRIFT since the last refresh" in result.stdout
    assert "+ /awards/summary" in result.stdout


def test_a_refresh_that_did_not_cover_this_service_does_not_clear_it(pack):
    _SpecHandler.paths = ["/awards/{nin}", "/awards"]
    _record(pack, baseline=["/awards/{nin}"], refreshes=[
        {"at": "2026-08-10T00:00:00+00:00", "endpoints": {"other-api": ["/other"]}},
    ])
    result = _drift(pack)
    assert result.returncode == 1
    assert "did not cover this service" in result.stdout


def test_the_baseline_is_never_rewritten_by_reading_drift(pack):
    """drift is a read. If it ever amended the record, the join-time evidence
    would quietly become whatever was last observed."""
    _SpecHandler.paths = ["/awards/{nin}", "/awards"]
    _record(pack, baseline=["/awards/{nin}"])
    _drift(pack)
    conn = store.connect(store.init(pack / "out"))
    record = store.load_request(conn, "abc123")
    conn.close()
    assert record["endpoint_baseline"][SERVICE] == ["/awards/{nin}"]
    assert "refreshes" not in record


# -- the origin check: refuse IP-literal spec_urls and redirects off the
# -- declared allowlist ------------------------------------------------------


def test_drift_refuses_a_redirect_rather_than_walking_it_off_the_allowlist(pack, _redirect_server):
    """spec_url itself is on join.spec_url_hosts (SPEC_HOST) -- origin_error
    lets it through -- but the server answers with a 302 to a host nobody
    declared. Before this check existed, urllib.request.urlopen would have
    followed that redirect with no allowlist and no IP-literal refusal at all
    (docs/production-delta.md row 41). no_redirect_opener refuses it
    outright, so this is reported the same way an unreachable spec is:
    printed, drift continues to the next service, and the run still exits
    non-zero."""
    port = _redirect_server
    member_dir = pack / "configs" / "member-ptsb"
    (member_dir / "ptsb.yaml").write_text(yaml.safe_dump({
        "module": "member-ptsb",
        "services": [{"code": SERVICE, "spec_url": f"http://{SPEC_HOST}:{port}/spec.yaml"}],
    }))
    _record(pack, baseline=["/awards/{nin}"])
    result = _drift(pack)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "could not fetch current spec" in result.stdout
    assert "redirect refused" in result.stdout
