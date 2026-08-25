"""apps/console/xroad.py -- the only thing in this container that talks to
X-Road. Two distinct clients, because they hit two distinct surfaces:

- AdminSession: the Security Server admin REST API on :4000, session-login
  authenticated (mirrors scripts/lib-stack.sh's api_key()/api() exactly). Used for
  reading and mutating ACLs.
- exchange(): the r1 proxy interface -- :8080 plain, or :8443 TLS when the
  consumer's connection_type says so -- authenticated by the X-Road-Client
  header, not an admin session. Used for the counter/inspector tabs' actual
  once-only-exchange calls.

The two now differ in exactly one way, and it is the point of
docs/production-delta.md row 19:

- AdminSession is TOFU-pinned, not unverified. The :4000 admin UI presents
  the sidecar's own self-signed proxy-ui certificate, which nothing issues,
  so there is no CA to verify it against -- but hurl/run-linkup.sh captures
  each server's own certificate at deploy time into
  KP2_XROAD_ADMIN_CERT_DIR/<host>.pem, and
  that captured leaf is trusted as its own root from then on. This closes
  "any attacker on the path" and does NOT close "an attacker was on the
  path during the very first deploy, before the certificate was captured"
  -- the same honesty caveat scripts/lib-stack.sh's testca_bundle()
  already carries for the Test CA. Hostname verification stays off: the
  certificate's CN/SAN name the container's own runtime hostname, which is
  neither predictable nor what any caller here connects with. No captured
  certificate for a host (not yet deployed through run-linkup.sh, or a unit
  test with no cert directory at all) falls back to verify=False, logged
  once -- the demo compromise this row used to describe unconditionally is
  now the exception, not the rule.
- exchange() has always been different. A consumer's TLS client proxy
  presents an internal TLS certificate the federation's CA issued for that
  server's name (hurl's ss.internal_tls_cert step), so this connection is
  verified against KP2_XROAD_CA_BUNDLE like any other real TLS client.
  Turning verification off here would leave the hop encrypted against an
  observer and open to anyone able to answer for `ss-pnea` -- which is not
  a smaller version of the property row 19 claims, it is the absence of it.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import pathlib
import ssl
import threading
import time

import httpx

_LOG = logging.getLogger("kp2.console.xroad")

# One pooled client PER ADMIN HOST, instead of a fresh httpx.Client per call
# -- those were never closed, so a console left open for a demo accumulated
# one connection pool per poll (the page polls /api/topology and /api/acl
# every 30s) until the garbage collector happened to reap them. Reusing a
# pool also keeps the TLS handshake off every repeat call. httpx.Client is
# thread-safe, which matters here: FastAPI runs this app's sync endpoints in
# a threadpool.
#
# Per host, not one shared client (the old SHARED_CLIENT), because each
# admin host is now pinned against ITS OWN captured certificate -- httpx
# fixes `verify=` at Client construction, so one trust decision cannot serve
# every host once the decision differs per host.
#
# Each entry is (client, pin_fingerprint) -- see _admin_pin_fingerprint()'s
# own docstring for why the fingerprint is kept and re-checked on every
# call, not just built once. Found in review.
_ADMIN_CLIENTS: dict[str, tuple[httpx.Client, tuple[str, int, int, int] | None]] = {}
_ADMIN_CLIENTS_LOCK = threading.Lock()
_WARNED_UNPINNED: set[str] = set()


def _admin_pin_fingerprint(host: str) -> tuple[str, int, int, int] | None:
    """What admin_client(host) would pin against RIGHT NOW, cheaply -- the
    pinned pem's path, mtime, inode and size, or None when unpinned.
    Comparing this against the fingerprint a cached client was built from is
    what makes admin_client() notice a certificate that did not exist yet at
    first contact (this console started before hurl/run-linkup.sh's capture
    step, or before a member's own server was joined and captured by
    scripts/join-agent.sh) or that changed since (a redeploy while this
    process kept running) -- without this, the trust decision made on the
    FIRST call to a host was frozen for the container's entire lifetime,
    silently, which is a worse failure mode than the fallback it was
    supposed to be temporary cover for.

    mtime alone is not reliable here (found in review, second pass):
    scripts/lib-stack.sh's _capture_admin_cert() writes via mv (rename(2)),
    which carries the source file's mtime over rather than stamping a fresh
    one -- confirmed live, a captured cert's mtime does not change across a
    re-capture that produced different bytes. Inode DOES change on every
    mv -- also confirmed live -- so it is included, free (same stat() call),
    alongside size as a second independent signal against the coarser mtime
    resolution a Docker bind mount can have."""
    cert_dir = os.environ.get("KP2_XROAD_ADMIN_CERT_DIR")
    if not cert_dir:
        return None
    pem = pathlib.Path(cert_dir) / f"{host}.pem"
    try:
        st = pem.stat()
        return (str(pem), st.st_mtime_ns, st.st_ino, st.st_size)
    except OSError:
        return None


def _admin_ssl_context(host: str) -> ssl.SSLContext | bool:
    """Trust decision for one admin host's :4000 (module docstring: TOFU
    pinning). The pinned file is named exactly `host` because that is the
    same string every caller here already uses as the admin host --
    hurl/run-linkup.sh captures it under that name for the same reason.

    check_hostname is off even when a pinned certificate is found: its
    CN/SAN name the sidecar container's own runtime hostname (verified live
    -- a random per-container value, never `host`), so hostname matching
    would fail even against the correct, captured certificate. What this
    verifies is "the same certificate this server presented at capture
    time", not "a certificate naming this server".
    """
    cert_dir = os.environ.get("KP2_XROAD_ADMIN_CERT_DIR")
    pem = pathlib.Path(cert_dir) / f"{host}.pem" if cert_dir else None
    if pem is None or not pem.is_file():
        if host not in _WARNED_UNPINNED:
            _WARNED_UNPINNED.add(host)
            _LOG.warning(
                "xroad.py: no pinned certificate for admin host %r (KP2_XROAD_ADMIN_CERT_DIR=%r) "
                "-- falling back to verify=False for this host's :4000 admin API. Run "
                "hurl/run-linkup.sh to capture it.",
                host, cert_dir,
            )
        return False
    ctx = ssl.create_default_context(cafile=str(pem))
    ctx.check_hostname = False
    return ctx


def admin_client(host: str) -> httpx.Client:
    """The pooled, pinned client for `host`'s :4000 admin API -- reused for
    every AdminSession and reachability probe against it (app.py's
    /api/topology), for as long as the trust decision it was built with
    hasn't changed. Public: AdminSession.__init__ and app.py's reachability
    probe both need one, and there is exactly one way to build it.

    Re-checked on every call, not just built once (found in review): a
    console that started before its host's certificate was captured, or
    that outlives a redeploy, must not stay pinned to a stale decision --
    or stuck unverified -- for its whole process lifetime. The check is one
    stat() (_admin_pin_fingerprint), cheap enough to run on every call
    rather than on a timer.

    Closing the STALE client here, not merely dropping the reference, is
    what makes this safe to call from a caller that never sees the old one
    again -- but AdminSession does not hold `self._client` the way it looks
    like it might: its `_client` is a property that calls this function
    fresh on every access (see AdminSession's own comment on why), so a
    close here never orphans a long-lived session on a dead transport.

    A cert landing between this function's fingerprint read (outside the
    lock) and _admin_ssl_context()'s own re-read (inside it, below) tags a
    correctly-pinned client with a stale fingerprint -- self-correcting: the
    next call recomputes the fingerprint fresh and rebuilds once more."""
    fingerprint = _admin_pin_fingerprint(host)
    with _ADMIN_CLIENTS_LOCK:
        cached = _ADMIN_CLIENTS.get(host)
        if cached is not None and cached[1] == fingerprint:
            return cached[0]
        if cached is not None:
            cached[0].close()
        client = httpx.Client(verify=_admin_ssl_context(host), timeout=10.0)
        _ADMIN_CLIENTS[host] = (client, fingerprint)
        return client


def close_admin_clients() -> None:
    """Shutdown counterpart to admin_client() -- app.py's lifespan used to
    close one SHARED_CLIENT; now there is one per host actually contacted,
    so this closes whichever of those were ever created."""
    with _ADMIN_CLIENTS_LOCK:
        for client, _fingerprint in _ADMIN_CLIENTS.values():
            client.close()
        _ADMIN_CLIENTS.clear()


def _exchange_ssl_context() -> ssl.SSLContext:
    """Trust store for the consumer hop (docs/production-delta.md row 19):
    the public roots PLUS whatever KP2_XROAD_CA_BUNDLE names, never instead
    of them and never a bypass. Unset => stock verification, which is the
    right answer for any deployment whose Security Servers hold real
    certificates."""
    ctx = ssl.create_default_context()
    bundle = os.environ.get("KP2_XROAD_CA_BUNDLE")
    if bundle:
        ctx.load_verify_locations(cafile=bundle)
    return ctx


# Separate from the admin clients on purpose -- see this module's docstring
# for why the two never share a trust decision. Same pooling
# rationale, same thread-safety.
EXCHANGE_CLIENT = httpx.Client(verify=_exchange_ssl_context(), timeout=10.0)


class AdminSession:
    """Session-login client for one Security Server's admin API (:4000).

    The admin API authenticates by session login and XSRF token, not API
    key -- POST /login with form params, then
    send the XSRF-TOKEN cookie back as X-XSRF-TOKEN on every call. Same
    mechanics as scripts/lib-stack.sh's api_key()/api().
    """

    def __init__(self, host: str, user: str, password: str, *, client: httpx.Client | None = None):
        self.host = host
        self._user = user
        self._password = password
        # NOT bound once at construction (found in review, second pass): a
        # cached AdminSession (app.py's _SESSIONS, one per host, kept for the
        # process lifetime) would otherwise keep using the client that was
        # current the moment it first logged in -- and admin_client()'s
        # cache-invalidation (this module's own docstring on
        # _admin_pin_fingerprint) CLOSES the stale client once the pin state
        # changes, which would make every later call through a cached session
        # raise RuntimeError("client has been closed") forever, with no retry
        # path (_request only retries on 401, never on a dead transport).
        # Resolved fresh on every access instead, through the same property
        # below -- so a session picks up a rebuilt (and now-pinned) client
        # exactly like a fresh one would, and closing the old client is safe.
        # `client=` (every test in this module) still pins one client for the
        # session's whole life, same as before.
        self._explicit_client = client
        self._login()

    @property
    def _client(self) -> httpx.Client:
        return self._explicit_client or admin_client(self.host)

    def _login(self) -> None:
        """A login is a SERVER-SIDE session on that Security Server's admin
        UI, not just a cookie here -- so this is deliberately called once
        per host and then reused (app.py caches the session), rather than
        once per API hit. The admin UI's own concurrent-session behaviour
        (runbook.md's "Admin UIs": sessions in one browser log each other
        out) is why piling up logins is worth avoiding and not merely
        untidy."""
        resp = self._client.post(
            f"https://{self.host}:4000/login",
            data={"username": self._user, "password": self._password},
        )
        resp.raise_for_status()
        token = resp.cookies.get("XSRF-TOKEN")
        if not token:
            raise RuntimeError(f"AdminSession: no XSRF-TOKEN cookie from {self.host}'s /login")
        self._xsrf = token

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Retries ONCE after a fresh login on 401, which is what makes a
        long-lived cached session safe: the server can expire it out from
        under us at any point, and the alternative (log in per call) is the
        accumulation this class exists to avoid. 401 only -- 403 is how the
        admin API answers a request that authenticated fine and was refused
        anyway, and re-logging-in would neither fix nor explain that."""
        url = f"https://{self.host}:4000/api/v1{path}"
        resp = self._client.request(method, url, headers={"X-XSRF-TOKEN": self._xsrf}, **kwargs)
        if resp.status_code == 401:
            self._login()
            resp = self._client.request(method, url, headers={"X-XSRF-TOKEN": self._xsrf}, **kwargs)
        return resp

    def get(self, path: str) -> httpx.Response:
        return self._request("GET", path)

    def post(self, path: str, json_body: dict | None = None) -> httpx.Response:
        return self._request("POST", path, json=json_body)

    # -- ACL operations -- all four verified against the running stack, not
    # just the OpenAPI model.

    def read_subjects(self, client_id: str) -> list[str]:
        """Every subject granted ANY access on this client."""
        resp = self.get(f"/clients/{client_id}/service-clients")
        resp.raise_for_status()
        return [item["id"] for item in resp.json()]

    def read_acl(self, client_id: str, subject_id: str) -> list[str]:
        """Which service codes this subject holds on this client.

        A subject with zero access rights is
        not a "service client" of this client at all, so the admin API
        404s here rather than returning []  -- the asymmetry with
        read_subjects() (which naturally omits such a subject from its
        list, no error) matters when this is used to determine prior_state
        before a mutation: the fully-revoked case must read as [], not
        raise, or callers can never observe "currently has nothing granted".
        """
        resp = self.get(f"/clients/{client_id}/service-clients/{subject_id}/access-rights")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return [item["service_code"] for item in resp.json()]

    def grant(self, client_id: str, subject_id: str, service_code: str) -> None:
        resp = self.post(
            f"/clients/{client_id}/service-clients/{subject_id}/access-rights",
            json_body={"items": [{"service_code": service_code}]},
        )
        if resp.status_code == 409:
            return  # already granted -- reset must treat this as success, not failure
        resp.raise_for_status()

    def revoke(self, client_id: str, subject_id: str, service_code: str) -> None:
        resp = self.post(
            f"/clients/{client_id}/service-clients/{subject_id}/access-rights/delete",
            json_body={"items": [{"service_code": service_code}]},
        )
        if resp.status_code == 409:
            return  # already revoked (409 accessright_not_found) --
            # the target state (no grant) already holds, so this is success, same
            # reasoning as grant()'s 409 handling. Load-bearing for reset(): a
            # crash-mid-write can replay an entry whose live call already
            # succeeded, and reset must not fail on that replay.
        resp.raise_for_status()


