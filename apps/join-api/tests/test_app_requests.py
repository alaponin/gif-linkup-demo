"""POST /requests and GET /requests/{id} through FastAPI's
TestClient, against a temporary copy of the pack -- never the real
checkout. Same env-vars-before-import pattern test_app_health.py uses
(loaded by path under a distinct module name -- see that file's own
comment for why `import app` is avoided across apps/console, apps/join-api,
apps/mock-registry). PACK_DIR/OUT_DIR are then monkeypatched onto the
already-imported module per test: app.py reads them from the environment
once, at import time, into module-level constants, so changing os.environ
afterwards would have no effect -- reassigning the module attribute does.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-requests"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-requests"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"
# _DATASTORE_KIND stays "sqlite" here (no deployment.yaml at this PACK_DIR),
# so KP2_JOIN_DB_URL is never actually connected to -- it only needs to be
# present for _SINK_SECRETS to pick its password up at import time, the
# same way hurl/generate.py's subprocess would see it in a real .env.
os.environ["KP2_JOIN_DB_URL"] = "postgresql://joinapi:s3cr3t-dsn-pw@db:5432/join"

_spec = importlib.util.spec_from_file_location(
    "join_api_app_requests", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app_module)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import writer  # noqa: E402

# apps/join-api/tests/test_app_requests.py -> tests -> join-api -> apps -> pack root
REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]

CONSOLE_HEADER = "X-KP2-Console"
AUTH = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}


@pytest.fixture
def client(tmp_path):
    """A fresh temp copy of the pack per test, with app_module's globals
    pointed at it -- writer.dry_run_diff only ever reads REAL_PACK_DIR once,
    to seed this copy (writer._copy_pack), same as every writer.py test."""
    # Each test gets its own budget: the limiter's buckets are module-level
    # state and a suite is not a caller (app.py's rate_limit).
    app_module._BUCKETS.clear()
    pack = tmp_path / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    app_module.PACK_DIR = pack
    app_module.OUT_DIR = tmp_path / "out"
    return TestClient(app_module.app)


def _payload(**overrides) -> dict:
    base = dict(
        code="PTSB",
        name="Progressa Tertiary Scholarship Board",
        subsystem="SCHOLARSHIP",
        subsystem_description="Scholarship award management",
        security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
        backend={"auth": "network_allowlist"},
        requested_access=["PROGRESSA/GOV/PNIA/IDENTITY"],
        member_requirements={
            "has_security_server": True,
            "has_registered_identity": True,
            "standards_portfolio_adopted": True,
            "data_conformant": True,
            "lawful_basis": "consent",
            "technical_contact": "Jane Doe",
        },
    )
    base.update(overrides)
    return base


# -- POST /requests -------------------------------------------------------------


def test_submit_valid_join_returns_201_submitted_with_a_diff(client):
    resp = client.post("/requests", json=_payload(), headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "SUBMITTED"
    assert "configs/member-ptsb" in body["diff"]
    assert body["payload"]["code"] == "PTSB"
    assert "origin" not in body["payload"]

    got = client.get(f"/requests/{body['id']}", headers=AUTH)
    assert got.status_code == 200
    assert got.json() == body


def test_submit_never_writes_to_the_real_checkout(client):
    before = (REAL_PACK_DIR / "manifest.yaml").read_text()
    client.post("/requests", json=_payload(), headers=AUTH)
    after = (REAL_PACK_DIR / "manifest.yaml").read_text()
    assert before == after


def test_submit_rejects_bad_schema_with_check_schema(client):
    bad = _payload()
    del bad["backend"]
    resp = client.post("/requests", json=bad, headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "REJECTED"
    assert body["rejection"]["check"] == "schema"


def test_oversized_body_gets_413(client):
    """The in-app body-size middleware, not schema.py's own max_length --
    a body over app_module.MAX_BODY_BYTES is rejected before it is ever
    parsed as JSON, let alone validated against JoinPayload."""
    huge = _payload(subsystem_description="x" * (app_module.MAX_BODY_BYTES + 1000))
    resp = client.post("/requests", json=huge, headers=AUTH)
    assert resp.status_code == 413


def test_51_services_is_rejected_by_schema_not_a_spec_fetch_storm(client):
    """The `services` bound (50) is enforced by JoinPayload itself, at
    parse time -- the 51st entry must never reach check 9 (the per-service
    spec fetch): a request naming 51 fake spec_urls would otherwise cost 51
    outbound fetch attempts before failing. Proven the same way
    test_submit_rejects_bad_schema_with_check_schema does: check == "schema",
    reached without ever calling out to app-ptsb or app-plr (the fixture
    services below reuse a spec_url no real backend on this pack serves --
    a fetch attempt would time out, not just fail fast)."""
    services = [
        {"code": f"svc-{i}", "spec_url": "http://nonexistent-backend-that-must-never-be-fetched:8000/spec.yaml"}
        for i in range(51)
    ]
    resp = client.post("/requests", json=_payload(services=services), headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "REJECTED"
    assert body["rejection"]["check"] == "schema"


def test_submit_rejects_a_canonical_code(client):
    resp = client.post(
        "/requests",
        json=_payload(
            code="PDGA",
            subsystem="MGMT",
            security_server={"code": "SS-X", "dns_name": "ss-x", "hosted_on": "ss-plr"},
        ),
        headers=AUTH,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "REJECTED"
    assert body["rejection"]["check"] == "not_canonical"


def test_rejected_request_writes_nothing_under_configs(client, tmp_path):
    resp = client.post(
        "/requests",
        json=_payload(code="PNIA", security_server={"code": "SS-X", "dns_name": "ss-x", "hosted_on": "ss-plr"}),
        headers=AUTH,
    )
    body = resp.json()
    assert body["state"] == "REJECTED"
    assert not (app_module.PACK_DIR / "configs" / "member-pnia2").exists()


def test_submit_requires_the_console_header(client):
    resp = client.post("/requests", json=_payload(), headers={"Authorization": "Bearer test-applicant-token"})
    assert resp.status_code == 403


def test_submit_requires_a_bearer_token(client):
    resp = client.post("/requests", json=_payload(), headers={CONSOLE_HEADER: "1"})
    assert resp.status_code == 401


def test_submit_response_never_carries_a_credential(client):
    resp = client.post("/requests", json=_payload(), headers=AUTH)
    body = resp.text
    for secret in (
        app_module.ADMIN_PASSWORD,
        app_module.TOKEN_PIN,
        app_module.APPLICANT_TOKEN,
        app_module.OPERATOR_TOKEN,
    ):
        assert secret not in body


def test_a_generate_failure_at_submission_is_scrubbed_of_the_full_sink_secret_set(client, monkeypatch):
    """dry_run_diff's GenerateFailure stderr goes into a REJECTED record AND
    the applicant's own response body (app.py's dry_run_diff except-clause,
    docstring: "stderr is passed through verbatim" -- scrubbed first). Before
    this fix that scrub used JOB_SECRETS (the three Hurl credentials only),
    not app._SINK_SECRETS -- so hurl/generate.py reading the whole .env
    (writer.py's own comment on this) could hand an applicant the operator's
    bearer token, its own bearer token back with the wrong casing intact, or
    the join-api database's password, in a response IT received directly."""
    operator_token = app_module.OPERATOR_TOKEN
    applicant_token = app_module.APPLICANT_TOKEN
    dsn_password = "s3cr3t-dsn-pw"  # matches KP2_JOIN_DB_URL set at module import, above
    stderr = (
        f"Traceback ...\nKP2_JOIN_OPERATOR_TOKEN={operator_token}\n"
        f"KP2_JOIN_APPLICANT_TOKEN={applicant_token}\n"
        f"KP2_JOIN_DB_URL=postgresql://joinapi:{dsn_password}@db:5432/join\n"
    )

    def boom(*args, **kwargs):
        raise writer.GenerateFailure(stderr, 1)

    monkeypatch.setattr(app_module.writer, "dry_run_diff", boom)
    resp = client.post("/requests", json=_payload(), headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "REJECTED"

    stored = app_module.store.load_request(app_module._conn(), body["id"])
    for blob in (resp.text, json.dumps(stored)):
        assert operator_token not in blob
        assert applicant_token not in blob
        assert dsn_password not in blob
        assert blob.count("***") == 3


# -- GET /requests/{id} ----------------------------------------------------------


def test_get_unknown_request_id_is_404(client):
    resp = client.get("/requests/does-not-exist", headers=AUTH)
    assert resp.status_code == 404


def test_get_request_id_path_traversal_never_reaches_the_filesystem(client):
    resp = client.get("/requests/..%2f..%2f..%2fetc%2fpasswd", headers=AUTH)
    assert resp.status_code != 500


# -- ownership (join_workflow.enforce_ownership, row 28) -----------------------
#
# deployment.yaml is read once, at import time, into app_module._ENFORCE_
# OWNERSHIP (module-level, like _COMMIT_GATE) -- the `client` fixture above
# reassigns app_module.PACK_DIR/OUT_DIR AFTER import, which the enforcement
# flag never re-reads. Exercising `true` therefore needs its own import
# under a distinct module name and a distinct PACK_DIR that already carries
# the flag before app.py runs, same pattern test_app_tokens.py's sentinel
# tests and test_app_startup.py's migration-refusal test both use.


def _import_app_with_ownership_enforced(tmp_path, *, applicant_token="test-applicant-token"):
    pack = tmp_path / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    (pack / "deployment.yaml").write_text("join_workflow:\n  enforce_ownership: true\n")
    env_backup = dict(os.environ)
    os.environ["PACK_DIR"] = str(pack)
    os.environ["OUT_DIR"] = str(tmp_path / "out")
    os.environ["KP2_JOIN_APPLICANT_TOKEN"] = applicant_token
    os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"
    try:
        spec = importlib.util.spec_from_file_location(
            f"join_api_app_requests_ownership_{id(tmp_path)}",
            pathlib.Path(__file__).resolve().parent.parent / "app.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


def _seed(module, request_id: str, submitted_by) -> None:
    """Seeds a bare SUBMITTED record directly into the store -- GET
    /requests/{id} needs nothing about the payload, so this skips the whole
    submit pipeline (manifest.yaml, join-policy.yaml, a real backend fetch)
    that a full POST /requests would otherwise require."""
    conn = module._conn()
    module.store.save_request(conn, {
        "id": request_id,
        "state": "SUBMITTED",
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "submitted_by": submitted_by,
        "payload": {},
    }, actor="system", event="test-seed")
    conn.close()


def _operator_header() -> dict:
    return {"Authorization": "Bearer test-operator-token", CONSOLE_HEADER: "1"}


def test_ownership_operator_reads_any_record(tmp_path):
    module = _import_app_with_ownership_enforced(tmp_path)
    _seed(module, "r1", "ptsb")
    client = TestClient(module.app)
    assert client.get("/requests/r1", headers=_operator_header()).status_code == 200


def test_ownership_owner_reads_its_own_record(tmp_path):
    module = _import_app_with_ownership_enforced(tmp_path)
    _seed(module, "r1", "ptsb")
    client = TestClient(module.app)
    token = client.post("/tokens", json={"agency": "ptsb"}, headers=_operator_header()).json()["token"]
    owner = {"Authorization": f"Bearer {token}", CONSOLE_HEADER: "1"}
    assert client.get("/requests/r1", headers=owner).status_code == 200


def test_ownership_another_agencys_issued_token_gets_404_not_403(tmp_path):
    """No existence oracle: get_request raises the SAME HTTPException(404,
    f"no join request {request_id!r}") whether the record is missing or
    merely not owned by the caller -- app.py's single
    `if record is None or (_ENFORCE_OWNERSHIP and not _owns_record(...))`
    guard -- never 403, matching the path-traversal case's posture above."""
    module = _import_app_with_ownership_enforced(tmp_path)
    _seed(module, "r1", "ptsb")
    client = TestClient(module.app)
    token = client.post("/tokens", json={"agency": "pvtb"}, headers=_operator_header()).json()["token"]
    other = {"Authorization": f"Bearer {token}", CONSOLE_HEADER: "1"}
    resp = client.get("/requests/r1", headers=other)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "no join request 'r1'"}


def test_ownership_shared_token_reads_a_null_submitted_by_record(tmp_path):
    """submitted_by: null is what the shared applicant token itself
    produces on submission -- the records nothing else could have made."""
    module = _import_app_with_ownership_enforced(tmp_path)
    _seed(module, "r1", None)
    client = TestClient(module.app)
    shared = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}
    assert client.get("/requests/r1", headers=shared).status_code == 200


def test_ownership_shared_token_cannot_read_an_agencys_own_record(tmp_path):
    module = _import_app_with_ownership_enforced(tmp_path)
    _seed(module, "r1", "ptsb")
    client = TestClient(module.app)
    shared = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}
    assert client.get("/requests/r1", headers=shared).status_code == 404


