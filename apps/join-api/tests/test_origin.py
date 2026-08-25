"""Unit tests for apps/join-api/origin.py -- the shared SSRF containment
rule. test_validate.py's own
test_spec_url_origin_refuses_before_anything_is_fetched already proves
this logic end to end through validate.py's wrapper; this file tests the
extracted module directly, plus no_redirect_opener, which validate.py does
not use (it fetches via httpx with follow_redirects=False) but
scripts/member.sh does.
"""
from __future__ import annotations

import http.server
import pathlib
import sys
import threading
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from origin import no_redirect_opener, origin_error  # noqa: E402

ALLOWED = ["app-ptsb", "app-plr"]


# -- origin_error ---------------------------------------------------------


def test_ip_literal_is_refused_even_when_allowlisted():
    err = origin_error("label", "http://127.0.0.1:8000/spec.yaml", ["127.0.0.1"])
    assert err is not None
    assert "IP address" in err


def test_cloud_metadata_address_is_refused():
    err = origin_error("label", "http://169.254.169.254/latest/meta-data/", ALLOWED)
    assert err is not None
    assert "169.254.169.254" in err


def test_ipv6_loopback_literal_is_refused():
    err = origin_error("label", "http://[::1]:4000/", ALLOWED)
    assert err is not None
    assert "IP address" in err


def test_localhost_is_refused():
    err = origin_error("label", "http://localhost:8091/health", ALLOWED)
    assert err is not None
    assert "localhost" in err


def test_dot_localhost_is_refused():
    err = origin_error("label", "http://evil.localhost:8091/", ALLOWED)
    assert err is not None
    assert "localhost" in err


def test_a_non_http_scheme_is_refused():
    err = origin_error("label", "file:///pack/.env", ALLOWED)
    assert err is not None
    assert "scheme" in err


def test_an_empty_allowlist_fails_closed():
    err = origin_error("label", "http://app-ptsb:8000/spec.yaml", [])
    assert err is not None
    assert "spec_url_hosts" in err


def test_a_none_allowlist_fails_closed():
    err = origin_error("label", "http://app-ptsb:8000/spec.yaml", None)
    assert err is not None
    assert "spec_url_hosts" in err


def test_an_allowlisted_host_passes():
    assert origin_error("label", "http://app-ptsb:8000/spec.yaml", ALLOWED) is None


def test_a_host_not_on_the_allowlist_is_refused():
    err = origin_error("label", "http://evil.example.com/spec.yaml", ALLOWED)
    assert err is not None
    assert "evil.example.com" in err


# -- no_redirect_opener -----------------------------------------------------


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- stdlib method name
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/elsewhere")
        self.end_headers()

    def log_message(self, *args):  # keep pytest -q output clean
        pass


@pytest.fixture(scope="module")
def redirecting_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    thread.join()


def test_a_redirect_is_refused_rather_than_followed(redirecting_server):
    port = redirecting_server
    opener = no_redirect_opener()
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        opener.open(f"http://127.0.0.1:{port}/spec.yaml", timeout=5)
    assert exc_info.value.code == 302


def test_no_redirect_opener_accepts_an_optional_ssl_context():
    """member.sh's fetch sites need TLS handling and no-redirect together --
    building the opener must not require choosing one or the other."""
    import ssl

    opener = no_redirect_opener(context=ssl.create_default_context())
    assert opener is not None
