"""GET /catalogue through FastAPI's TestClient, against a temporary copy of
the pack -- never the real checkout. Same env-vars-before-import pattern
test_app_requests.py uses (loaded by path under a distinct module name; see
test_app_health.py's own comment for why `import app` is avoided across
apps/console, apps/join-api and apps/mock-registry).
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import sys

import yaml

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-catalogue"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-catalogue"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

_spec = importlib.util.spec_from_file_location(
    "join_api_app_catalogue", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app_module)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import writer  # noqa: E402

# apps/join-api/tests/test_app_catalogue.py -> tests -> join-api -> apps -> pack root
REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]

CONSOLE_HEADER = "X-KP2-Console"
APPLICANT = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}
OPERATOR = {"Authorization": "Bearer test-operator-token", CONSOLE_HEADER: "1"}


@pytest.fixture
def pack(tmp_path):
    pack = tmp_path / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    app_module.PACK_DIR = pack
    app_module.OUT_DIR = tmp_path / "out"
    return pack


@pytest.fixture
def client(pack):
    return TestClient(app_module.app)


def test_the_applicant_token_reaches_the_catalogue(client):
    """The credential a body that has just joined actually holds -- gating
    discovery behind the operator token would defeat the point of it."""
    resp = client.get("/catalogue", headers=APPLICANT)
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["services"]]
    assert "PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api" in ids


def test_the_operator_token_reaches_it_too(client):
    assert client.get("/catalogue", headers=OPERATOR).status_code == 200


def test_no_token_is_refused(client):
    assert client.get("/catalogue", headers={CONSOLE_HEADER: "1"}).status_code == 401


def test_an_unknown_token_is_refused(client):
    resp = client.get("/catalogue", headers={"Authorization": "Bearer nope", CONSOLE_HEADER: "1"})
    assert resp.status_code == 403


def test_the_request_boundary_guard_still_applies(client):
    assert client.get("/catalogue", headers={"Authorization": "Bearer test-applicant-token"}).status_code == 403


def test_the_response_says_on_its_face_that_it_is_not_an_authorisation(client):
    """A top-level field, not a line in the documentation: a client that
    renders this payload should be unable to drop the caveat by accident."""
    body = client.get("/catalogue", headers=APPLICANT).json()
    assert "grants nothing" in body["publication_is_not_permission"]


def test_the_response_matches_the_yaml_for_the_same_inputs(client, pack):
    body = client.get("/catalogue", headers=APPLICANT).json()
    assert body == yaml.safe_load(writer.render_catalogue(pack))


def test_a_removed_members_services_leave_the_response(client, pack):
    """No delete path anywhere: the endpoint re-derives from the configs on
    every call, so a member whose config is gone is simply not found."""
    assert [s for s in client.get("/catalogue", headers=APPLICANT).json()["services"]
            if s["provider"]["key"] == "plr"]
    shutil.rmtree(pack / "configs" / "member-plr")
    assert not [s for s in client.get("/catalogue", headers=APPLICANT).json()["services"]
                if s["provider"]["key"] == "plr"]
