"""Unit tests for apps/console/xroad.py. httpx.MockTransport stubs every
response -- no network, no Docker, no live stack required. The one
exception is test_admin_ssl_context_rejects_a_different_certificate below,
which opens a real loopback TLS socket -- see its own docstring for why
MockTransport cannot prove the property it proves."""
import json
import pathlib
import socket
import ssl
import subprocess
import sys
import threading

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

    EXCHANGE_CLIENT, not an admin_client(): the two stopped sharing a trust
    decision at docs/production-delta.md row 19 -- the admin clients pin
    (or, unpinned, run verify=False for) the unverifiable :4000 admin
    certificate, and the consumer hop must never inherit that."""
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


# -- admin-API TOFU pinning (security-review-remediation-plan.md Phase C, M1) -


@pytest.fixture(autouse=True)
def _reset_admin_client_cache():
    """admin_client() memoises by host in a module-level dict -- these tests
    each construct one for a throwaway host name, so the cache must not leak
    into another test (or another test module) that reuses the same name."""
    import xroad

    yield
    with xroad._ADMIN_CLIENTS_LOCK:
        for client, _fingerprint in xroad._ADMIN_CLIENTS.values():
            client.close()
        xroad._ADMIN_CLIENTS.clear()
    xroad._WARNED_UNPINNED.clear()


def test_shared_client_no_longer_exists():
    """The old module-level SHARED_CLIENT ran verify=False for every host,
    unconditionally -- removed outright, not left as a dead, temptingly
    reusable escape hatch, once every caller moved to admin_client(host)."""
    import xroad

    assert not hasattr(xroad, "SHARED_CLIENT")


def test_admin_ssl_context_pins_the_captured_certificate(monkeypatch):
    """A host with a captured certificate at KP2_XROAD_ADMIN_CERT_DIR/<host>.pem
    gets a verifying context built from exactly that file -- and
    check_hostname stays off, because the certificate's CN is the
    container's own runtime hostname, never the admin host name callers
    connect with (verified live -- see the module docstring)."""
    import ssl

    import xroad

    monkeypatch.setenv("KP2_XROAD_ADMIN_CERT_DIR", str(FIXTURES))
    (FIXTURES / "ss-pinned-test.pem").write_bytes((FIXTURES / "pinned-admin-cert.pem").read_bytes())
    try:
        ctx = xroad._admin_ssl_context("ss-pinned-test")
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert ctx.check_hostname is False
    finally:
        (FIXTURES / "ss-pinned-test.pem").unlink()


def test_admin_ssl_context_falls_back_to_unverified_when_unpinned(monkeypatch, tmp_path):
    """No captured certificate for this host (not yet deployed through
    hurl/run-linkup.sh, or KP2_XROAD_ADMIN_CERT_DIR unset entirely, as in
    every test above that passes client= directly) -- verify=False, same as
    before this phase, so docker-local's zero-setup demo path is unaffected
    the first time a container starts."""
    import xroad

    monkeypatch.setenv("KP2_XROAD_ADMIN_CERT_DIR", str(tmp_path))
    assert xroad._admin_ssl_context("ss-never-captured") is False

    monkeypatch.delenv("KP2_XROAD_ADMIN_CERT_DIR")
    assert xroad._admin_ssl_context("ss-never-captured-2") is False


def test_admin_client_is_pinned_and_cached_per_host(monkeypatch):
    """admin_client() builds the client from _admin_ssl_context(host), and
    the same host returns the SAME client object -- the pooling
    admin_client()'s own docstring promises."""
    import xroad

    monkeypatch.setenv("KP2_XROAD_ADMIN_CERT_DIR", str(FIXTURES))
    (FIXTURES / "ss-pinned-test-2.pem").write_bytes((FIXTURES / "pinned-admin-cert.pem").read_bytes())
    try:
        first = xroad.admin_client("ss-pinned-test-2")
        second = xroad.admin_client("ss-pinned-test-2")
        assert first is second
    finally:
        (FIXTURES / "ss-pinned-test-2.pem").unlink()


def test_admin_client_rebuilds_when_the_pin_state_changes(monkeypatch, tmp_path):
    """The cache is keyed on more than the host name -- a certificate
    captured AFTER the first call (this console started before
    hurl/run-linkup.sh's capture step, or before scripts/join-agent.sh
    captured a newly joined member's own server) must not leave this host
    pinned to `verify=False` for the rest of the process's life. Found in
    review: the original cache kept the client built on the FIRST call
    forever, silently."""
    import xroad

    monkeypatch.setenv("KP2_XROAD_ADMIN_CERT_DIR", str(tmp_path))

    # First call: nothing captured yet -- unpinned.
    unpinned = xroad.admin_client("ss-late-capture")

    # A certificate now appears, as it would once hurl/run-linkup.sh (or
    # join-agent.sh) captures it.
    (tmp_path / "ss-late-capture.pem").write_bytes((FIXTURES / "pinned-admin-cert.pem").read_bytes())
    pinned = xroad.admin_client("ss-late-capture")
    assert pinned is not unpinned
    # The stale client is actually closed, not merely dropped -- this is
    # what AdminSession's `_client` property (resolved fresh on every
    # access, never cached on the instance) makes safe. Found missing in
    # review, second pass: this assertion would have caught the original
    # bug (AdminSession DID cache `_client` at __init__) on its own.
    assert unpinned.is_closed

    # And calling again with the SAME (now pinned) state is still cached.
    pinned_again = xroad.admin_client("ss-late-capture")
    assert pinned_again is pinned
    assert not pinned.is_closed


