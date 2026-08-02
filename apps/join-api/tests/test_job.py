"""Tests for apps/join-api/job.py (join-b Task 4).

No containers and no network: the executor takes its "run this rendered file,
give me the report" function as an argument (spec S12's --fast row: the step
engine driven against recorded fixtures), and these tests pass a fake that
replays tests/fixtures/xroad/*.json -- real Hurl report elements sliced out of
a real lite-profile deploy (out/hurl-report/report.json), see each fixture's
own _source field. Everything else is the real code path: the real registry
(hurl/steps.py), the real templates rendered by the real hurl/generate.py, the
real pack's manifest/configs/topology.

Rendering and planning read REAL_PACK_DIR directly. That is safe here in a way
it is not for writer.py: job.py never writes to the pack.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

# Same env-before-import convention the other join-api test modules use;
# setdefault so that whichever of them pytest collects first wins and the
# credential test below still asserts against whatever the process really has.
os.environ.setdefault("XROAD_ADMIN_USER", "xrd")
os.environ.setdefault("XROAD_ADMIN_PASSWORD", "secret")
os.environ.setdefault("XROAD_TOKEN_PIN", "1234")
os.environ.setdefault("KP2_JOIN_APPLICANT_TOKEN", "test-applicant-token")
os.environ.setdefault("KP2_JOIN_OPERATOR_TOKEN", "test-operator-token")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import job  # noqa: E402
import writer  # noqa: E402
from schema import JoinPayload  # noqa: E402

# apps/join-api/tests/test_job.py -> tests -> join-api -> apps -> pack root
REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "xroad"

SECRETS = {
    "ss_admin_user": "xrd",
    "ss_admin_password": "test-admin-password-9f3a",
    "token_pin": "test-token-pin-6c21",
}

# The full sequence a hosted join with one service and one ACL grant runs.
EXPECTED_IDS = [
    "cs.init",
    "cs.members_member",
    "cs.anchor",
    "ss.bringup_init",
    "ss.ca_name_capture",
    "ss.client_add",
    "ss.sign_key_csr",
    "ss.client_register",
    "service.publish:awards-api",
    "service.acl:awards-api:PROGRESSA/GOV/PNEA/EXAMS",
    "join.r1_verify",
]


def _payload(**overrides) -> JoinPayload:
    base = dict(
        code="PTSB",
        name="Progressa Tertiary Scholarship Board",
        subsystem="SCHOLARSHIP",
        subsystem_description="Scholarship award management",
        security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
        services=[
            {
                "code": "awards-api",
                "spec_url": "http://app-ptsb:8000/spec.yaml",
                "access": ["PROGRESSA/GOV/PNEA/EXAMS"],
            }
        ],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id", "status"]},
        backend={"auth": "network_allowlist"},
    )
    base.update(overrides)
    return JoinPayload(**base)


def _record(**overrides) -> dict:
    record = {
        "id": "test-request",
        "state": "APPROVED",
        "payload": _payload().model_dump(mode="json"),
    }
    record.update(overrides)
    return record


class FakeHurl:
    """Replays a recorded report per step id, and records what it was asked to
    run. `overrides` maps a step id to either a report element or a list of
    elements returned one per attempt (the tail repeating)."""

    def __init__(self, overrides: dict | None = None):
        self.overrides = overrides or {}
        self.calls: list[str] = []
        self.variables: list[dict] = []

    def __call__(self, label: str, body: str, variables: dict) -> dict:
        self.calls.append(label)
        self.variables.append(dict(variables))
        assert body.strip(), f"{label} rendered an empty Hurl file"
        override = self.overrides.get(label)
        if isinstance(override, list):
            return override[min(self.calls.count(label), len(override)) - 1]
        if override is not None:
            return override
        fixture = FIXTURES / f"{label.split(':')[0]}.json"
        if not fixture.exists() and label.endswith("#probe"):
            # A probe with no recorded answer answers "no" -- which is what a
            # probe that cannot run does in production too.
            return {"success": False, "entries": []}
        return json.loads(fixture.read_text())


def _fake_r1(ok: bool = True, detail: str = "http://ss-pnea:8080/...: HTTP 200"):
    def call(url: str, client_header: str) -> tuple[bool, str]:
        return ok, detail

    return call


def _run(record: dict, hurl: FakeHurl, *, r1=None, saves: list | None = None, server_up=None) -> dict:
    def save(rec: dict) -> None:
        if saves is not None:
            saves.append(json.loads(json.dumps(rec)))

    return job.run(
        record,
        REAL_PACK_DIR,
        secrets=SECRETS,
        save=save,
        run_hurl=hurl,
        r1_call=r1 or _fake_r1(),
        # A hosted join has no actor: member step, so this is never called
        # there; the own-server tests at the bottom of this file pass their
        # own. Defaulting it to "up" rather than leaving job.py's real HTTP
        # probe in place keeps every test in this module network-free.
        server_up=server_up or (lambda dns: True),
        retry_interval=0,
        blocked_poll_interval=0,
    )


# -- the sequence (Task 4 Step 1) ---------------------------------------------


def test_hosted_join_runs_the_documented_sequence_in_order():
    steps = job.build_sequence(REAL_PACK_DIR, _payload())
    assert [s.id for s in steps] == EXPECTED_IDS


def test_every_step_of_a_hosted_join_is_the_operators():
    """hurl/steps.py defaults ss.client_add/ss.sign_key_csr/service.publish to
    actor="member" -- that default is for a member bringing up its own server.
    Under hosted_on there is no member-side infrastructure at all (join-a plan
    Task 3 Step 4)."""
    assert {s.actor for s in job.build_sequence(REAL_PACK_DIR, _payload())} == {"operator"}


def test_sign_key_names_the_hosting_servers_code_not_the_joining_members():
    """build_hosted_client()'s docstring: the cert genuinely lives on the
    host's token, and naming a server that was never brought up would be a lie
    in the cert."""
    step = next(s for s in job.build_sequence(REAL_PACK_DIR, _payload()) if s.id == "ss.sign_key_csr")
    assert step.tokens["SS_CODE"] == "SS-PLR"
    assert step.tokens["MEMBER_CODE"] == "PTSB"
    assert step.tokens["SESS_P"] == "ss_plr"
    assert step.tokens["CAP_P"] == "ss_ptsb"


def test_captures_land_in_the_joining_members_namespace():
    steps = {s.id: s for s in job.build_sequence(REAL_PACK_DIR, _payload())}
    assert steps["ss.client_add"].provides == ("ss_ptsb_client_id",)
    assert steps["ss.bringup_init"].provides == ("ss_plr_xsrf_token",)  # the HOST's session
    assert "ss_ptsb_awards_api_description_id" in steps["service.publish:awards-api"].provides


def test_every_steps_requires_is_satisfied_by_an_earlier_provides_or_a_constant():
    """The registry's requires/provides contract, checked over this job's
    actual ordering rather than over cold deploy's."""
    payload = _payload()
    available = set(job.build_constants(REAL_PACK_DIR, payload, SECRETS))
    for step in job.build_sequence(REAL_PACK_DIR, payload):
        missing = [name for name in step.requires if name not in available]
        assert not missing, f"{step.id} requires {missing}, which nothing before it provides"
        available.update(step.provides)


