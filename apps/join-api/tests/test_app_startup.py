"""A job's record can be left at RUNNING
forever if the process running it stops mid-job (scripts/join.sh down, a
rebuild, acceptance.sh's own 2.7 section) -- job.run() resumes correctly from
any record carrying last_completed_step, but resume_request only accepts
FAILED, so a RUNNING record was otherwise unrecoverable through the API.
apps/join-api/app.py now sweeps the SQLite store (store.recover_interrupted)
once at import time and rewrites any RUNNING record to FAILED. Env vars set
before import, same pattern as test_app_health.py; OUT_DIR is a dedicated
path (never reused by another test module in this directory) with the
RUNNING record already seeded into the store BEFORE app.py is imported, so
the sweep runs against it -- store.init() is idempotent, so app.py's own
module-level store.init(OUT_DIR) a moment later just finds the schema
already there."""
import importlib.util
import json
import os
import pathlib
import sys

OUT_DIR = pathlib.Path("/tmp/join-api-test-out-startup")
os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-startup"
os.environ["OUT_DIR"] = str(OUT_DIR)
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import store  # noqa: E402

_db_path = store.init(OUT_DIR)
_seed_conn = store.connect(_db_path)
store.save_request(_seed_conn, {
    "id": "running-job",
    "state": "RUNNING",
    "last_completed_step": "ss.client_add",
    "submitted_at": "2026-01-01T00:00:00+00:00",
}, actor="system", event="test-seed")
store.save_request(_seed_conn, {
    "id": "active-job",
    "state": "ACTIVE",
    "submitted_at": "2026-01-01T00:00:00+00:00",
}, actor="system", event="test-seed")
store.save_request(_seed_conn, {
    "id": "failed-job",
    "state": "FAILED",
    "submitted_at": "2026-01-01T00:00:00+00:00",
    "error": {"step": "cs.init", "message": "already failed, untouched by the sweep"},
}, actor="system", event="test-seed")
_seed_conn.close()

# Loaded by path under a distinct module name (same reason test_app_health.py
# gives): importing this module RUNS the startup sweep, since it is
# module-level code in app.py, not a route handler.
_spec = importlib.util.spec_from_file_location(
    "join_api_app_startup", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)


def _read(name: str) -> dict:
    return store.load_request(store.connect(_db_path), name)


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
    assert _read("active-job") == {
        "id": "active-job",
        "state": "ACTIVE",
        "submitted_at": "2026-01-01T00:00:00+00:00",
    }
    assert _read("failed-job") == {
        "id": "failed-job",
        "state": "FAILED",
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "error": {"step": "cs.init", "message": "already failed, untouched by the sweep"},
    }


# test_an_unparseable_record_does_not_crash_the_sweep is deliberately not
# ported: it guarded against a hand-written *.json file with invalid JSON
# surviving _recover_interrupted_jobs's glob loop. store.py's `record` column
# is always written via json.dumps by save_request -- there is no write path
# through this module that can leave invalid JSON in it, so the failure mode
# the test existed to catch no longer exists (see task-2-report.md's note on
# this deletion).


def test_startup_refuses_when_unmigrated_json_files_sit_beside_an_empty_store():
    """plan §2's migration refusal (app.py's module-level startup block):
    out/join/*.json files beside a DB that holds none must stop the process
    from starting, naming scripts/migrate-join-store.py, rather than quietly
    serving an empty store while unmigrated evidence sits next to it."""
    stale_out = pathlib.Path("/tmp/join-api-test-out-startup-refusal")
    stale_requests = stale_out / "join"
    stale_requests.mkdir(parents=True, exist_ok=True)
    (stale_requests / "leftover.json").write_text(json.dumps({"id": "leftover", "state": "ACTIVE"}))
    store.init(stale_out)  # the DB exists, but holds nothing -- the refusal condition

    env_backup = dict(os.environ)
    os.environ["OUT_DIR"] = str(stale_out)
    try:
        refusal_spec = importlib.util.spec_from_file_location(
            "join_api_app_startup_refusal", pathlib.Path(__file__).resolve().parent.parent / "app.py"
        )
        refusal_module = importlib.util.module_from_spec(refusal_spec)
        try:
            refusal_spec.loader.exec_module(refusal_module)
            raise AssertionError("import should have refused with unmigrated *.json files present")
        except RuntimeError as exc:
            assert "scripts/migrate-join-store.py" in str(exc)
            assert str(stale_requests) in str(exc)
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