def test_admin_client_rebuilds_when_the_pinned_certificate_changes(monkeypatch, tmp_path):
    """The present -> CHANGED transition (a redeploy while this process
    keeps running), not just absent -> present -- and specifically a
    same-mtime replacement, the case mtime alone cannot detect. Found in
    review, second pass: scripts/lib-stack.sh's _capture_admin_cert() writes
    via `mv` (rename(2)), which carries the source file's mtime over rather
    than stamping a fresh one -- confirmed live, a re-capture that produced
    different bytes did not change the destination's mtime. Inode DOES
    change on every such replacement, which is why _admin_pin_fingerprint()
    includes it."""
    import os

    import xroad

    monkeypatch.setenv("KP2_XROAD_ADMIN_CERT_DIR", str(tmp_path))
    cert_a, _key_a = _self_signed(tmp_path, "cycled-a")
    cert_b, _key_b = _self_signed(tmp_path, "cycled-b")
    pinned_pem = tmp_path / "ss-cycled.pem"

    def replace_with_same_mtime(source: pathlib.Path):
        # The same technique scripts/lib-stack.sh's _capture_admin_cert()
        # uses (write a .tmp, then move it into place) -- mv/rename(2)
        # carries the SOURCE's mtime, so forcing both temp files to an
        # identical mtime here reproduces the exact case where mtime alone
        # would miss the change.
        tmp = tmp_path / "ss-cycled.pem.tmp"
        tmp.write_bytes(source.read_bytes())
        os.utime(tmp, (1_700_000_000, 1_700_000_000))
        os.replace(tmp, pinned_pem)

    replace_with_same_mtime(cert_a)
    first = xroad.admin_client("ss-cycled")

    replace_with_same_mtime(cert_b)
    second = xroad.admin_client("ss-cycled")

    assert second is not first
    assert first.is_closed


def test_admin_session_defaults_to_the_pinned_admin_client(monkeypatch):
    """AdminSession's default client (no client= override, unlike every
    other test in this module) is admin_client(host) -- not a bare
    verify=False client shared across every host. Resolved through the
    `_client` PROPERTY on every access, not bound once at __init__ (found
    in review, second pass: a cached AdminSession must follow admin_client()'s
    own cache-invalidation, or a client it closed out from under a stale
    binding would break every later call on that session forever) -- so
    `fake_admin_client` is called more than once here, and that is the
    point, not a loosened assertion."""
    import xroad

    fake_client = httpx.Client(transport=httpx.MockTransport(_login_response))
    seen_hosts = []

    def fake_admin_client(host):
        seen_hosts.append(host)
        return fake_client

    monkeypatch.setattr(xroad, "admin_client", fake_admin_client)
    session = AdminSession("ss-pinned-test-3", "xrd", "secret")
    assert seen_hosts and set(seen_hosts) == {"ss-pinned-test-3"}
    assert session._client is fake_client


def _self_signed(out_dir: pathlib.Path, cn: str) -> pathlib.Path:
    """A throwaway self-signed cert+key pair, generated fresh rather than
    committed -- there is no reason to keep a private key, even a test-only
    one, in the repo when openssl (already a hard preflight.sh requirement)
    can make one in milliseconds."""
    cert = out_dir / f"{cn}.pem"
    key = out_dir / f"{cn}.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", f"/CN={cn}"],
        check=True, capture_output=True,
    )
    return cert, key


def _serve_one_tls_connection(cert: pathlib.Path, key: pathlib.Path):
    """Binds a loopback TLS listener presenting `cert`/`key`, accepts
    exactly one connection in a background thread, and returns the port
    once bound. Used only by the test below."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert), str(key))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)
    sock.settimeout(5)

    def accept_once():
        try:
            conn, _ = sock.accept()
            try:
                with server_ctx.wrap_socket(conn, server_side=True):
                    pass
            except ssl.SSLError:
                pass  # the client refused the handshake -- expected in the mismatch case
        except socket.timeout:
            pass
        finally:
            sock.close()

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    return port, thread


def test_admin_ssl_context_rejects_a_different_certificate(tmp_path, monkeypatch):
    """The security property _admin_ssl_context() exists for, proven against
    a real TLS handshake -- every other test in this module only inspects
    the returned ssl.SSLContext's attributes (verify_mode, check_hostname),
    which proves the context is CONFIGURED to verify, not that it actually
    REJECTS an impostor. A live socket is the only way to prove that:
    httpx.MockTransport never performs a TLS handshake at all."""
    import xroad

    cert_a, key_a = _self_signed(tmp_path, "server-a")
    cert_b, _key_b = _self_signed(tmp_path, "server-b")

    # Pinned to A, server presents A -- must succeed.
    port, thread = _serve_one_tls_connection(cert_a, key_a)
    ctx = ssl.create_default_context(cafile=str(cert_a))
    ctx.check_hostname = False
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        with ctx.wrap_socket(sock):
            pass  # no exception -- the matching pin verified
    thread.join(5)

    # Pinned to B, server presents A -- must fail, not silently pass.
    port, thread = _serve_one_tls_connection(cert_a, key_a)
    ctx = ssl.create_default_context(cafile=str(cert_b))
    ctx.check_hostname = False
    with pytest.raises(ssl.SSLCertVerificationError):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            with ctx.wrap_socket(sock):
                pass
    thread.join(5)

    # And this is exactly the context _admin_ssl_context() builds: same
    # cafile+check_hostname=False shape, not a hand-rolled stand-in.
    cert_dir = tmp_path / "pinned"
    cert_dir.mkdir()
    (cert_dir / "some-host.pem").write_bytes(cert_a.read_bytes())
    monkeypatch.setenv("KP2_XROAD_ADMIN_CERT_DIR", str(cert_dir))
    real_ctx = xroad._admin_ssl_context("some-host")
    assert real_ctx.verify_mode is ssl.CERT_REQUIRED
    assert real_ctx.check_hostname is False