def test_each_service_is_published_against_its_own_spec_url():
    """Nothing caps services[], and one shared <member>_spec_url variable
    would publish both services against whichever URL won the name (found in
    review, 2026-08-02)."""
    payload = _payload(
        services=[
            {"code": "awards-api", "spec_url": "http://app-ptsb:8000/awards.yaml", "access": []},
            {"code": "grants-api", "spec_url": "http://app-ptsb:8000/grants.yaml", "access": []},
        ],
        semantic=None,
    )
    constants = job.build_constants(REAL_PACK_DIR, payload, SECRETS)
    steps = {s.id: s for s in job.build_sequence(REAL_PACK_DIR, payload)}
    urls = {
        step_id: constants[step.tokens["SPECVAR"]]
        for step_id, step in steps.items()
        if step_id.startswith("service.publish")
    }
    assert urls == {
        "service.publish:awards-api": "http://app-ptsb:8000/awards.yaml",
        "service.publish:grants-api": "http://app-ptsb:8000/grants.yaml",
    }


def test_consume_only_join_has_no_reachability_step():
    """Nothing published, nothing to be reachable: spec S4 says a consume-only
    member's ACTIVE means registered and able to reach the global
    configuration, not callable."""
    steps = job.build_sequence(REAL_PACK_DIR, _payload(services=[], semantic=None))
    assert "join.r1_verify" not in [s.id for s in steps]


def test_r1_target_is_the_consumers_own_security_server():
    target = job._r1_target(REAL_PACK_DIR, _payload())
    assert target["client_header"] == "PROGRESSA/GOV/PNEA/EXAMS"
    assert target["url"].startswith("http://ss-pnea:8080/r1/PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api")


def test_r1_target_raises_loud_not_silent_when_topology_has_drifted_from_manifest(tmp_path):
    """Review finding (2026-08-02): an ACL subject that check 7 (ACL sanity,
    validate.py) already proved exists in manifest.yaml but is missing from
    hurl/topology.json at job-run time -- the two files disagreeing is
    exactly what job.py's own module docstring (S12) calls "registry-perfect
    but dead": the case join.r1_verify exists to catch. The previous
    behaviour was to return None and silently drop the step, reaching ACTIVE
    with `verified` never set. This should never happen if check 7 did its
    job -- reproduced here by copying the real pack (writer._copy_pack, the
    same fixture pattern test_writer.py uses) and deleting the one topology
    entry the default payload's access[] names, which is exactly the kind of
    manifest/topology divergence check 7 cannot see (it only reads
    manifest.yaml, never topology.json)."""
    writer._copy_pack(REAL_PACK_DIR, tmp_path)
    topology_path = tmp_path / "hurl" / "topology.json"
    topology = json.loads(topology_path.read_text())
    topology["subsystems"] = [s for s in topology["subsystems"] if s["id"] != "PROGRESSA:GOV:PNEA:EXAMS"]
    topology_path.write_text(json.dumps(topology))

    with pytest.raises(job.StepFailure) as exc_info:
        job._r1_target(tmp_path, _payload())
    assert "PROGRESSA:GOV:PNEA:EXAMS" in exc_info.value.message
    assert "topology.json" in exc_info.value.message


