"""Unit tests for apps/spec-fetcher/app.py -- the credential-free service
apps/join-api/validate.py now delegates every applicant-controlled fetch to
(docs/production-delta.md row 41). No real network: app._CLIENT is swapped
for an httpx.Client wired to httpx.MockTransport, the same
inject-the-transport-not-the-logic shape apps/join-api/tests/
test_validate.py uses for its own reachability check (a local http.server
there; a MockTransport here, because this suite also needs to prove a
redirect target is never actually requested, which a MockTransport's call
log can assert directly).

Every case here is one of the controls app.py's own docstring says are
defence in depth once the topology (docker-compose.yml's `specs` network)
is the primary guard: host allowlist, IP-literal refusal, redirect
refusal, response-size cap, timeout. None of that changes what this test
suite is: a small, credential-free service with two endpoints, tested the
same size as the code it tests.
"""
from __future__ import annotations

import importlib.util
import pathlib
import ssl

import httpx
import pytest
from fastapi.testclient import TestClient

# Loaded by path under a distinct module name, not `sys.path.insert` +
# `import app` -- apps/console, apps/join-api and apps/mock-registry all
# have their own app.py, and when verify.sh runs every apps/*/tests
# directory in one pytest session, a plain `import app` here would
# silently reuse whichever one of those got imported first instead of
# loading this one (same fix as apps/join-api/tests/test_app_health.py and
# apps/mock-registry/tests/test_app.py).
_spec = importlib.util.spec_from_file_location(
    "spec_fetcher_app", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app_module)

GOOD_HOST = "app-ptsb"
GOOD_URL = f"http://{GOOD_HOST}:8000/spec.yaml"


def _client_with(handler):
    """Swaps app._CLIENT for one backed by a MockTransport -- see this
    file's own docstring on why app.py reads _CLIENT from the module
    global rather than a bound default parameter."""
    calls = []

    def _counting_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    app_module._CLIENT = httpx.Client(transport=httpx.MockTransport(_counting_handler))
    return TestClient(app_module.app), calls


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="openapi: 3.0.0")


# -- the allowlist ---------------------------------------------------------


def test_an_allowed_host_is_fetched():
    client, calls = _client_with(_ok)
    resp = client.get("/fetch", params={"url": GOOD_URL, "allowed_hosts": [GOOD_HOST]})
    assert resp.status_code == 200
    assert resp.text == "openapi: 3.0.0"
    assert calls == [GOOD_URL]


def test_a_host_not_on_the_allowlist_is_refused_before_any_fetch():
    client, calls = _client_with(_ok)
    resp = client.get(
        "/fetch", params={"url": "http://evil.example.com/spec.yaml", "allowed_hosts": [GOOD_HOST]}
    )
    assert resp.status_code == 400
    assert "not in allowed_hosts" in resp.text
    assert calls == []  # never reached the transport


def test_no_allowed_hosts_at_all_fails_closed():
    """Fail closed, same rule as validate.py's own _origin_error: an absent
    allowlist is not "allow anything"."""
    client, calls = _client_with(_ok)
    resp = client.get("/fetch", params={"url": GOOD_URL})
    assert resp.status_code == 400
    assert "no allowed_hosts" in resp.text
    assert calls == []


# -- IP-literal / localhost refusal, unconditional on the allowlist --------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:4000/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:4000/",
    "http://localhost:8091/health",
])
def test_ip_literals_and_localhost_are_refused_even_when_allowlisted(url):
    client, calls = _client_with(_ok)
    host = url.split("//", 1)[1].split("/", 1)[0].split(":")[0].strip("[]") or "::1"
    resp = client.get("/fetch", params={"url": url, "allowed_hosts": [host, "localhost"]})
    assert resp.status_code == 400
    assert calls == []


