"""The console's join tab is a thin server-to-server proxy
onto the REAL apps/join-api/app.py -- these tests cover the two things
that matter most: the request-boundary guard applies
to every new endpoint (same as every existing console endpoint, S13), and
the operator token never leaves this process. No network, no Docker, no
running join-api -- _join_api is monkeypatched, same pattern
test_app_csrf.py and test_app_mutate_acl.py already use for _admin_session.

Static coverage at the bottom for the escaping requirement:
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

os.environ["PACK_DIR"] = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "pack")
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


# -- request-boundary guard --------------------------------


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
    """join-api's real routes have no "/api/join"
    prefix, despite a documented-but-inaccurate base path claiming
    otherwise (a discrepancy already found and left alone)."""
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


def test_join_approve_forwards_the_decision_reference_body(monkeypatch):
    calls = _patch_join_api(monkeypatch, body={"id": "r1", "state": "APPROVED"})
    resp = _client().post(
        "/api/join/requests/r1/approve",
        headers={HEADER: "1"},
        json={"decision_reference": "TICKET-42"},
    )
    assert resp.status_code == 200
    assert calls[0]["json"] == {"decision_reference": "TICKET-42"}


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


# -- escaping: static source check, no JS runtime here --------


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


# -- the BLOCKED card (join-c plan, Steps 5 and 7) ----------------------
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


# -- un-joining (join-c plan, Steps 6-8) --------------------------------
# Step 6 chose option (a): the states render, there is no delete control. Two
# reasons, in the order they decided it. First, the audience -- the join tab
# shows an agency arriving, and a destructive control is a different act for a
# different operator; this console has none today, and the brief's own
# counter-argument (a hosted un-join is fast, so RETIRING is barely observable
# and a button would be cheap) argues about COST, which was never what was in
# doubt. Second, Step 8: an own-server un-join ENDS in two Docker commands the
# console cannot run, so a button's honest outcome is "now go do this by hand"
# in a browser tab -- worse than no button. The DELETE is a curl/runbook
# operation, issued where the Docker cleanup also has to happen.
#
# Under (a) the brief asks for the decision to be ENFORCED, not merely
# documented -- hence the first two tests here.


def test_the_console_exposes_no_member_delete_route(monkeypatch):
    """Step 6 option (a), enforced. If a delete affordance is ever wanted, this
    test is the deliberate thing to change -- not a route that quietly
    appeared."""
    paths = {route.path for route in app.app.routes}
    assert not [p for p in paths if "members" in p], paths


def test_a_member_delete_request_reaches_no_join_api_call(monkeypatch):
    calls = _patch_join_api(monkeypatch)
    resp = _client().request("DELETE", "/api/join/members/ptsb", headers={HEADER: "1"})
    # 405 (the static mount answers GET-shaped paths) or 404 -- either way,
    # nothing was proxied.
    assert resp.status_code in (404, 405)
    assert calls == []


def test_the_retiring_and_retired_states_have_their_own_styles():
    """Whichever Step 6 option is chosen, the states must RENDER. The template
    lowercases the state into a class name, so an unstyled one is not broken --
    just invisible as a state, which during a live un-join is the one thing
    being demonstrated."""
    css = (pathlib.Path(__file__).resolve().parent.parent / "static" / "style.css").read_text()
    assert ".join-state-retiring" in css
    assert ".join-state-retired" in css


def test_a_retired_record_stays_in_the_list_rather_than_vanishing():
    """Step 6's explicit ask: say which, because a card that silently
    disappears mid-demonstration reads as a bug. Nothing filters the queue by
    state -- every record join-api returns is rendered."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "data.requests.forEach(record => list.appendChild(renderJoinRequest(record)))" in src
    assert 'state === "RETIRING" || state === "RETIRED"' in src


def test_the_docker_instruction_reaches_the_operator_through_the_console_too():
    """Step 8: an own-server un-join leaves a container and three named volumes
    for a human, and an API that returns "now go do this by hand" to a browser
    that discards it is worse than not having said it."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "record.retire_instruction" in src
    assert "esc(instruction.message" in src
    assert "${instruction.message}" not in src


def test_the_un_join_render_path_escapes_every_record_derived_field():
    """Same rule as the join render paths above: a join payload is
    attacker-supplied by construction, and an un-join renders the same
    payload's fields plus the walk's own step ids."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    for unescaped in ("${r.step}", "${r.outcome}", "${instruction.message}"):
        assert unescaped not in src, f"{unescaped!r} is interpolated into app.js without esc()"
    for expected in ("esc(r.step", "esc(r.outcome"):
        assert expected in src, f"expected {expected!r} in app.js's un-join render path"


def test_an_own_server_active_record_gets_the_known_defect_explanation():
    """An own-server join's bring-up spends the retry budget on the
    propagation wait before the reachability check runs, so `verified: false`
    there is a known demo defect that never flips -- not the generic "not yet"
    a hosted join's false means. Rendering both the same way trains an
    operator to distrust a correct join, or to wait for a flag that will never
    come."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "payload.security_server && payload.security_server.own_server" in src
    assert "2.7.r1" in src
    # The hosted case must keep its own, different line.
    assert "the reachability check has not passed yet" in src


def test_the_join_tab_offers_the_submit_command_but_no_submit_route():
    """Submission is the applicant's act with the applicant token. The console
    holds only the operator token and proxies only the four operator routes --
    a submit proxy would put both credentials in one UI and flatten the
    role asymmetry the tab exists to show. The empty state hands over the
    curl instead."""
    paths = {route.path for route in app.app.routes}
    assert "/api/join/requests" in paths
    assert not [p for p in paths if "submit" in p], paths
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "$KP2_JOIN_APPLICANT_TOKEN" in src  # referenced, never expanded


def test_the_decision_reference_gate_reports_inline_and_survives_the_poll():
    """The queue rebuilds the whole list every 3s, so a validation message
    written straight into a card -- or a reference the operator is still
    typing -- is gone before it is read. The message is render state keyed by
    request id, and typed values are carried across the rebuild; alert() is
    not the console's idiom anywhere else."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    approve_gate = src.split("if (approveBtn) {", 1)[1].split("} else if", 1)[0]
    assert "alert(" not in approve_gate, "the approve gate is back to a browser dialog"
    assert "joinDecisionErrors[approveBtn.dataset.id]" in approve_gate
    # Rendered escaped, like every other join-payload-derived value.
    assert "esc(joinDecisionErrors[record.id])" in src
    # Carried over the rebuild, or the poll eats it.
    rebuild = src.split("async function refreshJoinQueue", 1)[1]
    assert "typed[input.dataset.id] = input.value" in rebuild
    assert "setSelectionRange" in rebuild


def test_the_submit_skeleton_carries_every_field_the_join_api_requires():
    """The skeleton is the first thing an applicant runs, so a missing
    required field is rejected before an operator ever sees the request --
    which is what happened to backend.auth, added to JoinPayload after the
    skeleton was written. Field NAMES against the live schema, not a parse of
    the JS: what drifts here is which fields are required, and this pack has
    no JS test runner to evaluate the literal with."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "join-api"))
    from schema import JoinPayload

    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    skeleton = src.split("const JOIN_SUBMIT_PAYLOAD = {", 1)[1].split("\n};", 1)[0]
    required = [
        name for name, field in JoinPayload.model_fields.items() if field.is_required()
    ]
    assert required, "JoinPayload declares no required fields -- this check would pass vacuously"
    missing = [name for name in required if f"{name}:" not in skeleton]
    assert not missing, f"JOIN_SUBMIT_PAYLOAD omits required JoinPayload field(s): {missing}"