def test_a_malformed_bearer_token_opens_no_store_connection(monkeypatch):
    """E.1: require_applicant's well-formedness gate must reject garbage
    BEFORE store.connect() is ever called for the issued-token DB fallback
    -- a bad-token flood should cost a regex, not a connection. Counting
    fake in the same style test_store.py's RacingConnection subclass uses
    to intercept a call the stdlib type does not support monkeypatching
    directly -- here it's simpler still: store.connect is a plain module
    function, so wrapping it in place is enough."""
    from fastapi.testclient import TestClient

    calls = []
    real_connect = store.connect

    def counting_connect(*args, **kwargs):
        calls.append(1)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(store, "connect", counting_connect)

    client = TestClient(app.app)
    # 8 characters: fails _TOKEN_WELLFORMED_RE's {16,64} length bound
    # regardless of alphabet, so this is unambiguously malformed, not
    # merely "a token nobody issued".
    resp = client.get("/catalogue", headers={
        "Authorization": "Bearer tooshort", "X-KP2-Console": "1",
    })
    assert resp.status_code == 403
    assert calls == []


def test_get_conn_and_rate_limit_deps_are_ordered_after_auth_in_every_route():
    """security-review-remediation-plan.md E.1 relies on FastAPI resolving
    Depends() parameters in signature order, aborting on the first one that
    raises: require_applicant/require_operator must be declared BEFORE any
    Depends(get_conn) or Depends(rate_limit) parameter in a route's
    signature, or a malformed bearer token stops short-circuiting the store
    connection E.1 was specifically about avoiding. The test above
    (test_a_malformed_bearer_token_opens_no_store_connection) only proves
    the well-formedness gate inside require_applicant itself works -- it
    hits /catalogue, which has no get_conn dependency at all, so it proves
    nothing about parameter ORDER on a route that has one. This is a
    parse-level regression guard for that ordering, in the same style
    test_writer.py's test_every_subprocess_run_in_writer_passes_a_timeout
    and test_app_requests.py's test_no_job_scrub_call_site_passes_the_
    narrow_job_secrets_set use: walk app.py's AST rather than exercise it,
    so a future route that reorders its Depends() parameters fails this
    test even though every existing behavioural test would stay green (a
    reordered-but-still-well-formed token still 403s the same way)."""
    import ast

    tree = ast.parse((pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text())

    AUTH_DEPS = {"require_applicant", "require_operator"}
    GUARDED_DEPS = {"get_conn", "rate_limit"}

    def _depends_name(node) -> str | None:
        """The dependency callable's name out of a `Depends(name)` default
        expression -- every route dependency in this file is exactly that
        shape (a bare Depends(<Name>) call, never Depends(lambda ...) or a
        parametrised factory) -- or None if this default isn't one."""
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Depends"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
        ):
            return node.args[0].id
        return None

    offending = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # @app.get(...)/@app.post(...)/etc -- NOT @app.middleware("http"),
        # which shares the same `app.<attr>(...)` decorator shape but wraps
        # a (request, call_next) function with no Depends() parameters at
        # all (_body_size_limit_middleware, _request_id_middleware above).
        is_route = any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in ("get", "post", "put", "delete", "patch")
            and isinstance(dec.func.value, ast.Name)
            and dec.func.value.id == "app"
            for dec in node.decorator_list
        )
        if not is_route:
            continue
        args = node.args.posonlyargs + node.args.args
        defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
        params = list(zip(args, defaults)) + list(zip(node.args.kwonlyargs, node.args.kw_defaults))
        auth_index = None
        guarded_index = None
        for i, (_arg, default) in enumerate(params):
            if default is None:
                continue
            dep = _depends_name(default)
            if dep in AUTH_DEPS and auth_index is None:
                auth_index = i
            if dep in GUARDED_DEPS and guarded_index is None:
                guarded_index = i
        if guarded_index is not None and (auth_index is None or auth_index > guarded_index):
            offending.append((node.name, node.lineno))

    assert not offending, (
        f"app.py route(s) {offending}: a Depends(get_conn)/Depends(rate_limit) "
        "parameter appears with no preceding Depends(require_applicant)/"
        "Depends(require_operator) parameter in the signature. FastAPI resolves "
        "Depends() in signature order, aborting on the first one that raises -- "
        "this ordering is what makes a malformed or invalid bearer token 403 "
        "before any store connection or rate-limit lookup opens one "
        "(security-review-remediation-plan.md E.1). Move the auth dependency "
        "earlier in the parameter list."
    )
