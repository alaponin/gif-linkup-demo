"""apps/spec-fetcher/app.py -- the one place in this pack that still fetches
an applicant-controlled URL, and the only container that does it holding no
credentials at all.

Why this service exists (docs/production-delta.md row 41): apps/join-api's
validate.py used to run httpx.get() against a joining applicant's spec_url
(and the servers[].url inside the OpenAPI document that URL returns) from
inside the join-api container -- which holds JOB_SECRETS (the federation's
admin user, admin password and token PIN) and, via the `linkup` Docker
network, a route to every Security Server's and the Central Server's admin
API on :4000. An allowlist checked before that fetch (validate.py's
spec_url_origin check) is not segregation: it is one list, one edit away
from being widened carelessly, guarding a fetch that still runs somewhere
that can reach the admin plane and still holds the credentials to use it.

The fix here is topological, not another list. This service:
  - runs in its OWN container, on its OWN Docker network (docker-compose.
    yml's `specs`, internal: true) -- no route to `cs`, `ca`, or any `ss-*`,
    and no external egress either (internal: true forbids that too, on
    docker-local -- see docker-compose.yml's own comment and docs/
    deployment-targets.md's "Spec fetch egress" row for what a target with
    real external spec hosts has to decide instead);
  - has no volumes and no secrets in its environment -- nothing here is
    worth stealing, so an SSRF payload in a spec_url lands somewhere with no
    admin plane to reach and nothing to exfiltrate;
  - re-applies validate.py's own controls anyway (the host allowlist --
    passed as a request parameter, since this container reads no policy
    file of its own -- the IP-literal refusal, follow_redirects=False, a
    timeout and a response-size cap). The topology is the primary control;
    this is defence in depth, applied a second time in a second place, so
    each layer is sufficient alone.

Two endpoints, mirroring validate.py's two callables exactly:
  GET /fetch?url=...&allowed_hosts=...   -- the OpenAPI document's text
                                             (validate.py's fetch_spec)
  GET /probe?url=...&allowed_hosts=...   -- resolve-and-connect only, any
                                             response counts as reachable
                                             (validate.py's check_reachable)
`allowed_hosts` may repeat (one query param per allowed host) or be absent,
which -- like validate.py's own _origin_error -- means "fetch nothing":
there is no default-allow here any more than there is in validate.py.
"""
from __future__ import annotations

import ipaddress
import os
import ssl
import urllib.parse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

app = FastAPI()

_ALLOWED_SCHEMES = frozenset({"http", "https"})
# 1 MiB. Every real fixture in this pack (apps/specs/*.openapi.yaml) is a
# few KiB; a legitimate OpenAPI document for a handful of GET-only services
# is nowhere near this. Generous enough not to be a footgun for a real
# member's spec, small enough that an applicant-controlled backend cannot
# turn a fetch into a memory exhaustion.
_MAX_BODY_BYTES = 1 * 1024 * 1024
_TIMEOUT = 5.0

# A module-level client rather than a bare httpx.stream(...) call per
# request, for exactly one reason: apps/spec-fetcher/tests/test_app.py
# swaps this for an httpx.Client(transport=httpx.MockTransport(...)) so the
# allowlist/IP-refusal/redirect/size-cap/timeout tests below can run
# against a fake transport with no real socket, the same way apps/join-api/
# tests/test_validate.py's own reachability test stands in a local
# http.server rather than mocking validate.py's logic away. _get() reads
# this name from the module global at call time (not as a default
# parameter value, which Python would bind once at import time and a test's
# later reassignment would never be seen).
def _ssl_context() -> ssl.SSLContext:
    """The TLS trust store for every https fetch this service makes
    (docs/production-delta.md row 18).

    Stock public verification (certifi via ssl.create_default_context) PLUS
    whatever SPEC_FETCHER_CA_BUNDLE names, never instead of it: on
    docker-local that variable is the Test CA's public certificate, mounted
    read-only from the `ca-certs` volume, because the demo's own mock
    backends now serve https with Test CA-issued certificates
    (apps/mock-registry/entrypoint.sh). Unset on any target with real spec
    hosts => stock verification, unchanged.

    What this is NOT, and must never become: `verify=False`. Verification
    off would make an https spec_url weaker than the http one it replaced --
    an attacker on the path could serve any OpenAPI document it liked and
    check 9 would believe it. An explicit CA file is the whole difference
    between row 18 being closed and being theatre.
    """
    ctx = ssl.create_default_context()
    bundle = os.environ.get("SPEC_FETCHER_CA_BUNDLE")
    if bundle:
        ctx.load_verify_locations(cafile=bundle)
    return ctx


