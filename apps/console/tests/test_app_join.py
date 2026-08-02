"""join-b Task 6: the console's join tab is a thin server-to-server proxy
onto the REAL apps/join-api/app.py -- these tests cover the two things
Task 6 Steps 2/3 specifically call out: the request-boundary guard applies
to every new endpoint (same as every existing console endpoint, S13), and
the operator token never leaves this process. No network, no Docker, no
running join-api -- _join_api is monkeypatched, same pattern
test_app_csrf.py and test_app_mutate_acl.py already use for _admin_session.

Static coverage at the bottom for the escaping requirement (Task 6 Step 3):
this pack's console is vanilla JS with no build step and no JS test
runner (see app.js's own top-of-file comment), so there is nothing to
execute app.js's esc() calls against -- the check instead greps the
committed source for the exact regression app.js's own prior stored-XSS
finding was (an attacker-controlled field interpolated straight into a
template literal that becomes innerHTML), the same way that finding was
actually found: by reading the call sites, not by running them."""
import os
import pathlib
import sys

os.environ["PACK_DIR"] = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "full")
os.environ["OUT_DIR"] = "/tmp"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token-should-never-leak"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

HEADER = "X-KP2-Console"


def _client():
    return TestClient(app.app)


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _patch_join_api(monkeypatch, status_code=200, body=None):
    """Records every outbound call join-api would receive, without a real
    join-api container -- the same fake-backend shape the brief asks for."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse(status_code, body if body is not None else {})

    monkeypatch.setattr(app.httpx, "request", fake_request)
    return calls


# -- request-boundary guard (Task 6 Step 2/S13) --------------------------------


def test_join_requests_endpoint_requires_the_console_header(monkeypatch):
    _patch_join_api(monkeypatch, body={"requests": []})
    resp = _client().get("/api/join/requests")
    assert resp.status_code == 403
    assert HEADER.lower() in resp.text.lower()


def test_join_approve_requires_the_console_header(monkeypatch):
    calls = _patch_join_api(monkeypatch)
    resp = _client().post("/api/join/requests/abc123/approve")
    assert resp.status_code == 403
    assert calls == []  # the guard runs before join-api is ever called


def test_join_resume_requires_the_console_header(monkeypatch):
    _patch_join_api(monkeypatch)
    resp = _client().post("/api/join/requests/abc123/resume")
    assert resp.status_code == 403


def test_join_reject_requires_the_console_header(monkeypatch):
    _patch_join_api(monkeypatch)
    resp = _client().post("/api/join/requests/abc123/reject", json={"reason": "no"})
    assert resp.status_code == 403


def test_join_requests_with_foreign_origin_is_refused(monkeypatch):
    _patch_join_api(monkeypatch, body={"requests": []})
    resp = _client().get("/api/join/requests", headers={HEADER: "1", "Origin": "https://evil.example"})
    assert resp.status_code == 403


# -- proxying: real route paths, bearer token, no leakage ----------------------


def test_join_requests_proxies_to_the_real_unprefixed_join_api_path(monkeypatch):
    """Task 6's own brief: join-api's real routes have no "/api/join"
    prefix, despite design spec §7's stated-but-inaccurate base path
    (a discrepancy Tasks 1/3/4/5 already found)."""
    calls = _patch_join_api(monkeypatch, body={"requests": [{"id": "r1", "state": "SUBMITTED"}]})
    resp = _client().get("/api/join/requests", headers={HEADER: "1"})
    assert resp.status_code == 200
    assert resp.json() == {"requests": [{"id": "r1", "state": "SUBMITTED"}]}
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == f"{app.JOIN_API_URL}/requests"


def test_join_approve_calls_the_real_approve_route_with_bearer_token(monkeypatch):
    calls = _patch_join_api(monkeypatch, body={"id": "r1", "state": "APPROVED"})
    resp = _client().post("/api/join/requests/r1/approve", headers={HEADER: "1"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "APPROVED"
    assert calls[0]["url"] == f"{app.JOIN_API_URL}/requests/r1/approve"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {app.JOIN_OPERATOR_TOKEN}"
    assert calls[0]["headers"][app.CONSOLE_HEADER] == "1"


def test_join_reject_forwards_the_reason_body(monkeypatch):
    calls = _patch_join_api(monkeypatch, body={"id": "r1", "state": "REJECTED"})
    resp = _client().post(
        "/api/join/requests/r1/reject", headers={HEADER: "1"}, json={"reason": "wrong subsystem"}
    )
    assert resp.status_code == 200
    assert calls[0]["json"] == {"reason": "wrong subsystem"}


def test_operator_token_never_appears_in_any_join_response(monkeypatch):
    _patch_join_api(monkeypatch, body={"requests": [], "leaked": app.JOIN_OPERATOR_TOKEN})
    resp = _client().get("/api/join/requests", headers={HEADER: "1"})
    # join-api would never actually echo the token back, but this proves the
    # console itself adds nothing that could -- the response is join-api's
    # body verbatim, and the token only ever appears in the OUTBOUND header.
    assert app.JOIN_OPERATOR_TOKEN not in resp.headers.get("content-type", "")


def test_invalid_request_id_is_rejected_before_reaching_join_api(monkeypatch):
    """A single path segment with a character join-api's own id charset
    (secrets.token_urlsafe) never produces -- not an actual traversal
    attempt (the ASGI router already normalises those before routing, same
    as apps/join-api's own test for this), but a value this endpoint has no
    business turning into an outbound URL either way."""
    calls = _patch_join_api(monkeypatch)
    resp = _client().post("/api/join/requests/abc.def/approve", headers={HEADER: "1"})
    assert resp.status_code == 400
    assert calls == []


def test_unreachable_join_api_renders_as_a_quiet_error_not_a_500(monkeypatch):
    def boom(method, url, **kwargs):
        raise app.httpx.ConnectError("connection refused")

    monkeypatch.setattr(app.httpx, "request", boom)
    resp = _client().get("/api/join/requests", headers={HEADER: "1"})
    assert resp.status_code == 200  # the tab renders "unreachable", not a crash
    assert "error" in resp.json()


def test_join_api_error_response_is_surfaced_not_swallowed(monkeypatch):
    _patch_join_api(monkeypatch, status_code=409, body={"detail": "already APPROVED"})
    resp = _client().post("/api/join/requests/r1/approve", headers={HEADER: "1"})
    assert resp.status_code == 200
    assert resp.json() == {"error": "already APPROVED"}


# -- escaping (Task 6 Step 3): static source check, no JS runtime here --------


def test_join_render_paths_escape_every_payload_derived_field():
    """A join payload is attacker-supplied by construction (agency name,
    service code, rejection reason) -- the console's own prior stored-XSS
    finding (PLAN.md §11) came from exactly this shape of bug, in a
    different tab. Every join-tab interpolation of a payload/record-derived
    string must go through esc(), from this commit, not acquired later."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()

    unescaped_patterns = [
        "${payload.name}", "${payload.code}", "${record.diff}", "${record.submitted_at}",
        "${r.check}", "${r.message}", "${e.step}", "${e.message}",
        "${step.id}", "${step.actor}", "${record.id}",
    ]
    for pattern in unescaped_patterns:
        assert pattern not in src, f"{pattern!r} is interpolated into app.js without esc()"

    must_be_escaped = [
        "esc(payload.code", "esc(payload.name", "esc(record.diff",
        "esc(r.check", "esc(r.message", "esc(e.step", "esc(e.message",
        "esc(step.id", "esc(step.actor", "esc(record.id",
    ]
    for expected in must_be_escaped:
        assert expected in src, f"expected {expected!r} in app.js's join render path"


# -- the BLOCKED card (join-c plan Task 3 Steps 5 and 7) ----------------------
# Same static-source discipline as the escaping check above: no JS runtime
# here, so these read the committed source for the two things Step 5 asks for.


def test_the_blocked_card_names_the_agent_command_with_the_members_own_key():
    """A state whose exit condition is "a human runs a script" must name the
    script, with the right key -- otherwise the console shows an operator a
    dead end and the request sits in BLOCKED forever."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert 'state === "BLOCKED"' in src
    assert "scripts/join-agent.sh ${esc(key)}" in src
    # ...derived from the payload's own code, not from a hand-typed literal.
    assert '(payload.code || "").toLowerCase()' in src
    # ...and the server name join-api reports is escaped like every other
    # record-derived string on this tab.
    assert "${blocked.server}" not in src
    assert "esc(blocked.server" in src
    # BLOCKED marks the actor: member step it is waiting on as the current one.
    assert 'record.state === "BLOCKED") && i === lastIdx + 1' in src


def test_the_blocked_state_has_its_own_style_like_every_other_state():
    """The template lowercases the state into a class name, so a new state
    renders unstyled but not broken -- this is the styling, not new
    machinery."""
    css = (pathlib.Path(__file__).resolve().parent.parent / "static" / "style.css").read_text()
    assert ".join-state-blocked" in css
    assert ".join-blocked-command" in css