def test_ownership_off_by_default_cross_agency_reads_still_succeed(client):
    """docker-local's zero-setup demo path, unchanged: the `client` fixture
    above imports app.py against a PACK_DIR with no deployment.yaml
    (_ENFORCE_OWNERSHIP defaults False), so a differently-named issued
    credential can still read a record it did not submit."""
    resp = client.post("/requests", json=_payload(), headers=AUTH)
    request_id = resp.json()["id"]
    operator = {"Authorization": "Bearer test-operator-token", CONSOLE_HEADER: "1"}
    token = client.post("/tokens", json={"agency": "someone-else"}, headers=operator).json()["token"]
    other = {"Authorization": f"Bearer {token}", CONSOLE_HEADER: "1"}
    assert client.get(f"/requests/{request_id}", headers=other).status_code == 200


def test_the_shared_token_disabled_plus_ownership_enforced_closes_the_loop(tmp_path):
    """The production posture: the shared credential is off AND ownership
    is on. An issued per-agency token reads its own
    record; the old shared-token value is refused outright (403, not merely
    denied by ownership -- require_applicant never even reaches the
    ownership check for a credential it does not recognise at all)."""
    module = _import_app_with_ownership_enforced(tmp_path, applicant_token="disabled")
    _seed(module, "r1", "ptsb")
    client = TestClient(module.app)
    token = client.post("/tokens", json={"agency": "ptsb"}, headers=_operator_header()).json()["token"]
    owner = {"Authorization": f"Bearer {token}", CONSOLE_HEADER: "1"}
    assert client.get("/requests/r1", headers=owner).status_code == 200

    old_shared = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}
    assert client.get("/requests/r1", headers=old_shared).status_code == 403


