"""Two things the console must survive without: the join-api operator
token, and join-api itself.

A missing KP2_JOIN_OPERATOR_TOKEN used to kill the whole process at import
(and, through docker-compose.yml's file-wide interpolation, every other
service with it). It now disables the join tab only. The catalogue tab is
the other half: it reads onboarding/catalogue.yaml off the pack mount, so
it answers with `scripts/join.sh down`, which is a Tier-1 demo's normal
state.

Same fixture pack and no-network discipline as test_app_join.py.
"""
import os
import pathlib
import subprocess
import sys

FIXTURE_PACK = pathlib.Path(__file__).resolve().parent / "fixtures" / "pack"
CONSOLE_DIR = pathlib.Path(__file__).resolve().parent.parent

os.environ["PACK_DIR"] = str(FIXTURE_PACK)
os.environ["OUT_DIR"] = "/tmp"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token-should-never-leak"

sys.path.insert(0, str(CONSOLE_DIR))
import app  # noqa: E402
import truth  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

HEADER = "X-KP2-Console"


def _client():
    return TestClient(app.app)


# -- no operator token ---------------------------------------------------------


def test_console_imports_without_the_join_operator_token():
    """A subprocess, not monkeypatch: the regression was at IMPORT time, and
    this module has already imported app with the variable set."""
    env = {k: v for k, v in os.environ.items() if k != "KP2_JOIN_OPERATOR_TOKEN"}
    env.update(
        PACK_DIR=str(FIXTURE_PACK), OUT_DIR="/tmp",
        XROAD_ADMIN_USER="xrd", XROAD_ADMIN_PASSWORD="secret",
    )
    proc = subprocess.run(
        [sys.executable, "-c", "import app; assert app.JOIN_OPERATOR_TOKEN == ''"],
        cwd=CONSOLE_DIR, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def _refuse_to_call(*args, **kwargs):
    raise AssertionError("join-api must not be called without a token")


def test_join_tab_renders_the_remedy_when_the_token_is_absent(monkeypatch):
    monkeypatch.setattr(app, "JOIN_OPERATOR_TOKEN", "")
    # An outbound call at all would be the bug: the short-circuit is the point.
    monkeypatch.setattr(app.httpx, "request", _refuse_to_call)
    body = _client().get("/api/join/requests", headers={HEADER: "1"}).json()
    assert "gen-secrets.sh" in body["error"]
    assert "requests" not in body


def test_health_is_ok_without_the_token(monkeypatch):
    monkeypatch.setattr(app, "JOIN_OPERATOR_TOKEN", "")
    assert _client().get("/api/health").json() == {"status": "ok"}


# -- the catalogue endpoint ----------------------------------------------------


def test_catalogue_requires_the_console_header():
    assert _client().get("/api/catalogue").status_code == 403


def test_catalogue_returns_every_service_and_the_disclaimer():
    body = _client().get("/api/catalogue", headers={HEADER: "1"}).json()
    assert body["publication_is_not_permission"]
    assert body["source"] == "onboarding/catalogue.yaml"
    codes = [svc["service_code"] for svc in body["services"]]
    fixture = truth.load_catalogue(FIXTURE_PACK)
    assert codes == [svc["service_code"] for svc in fixture["services"]]
    assert codes  # a fixture that lost its services would pass the line above


def test_catalogue_reports_an_unrendered_file_rather_than_500(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "PACK_DIR", tmp_path)
    body = _client().get("/api/catalogue", headers={HEADER: "1"}).json()
    assert "render-onboarding.sh" in body["error"]


# -- the inspector's per-layer "where this lives" ------------------------------


def test_layer_sources_name_a_file_and_the_string_it_holds():
    sources = app._layer_sources()
    assert "Fixture Identity Decree" in "\n".join(sources["legal"])
    assert "configs/member-pnia/pnia.yaml" in "\n".join(sources["legal"])
    assert "FIXTURE/GOV/PNEA/EXAMS" in "\n".join(sources["organisational"])
    assert "CEDS" in "\n".join(sources["semantic"])
    assert "once-only-exchange.yaml" in "\n".join(sources["technical"])


def test_layer_sources_are_empty_when_the_catalogue_is_unrendered(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "PACK_DIR", tmp_path)
    assert app._layer_sources() == {}


# -- static assets must revalidate ---------------------------------------------


def test_static_assets_carry_no_cache_so_a_rebuild_is_picked_up():
    """FileResponse sends Last-Modified and no Cache-Control, which lets a
    browser heuristically cache app.js and never ask again. Found live: after
    a rebuild the page served fresh index.html (new tab button) against a
    cached app.js (no loader for it) -- a dead tab that a plain reload does
    not fix."""
    client = _client()
    for path in ("/", "/index.html", "/app.js", "/style.css"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-cache", path


def test_revalidation_still_answers_304_rather_than_resending_the_body():
    """no-cache means "ask", not "never cache" -- the ETag StaticFiles
    already emits must still make the answer bodyless, or every page load
    re-downloads every asset."""
    client = _client()
    first = client.get("/app.js")
    again = client.get("/app.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["cache-control"] == "no-cache"
