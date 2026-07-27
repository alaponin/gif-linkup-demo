"""Unit tests for apps/console/xroad.py. httpx.MockTransport stubs every
response -- no network, no Docker, no live stack required."""
import json
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from xroad import AdminSession, exchange  # noqa: E402


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
    """Confirmed live (2026-07-27): a subject with zero access rights isn't
    a service-client at all, so the admin API 404s here rather than
    returning []. Found because app.py's _mutate_acl reads this to
    determine prior_state before mutating -- the fully-revoked case must
    read as [], or the caller can never observe "nothing currently granted"."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        return httpx.Response(404, json={"detail": "not found"}, request=request)

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
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        return httpx.Response(409, json={"error": "already granted"}, request=request)

    session = AdminSession("ss-plr", "xrd", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    session.grant("PROGRESSA:GOV:PNIA:IDENTITY", "PROGRESSA:GOV:PNEA:EXAMS", "identity-api")  # must not raise


def test_revoke_already_revoked_409_is_success_not_failure():
    """Confirmed live 2026-07-26: revoking an already-revoked right returns
    409 accessright_not_found -- the target state already holds, so this
    must not raise (load-bearing for reset()'s crash-recovery replay)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return _login_response(request)
        return httpx.Response(409, json={"error": {"code": "accessright_not_found"}}, request=request)

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
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"type": "Server.ServerProxy.AccessDenied", "message": "Request is not allowed"},
            request=request,
        )

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