# -- executing (Task 4 Step 2) -------------------------------------------------


def test_a_hosted_join_runs_to_active_and_verified():
    hurl = FakeHurl()
    record = _run(_record(), hurl)
    assert record["state"] == "ACTIVE"
    assert record["verified"] is True
    assert record["last_completed_step"] == "join.r1_verify"
    assert hurl.calls == EXPECTED_IDS[:-1]


def test_captures_are_parsed_out_of_the_report_and_threaded_into_later_steps():
    """Spec open question 3, resolved: captures do appear at step granularity
    (entries[].captures[]), which is what makes per-step execution possible."""
    hurl = FakeHurl()
    record = _run(_record(), hurl)
    assert record["context"]["ss_ptsb_client_id"] == "PROGRESSA:GOV:PTSB:SCHOLARSHIP"
    assert record["context"]["ca_name"]
    # ss.client_register reads the client id captured by ss.client_add.
    at_register = hurl.variables[hurl.calls.index("ss.client_register")]
    assert at_register["ss_ptsb_client_id"] == "PROGRESSA:GOV:PTSB:SCHOLARSHIP"


def test_session_tokens_are_never_persisted_but_are_still_injected():
    hurl = FakeHurl()
    record = _run(_record(), hurl)
    assert not [k for k in record["context"] if k.endswith("_xsrf_token")]
    at_client_add = hurl.variables[hurl.calls.index("ss.client_add")]
    assert at_client_add["ss_plr_xsrf_token"]


def test_409_on_a_repeat_counts_as_success():
    """The idempotence default (spec S5.3): the templates assert HTTP 201, so
    a step whose effect already exists fails its assert with a 409 on the
    wire -- proven live for service.acl (PLAN.md S11)."""
    conflict = {
        "success": False,
        "entries": [{"captures": [], "calls": [{"response": {"status": 409}}]}],
    }
    record = _run(_record(), FakeHurl({"service.acl:awards-api:PROGRESSA/GOV/PNEA/EXAMS": conflict}))
    assert record["state"] == "ACTIVE"


# -- the retry budget (Task 4 Step 4) -----------------------------------------

_FAILED = {"success": False, "entries": [], "_stderr": "HTTP 500 from the Security Server"}


def test_a_step_that_exhausts_the_budget_fails_with_its_id_and_the_last_response():
    hurl = FakeHurl({"ss.client_add": _FAILED})
    record = _run(_record(), hurl)
    assert record["state"] == "FAILED"
    assert record["error"]["step"] == "ss.client_add"
    assert "HTTP 500 from the Security Server" in record["error"]["message"]
    assert record["last_completed_step"] == "ss.ca_name_capture"


def test_the_retry_budget_is_one_for_the_run_not_one_per_step():
    """Spec S5.5. Three attempts burnt early leave the later step nine, not a
    fresh twelve."""
    flaky = [_FAILED, _FAILED, _FAILED, json.loads((FIXTURES / "cs.init.json").read_text())]
    hurl = FakeHurl({"cs.init": flaky, "ss.client_add": _FAILED})
    record = _run(_record(), hurl)
    assert record["state"] == "FAILED"
    assert hurl.calls.count("cs.init") == 4
    assert hurl.calls.count("ss.client_add") == job.RETRY_BUDGET - 3 + 1


def test_ocsp_staleness_is_named_rather_than_surfaced_as_a_tls_error():
    """The single most likely way a live demo of this module breaks (spec
    S5.5, PLAN.md S8): a federation idle overnight must not fail with what
    reads as a certificate fault."""
    stale = {
        "success": False,
        "entries": [],
        "_bodies": '{"type":"Server.ClientProxy.SslAuthenticationFailed","message":"..."}',
    }
    record = _run(_record(), FakeHurl({"ss.client_add": stale}))
    assert record["state"] == "FAILED"
    message = record["error"]["message"]
    assert "OCSP" in message and "idle" in message
    assert "redeploy" in message


def test_a_failed_reachability_call_is_active_unverified_not_failed():
    """Spec S4: a member that registered and published but whose reachability
    call has not passed is ACTIVE with verified: false -- one fact about the
    member, not a place in the lifecycle."""
    record = _run(_record(), FakeHurl(), r1=_fake_r1(False, "connection refused"))
    assert record["state"] == "ACTIVE"
    assert record["verified"] is False


# -- resume (Task 4 Steps 3 and 6) --------------------------------------------


