"""The join-api skeleton. No live containers, no network --
just GET /health, and the auth/origin-guard dependencies unit-tested
directly (no protected route exists to hang them off yet; that arrives with
the real endpoints later). Env vars set before import, same
pattern as apps/console/tests/test_app_csrf.py."""
import importlib.util
import os
import pathlib

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

# Loaded by path under a distinct module name, not `sys.path.insert` +
# `import app` -- apps/console/tests already claim the plain name "app" in
# sys.modules, and when verify.sh runs every apps/*/tests directory in one
# pytest session, a second `import app` here would silently reuse the
# console's cached module instead of loading this one (same fix as
# apps/mock-registry/tests/test_app.py).
_spec = importlib.util.spec_from_file_location(
    "join_api_app", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

CONSOLE_HEADER = "X-KP2-Console"


def _request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_health_returns_ok_with_no_auth():
    client = TestClient(app.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_response_never_carries_a_credential():
    client = TestClient(app.app)
    resp = client.get("/health")
    body = resp.text
    for secret in (app.ADMIN_PASSWORD, app.TOKEN_PIN, app.APPLICANT_TOKEN, app.OPERATOR_TOKEN):
        assert secret not in body


# -- bearer-token auth (spec S7, decision 10) --------------------------------

def test_require_applicant_accepts_the_applicant_token():
    req = _request({"authorization": "Bearer test-applicant-token"})
    assert app.require_applicant(req) == "applicant"


def test_require_applicant_accepts_the_operator_token_too():
    req = _request({"authorization": "Bearer test-operator-token"})
    assert app.require_applicant(req) == "operator"


def test_require_applicant_rejects_an_unknown_token():
    req = _request({"authorization": "Bearer not-a-real-token"})
    try:
        app.require_applicant(req)
        assert False, "expected HTTPException"
    except app.HTTPException as exc:
        assert exc.status_code == 403


def test_require_applicant_rejects_a_missing_header():
    req = _request({})
    try:
        app.require_applicant(req)
        assert False, "expected HTTPException"
    except app.HTTPException as exc:
        assert exc.status_code == 401


def test_require_operator_accepts_the_operator_token():
    req = _request({"authorization": "Bearer test-operator-token"})
    assert app.require_operator(req) == "operator"


def test_require_operator_rejects_the_applicant_token():
    """The asymmetry that is decision 10's whole teaching point: an
    applicant cannot reach an operator-only endpoint."""
    req = _request({"authorization": "Bearer test-applicant-token"})
    try:
        app.require_operator(req)
        assert False, "expected HTTPException"
    except app.HTTPException as exc:
        assert exc.status_code == 403


# -- request-boundary guard (copied from apps/console's, S12/S13) -----------

def test_console_origin_guard_rejects_a_missing_header():
    req = _request({})
    try:
        app._require_console_origin(req)
        assert False, "expected HTTPException"
    except app.HTTPException as exc:
        assert exc.status_code == 403
        assert CONSOLE_HEADER.lower() in str(exc.detail).lower()


def test_console_origin_guard_accepts_the_header_with_no_origin():
    req = _request({CONSOLE_HEADER: "1"})
    app._require_console_origin(req)  # does not raise


def test_console_origin_guard_rejects_a_foreign_origin():
    req = _request({CONSOLE_HEADER: "1", "origin": "https://evil.example", "host": "testserver"})
    try:
        app._require_console_origin(req)
        assert False, "expected HTTPException"
    except app.HTTPException as exc:
        assert exc.status_code == 403


def test_console_origin_guard_accepts_a_matching_origin():
    req = _request({CONSOLE_HEADER: "1", "origin": "http://testserver", "host": "testserver"})
    app._require_console_origin(req)  # does not raise
