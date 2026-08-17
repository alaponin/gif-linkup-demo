"""scripts/member.sh drift's reporting, offline.

`refresh` is what remedies drift, and it needs a live Security Server admin
API -- so it is proved by running exercise 3 end to end, not here. What IS
testable without a federation is the half a reader actually acts on: whether
drift can tell "moved since join" from "moved since the operator last
remediated". That distinction is the whole reason `refresh` amends the record
instead of rewriting endpoint_baseline, and getting it backwards produces a
warning that either never clears or clears when it should not.

No Docker, no admin API, no join-api: a temp pack, a synthetic ACTIVE join
record, and a local http.server serving the "current" spec -- which is a real
fetch, because that is what drift does.
"""
from __future__ import annotations

import http.server
import json
import pathlib
import shutil
import subprocess
import threading

import pytest
import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
PORT = 18766
SERVICE = "awards-api"


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


@pytest.fixture
def pack(tmp_path):
    """Just enough pack for scripts/member.sh drift: lib-core.sh resolves
    PACK_DIR from its own location, so the script has to run from a copy."""
    pack = tmp_path / "pack"
    (pack / "scripts").mkdir(parents=True)
    for name in ("member.sh", "lib-core.sh"):
        shutil.copy(PACK / "scripts" / name, pack / "scripts" / name)
    member_dir = pack / "configs" / "member-ptsb"
    member_dir.mkdir(parents=True)
    (member_dir / "ptsb.yaml").write_text(yaml.safe_dump({
        "module": "member-ptsb",
        "services": [{"code": SERVICE, "spec_url": f"http://127.0.0.1:{PORT}/spec.yaml"}],
    }))
    (pack / "out" / "join").mkdir(parents=True)
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
    (pack / "out" / "join" / "abc123.json").write_text(json.dumps(record))


def _drift(pack):
    return subprocess.run(
        ["bash", str(pack / "scripts" / "member.sh"), "drift", "ptsb"],
        capture_output=True, text=True,
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
    record = json.loads((pack / "out" / "join" / "abc123.json").read_text())
    assert record["endpoint_baseline"][SERVICE] == ["/awards/{nin}"]
    assert "refreshes" not in record