def test_a_job_killed_mid_run_resumes_to_completion_without_rerunning_completed_steps():
    """Task 4 Step 6's first headline test. The kill is simulated the way a
    real one lands: a record on disk whose last_completed_step names a step in
    the middle of the sequence."""
    first = FakeHurl({"ss.sign_key_csr": _FAILED})
    record = _run(_record(), first)
    assert record["state"] == "FAILED"
    assert record["last_completed_step"] == "ss.client_add"

    resumed = FakeHurl()
    record = _run(record, resumed)
    assert record["state"] == "ACTIVE"
    assert record["last_completed_step"] == "join.r1_verify"
    # Neither mutating step before the failure runs a second time...
    assert "cs.members_member" not in resumed.calls
    assert "ss.client_add" not in resumed.calls
    # ...but the two session steps do, because their captures are credentials
    # and were never persisted. ss.sign_key_csr is probed first (it is class
    # (c): a repeat would silently make a second key), and re-run because no
    # recorded probe answer says otherwise.
    assert resumed.calls[:4] == ["cs.init", "ss.bringup_init", "ss.sign_key_csr#probe", "ss.sign_key_csr"]


def test_last_completed_step_never_regresses_while_a_resume_re_runs_session_steps():
    """The marker is persisted after every step, so it is not enough for the
    FINAL value to be right: a session step re-run on resume must not move it
    backwards, or a second kill in that window would make the next resume
    re-run steps this one skipped (found in review, 2026-08-02)."""
    record = _run(_record(), FakeHurl({"ss.sign_key_csr": _FAILED}))
    assert record["last_completed_step"] == "ss.client_add"

    saves: list[dict] = []
    _run(record, FakeHurl(), saves=saves)
    markers = [s.get("last_completed_step") for s in saves]
    order = EXPECTED_IDS.index
    assert all(
        order(later) >= order(earlier)
        for earlier, later in zip(markers, markers[1:])
    ), markers
    assert "cs.init" not in markers  # the run started past it


def test_resume_reinjects_the_captures_the_first_run_persisted():
    record = _run(_record(), FakeHurl({"ss.sign_key_csr": _FAILED}))
    resumed = FakeHurl()
    _run(record, resumed)
    at_sign_key = resumed.variables[resumed.calls.index("ss.sign_key_csr")]
    assert at_sign_key["ca_name"]  # captured by ss.ca_name_capture on the first run
    assert at_sign_key["ss_ptsb_client_id"] == "PROGRESSA:GOV:PTSB:SCHOLARSHIP"


def test_resume_probes_an_ambiguous_step_and_skips_it_when_it_already_happened():
    """Probes only on the steps join-a plan Task 5 classified as ambiguous,
    and only on resume (spec S5.3: resume does not need probes for the steps
    the job context already accounts for)."""
    record = _run(_record(), FakeHurl({"ss.client_register": _FAILED}))
    assert record["last_completed_step"] == "ss.sign_key_csr"

    probe_says_done = json.loads((FIXTURES / "ss.client_register.probe.json").read_text())
    resumed = FakeHurl({"ss.client_register#probe": probe_says_done})
    record = _run(record, resumed)
    assert record["state"] == "ACTIVE"
    assert "ss.client_register#probe" in resumed.calls
    assert "ss.client_register" not in resumed.calls


def test_resume_refuses_to_cross_a_step_flagged_unsafe_to_repeat(monkeypatch):
    """Class (d) of the join-a Task 5 audit is empty today and
    tests/test_steps.py keeps it that way -- this asserts the runner would
    refuse rather than silently re-run if that ever changed."""
    record = _run(_record(), FakeHurl({"ss.client_register": _FAILED}))
    assert record["last_completed_step"] == "ss.sign_key_csr"

    unsafe = [
        dataclasses.replace(s, unsafe_to_repeat=True) if s.id == "ss.client_register" else s
        for s in job.build_sequence(REAL_PACK_DIR, _payload())
    ]
    monkeypatch.setattr(job, "build_sequence", lambda *a, **k: unsafe)
    resumed = FakeHurl()
    record = _run(record, resumed)
    assert record["state"] == "FAILED"
    assert "unsafe_to_repeat" in record["error"]["message"]
    assert "ss.client_register" not in resumed.calls


def test_resume_refuses_a_last_completed_step_that_is_not_in_this_sequence():
    with pytest.raises(job.StepFailure):
        _run(_record(last_completed_step="ss.mgmt_register"), FakeHurl())


# -- credentials (Task 4 Step 6) ----------------------------------------------


def test_the_serialised_job_context_of_a_completed_job_carries_no_credential(tmp_path):
    """Task 4 Step 6's second headline test, and spec S5.4's explicit ask:
    asserted over the real serialised file against the real values in the
    environment, not by reading the code. The values below are the ones
    app.py hands job.run() (XROAD_ADMIN_PASSWORD, XROAD_TOKEN_PIN) plus both
    join tokens."""
    path = tmp_path / "test-request.json"
    secrets = {
        "ss_admin_user": os.environ.get("XROAD_ADMIN_USER", "xrd"),
        "ss_admin_password": os.environ["XROAD_ADMIN_PASSWORD"],
        "token_pin": os.environ["XROAD_TOKEN_PIN"],
    }
    record = job.run(
        _record(),
        REAL_PACK_DIR,
        secrets=secrets,
        save=lambda rec: path.write_text(json.dumps(rec, indent=2)),
        run_hurl=FakeHurl(),
        r1_call=_fake_r1(),
        retry_interval=0,
    )
    assert record["state"] == "ACTIVE"
    serialised = path.read_text()
    for name in (
        "XROAD_ADMIN_PASSWORD",
        "XROAD_TOKEN_PIN",
        "KP2_JOIN_APPLICANT_TOKEN",
        "KP2_JOIN_OPERATOR_TOKEN",
    ):
        value = os.environ[name]
        assert value not in serialised, f"{name}'s value reached out/join/<id>.json"


