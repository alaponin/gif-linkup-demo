"""The join-api skeleton. No live containers, no network --
just GET /health, and the auth/origin-guard dependencies unit-tested
directly (no protected route exists to hang them off yet; that arrives with
the real endpoints later). Env vars set before import, same
pattern as apps/console/tests/test_app_csrf.py."""
import importlib.util
import io
import json
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


def _conn():
    return app.store.connect(app.store.init(app.OUT_DIR))


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


# -- bearer-token auth --------------------------------------------------

def test_require_applicant_accepts_the_applicant_token():
    req = _request({"authorization": "Bearer test-applicant-token"})
    assert app.require_applicant(req, _conn()) == "applicant"


def test_require_applicant_accepts_the_operator_token_too():
    req = _request({"authorization": "Bearer test-operator-token"})
    assert app.require_applicant(req, _conn()) == "operator"


def test_require_applicant_rejects_an_unknown_token():
    req = _request({"authorization": "Bearer not-a-real-token"})
    try:
        app.require_applicant(req, _conn())
        assert False, "expected HTTPException"
    except app.HTTPException as exc:
        assert exc.status_code == 403


def test_require_applicant_rejects_a_missing_header():
    req = _request({})
    try:
        app.require_applicant(req, _conn())
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


# -- structured logging (E.1, docs/production-delta.md row 34) ---------------

def _captured_log_lines(fn) -> list[dict]:
    """Runs `fn()` with app._LOG's real handler pointed at an in-memory
    buffer instead of stdout, and returns every JSON line it wrote --
    the same handler/formatter/filter the running process actually uses,
    not a stand-in."""
    handler = app._LOG.handlers[0]
    buf = io.StringIO()
    original_stream = handler.stream
    handler.stream = buf
    try:
        fn()
    finally:
        handler.stream = original_stream
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_request_id_middleware_sets_a_header_and_a_different_one_per_request():
    client = TestClient(app.app)
    r1 = client.get("/health")
    r2 = client.get("/health")
    assert r1.headers["x-request-id"]
    assert r2.headers["x-request-id"]
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


def test_log_records_are_scrubbed_of_a_real_secret_value():
    """job.scrub(..., JOB_SECRETS) runs on every record this logger emits
    (logging_setup.ScrubFilter) -- proven with the actual ADMIN_PASSWORD/
    TOKEN_PIN values this test process holds, not by reasoning about the
    filter alone (the task's own binding constraint)."""
    lines = _captured_log_lines(
        lambda: app._LOG.info(
            "auth attempt password=%s pin=%s", app.ADMIN_PASSWORD, app.TOKEN_PIN
        )
    )
    assert len(lines) == 1
    raw = json.dumps(lines[0])
    assert app.ADMIN_PASSWORD not in raw
    assert app.TOKEN_PIN not in raw
    assert "***" in lines[0]["message"]


def test_job_log_carries_the_join_id_for_correlation_across_the_jobs_lifecycle():
    lines = _captured_log_lines(lambda: app._job_log("join-abc123", "job.start", state="RUNNING"))
    assert lines == [
        {
            "ts": lines[0]["ts"], "level": "INFO", "logger": "kp2.join-api",
            "message": "job.start", "request_id": None, "join_id": "join-abc123", "state": "RUNNING",
        }
    ]


def test_job_log_with_metrics_bumps_the_matching_step_counter():
    before_ok = app._METRICS.get("job_steps_completed_total", 0)
    before_failed = app._METRICS.get("job_steps_failed_total", 0)
    log = app._job_log_with_metrics("some-join-id")
    log("job.step.end", step="cs.init", outcome="success", duration_s=1.2)
    log("job.step.end", step="ss.client_add", outcome="failed", duration_s=0.4)
    log("unjoin.step.end", step="service.acl", outcome="reversed", duration_s=0.1)
    assert app._METRICS["job_steps_completed_total"] == before_ok + 2
    assert app._METRICS["job_steps_failed_total"] == before_failed + 1


# -- GET /metrics (E.2, docs/production-delta.md row 34) ---------------------

def test_metrics_rejects_a_missing_bearer_token():
    client = TestClient(app.app)
    resp = client.get("/metrics")
    assert resp.status_code == 401


def test_metrics_rejects_the_applicant_token():
    """Operator-only, reusing require_operator -- the same asymmetry
    test_require_operator_rejects_the_applicant_token asserts directly."""
    client = TestClient(app.app)
    resp = client.get("/metrics", headers={"authorization": "Bearer test-applicant-token"})
    assert resp.status_code == 403


def test_metrics_accepts_the_operator_token_and_returns_prometheus_text():
    client = TestClient(app.app)
    resp = client.get("/metrics", headers={"authorization": "Bearer test-operator-token"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    for name in (
        "kp2_join_requests", "kp2_join_store_requests", "kp2_join_store_quota",
        "kp2_join_rate_limited_total", "kp2_join_job_steps_total",
        "kp2_join_job_duration_seconds_sum", "kp2_join_job_duration_seconds_count",
    ):
        assert name in body, f"{name} missing from /metrics output:\n{body}"


def test_metrics_response_never_carries_a_credential():
    client = TestClient(app.app)
    resp = client.get("/metrics", headers={"authorization": "Bearer test-operator-token"})
    for secret in (app.ADMIN_PASSWORD, app.TOKEN_PIN, app.APPLICANT_TOKEN, app.OPERATOR_TOKEN):
        assert secret not in resp.text
