"""Request-boundary plan Task 2 (S13): POST /api/acl/revoke, /api/acl/grant
and /api/reset accepted a request with no body, no custom header and no
token -- a plain cross-origin <form method=POST> could flip a live ACL.
No network, no Docker -- PACK_DIR points at the existing test fixtures;
the admin session is monkeypatched, same pattern as test_app_mutate_acl.py."""
import os
import pathlib
import sys

os.environ["PACK_DIR"] = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "pack")
os.environ["OUT_DIR"] = "/tmp"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

HEADER = "X-KP2-Console"


class _FakeSession:
    """Matches TRUTH.expected_acl for the "pack" fixture exactly, so
    post_reset succeeds trivially with an empty journal (nothing to
    reverse, live already equals expected)."""

    def __init__(self, live_service_codes, subject="PROGRESSA:GOV:PNEA:EXAMS"):
        self._live = live_service_codes
        self._subject = subject

    def read_acl(self, client_id, subject_id):
        return self._live

    def read_subjects(self, client_id):
        return [self._subject] if self._live else []

    def grant(self, client_id, subject_id, service_code):
        self._live = list({*self._live, service_code})

    def revoke(self, client_id, subject_id, service_code):
        self._live = [s for s in self._live if s != service_code]


def _sessions():
    return {
        "ss-pnia": _FakeSession(["identity-api"]),
        "ss-plr": _FakeSession(["enrolment-api"]),
        "ss-moeys": _FakeSession([]),
    }


def _client(monkeypatch, tmp_path):
    app.JOURNAL = app.journal_mod.Journal(tmp_path / "journal.json")
    sessions = _sessions()
    monkeypatch.setattr(app, "_admin_session", lambda host: sessions[host])
    # raise_server_exceptions=False: test_get_exchange_also_requires_the_header
    # only needs to prove the guard runs before the handler body, not that
    # get_exchange fully succeeds against this fixture's incomplete member
    # configs -- an unrelated 500 past the guard must not fail this test.
    return TestClient(app.app, raise_server_exceptions=False)


def test_no_header_yields_403_and_does_not_mutate_journal(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/api/acl/revoke")
    assert resp.status_code == 403
    assert HEADER.lower() in resp.text.lower()  # names the missing header
    assert app.JOURNAL.entries() == []


def test_header_no_origin_succeeds_the_curl_case(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/api/reset", headers={HEADER: "1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_header_with_foreign_origin_yields_403(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post(
        "/api/acl/revoke",
        headers={HEADER: "1", "Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert app.JOURNAL.entries() == []


def test_header_with_own_origin_succeeds(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post(
        "/api/reset",
        headers={HEADER: "1", "Origin": "http://testserver"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_get_exchange_also_requires_the_header(monkeypatch, tmp_path):
    """Step 5's decision: reads that trigger real bus calls are guarded
    too, not just the three that write."""
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/exchange/02831663233")
    assert resp.status_code == 403
    resp = client.get("/api/exchange/02831663233", headers={HEADER: "1"})
    assert resp.status_code != 403