def test_scrub_redacts_credentials_but_leaves_the_non_secret_admin_username():
    """Review finding (2026-08-02): ss_admin_user ("xrd") is a short, publicly
    documented test/dev username, not a secret -- redacting it bought
    nothing and, being short, risked stripping legitimate diagnostic text
    that happened to contain "xrd" as a substring."""
    text = "user xrd failed with pin 1234 and password hunter2"
    scrubbed = job.scrub(text, {"ss_admin_user": "xrd", "token_pin": "1234", "ss_admin_password": "hunter2"})
    assert scrubbed == "user xrd failed with pin *** and password ***"


def test_a_failure_message_is_scrubbed_of_every_credential(tmp_path):
    """Hurl quotes the template source, not the expanded value, so this should
    never have anything to do -- which is exactly why it is asserted rather
    than trusted."""
    leaky = {"success": False, "entries": [], "_stderr": f"boom {SECRETS['token_pin']}"}
    path = tmp_path / "leaky.json"
    record = job.run(
        _record(),
        REAL_PACK_DIR,
        secrets=SECRETS,
        save=lambda rec: path.write_text(json.dumps(rec, indent=2)),
        run_hurl=FakeHurl({"cs.init": leaky}),
        r1_call=_fake_r1(),
        retry_interval=0,
    )
    assert record["state"] == "FAILED"
    assert SECRETS["token_pin"] not in path.read_text()


# -- the shared cookie jar (Task 6 review finding, 2026-08-02) ----------------
# The live proof's own second real bug: job.py runs one Hurl PROCESS per
# step, so nothing carried cs.init's JSESSIONID cookie to the next step's
# authenticated call -- confirmed live with plain curl (a bare X-XSRF-TOKEN
# header without the matching session cookie is a 401, not a 403). Fixed
# with a shared --cookie/--cookie-jar file for the whole run. Shipped with
# no coverage of its own: every other test in this file injects a FakeHurl,
# which never touches _default_run_hurl or run()'s cookie-jar gating at
# all. These tests fake subprocess.run only, so the real code paths run.


def test_default_run_hurl_adds_cookie_flags_when_a_jar_is_given(monkeypatch, tmp_path):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(job.subprocess, "run", fake_run)
    jar = tmp_path / "jar.txt"

    job._default_run_hurl("step", "GET http://x\n\nHTTP 200\n", {}, cookie_jar=jar)

    args = captured["args"]
    assert args[args.index("--cookie") + 1] == str(jar)
    assert args[args.index("--cookie-jar") + 1] == str(jar)