@dataclasses.dataclass(frozen=True)
class CallResult:
    service: str
    url: str
    status_code: int | None
    elapsed_ms: float
    body: dict | str | None
    headers: dict[str, str]
    denied: bool
    fault_type: str | None
    error: str | None  # set only on a genuine transport failure, never on a denial


def exchange(
    entrypoint: str,
    calls: list[dict],
    nin: str,
    client_header: str,
    *,
    http_client: httpx.Client | None = None,
) -> list[CallResult]:
    """Issue every call in 2.6.yaml's exchange.calls against one entrypoint.

    A genuine transport failure (connection refused, timeout) is captured in
    .error and never reported as .denied -- a real denial is
    HTTP 500 with body {"type": "Server.ServerProxy.AccessDenied", ...}; a
    transport failure never reaches that far and must not be presented as a
    permission decision.

    `nin` is expected to already be validated by the caller -- app.py is the
    boundary, not this library function. A
    second check here would be a second place to keep in sync.
    """
    client = http_client or EXCHANGE_CLIENT
    results: list[CallResult] = []
    for call in calls:
        url = entrypoint.rstrip("/") + call["r1_path"].format(nin=nin)
        start = time.monotonic()
        try:
            resp = client.get(url, headers={"X-Road-Client": client_header})
        except httpx.HTTPError as exc:
            results.append(CallResult(
                service=call["service"], url=url, status_code=None,
                elapsed_ms=(time.monotonic() - start) * 1000, body=None, headers={},
                denied=False, fault_type=None, error=str(exc),
            ))
            continue
        elapsed_ms = (time.monotonic() - start) * 1000
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        denied = (
            resp.status_code == 500
            and isinstance(body, dict)
            and body.get("type") == "Server.ServerProxy.AccessDenied"
        )
        results.append(CallResult(
            service=call["service"], url=url, status_code=resp.status_code,
            elapsed_ms=elapsed_ms, body=body, headers=dict(resp.headers),
            denied=denied, fault_type=body.get("type") if isinstance(body, dict) else None,
            error=None,
        ))
    return results