_CLIENT = httpx.Client(verify=_ssl_context())


def _origin_error(url: str, allowed_hosts: list[str]) -> str | None:
    """None if `url` may be fetched from this container; a rejection
    message otherwise. Deliberately the same logic as apps/join-api/
    validate.py's _origin_error -- scheme, IP-literal and localhost refusal
    are unconditional; the allowlist itself comes from the caller (this
    container has no join-policy.yaml of its own), which is what makes this
    a second, independent application of the same rule rather than a share
    of the same code."""
    if not allowed_hosts:
        return (
            "no allowed_hosts were supplied with this request -- this "
            "service refuses to fetch any URL without an explicit "
            "allowlist to judge it against, the same fail-closed rule "
            "join-api's own join.spec_url_hosts check applies"
        )
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return (
            f"{url!r} uses scheme {parsed.scheme or '(none)'!r} -- only "
            f"{sorted(_ALLOWED_SCHEMES)} are fetched"
        )
    host = parsed.hostname
    if not host:
        return f"{url!r} names no host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return (
            f"{url!r} names an IP address rather than a host name -- "
            "addresses are refused outright (loopback, link-local and the "
            "cloud metadata address 169.254.169.254 among them), whatever "
            "allowed_hosts says"
        )
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return f"{url!r} names {host!r}, which resolves inside this container"
    if host not in allowed_hosts:
        return f"{url!r} names host {host!r}, which is not in allowed_hosts {sorted(allowed_hosts)}"
    return None


def _get(url: str) -> tuple[int, str]:
    """The one real network call this service makes, with every control
    validate.py's own comment on _default_fetch_spec already argued for:
    follow_redirects=False (an allowlisted host's 302 must not walk past
    the check that already ran), a bounded timeout, and a response-size cap
    enforced by streaming rather than trusting Content-Length (an
    applicant-controlled server can lie about or omit that header). Returns
    (status_code, body text) -- both callers only ever need those two."""
    with _CLIENT.stream("GET", url, timeout=_TIMEOUT, follow_redirects=False) as resp:
        body = bytearray()
        for chunk in resp.iter_bytes():
            body.extend(chunk)
            if len(body) > _MAX_BODY_BYTES:
                raise HTTPException(
                    502,
                    f"response from {url} exceeded the {_MAX_BODY_BYTES}-byte cap",
                )
        return resp.status_code, body.decode("utf-8", errors="replace")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/fetch")
def fetch(url: str, allowed_hosts: list[str] = Query(default=[])) -> PlainTextResponse:
    """Mirrors validate.py's _default_fetch_spec: fetch the URL, return its
    body verbatim with the target's own status code so the caller's
    resp.raise_for_status() behaves exactly as it did when validate.py made
    this call itself."""
    error = _origin_error(url, allowed_hosts)
    if error:
        raise HTTPException(400, error)
    try:
        status_code, text = _get(url)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not fetch {url}: {exc}") from exc
    return PlainTextResponse(text, status_code=status_code)


@app.get("/probe")
def probe(url: str, allowed_hosts: list[str] = Query(default=[])) -> dict:
    """Mirrors validate.py's _default_check_reachable: resolve-and-connect
    only. Any response -- even a 404 -- proves the TCP/TLS handshake and
    HTTP exchange succeeded, so this returns 200 for any status the target
    gave back, and only a real connection failure becomes a non-2xx here."""
    error = _origin_error(url, allowed_hosts)
    if error:
        raise HTTPException(400, error)
    try:
        _get(url)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach {url}: {exc}") from exc
    return {"reachable": True}