def test_default_run_hurl_omits_cookie_flags_when_no_jar_is_given(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(job.subprocess, "run", fake_run)

    job._default_run_hurl("step", "GET http://x\n\nHTTP 200\n", {})

    assert "--cookie" not in captured["args"]
    assert "--cookie-jar" not in captured["args"]


def test_run_wires_a_shared_cookie_jar_only_for_the_real_default_run_hurl(monkeypatch):
    """run()'s own gating (`if run_hurl is _default_run_hurl`): the wiring
    must apply when the caller leaves run_hurl at its default -- the shape
    every real caller (apps/join-api/app.py's _run_job) actually uses --
    and every invocation within one run must share the SAME jar file, which
    is the entire point of the fix (one session per host, not one per
    process). run_hurl is deliberately NOT overridden here, unlike every
    other test in this file: that is what proves the real default is what
    gets exercised, not a second copy of the assertion."""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(job.subprocess, "run", fake_run)

    record = job.run(
        _record(),
        REAL_PACK_DIR,
        secrets=SECRETS,
        save=lambda rec: None,
        retry_interval=0,
        # run_hurl left at its default (_default_run_hurl) on purpose.
    )

    # The fake subprocess writes no --report-json output, so every attempt
    # "fails" and the run exhausts its retry budget -- expected; the point
    # here is what argv the real _default_run_hurl constructed, not the
    # outcome (test_job.py's other tests already cover outcomes, via
    # FakeHurl, which never reaches this code at all).
    assert record["state"] == "FAILED"
    assert calls, "the real _default_run_hurl was never invoked"

    first = calls[0]
    assert "--cookie" in first and "--cookie-jar" in first
    jar_path = pathlib.Path(first[first.index("--cookie") + 1])

    for args in calls:
        assert args[args.index("--cookie") + 1] == str(jar_path), "every step must share one jar"
        assert args[args.index("--cookie-jar") + 1] == str(jar_path)

    # run()'s finally block cleans up its temp jar dir -- never left behind.
    assert not jar_path.parent.exists()


def test_run_does_not_wire_a_cookie_jar_for_an_injected_run_hurl(monkeypatch):
    """The inverse of the test above: every OTHER test in this file passes
    its own run_hurl (FakeHurl) and must keep working exactly as before --
    no cookie_jar kwarg reaches a fake that never asked for one. Guards
    against the gating check (`run_hurl is _default_run_hurl`) becoming
    something looser that would break every fixture-driven test."""
    fake = FakeHurl()
    record = job.run(
        _record(),
        REAL_PACK_DIR,
        secrets=SECRETS,
        save=lambda rec: None,
        run_hurl=fake,
        r1_call=_fake_r1(),
        retry_interval=0,
    )
    assert record["state"] == "ACTIVE"
    assert fake.calls  # FakeHurl.__call__'s 3-arg signature was never asked for a 4th


# -- the bundled Hurl binary ---------------------------------------------------


@pytest.mark.skipif(
    not pathlib.Path(job.HURL_BIN).exists(),
    reason=f"{job.HURL_BIN} is bundled into the join-api image, not onto the host",
)
def test_the_bundled_hurl_binary_actually_runs_and_writes_a_parseable_report():
    """Proves the Dockerfile's multi-stage copy produced a working binary and
    that _default_run_hurl can drive it THROUGH ITS OWN subprocess call -- the
    env= it passes carries no PATH, so this is what catches a binary that a
    shell with a normal PATH would find and this code would not (it did:
    review, 2026-08-02). No live server: the request is to a closed port, so
    the run fails and the assertion is on the report, which Hurl still writes.

    Skipped on a dev host, where nothing bundles Hurl. RUN IT IN THE IMAGE
    when either the Dockerfile's hurl stanza or _default_run_hurl changes:
        docker build -t kp2-join-api apps/join-api
        docker run --rm -v "$PWD/../../..":/repo kp2-join-api sh -c \\
          'pip install -q pytest && cd /repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack \\
           && python -m pytest apps/join-api/tests/test_job.py -q'
    """
    element = job._default_run_hurl(
        "selftest", "GET http://127.0.0.1:1/{{who}}\n\nHTTP 200\n", {"who": "x"}
    )
    assert element["success"] is False
    assert element["entries"][0]["curl_cmd"].endswith("'http://127.0.0.1:1/x'")


def test_hurl_never_inherits_this_processes_environment():
    """HURL_ENV is the child's entire environment: no credential this process
    holds can reach the Hurl child other than through the --variable values
    the executor chooses (same rule as writer._run_generate's "no env=")."""
    assert set(job.HURL_ENV) == {"LD_LIBRARY_PATH"}


def test_the_dockerfile_bundles_the_same_hurl_image_the_compose_overlay_pins():
    """One pin, two consumers: if hurl/compose.hurl.yml is re-pinned, this
    fails until the Dockerfile follows."""
    pinned = [
        line.split("image:")[1].strip()
        for line in (REAL_PACK_DIR / "hurl" / "compose.hurl.yml").read_text().splitlines()
        if "image:" in line and "ghcr.io/orange-opensource/hurl" in line
    ]
    dockerfile = (REAL_PACK_DIR / "apps" / "join-api" / "Dockerfile").read_text()
    assert pinned and f"FROM {pinned[0]} AS hurl" in dockerfile
    # A `docker build` + `hurl --version` proof of the multi-stage copy is
    # deliberately NOT a test here: --fast is a ~16s no-container tier and one
    # image build would double it. It was run by hand when the Dockerfile was
    # written (task-4 report) and is covered continuously by --full, which
    # builds this image anyway.


# -- own-server joins (join-c plan Task 3) ------------------------------------
# The other half of spec S6: the joining member brings up its OWN Security
# Server, so the sequence is cold deploy's build_ss_file() rather than
# build_hosted_client()'s, the registry's per-step `actor` is read as declared
# instead of overridden to "operator", and BLOCKED becomes reachable.

OWN_SERVER_IDS = [
    "cs.init",
    "cs.members_member",
    "cs.anchor",
    "ss.bringup_init",
    "ss.ca_name_capture",
    "ss.auth_key_csr",
    "ss.sign_key_csr",
    "ss.bringup_register",
    "ss.activate",
    "ss.tsa_capture",
    "ss.tsa_post",
    "ss.client_add",
    "ss.client_register",
    "service.publish:awards-api",
    "service.acl:awards-api:PROGRESSA/GOV/PNEA/EXAMS",
    "join.r1_verify",
]


def _own_payload(**overrides) -> JoinPayload:
    return _payload(
        security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "own_server": True},
        **overrides,
    )


