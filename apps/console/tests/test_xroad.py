"""Unit tests for apps/console/xroad.py. httpx.MockTransport stubs every
response -- no network, no Docker, no live stack required."""
import json
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from xroad import AdminSession, exchange  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "xroad"


def _fixture(name: str) -> dict:
    """A real recorded response (status + headers + body), not a
    hand-written guess. See the fixture's own
    "context" field and docs/decisions/xroad-770-notes.md for what each one documents."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _login_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, headers={"set-cookie": "XSRF-TOKEN=test-token; Path=/"}, request=request
    )


def test_admin_session_login_captures_xsrf_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login"
        return _login_response(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    session = AdminSession("ss-plr", "xrd", "secret", client=client)
    assert session._xsrf == "test-token"


def test_admin_session_sends_xsrf_header_on_get():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        seen_headers.update(request.headers)
        return httpx.Response(200, json=[], request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    session = AdminSession("ss-plr", "xrd", "secret", client=client)
    session.get("/clients/X/service-clients")
    assert seen_headers["x-xsrf-token"] == "test-token"


def test_read_subjects_and_read_acl():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        if request.url.path.endswith("/service-clients"):
            return httpx.Response(200, json=[{"id": "PROGRESSA:GOV:PNEA:EXAMS"}], request=request)
        if request.url.path.endswith("/access-rights"):
            return httpx.Response(200, json=[{"service_code": "identity-api"}], request=request)
        raise AssertionError(f"unexpected path {request.url.path}")

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert session.read_subjects("PROGRESSA:GOV:PNIA:IDENTITY") == ["PROGRESSA:GOV:PNEA:EXAMS"]
    assert session.read_acl("PROGRESSA:GOV:PNIA:IDENTITY", "PROGRESSA:GOV:PNEA:EXAMS") == ["identity-api"]


def test_read_acl_returns_empty_list_on_404_not_raises():
    """A subject with
    zero access rights isn't a service-client at all, so the admin API
    404s here rather than returning []. app.py's
    _mutate_acl reads this to determine prior_state before mutating -- the
    fully-revoked case must read as [], or the caller can never observe
    "nothing currently granted". The real body is
    {"status":404,"error":{"code":"service_client_not_found"}} -- a hand-
    written {"detail": "not found"} would have passed this test just as
    well without ever matching what X-Road actually sends. See
    docs/decisions/xroad-770-notes.md §10."""
    fx = _fixture("read_acl_404")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        return httpx.Response(fx["status"], json=fx["body"], request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert session.read_acl("PROGRESSA:GOV:PNIA:IDENTITY", "PROGRESSA:GOV:PNEA:EXAMS") == []


def test_grant_success():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        calls.append(json.loads(request.content))
        return httpx.Response(201, json={}, request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    session.grant("PROGRESSA:GOV:PNIA:IDENTITY", "PROGRESSA:GOV:PNEA:EXAMS", "identity-api")
    assert calls == [{"items": [{"service_code": "identity-api"}]}]


def test_grant_already_granted_409_is_success_not_failure():
    """Real body:
    {"status":409,"error":{"code":"duplicate_accessright"}} -- the earlier
    hand-written {"error": "already granted"} exercised the same code path
    but never matched what X-Road actually sends. See
    docs/decisions/xroad-770-notes.md §10."""
    fx = _fixture("grant_409_duplicate")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        return httpx.Response(fx["status"], json=fx["body"], request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    session.grant("PROGRESSA:GOV:PNIA:IDENTITY", "PROGRESSA:GOV:PNEA:EXAMS", "identity-api")  # must not raise


def test_revoke_already_revoked_409_is_success_not_failure():
    """Revoking an
    already-revoked right returns 409 accessright_not_found -- the target
    state already holds, so this must not raise (load-bearing for
    reset()'s crash-recovery replay). See
    docs/decisions/xroad-770-notes.md §10."""
    fx = _fixture("revoke_409_not_found")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        return httpx.Response(fx["status"], json=fx["body"], request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    session.revoke("PROGRESSA:GOV:PNIA:IDENTITY", "PROGRESSA:GOV:PNEA:EXAMS", "identity-api")  # must not raise


def test_revoke_uses_delete_path_and_204():
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        seen_paths.append(request.url.path)
        return httpx.Response(204, request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    session.revoke("PROGRESSA:GOV:PNIA:IDENTITY", "PROGRESSA:GOV:PNEA:EXAMS", "identity-api")
    assert seen_paths == [
        "/api/v1/clients/PROGRESSA:GOV:PNIA:IDENTITY/service-clients/PROGRESSA:GOV:PNEA:EXAMS/access-rights/delete"
    ]


CALLS = [
    {"service": "PROGRESSA/GOV/PNIA/IDENTITY/identity-api", "r1_path": "/r1/.../persons/{nin}"},
]


def test_exchange_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-road-client"] == "PROGRESSA/GOV/PNEA/EXAMS"
        return httpx.Response(200, json={"nin": "123", "given_name": "Binta"}, request=request)

    results = exchange(
        "http://ss-plr:8080", CALLS, "123", "PROGRESSA/GOV/PNEA/EXAMS",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert len(results) == 1
    assert results[0].status_code == 200
    assert results[0].denied is False
    assert results[0].error is None
    assert results[0].body["given_name"] == "Binta"


def test_exchange_denied_parses_exact_fault_shape():
    """Real body from a live denied r1 call:
    {"type":"Server.ServerProxy.AccessDenied","message":"Request is not
    allowed: SERVICE:PROGRESSA/GOV/PNIA/IDENTITY/identity-api","detail":
    "<uuid>"} -- the earlier hand-written version was missing both the
    "SERVICE:..." suffix on message and the "detail" field entirely; a
    parser that happened to read only .type would never have caught that
    it was checking against an incomplete shape. See
    docs/decisions/xroad-770-notes.md §10."""
    fx = _fixture("exchange_access_denied")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(fx["status"], json=fx["body"], request=request)

    results = exchange(
        "http://ss-plr:8080", CALLS, "123", "PROGRESSA/GOV/MOEYS/PEMIS",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert results[0].denied is True
    assert results[0].fault_type == "Server.ServerProxy.AccessDenied"
    assert results[0].error is None


def test_exchange_other_500_is_not_misclassified_as_denial():
    """A 500 that isn't the AccessDenied shape must not read as a permission decision."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"type": "Server.ServerProxy.SomeOtherFault"}, request=request)

    results = exchange(
        "http://ss-plr:8080", CALLS, "123", "PROGRESSA/GOV/PNEA/EXAMS",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert results[0].denied is False
    assert results[0].status_code == 500


def test_exchange_transport_failure_is_not_misclassified_as_denial():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    results = exchange(
        "http://ss-plr:8080", CALLS, "123", "PROGRESSA/GOV/PNEA/EXAMS",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert results[0].denied is False
    assert results[0].status_code is None
    assert "connection refused" in results[0].error


def test_expired_session_re_logs_in_and_retries_once():
    """What makes app.py's per-host session cache safe: the server can drop
    the session at any point, and the next call has to recover rather than
    surface a 401 to the page."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/login":
            return _login_response(request)
        # 401 on the first API call only -- the retry after re-login succeeds.
        if calls.count("/api/v1/clients/X/service-clients") == 1:
            return httpx.Response(401, request=request)
        return httpx.Response(200, json=[{"id": "PROGRESSA:GOV:PNEA:EXAMS"}], request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert session.read_subjects("X") == ["PROGRESSA:GOV:PNEA:EXAMS"]
    assert calls.count("/login") == 2  # the initial login, then one re-login


def test_a_403_is_not_retried():
    """403 means authenticated and refused. Logging in again would neither
    fix it nor explain it, and would double the sessions on a server that is
    already saying no."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/login":
            return _login_response(request)
        return httpx.Response(403, request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert session.get("/clients/X/service-clients").status_code == 403
    assert calls.count("/login") == 1


def test_exchange_defaults_to_the_exchange_pool(monkeypatch):
    """The leak this replaced: exchange() built a fresh, never-closed
    httpx.Client on every call, and the counter tab calls it per lookup.

    EXCHANGE_CLIENT, not SHARED_CLIENT: the two stopped sharing a trust
    decision at docs/production-delta.md row 19 -- SHARED_CLIENT still runs
    verify=False for the unverifiable :4000 admin certificate, and the
    consumer hop must never inherit that."""
    import xroad

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(xroad, "EXCHANGE_CLIENT", httpx.Client(transport=httpx.MockTransport(handler)))
    results = xroad.exchange(
        "http://ss-pdga:8080/r1",
        [{"service": "identity-api", "r1_path": "/persons/{nin}"}],
        "02831663233",
        "PROGRESSA:GOV:PDGA:MANAGEMENT",
    )
    assert [r.status_code for r in results] == [200]
    assert seen == ["http://ss-pdga:8080/r1/persons/02831663233"]


def test_the_consumer_hop_never_runs_with_verification_off():
    """Row 19's one non-negotiable. The admin session's own client may still
    be unverified -- there is nothing to verify a self-signed :4000
    certificate against -- but the exchange path must not inherit that, or
    an encrypted consumer hop is open to anyone able to answer for the
    Security Server's name."""
    import ssl

    import xroad

    ctx = xroad._exchange_ssl_context()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
