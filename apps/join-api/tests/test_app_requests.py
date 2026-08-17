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


# -- GET /requests/{id} ----------------------------------------------------------


def test_get_unknown_request_id_is_404(client):
    resp = client.get("/requests/does-not-exist", headers=AUTH)
    assert resp.status_code == 404


def test_get_request_id_path_traversal_never_reaches_the_filesystem(client):
    resp = client.get("/requests/..%2f..%2f..%2fetc%2fpasswd", headers=AUTH)
    assert resp.status_code != 500