class OwnServerHurl(FakeHurl):
    """FakeHurl with a SYNTHESISED success element per step instead of a
    recorded one, built from that step's DECLARED `provides` in
    hurl/steps.py.

    The recorded fixtures cannot be replayed here even for the steps a hosted
    join shares: their capture NAMES carry the hosted run's prefix
    (`ss_plr_xsrf_token`), and an own-server join captures under its own
    (`ss_ptsb_xsrf_token`) -- replaying one would leave every later step
    missing the variable it requires. And five of these steps
    (ss.auth_key_csr, ss.bringup_register, ss.activate, ss.tsa_capture,
    ss.tsa_post) have no recorded element at all, because no hosted join ever
    ran them.

    Writing five new files under fixtures/xroad/ was the alternative and was
    rejected: every file there carries a `_source` naming the real deploy it
    was sliced out of, and inventing some would make fabricated documents
    indistinguishable from real ones. What these tests exercise is the step
    ENGINE -- ordering, actor, requires/provides threading, BLOCKED -- and a
    success element carrying each step's declared captures is the right
    stand-in for that: it comes from the registry, not from this file's
    imagination. Task 5 owns the live proof that the real responses match."""

    def __call__(self, label: str, body: str, variables: dict) -> dict:
        if label in self.overrides:
            return super().__call__(label, body, variables)
        self.calls.append(label)
        self.variables.append(dict(variables))
        assert body.strip(), f"{label} rendered an empty Hurl file"
        if label.endswith("#probe"):
            return {"success": False, "entries": []}  # "no" -- re-run the step
        step = next(s for s in job.build_sequence(REAL_PACK_DIR, _own_payload()) if s.id == label)
        return {
            "success": True,
            "entries": [{"captures": [{"name": name, "value": f"{name}-value"} for name in step.provides]}],
        }


def test_an_own_server_join_runs_cold_deploys_own_bring_up_sequence():
    """hurl/generate.py's build_ss_file() order, verbatim, wrapped in the CS
    prologue and the service epilogue -- not build_hosted_client()'s."""
    steps = job.build_sequence(REAL_PACK_DIR, _own_payload())
    assert [s.id for s in steps] == OWN_SERVER_IDS


def test_an_own_server_join_has_real_member_steps_and_real_operator_steps():
    """The point of the whole branch: BLOCKED is only meaningful if steps the
    member's own infrastructure runs actually exist in the sequence. These
    actors come from hurl/steps.py's REGISTRY, not from job.py."""
    actors = {s.id: s.actor for s in job.build_sequence(REAL_PACK_DIR, _own_payload())}
    assert actors["ss.bringup_init"] == "member"
    assert actors["ss.auth_key_csr"] == "member"
    assert actors["ss.sign_key_csr"] == "member"
    assert actors["ss.activate"] == "member"
    assert actors["ss.client_add"] == "member"
    # ...and the Central-Server side stays the operator's.
    assert actors["cs.init"] == "operator"
    assert actors["cs.members_member"] == "operator"
    assert actors["ss.bringup_register"] == "operator"
    assert actors["ss.client_register"] == "operator"


def test_an_own_server_join_names_its_own_server_everywhere_the_hosted_one_names_a_host():
    steps = {s.id: s for s in job.build_sequence(REAL_PACK_DIR, _own_payload())}
    assert steps["ss.sign_key_csr"].tokens["SS_CODE"] == "SS-PTSB"
    assert steps["ss.sign_key_csr"].tokens["SESS_P"] == "ss_ptsb"
    assert steps["ss.sign_key_csr"].tokens["CAP_P"] == "ss_ptsb"
    assert steps["ss.bringup_init"].tokens["MEMBER_CODE"] == "PTSB"
    constants = job.build_constants(REAL_PACK_DIR, _own_payload(), SECRETS)
    assert constants["ss_ptsb_host"] == "ss-ptsb"
    assert "ss_plr_host" not in constants


def test_every_own_server_steps_requires_is_satisfied_by_an_earlier_provides():
    """The real check on the sequence's ORDER: ca_name, tsa_name/tsa_url and
    the AUTH cert hash are all re-established inside this job (nothing is in
    Hurl scope between its steps), so a missing or misplaced capture step
    shows up here rather than as a live failure."""
    payload = _own_payload()
    available = set(job.build_constants(REAL_PACK_DIR, payload, SECRETS))
    for step in job.build_sequence(REAL_PACK_DIR, payload):
        missing = [name for name in step.requires if name not in available]
        assert not missing, f"{step.id} requires {missing}, which nothing before it provides"
        available.update(step.provides)


def test_an_own_server_join_runs_to_active_when_the_members_server_is_up():
    hurl = OwnServerHurl()
    record = _run(_record(payload=_own_payload().model_dump(mode="json")), hurl, server_up=lambda dns: True)
    assert record["state"] == "ACTIVE"
    assert hurl.calls == OWN_SERVER_IDS[:-1]


# -- BLOCKED (join-c plan Task 3 Steps 1, 2 and 7) ----------------------------


def test_a_job_whose_members_server_is_absent_goes_blocked_not_failed():
    """Step 1: the first actor: member step is ss.bringup_init, and its target
    is the member's own server. Absent means BLOCKED -- the honest state, not
    an error."""
    hurl = OwnServerHurl()
    record = _run(
        _record(payload=_own_payload().model_dump(mode="json")), hurl, server_up=lambda dns: False
    )
    assert record["state"] == "BLOCKED"
    assert record["error"] is None
    assert record["blocked"]["step"] == "ss.bringup_init"
    assert record["blocked"]["server"] == "ss-ptsb"
    assert "scripts/join-agent.sh ptsb" in record["blocked"]["message"]
    # The operator-side prologue ran; nothing member-side was attempted.
    assert hurl.calls == ["cs.init", "cs.members_member", "cs.anchor"]
    assert record["last_completed_step"] == "cs.anchor"


