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


class AdminSession:
    """Session-login client for one Security Server's admin API (:4000).

    Confirmed live (2026-07-25/26): the admin API authenticates by session
    login and XSRF token, not API key -- POST /login with form params, then
    send the XSRF-TOKEN cookie back as X-XSRF-TOKEN on every call. Same
    mechanics as scripts/lib-stack.sh's api_key()/api().
    """

    def __init__(self, host: str, user: str, password: str, *, client: httpx.Client | None = None):
        self.host = host
        self._client = client or httpx.Client(verify=False, timeout=10.0)
        resp = self._client.post(
            f"https://{host}:4000/login",
            data={"username": user, "password": password},
        )
        resp.raise_for_status()
        token = resp.cookies.get("XSRF-TOKEN")
        if not token:
            raise RuntimeError(f"AdminSession: no XSRF-TOKEN cookie from {host}'s /login")
        self._xsrf = token

    def get(self, path: str) -> httpx.Response:
        return self._client.get(
            f"https://{self.host}:4000/api/v1{path}", headers={"X-XSRF-TOKEN": self._xsrf}
        )

    def post(self, path: str, json_body: dict | None = None) -> httpx.Response:
        return self._client.post(
            f"https://{self.host}:4000/api/v1{path}",
            json=json_body,
            headers={"X-XSRF-TOKEN": self._xsrf},
        )

    # -- ACL operations -- all four confirmed live against the running stack
    # (2026-07-26), not just the OpenAPI model. See docs/superpowers/plans/
    # 2026-07-26-kp2-demo-console.md Task 3 Step 3.

    def read_subjects(self, client_id: str) -> list[str]:
        """Every subject granted ANY access on this client."""
        resp = self.get(f"/clients/{client_id}/service-clients")
        resp.raise_for_status()
        return [item["id"] for item in resp.json()]

    def read_acl(self, client_id: str, subject_id: str) -> list[str]:
        """Which service codes this subject holds on this client.

        Confirmed live (2026-07-27): a subject with zero access rights is
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
            return  # already granted -- reset must treat this as success, not failure (C0)
        resp.raise_for_status()

    def revoke(self, client_id: str, subject_id: str, service_code: str) -> None:
        resp = self.post(
            f"/clients/{client_id}/service-clients/{subject_id}/access-rights/delete",
            json_body={"items": [{"service_code": service_code}]},
        )
        if resp.status_code == 409:
            return  # already revoked (confirmed live: 409 accessright_not_found) --
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
    .error and never reported as .denied -- confirmed live: a real denial is
    HTTP 500 with body {"type": "Server.ServerProxy.AccessDenied", ...}; a
    transport failure never reaches that far and must not be presented as a
    permission decision.

    `nin` is expected to already be validated by the caller -- app.py is the
    boundary (request-boundary plan S12), not this library function. A
    second check here would be a second place to keep in sync.
    """
    client = http_client or httpx.Client(verify=False, timeout=10.0)
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
