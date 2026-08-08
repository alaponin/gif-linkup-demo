"""A job's record can be left at RUNNING
forever if the process running it stops mid-job (scripts/join.sh down, a
rebuild, acceptance.sh's own 2.7 section) -- job.run() resumes correctly from
any record carrying last_completed_step, but resume_request only accepts
FAILED, so a RUNNING record was otherwise unrecoverable through the API.
apps/join-api/app.py now sweeps out/join/*.json once at import time and
rewrites any RUNNING record to FAILED. Env vars set before import, same
pattern as test_app_health.py; OUT_DIR is a dedicated path (never reused by
another test module in this directory) with the RUNNING record already
written to disk BEFORE app.py is imported, so the sweep runs against it."""
import importlib.util
import json
import os
import pathlib

OUT_DIR = pathlib.Path("/tmp/join-api-test-out-startup")
os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-startup"
os.environ["OUT_DIR"] = str(OUT_DIR)
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

REQUESTS_DIR = OUT_DIR / "join"
REQUESTS_DIR.mkdir(parents=True, exist_ok=True)

(REQUESTS_DIR / "running-job.json").write_text(json.dumps({
    "id": "running-job",
    "state": "RUNNING",
    "last_completed_step": "ss.client_add",
}))
(REQUESTS_DIR / "active-job.json").write_text(json.dumps({
    "id": "active-job",
    "state": "ACTIVE",
}))
(REQUESTS_DIR / "failed-job.json").write_text(json.dumps({
    "id": "failed-job",
    "state": "FAILED",
    "error": {"step": "cs.init", "message": "already failed, untouched by the sweep"},
}))
(REQUESTS_DIR / "not-json.json").write_text("{not valid json")

# Loaded by path under a distinct module name (same reason test_app_health.py
# gives): importing this module RUNS the startup sweep, since it is
# module-level code in app.py, not a route handler.
_spec = importlib.util.spec_from_file_location(
    "join_api_app_startup", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)


def _read(name: str) -> dict:
    return json.loads((REQUESTS_DIR / f"{name}.json").read_text())


def test_a_running_record_is_swept_to_failed_at_startup():
    record = _read("running-job")
    assert record["state"] == "FAILED"
    assert record["error"] == {
        "step": "ss.client_add",
        "message": "interrupted by a join-api restart",
    }


def test_the_swept_record_can_then_be_resumed_through_the_api():
    """The whole point: FAILED is the one state resume_request accepts."""
    from fastapi.testclient import TestClient

    client = TestClient(app.app)
    calls = []
    app._start_job = lambda request_id: calls.append(request_id)
    resp = client.post(
        "/requests/running-job/resume",
        headers={"Authorization": "Bearer test-operator-token", "X-KP2-Console": "1"},
    )
    assert resp.status_code == 202
    assert calls == ["running-job"]


def test_a_non_running_record_is_left_untouched_by_the_sweep():
    assert _read("active-job") == {"id": "active-job", "state": "ACTIVE"}
    assert _read("failed-job") == {
        "id": "failed-job",
        "state": "FAILED",
        "error": {"step": "cs.init", "message": "already failed, untouched by the sweep"},
    }


def test_an_unparseable_record_does_not_crash_the_sweep():
    # The sweep already ran (at import, above) without raising -- this just
    # asserts the file itself, and its neighbours, made it through.
    assert (REQUESTS_DIR / "not-json.json").read_text() == "{not valid json"