def test_a_blocked_job_resumes_to_active_once_the_server_appears():
    """Step 7's first half, and Step 2's exit condition: the poll IS the
    completion signal (spec S6.1) -- no callback, no work-order endpoint."""
    record = _run(
        _record(payload=_own_payload().model_dump(mode="json")), OwnServerHurl(), server_up=lambda dns: False
    )
    assert record["state"] == "BLOCKED"

    resumed = OwnServerHurl()
    record = _run(record, resumed, server_up=lambda dns: True)
    assert record["state"] == "ACTIVE"
    # cs.init re-runs (it provides a session token); cs.members_member does not.
    assert "cs.members_member" not in resumed.calls
    assert resumed.calls[0] == "cs.init"


def test_a_blocked_job_stays_blocked_indefinitely_without_ever_failing():
    """Step 7's second half. Five resumes with the server still absent: still
    BLOCKED, never FAILED, and the retry budget is untouched -- waiting for a
    human is not a retry."""
    record = _record(payload=_own_payload().model_dump(mode="json"))
    for _ in range(5):
        record = _run(record, OwnServerHurl(), server_up=lambda dns: False)
        assert record["state"] == "BLOCKED"
        assert record["retry_budget_left"] == job.RETRY_BUDGET


def test_a_hosted_join_can_never_reach_blocked():
    """Spec S4's BLOCKED row: "own-server joins only". Nothing about a hosted
    join asks the member for anything, so even a server_up that always says
    "down" cannot block one."""
    record = _run(_record(), FakeHurl(), server_up=lambda dns: False)
    assert record["state"] == "ACTIVE"


def test_the_health_poll_targets_the_joining_members_own_server():
    seen: list[str] = []

    def server_up(dns: str) -> bool:
        seen.append(dns)
        return True

    _run(_record(payload=_own_payload().model_dump(mode="json")), OwnServerHurl(), server_up=server_up)
    assert set(seen) == {"ss-ptsb"}


def test_resume_across_an_own_server_join_probes_the_registration_and_skips_it():
    """ss.bringup_register is the own-server step where 409-as-success is
    genuinely unsafe (PROBE_SS_BRINGUP_REGISTER's own comment: the PUT can
    land while the Central Server approval does not), and the only newly
    reachable one whose skip costs nothing downstream."""
    record = _run(
        _record(payload=_own_payload().model_dump(mode="json")),
        OwnServerHurl({"ss.bringup_register": _FAILED}),
    )
    assert record["state"] == "FAILED"
    assert record["last_completed_step"] == "ss.sign_key_csr"

    already_registered = {
        "success": True,
        "entries": [{"captures": [{"name": "ss_ptsb_auth_cert_status", "value": "REGISTERED"}]}],
    }
    resumed = OwnServerHurl({"ss.bringup_register#probe": already_registered})
    record = _run(record, resumed)
    assert record["state"] == "ACTIVE"
    assert "ss.bringup_register#probe" in resumed.calls
    assert "ss.bringup_register" not in resumed.calls


def test_ss_auth_key_csr_deliberately_has_no_probe_interpreter():
    """It is the clearest (c) in the registry -- a repeat silently makes a
    second AUTH key -- and is still left to re-run, because _probe() returns a
    verdict and discards the probe's own captures: skipping this step would
    leave ss_ptsb_auth_key_cert_hash unset for ss.bringup_register and
    ss.activate, and the resume would fail outright. Asserted so the choice is
    a decision on record, not an oversight (job.py's comment above
    PROBE_INTERPRETERS explains it)."""
    assert "ss.auth_key_csr" not in job.PROBE_INTERPRETERS
    step = next(s for s in job.build_sequence(REAL_PACK_DIR, _own_payload()) if s.id == "ss.auth_key_csr")
    assert step.probe  # the registry does declare one; nothing here interprets it
    later = job.build_sequence(REAL_PACK_DIR, _own_payload())
    needs_hash = [s.id for s in later if "ss_ptsb_auth_key_cert_hash" in s.requires]
    assert needs_hash == ["ss.bringup_register", "ss.activate"]


def test_a_half_registered_auth_cert_is_not_read_as_done():
    """PROBE_SS_BRINGUP_REGISTER's own comment: the PUT can land while the
    Central Server approval does not, and 409-as-success would call that
    done."""
    step = next(
        s for s in job.build_sequence(REAL_PACK_DIR, _own_payload()) if s.id == "ss.bringup_register"
    )
    assert job._probe_auth_cert_registered(step, {"ss_ptsb_auth_cert_status": "REGISTERED"}) is True
    assert job._probe_auth_cert_registered(step, {"ss_ptsb_auth_cert_status": "REGISTRATION_IN_PROGRESS"}) is False
    assert job._probe_auth_cert_registered(step, {}) is False