# -- regression guard: job.scrub(...) call sites use the wide secrets set --


def test_no_job_scrub_call_site_passes_the_narrow_job_secrets_set():
    """The defect this guards against: six job.scrub(...) call sites at
    persisted-record/HTTP-response sinks passed JOB_SECRETS (the three Hurl
    credentials only) instead of the wider _SINK_SECRETS (JOB_SECRETS plus
    the operator/applicant bearer tokens and any DSN password) -- so
    hurl/generate.py's whole-.env stderr could leak a token or a database
    password straight into a REJECTED/FAILED record or a response body. The
    behavioural test above only proves today's six call sites got fixed;
    this is the one that stops an eventual seventh from regressing to the
    narrow set. job.run(..., secrets=JOB_SECRETS)/job.unjoin(...,
    secrets=JOB_SECRETS) are a different thing -- the credential set Hurl
    is actually handed, not a scrub set -- and are excluded here by
    matching the `job.scrub(` call shape specifically, not every
    JOB_SECRETS reference in the file."""
    import ast

    tree = ast.parse((pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text())
    offending = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "scrub"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "job"
        and any(isinstance(arg, ast.Name) and arg.id == "JOB_SECRETS" for arg in node.args)
    ]
    assert not offending, (
        f"app.py line(s) {offending}: job.scrub(..., JOB_SECRETS) scrubs a persisted "
        "record or HTTP response with the narrow three-credential set -- use "
        "_SINK_SECRETS, which also covers the operator/applicant bearer tokens and "
        "any DSN password."
    )
