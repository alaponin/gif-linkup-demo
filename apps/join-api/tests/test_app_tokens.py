"""Operator-issued applicant credentials: POST/GET/DELETE /tokens, and what
an issued token is worth on the rest of the API.

One shared applicant token for every agency meant a request could not say who
sent it and revoking one agency revoked all of them
(docs/production-delta.md). This suite is about the half of that this pack
builds: a named credential per agency, in the token model that already
exists. mTLS -- what the delta row actually asks for -- is not here and is
not tested for.

Same env-vars-before-import pattern as test_app_requests.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-tokens"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-tokens"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

_spec = importlib.util.spec_from_file_location(
    "join_api_app_tokens", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app_module)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import writer  # noqa: E402

REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]
CONSOLE_HEADER = "X-KP2-Console"
APPLICANT = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}
OPERATOR = {"Authorization": "Bearer test-operator-token", CONSOLE_HEADER: "1"}


def _conn():
    return app_module.store.connect(app_module.store.init(app_module.OUT_DIR))


@pytest.fixture
def client(tmp_path):
    app_module._BUCKETS.clear()
    pack = tmp_path / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    app_module.PACK_DIR = pack
    app_module.OUT_DIR = tmp_path / "out"
    return TestClient(app_module.app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", CONSOLE_HEADER: "1"}


def _issue(client, agency: str = "ptsb") -> str:
    resp = client.post("/tokens", json={"agency": agency}, headers=OPERATOR)
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


# -- issue ---------------------------------------------------------------------


def test_issuing_returns_the_value_once_and_stores_only_its_hash(client):
    """A store that can return a credential is a store that can leak one."""
    token = _issue(client, "ptsb")
    stored = app_module.store.list_tokens(_conn())
    assert [entry["name"] for entry in stored] == ["ptsb"]
    assert token not in json.dumps(stored)
    # store.list_tokens() deliberately never returns sha256 (it backs the
    # read-only GET /tokens route) -- a raw query is the simplest way to
    # check the hash without growing store.py's production surface for a
    # test-only need.
    row = _conn().execute("SELECT sha256 FROM tokens WHERE name = ?", ("ptsb",)).fetchone()
    assert row["sha256"] == app_module._token_digest(token)
    assert "issued_at" in stored[0]


def test_the_applicant_token_cannot_issue_credentials(client):
    resp = client.post("/tokens", json={"agency": "ptsb"}, headers=APPLICANT)
    assert resp.status_code == 403


def test_an_issued_token_cannot_issue_further_credentials(client):
    """An applicant credential that can mint applicant credentials is an
    operator credential wearing a different name."""
    token = _issue(client, "ptsb")
    assert client.post("/tokens", json={"agency": "other"},
                       headers=_bearer(token)).status_code == 403


@pytest.mark.parametrize("agency", ["", "with space", "../../etc/passwd", "a" * 65, None, 7])
def test_a_bad_agency_name_is_refused(client, agency):
    """The name becomes a URL path segment on DELETE, so it is validated at
    the boundary rather than trusted."""
    assert client.post("/tokens", json={"agency": agency},
                       headers=OPERATOR).status_code == 400


def test_issuing_twice_for_one_agency_is_refused(client):
    """One name, one live credential -- otherwise revoking a name leaves an
    older token for the same agency still working."""
    _issue(client, "ptsb")
    resp = client.post("/tokens", json={"agency": "ptsb"}, headers=OPERATOR)
    assert resp.status_code == 409
    assert "revoke" in resp.json()["detail"]


# -- list ----------------------------------------------------------------------


def test_listing_shows_names_and_dates_but_never_hashes(client):
    _issue(client, "ptsb")
    _issue(client, "pvtb")
    body = client.get("/tokens", headers=OPERATOR).json()
    assert [t["agency"] for t in body["tokens"]] == ["ptsb", "pvtb"]
    assert all("issued_at" in t for t in body["tokens"])
    assert "sha256" not in json.dumps(body)


def test_listing_is_operator_only(client):
    assert client.get("/tokens", headers=APPLICANT).status_code == 403


# -- authenticate with an issued token -----------------------------------------


def test_an_issued_token_reads_the_catalogue(client):
    token = _issue(client)
    assert client.get("/catalogue", headers=_bearer(token)).status_code == 200


def test_an_issued_token_cannot_approve_or_list_the_queue(client):
    """The asymmetry that is the teaching point: a named applicant is still
    an applicant."""
    token = _issue(client)
    assert client.get("/requests", headers=_bearer(token)).status_code == 403


def test_a_revoked_token_stops_working_on_the_next_request(client):
    token = _issue(client, "ptsb")
    assert client.get("/catalogue", headers=_bearer(token)).status_code == 200
    assert client.request("DELETE", "/tokens/ptsb", headers=OPERATOR).status_code == 200
    assert client.get("/catalogue", headers=_bearer(token)).status_code == 403


def test_revoking_a_name_nobody_holds_is_a_404(client):
    assert client.request("DELETE", "/tokens/nobody", headers=OPERATOR).status_code == 404


def test_revocation_is_operator_only(client):
    _issue(client, "ptsb")
    assert client.request("DELETE", "/tokens/ptsb", headers=APPLICANT).status_code == 403


def test_a_made_up_token_is_still_refused(client):
    _issue(client, "ptsb")
    assert client.get("/catalogue", headers=_bearer("not-a-real-token")).status_code == 403


# -- submitted_by --------------------------------------------------------------


PAYLOAD = dict(
    code="PTSB",
    name="Progressa Tertiary Scholarship Board",
    subsystem="SCHOLARSHIP",
    subsystem_description="Scholarship award management",
    security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
    backend={"auth": "network_allowlist"},
    member_requirements={
        "has_security_server": True,
        "has_registered_identity": True,
        "standards_portfolio_adopted": True,
        "data_conformant": True,
        "lawful_basis": "consent",
        "technical_contact": "Jane Doe",
    },
)


def test_a_submission_on_an_issued_token_records_which_agency_sent_it(client):
    token = _issue(client, "ptsb")
    record = client.post("/requests", json=PAYLOAD, headers=_bearer(token)).json()
    assert record["state"] == "SUBMITTED", record
    assert record["submitted_by"] == "ptsb"
    assert client.get(f"/requests/{record['id']}",
                      headers=OPERATOR).json()["submitted_by"] == "ptsb"


def test_a_rejected_submission_records_it_too(client):
    """Who submitted a request that was refused is exactly as much a fact as
    who submitted one that passed."""
    token = _issue(client, "ptsb")
    record = client.post("/requests", json={"code": "NOPE"}, headers=_bearer(token)).json()
    assert record["state"] == "REJECTED"
    assert record["submitted_by"] == "ptsb"


def test_the_shared_token_records_nobody(client):
    """"applicant" is not an agency. A field that looks like attribution and
    identifies nobody is worse than an absent one."""
    record = client.post("/requests", json=PAYLOAD, headers=APPLICANT).json()
    assert record["submitted_by"] is None


def test_the_queue_carries_submitted_by_to_the_operator(client):
    token = _issue(client, "ptsb")
    client.post("/requests", json=PAYLOAD, headers=_bearer(token))
    queue = client.get("/requests", headers=OPERATOR).json()["requests"]
    assert [r["submitted_by"] for r in queue] == ["ptsb"]


def test_a_revoked_agency_keeps_its_name_on_the_requests_it_made(client):
    """The record is evidence of a decision; revoking a credential does not
    unmake the submission it was used for."""
    token = _issue(client, "ptsb")
    record = client.post("/requests", json=PAYLOAD, headers=_bearer(token)).json()
    client.request("DELETE", "/tokens/ptsb", headers=OPERATOR)
    assert client.get(f"/requests/{record['id']}",
                      headers=OPERATOR).json()["submitted_by"] == "ptsb"


# -- tokens and requests never collide -----------------------------------------


def test_the_token_store_is_not_mistaken_for_a_join_request(client):
    """One SQLite database, two separate tables: a token row can never be
    miscounted as a request row by construction -- unlike the old
    out/join/*.json + out/join-tokens.json layout, where a second kind of
    document in out/join/ would have been globbed as a join request by
    _recover_interrupted_jobs, the store quota and scripts/member.sh drift
    alike."""
    _issue(client, "ptsb")
    assert app_module.store.count_requests(_conn()) == 0