def test_a_non_http_scheme_is_refused():
    client, calls = _client_with(_ok)
    resp = client.get(
        "/fetch", params={"url": "file:///etc/passwd", "allowed_hosts": [GOOD_HOST]}
    )
    assert resp.status_code == 400
    assert "scheme" in resp.text
    assert calls == []


# -- redirect refusal --------------------------------------------------------


def test_a_redirect_is_never_followed():
    """follow_redirects=False is pinned the same way validate.py pins it --
    the allowlist is checked once, before the fetch; a 302 to an
    unadvertised host must not walk past it. The call log proves the
    redirect target was never requested, not just that the final body looks
    unredirected."""

    def _redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://cs:4000/api/v1/clients"})

    client, calls = _client_with(_redirect)
    resp = client.get("/fetch", params={"url": GOOD_URL, "allowed_hosts": [GOOD_HOST]})
    assert resp.status_code == 302  # passed straight through, not followed
    assert calls == [GOOD_URL]  # exactly one request -- the redirect target was never touched


# -- response-size cap -------------------------------------------------------


def test_an_oversized_response_is_refused():
    def _huge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (app_module._MAX_BODY_BYTES + 1))

    client, calls = _client_with(_huge)
    resp = client.get("/fetch", params={"url": GOOD_URL, "allowed_hosts": [GOOD_HOST]})
    assert resp.status_code == 502
    assert "byte cap" in resp.text


def test_a_response_at_the_cap_is_accepted():
    def _at_cap(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * app_module._MAX_BODY_BYTES)

    client, _ = _client_with(_at_cap)
    resp = client.get("/fetch", params={"url": GOOD_URL, "allowed_hosts": [GOOD_HOST]})
    assert resp.status_code == 200


# -- timeout / connection failure --------------------------------------------


def test_a_timeout_becomes_a_502_not_a_500():
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client, _ = _client_with(_timeout)
    resp = client.get("/fetch", params={"url": GOOD_URL, "allowed_hosts": [GOOD_HOST]})
    assert resp.status_code == 502


def test_a_connection_failure_on_probe_is_not_reachable():
    def _refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, _ = _client_with(_refused)
    resp = client.get("/probe", params={"url": GOOD_URL, "allowed_hosts": [GOOD_HOST]})
    assert resp.status_code == 502


# -- /probe's "any response counts as reachable" ------------------------------


def test_probe_treats_a_404_as_reachable():
    """Mirrors validate.py's _default_check_reachable: no
    raise_for_status(), because reachability -- not endpoint correctness --
    is the question."""

    def _not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client, _ = _client_with(_not_found)
    resp = client.get("/probe", params={"url": GOOD_URL, "allowed_hosts": [GOOD_HOST]})
    assert resp.status_code == 200
    assert resp.json() == {"reachable": True}


def test_probe_also_refuses_a_disallowed_host_before_any_fetch():
    client, calls = _client_with(_ok)
    resp = client.get(
        "/probe", params={"url": "http://evil.example.com/", "allowed_hosts": [GOOD_HOST]}
    )
    assert resp.status_code == 400
    assert calls == []


def test_health():
    client, _ = _client_with(_ok)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- the TLS trust store (docs/production-delta.md row 18) ---------------------
# _ssl_context() is the whole of this service's https verification posture,
# and the one thing about it that must never regress is that it is a real
# trust store and not a bypass. These three assert exactly that, without a
# socket: verification stays on, the deployment's own CA is ADDED to the
# public roots rather than replacing them, and an unset variable leaves stock
# verification alone.


def test_the_ssl_context_verifies_and_checks_hostnames():
    ctx = app_module._ssl_context()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_an_extra_ca_bundle_is_added_to_the_public_roots_not_substituted(monkeypatch, tmp_path):
    """`load_verify_locations` adds; it does not replace. If a future edit
    ever swaps this for a context built with cafile= alone, this service
    would stop trusting every public CA the moment a deployment named its
    own -- which is how a "fix" for a private CA quietly breaks every real
    spec host."""
    stock = len(ssl.create_default_context().get_ca_certs())
    bundle = tmp_path / "extra-ca.pem"
    bundle.write_text(_SELF_SIGNED_CA_PEM)
    monkeypatch.setenv("SPEC_FETCHER_CA_BUNDLE", str(bundle))
    ctx = app_module._ssl_context()
    assert len(ctx.get_ca_certs()) == stock + 1


