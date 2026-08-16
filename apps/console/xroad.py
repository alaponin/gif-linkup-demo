"""apps/console/xroad.py -- the only thing in this container that talks to
X-Road. Two distinct clients, because they hit two distinct surfaces:

- AdminSession: the Security Server admin REST API on :4000, session-login
  authenticated (mirrors scripts/lib-stack.sh's api_key()/api() exactly). Used for
  reading and mutating ACLs.
- exchange(): the r1 proxy interface on :8080, authenticated by the
  X-Road-Client header, not an admin session. Used for the counter/inspector
  tabs' actual once-only-exchange calls.

Both are demo-only: verify=False everywhere, because the Test CA issues
self-signed certificates (docs/production-delta.md already flags this at
the deployment layer; this container inherits the same trust decision).
"""
from __future__ import annotations

import dataclasses
import time

import httpx

# One pooled client for every call this container makes, instead of a fresh
# httpx.Client per AdminSession and per exchange() -- those were never
# closed, so a console left open for a demo accumulated one connection pool
# per poll (the page polls /api/topology and /api/acl every 30s) until the
# garbage collector happened to reap them. Reusing one pool also keeps the
# TLS handshake off every repeat call. httpx.Client is thread-safe, which
# matters here: FastAPI runs this app's sync endpoints in a threadpool.
# Per-request `timeout=` still overrides this default where a caller wants a
# shorter one (app.py's reachability probe).
SHARED_CLIENT = httpx.Client(verify=False, timeout=10.0)


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
        self._client = client or SHARED_CLIENT
        self._login()

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
    client = http_client or SHARED_CLIENT
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