def test_no_ca_bundle_leaves_stock_verification_untouched(monkeypatch):
    monkeypatch.delenv("SPEC_FETCHER_CA_BUNDLE", raising=False)
    ctx = app_module._ssl_context()
    assert len(ctx.get_ca_certs()) == len(ssl.create_default_context().get_ca_certs())
    assert ctx.verify_mode is ssl.CERT_REQUIRED


# A throwaway CA certificate, generated once with
#   openssl req -x509 -newkey rsa:2048 -nodes -days 36500 \
#     -subj "/O=KP2 test/CN=spec-fetcher test CA" -keyout /dev/null -out -
# and pasted here so this suite needs neither a key on disk nor an openssl
# binary. It signs nothing; the only thing asserted about it is that
# _ssl_context() loads it alongside the public roots.
_SELF_SIGNED_CA_PEM = """\
-----BEGIN CERTIFICATE-----
MIIDRzCCAi+gAwIBAgIUDR4H+Jp7YjhdrscNErXDYH93cDUwDQYJKoZIhvcNAQEL
BQAwMjERMA8GA1UECgwIS1AyIHRlc3QxHTAbBgNVBAMMFHNwZWMtZmV0Y2hlciB0
ZXN0IENBMCAXDTI2MDgyMjE4NDM1MVoYDzIxMjYwNzI5MTg0MzUxWjAyMREwDwYD
VQQKDAhLUDIgdGVzdDEdMBsGA1UEAwwUc3BlYy1mZXRjaGVyIHRlc3QgQ0EwggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDfYN8LBvTpZmCofTjzzQJVs/Os
NL7+NkP+thUlelpzbJZlriMzqjN45N+RbtXUCvRkqD6Zhpnh1+398Oqnb0MWYlOf
7t/QMVC1seuTCFCG7K0WPHSC8Tz2ofEptN5ujXwOcR5/h1bhNfod1nWB07i8N9FG
49+MRipKxBZ3Z5VA3MC74gCqTW4DtXYNUARx8tp/fo5hTJ5Fl/O++lmL872c7EYn
EXond6OxBSJxr3GgFOPTA7a1lQnpKVJTnSSM5XZgF+z31iIn16ezaD+C4oQkSva4
N4sKajQySV2UhhJLGdtVsGlJz/aaxL+jbfLKJNLtu7hCLImt8yV/l9QoqiiTAgMB
AAGjUzBRMB0GA1UdDgQWBBR8/0n4XnU4oAD9f0kQxubFqaHWIjAfBgNVHSMEGDAW
gBR8/0n4XnU4oAD9f0kQxubFqaHWIjAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3
DQEBCwUAA4IBAQAjlctHshf+35TY2pvxI0oaYC/ZJlZD33D/Qx6FLi6JI+fVIhEd
VEgCFt97TstGtBVdeSMwxKsc7nHj/8tMfyTrH5ZL7kOzWwyzMIn3exCfEWAEjTvX
eyVDeSI5/1e7VeUyG969Ek6Tv0m+SQW1sm16sDbCU9xX1aVnaPQbVfLDYSuVbAk4
iZgSdViksVuHxphxfIsbiRJNobsoTHpvkMC24wKJsS0VWY/UbogYSD3TCeaR+Abz
WD59zbIsodgebk9XwQbgwccRSvpxoRixr5x8h46HluCLv5kcAJCClfSSwFnHnlHn
PcqxhsfA0eDVWka2NIF5QfCZc17kzePKNZtA
-----END CERTIFICATE-----
"""

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
