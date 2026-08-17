"""Tests for apps/join-api/job.py.

No containers and no network: the executor takes its "run this rendered file,
give me the report" function as an argument (the --fast row: the step
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
        member_requirements={
            "has_security_server": True,
            "has_registered_identity": True,
            "standards_portfolio_adopted": True,
            "data_conformant": True,
            "lawful_basis": None,
            "technical_contact": "Jane Doe",
        },
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


def _fake_r1(
    ok: bool = True,
    detail: str = "http://ss-pnea:8080/...: HTTP 200",
    mismatch: dict | None = None,
):
    def call(
        url: str, client_header: str, declared: frozenset, required: frozenset
    ) -> tuple[bool, str, dict | None]:
        return ok, detail, mismatch

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


# -- the sequence --------------------------------------------------------------


def test_hosted_join_runs_the_documented_sequence_in_order():
    steps = job.build_sequence(REAL_PACK_DIR, _payload())
    assert [s.id for s in steps] == EXPECTED_IDS


def test_every_step_of_a_hosted_join_is_the_operators():
    """hurl/steps.py defaults ss.client_add/ss.sign_key_csr/service.publish to
    actor="member" -- that default is for a member bringing up its own server.
    Under hosted_on there is no member-side infrastructure at all."""
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
    would publish both services against whichever URL won the name."""
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
    """Nothing published, nothing to be reachable: a consume-only
    member's ACTIVE means registered and able to reach the global
    configuration, not callable."""
    steps = job.build_sequence(REAL_PACK_DIR, _payload(services=[], semantic=None))
    assert "join.r1_verify" not in [s.id for s in steps]


def test_r1_target_is_the_consumers_own_security_server():
    target = job._r1_target(REAL_PACK_DIR, _payload())
    assert target["client_header"] == "PROGRESSA/GOV/PNEA/EXAMS"
    assert target["url"].startswith("http://ss-pnea:8080/r1/PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api")


def test_r1_target_raises_loud_not_silent_when_topology_has_drifted_from_manifest(tmp_path):
    """An ACL subject that validate.py's ACL sanity check already proved
    exists in manifest.yaml but is missing from hurl/topology.json at
    job-run time -- the two files disagreeing is exactly what job.py's own
    module docstring calls "registry-perfect but dead": the case
    join.r1_verify exists to catch. The previous behaviour was to return
    None and silently drop the step, reaching ACTIVE with `verified` never
    set. This should never happen if the ACL sanity check did its job --
    reproduced here by copying the real pack (writer._copy_pack, the same
    fixture pattern test_writer.py uses) and deleting the one topology entry
    the default payload's access[] names, which is exactly the kind of
    manifest/topology divergence the ACL sanity check cannot see (it only
    reads manifest.yaml, never topology.json)."""
    writer._copy_pack(REAL_PACK_DIR, tmp_path)
    topology_path = tmp_path / "hurl" / "topology.json"
    topology = json.loads(topology_path.read_text())
    topology["subsystems"] = [s for s in topology["subsystems"] if s["id"] != "PROGRESSA:GOV:PNEA:EXAMS"]
    topology_path.write_text(json.dumps(topology))

    with pytest.raises(job.StepFailure) as exc_info:
        job._r1_target(tmp_path, _payload())
    assert "PROGRESSA:GOV:PNEA:EXAMS" in exc_info.value.message
    assert "topology.json" in exc_info.value.message


# -- executing -------------------------------------------------------------


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
    """The idempotence default: the templates assert HTTP 201, so
    a step whose effect already exists fails its assert with a 409 on the
    wire -- proven live for service.acl."""
    conflict = {
        "success": False,
        "entries": [{"captures": [], "calls": [{"response": {"status": 409}}]}],
    }
    record = _run(_record(), FakeHurl({"service.acl:awards-api:PROGRESSA/GOV/PNEA/EXAMS": conflict}))
    assert record["state"] == "ACTIVE"


# -- the retry budget --------------------------------------------------------

_FAILED = {"success": False, "entries": [], "_stderr": "HTTP 500 from the Security Server"}


def test_a_step_that_exhausts_the_budget_fails_with_its_id_and_the_last_response():
    hurl = FakeHurl({"ss.client_add": _FAILED})
    record = _run(_record(), hurl)
    assert record["state"] == "FAILED"
    assert record["error"]["step"] == "ss.client_add"
    assert "HTTP 500 from the Security Server" in record["error"]["message"]
    assert record["last_completed_step"] == "ss.ca_name_capture"


def test_the_retry_budget_is_one_for_the_run_not_one_per_step():
    """Three attempts burnt early leave the later step nine, not a
    fresh twelve."""
    flaky = [_FAILED, _FAILED, _FAILED, json.loads((FIXTURES / "cs.init.json").read_text())]
    hurl = FakeHurl({"cs.init": flaky, "ss.client_add": _FAILED})
    record = _run(_record(), hurl)
    assert record["state"] == "FAILED"
    assert hurl.calls.count("cs.init") == 4
    assert hurl.calls.count("ss.client_add") == job.RETRY_BUDGET - 3 + 1


def test_the_r1_check_gets_its_own_budget_however_little_the_run_has_left():
    """The own-server defect: ss.client_register's
    CS-propagation wait ate 95-107s of the 120s run budget before the sequence
    reached join.r1_verify, so the r1 step got 13-25s against a reachability
    window measured live at 45s-8min -- verified: true was unreachable and
    resume had no way back to it.

    Eleven retries burnt early leave the run one. The r1 step still gets
    R1_RETRY_BUDGET of its own (it succeeds here on the 5th call, which the
    old shared-budget code could never have reached), and spends none of the
    run's: retry_budget_left is still the 1 the earlier step left behind."""
    flaky = [_FAILED] * (job.RETRY_BUDGET - 1) + [json.loads((FIXTURES / "cs.init.json").read_text())]
    attempts = []

    def r1(url: str, client_header: str, declared: frozenset, required: frozenset) -> tuple[bool, str, dict | None]:
        attempts.append(url)
        return len(attempts) >= 5, "http://ss-pnea:8080/...: HTTP 200", None

    hurl = FakeHurl({"cs.init": flaky})
    record = _run(_record(), hurl, r1=r1)
    assert hurl.calls.count("cs.init") == job.RETRY_BUDGET
    assert record["retry_budget_left"] == 1
    assert len(attempts) == 5
    assert record["state"] == "ACTIVE"
    assert record["verified"] is True


def test_ocsp_staleness_is_named_rather_than_surfaced_as_a_tls_error():
    """The single most likely way a live demo of this module breaks: a federation idle overnight must not fail with what
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
    """A member that registered and published but whose reachability
    call has not passed is ACTIVE with verified: false -- one fact about the
    member, not a place in the lifecycle."""
    record = _run(_record(), FakeHurl(), r1=_fake_r1(False, "connection refused"))
    assert record["state"] == "ACTIVE"
    assert record["verified"] is False


def test_a_contract_mismatch_is_active_unverified_not_failed_and_names_the_diff():
    """A route that reaches something whose response does not match its own
    contract is a different fact from an X-Road fault -- not a reason to
    keep spending the retry budget, and not a FAILED job. The member is
    joined; its service does not conform, and the diff is named as field
    NAMES only."""
    mismatch = {"undeclared": ["mother_name"], "missing": []}
    record = _run(
        _record(contract_fields={"awards-api": {"declared": ["award_id"], "required": []}}),
        FakeHurl(),
        r1=_fake_r1(True, "http://ss-pnea:8080/...: HTTP 200", mismatch),
    )
    assert record["state"] == "ACTIVE"
    assert record["verified"] is False
    assert "mother_name" in record["verified_by"]
    assert "undeclared" in record["verified_by"]


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _r1_against(monkeypatch, status_code, body, declared, required):
    """Drive the REAL _default_r1_call -- every other test here injects a fake
    that returns a preset mismatch, which is how the 404 case below went
    unnoticed."""
    monkeypatch.setattr(job.httpx, "get", lambda *a, **k: _FakeResponse(status_code, body))
    return job._default_r1_call(
        "http://ss-pnea:8080/r1/PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api/",
        "PROGRESSA/GOV/PNEA/EXAMS",
        frozenset(declared),
        frozenset(required),
    )


def test_a_backend_404_is_reachability_passed_not_a_contract_mismatch(monkeypatch):
    """_r1_target probes the service ROOT path on purpose and treats a
    backend 404 as proof the call traversed both proxies. A 404 body carries
    the backend's error shape, never the contract's fields, so comparing the
    two can only ever produce a false mismatch -- which is what made every
    join with a published service land ACTIVE, verified: false."""
    ok, detail, mismatch = _r1_against(
        monkeypatch, 404, {"detail": "award not found"},
        declared=["nin", "award_id", "program", "year"],
        required=["nin", "award_id", "program", "year"],
    )
    assert ok is True
    assert mismatch is None
    assert "404" in detail


def test_a_200_that_does_not_match_its_contract_is_still_a_mismatch(monkeypatch):
    """The other half: gating on 2xx must not blunt G5.9. A successful
    response carrying a field its contract never declared is the case this
    check exists for."""
    ok, _detail, mismatch = _r1_against(
        monkeypatch, 200, {"nin": "1", "mother_name": "leaked"},
        declared=["nin"], required=["nin"],
    )
    assert ok is True
    assert mismatch == {"undeclared": ["mother_name"], "missing": []}


def test_a_contract_mismatch_message_is_scrubbed_like_every_other_r1_message():
    """Every message written into a persisted record goes through
    job.scrub() -- this is a new path into out/join/*.json exactly like the
    existing FAILED/error paths already scrubbed."""
    secret = SECRETS["ss_admin_password"]
    mismatch = {"undeclared": [f"leaked-{secret}"], "missing": []}
    record = _run(
        _record(contract_fields={"awards-api": {"declared": [], "required": []}}),
        FakeHurl(),
        r1=_fake_r1(True, "http://ss-pnea:8080/...: HTTP 200", mismatch),
    )
    assert secret not in record["verified_by"]
    assert "***" in record["verified_by"]


# -- resume ------------------------------------------------------------------


def test_a_job_killed_mid_run_resumes_to_completion_without_rerunning_completed_steps():
    """The headline resume test. The kill is simulated the way a
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
    re-run steps this one skipped."""
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
    """Probes only on the steps classified as ambiguous,
    and only on resume (resume does not need probes for the steps
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
    """Class (d) of the probe-classification audit is empty today and
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


# -- credentials --------------------------------------------------------------


def test_the_serialised_job_context_of_a_completed_job_carries_no_credential(tmp_path):
    """A headline test for the explicit ask:
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
    """ss_admin_user ("xrd") is a short, publicly
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


# -- the shared cookie jar -----------------------------------------------------
# job.py runs one Hurl PROCESS per
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


def test_default_run_hurl_keeps_secrets_off_argv(monkeypatch):
    """Variable VALUES (admin password, token PIN, session tokens) must
    reach Hurl through a 0600 file, never through argv -- argv is readable
    by any user on the box via /proc/<pid>/cmdline while the child runs."""
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        path = pathlib.Path(args[args.index("--variables-file") + 1])
        captured["mode"] = path.stat().st_mode & 0o777
        captured["text"] = path.read_text()
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(job.subprocess, "run", fake_run)

    job._default_run_hurl("step", "GET http://x\n\nHTTP 200\n", {"ss_admin_password": "hunter2"})

    assert "hunter2" not in " ".join(captured["args"])
    assert "--variable" not in captured["args"]
    assert captured["text"] == "ss_admin_password=hunter2\n"
    assert captured["mode"] == 0o600


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
    shell with a normal PATH would find and this code would not.
    No live server: the request is to a closed port, so
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
    # written and is covered continuously by --full, which
    # builds this image anyway.


# -- own-server joins ---------------------------------------------------------
# The other half: the joining member brings up its OWN Security
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
    imagination. The live proof that the real responses match lives elsewhere."""

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


# -- BLOCKED -------------------------------------------------------------------


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
    completion signal -- no callback, no work-order endpoint."""
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
    """BLOCKED is "own-server joins only". Nothing about a hosted
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


# -- the reversal walk --------------------------------------------------------
# job.unjoin() drives the six live-verified reversal calls
# (docs/decisions/xroad-770-notes.md #11) in hurl/steps.py's REVERSAL_ORDER, each guarded
# by its own probe. The fake below replays the REAL recorded un-join exchanges
# in fixtures/xroad/unjoin.*.json -- unlike the forward fixtures those are raw
# HTTP captures rather than Hurl report elements, so they are shaped into
# elements here (status -> Hurl's own success verdict against the template's
# asserted status, body -> the [Captures] value the probe template declares).
# The shaping is mechanical and named; the bodies are the live ones.

UNJOIN_IDS = [
    "service.acl:awards-api:PROGRESSA/GOV/PNEA/EXAMS",
    "service.publish:awards-api",
    "ss.client_register",
    "ss.client_add",
    "ss.sign_key_csr",
    "cs.members_member",
]


def _raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _element(success: bool, captures: dict | None = None, statuses=(), bodies: str = "") -> dict:
    return {
        "success": success,
        "entries": [
            {
                "captures": [{"name": k, "value": v} for k, v in (captures or {}).items()],
                "calls": [{"response": {"status": s}} for s in statuses],
            }
        ],
        "_stderr": "",
        "_bodies": bodies,
    }


# The live token from unjoin.token_after_client_delete.json: FOUR SIGNING keys
# all labelled "Sign key", one per member on the shared host (PNIA, PLR, PTSB,
# MOEYS) plus the AUTH key -- exactly the shape that makes a label match delete
# a different agency's key.
TOKEN_WITH_PTSB = _raw("unjoin.token_after_client_delete")["body"]
PTSB_SIGN_KEY_ID = "0A55E5C2A2E61B4DBF99BE4D620D208ABC49B90A"
TOKEN_WITHOUT_PTSB = dict(
    TOKEN_WITH_PTSB,
    keys=[k for k in TOKEN_WITH_PTSB["keys"] if k["id"] != PTSB_SIGN_KEY_ID],
)
# The description id the forward run captured, read out of the recorded
# service.publish element rather than invented.
DESCRIPTION_ID = job._captures(json.loads((FIXTURES / "service.publish.json").read_text()))[
    "ss_ptsb_awards_api_description_id"
]

# "Already gone", straight off the recorded probes. Note that only two of the
# six are a 404 that Hurl reads as success against its template's `HTTP 404`
# assert: the rest prove absence with a 200 and a body.
PROBE_ABSENT = {
    "service.acl": _element(True, {"ss_ptsb_acl_revoke_probe_code": "service_client_not_found"}, [404]),
    "service.publish": _element(
        True, {"ss_ptsb_service_descriptions": json.dumps(_raw("unjoin.service_delete.probe")["body"])}, [200]
    ),
    # The client is fully gone by the time the walk could re-ask: the probe's
    # own `HTTP 200` assert fails on the 404.
    "ss.client_register": _element(False, statuses=[404]),
    "ss.client_add": _element(True, {"ss_ptsb_client_delete_probe_code": "client_not_found"}, [404]),
    "ss.sign_key_csr": _element(True, {"ss_ptsb_token": json.dumps(TOKEN_WITHOUT_PTSB)}, [200]),
    "cs.members_member": _element(
        True,
        {"cs_member_delete_probe_clients": json.dumps(_raw("unjoin.cs_member_delete.probe")["body"])},
        [200],
    ),
}

# "Still there" -- what each probe reads before its reversal has run.
PROBE_PRESENT = {
    # A live grant 200s, failing the probe template's `HTTP 404` assert.
    "service.acl": _element(False, statuses=[200]),
    "service.publish": _element(
        True, {"ss_ptsb_service_descriptions": json.dumps([{"id": DESCRIPTION_ID}])}, [200]
    ),
    "ss.client_register": _element(True, {"ss_ptsb_client_status": "REGISTERED"}, [200]),
    "ss.client_add": _element(False, statuses=[200]),
    "ss.sign_key_csr": _element(True, {"ss_ptsb_token": json.dumps(TOKEN_WITH_PTSB)}, [200]),
    # No live CS /clients capture exists in the fixtures (the spike only
    # recorded the empty one). Only emptiness is read, so a one-entry list is
    # a faithful stand-in for "not empty".
    "cs.members_member": _element(
        True, {"cs_member_delete_probe_clients": json.dumps({"clients": [{"id": "PROGRESSA:GOV:PTSB"}]})}, [200]
    ),
}

REVERSED_OK = _element(True, statuses=[204])


def _base(label: str) -> str:
    return label.split("#")[0].split(":")[0]


class ReverseHurl(FakeHurl):
    """Replays the un-join fixtures, tracking which reversals have happened so
    a probe re-asked after its own reversal answers "gone" -- which is what
    makes the resume test meaningful rather than a fixture arrangement.

    The walk's own prologue (the session steps, JobStep.must_rerun) gets a
    synthesised element carrying that step's DECLARED provides, the same
    stand-in and the same reasoning as OwnServerHurl: replaying the recorded
    cs.init/ss.bringup_init would bind a session token to the HOSTED run's
    prefix, and an own-server un-join captures under its own. What these tests
    exercise is the reversal engine; the forward tests above already drive the
    real recorded session elements."""

    def __init__(self, gone=(), overrides: dict | None = None, payload=None):
        super().__init__(overrides)
        self.gone = set(gone)
        self.steps = {s.id: s for s in job.build_sequence(REAL_PACK_DIR, payload or _payload())}

    def __call__(self, label: str, body: str, variables: dict) -> dict:
        if label in self.overrides:
            return super().__call__(label, body, variables)
        self.calls.append(label)
        self.variables.append(dict(variables))
        assert body.strip(), f"{label} rendered an empty Hurl file"
        base = _base(label)
        if label.endswith("#reverse-probe"):
            return PROBE_ABSENT[base] if base in self.gone else PROBE_PRESENT[base]
        if label.endswith("#reverse"):
            self.gone.add(base)
            return REVERSED_OK
        step = self.steps[label]
        return _element(True, {name: f"{name}-value" for name in step.provides})


def _active(**overrides) -> dict:
    """An ACTIVE record with the job context a real hosted join leaves behind
    -- produced by actually running one, not hand-written, so the un-join walks
    the same captures a live one would."""
    record = _run(_record(), FakeHurl())
    assert record["state"] == "ACTIVE"
    record.update(state="RETIRING", **overrides)
    return record


def _unjoin(record: dict, hurl: ReverseHurl, saves: list | None = None) -> dict:
    def save(rec: dict) -> None:
        if saves is not None:
            saves.append(json.loads(json.dumps(rec)))

    return job.unjoin(record, REAL_PACK_DIR, secrets=SECRETS, save=save, run_hurl=hurl, retry_interval=0)


def _reversed_ids(hurl: ReverseHurl) -> list[str]:
    """The reversals attempted, in order, de-duplicated: a retried one (the
    client-delete window) is still one entry in the walk."""
    out: list[str] = []
    for call in hurl.calls:
        if call.endswith("#reverse"):
            step_id = call[: -len("#reverse")]
            if step_id not in out:
                out.append(step_id)
    return out


# -- Step 2: the order, and the states -----------------------------------------


def test_every_step_the_walk_visits_has_an_absence_interpreter():
    """unjoin() walks hurl/steps.py's REVERSAL_ORDER and indexes
    REVERSAL_ABSENT by the same base id -- two hand-maintained lists of the
    same set. A REVERSAL_ORDER entry with no interpreter is a KeyError mid-walk
    (with part of the member already reversed); an interpreter for a step the
    order never names is dead code that reads as coverage (final review
    finding 1). tests/test_steps.py holds the third list, the registry's own
    `reverse=` fields, to the same set."""
    _, steps = job._hurl_modules(REAL_PACK_DIR)
    assert set(steps.REVERSAL_ORDER) == set(job.REVERSAL_ABSENT)


def test_the_walk_runs_the_live_verified_reversal_order_not_the_sequence_reversed():
    """hurl/steps.py's REVERSAL_ORDER, established live (#11 finding 5):
    ss.client_register -> ss.client_add -> ss.sign_key_csr, i.e. the client
    goes before its key backwards just as forwards. `reversed(sequence)` would
    put ss.sign_key_csr before ss.client_add and cs.members_member first."""
    hurl = ReverseHurl()
    record = _unjoin(_active(), hurl)
    assert record["state"] == "RETIRED"
    assert _reversed_ids(hurl) == UNJOIN_IDS


def test_the_walk_re_establishes_the_sessions_it_needs_before_touching_anything():
    """Session captures are never persisted, so the walk re-runs
    the steps that provide them -- the same thing a resume does, for the same
    reason. Nothing is reversed before both sessions exist."""
    hurl = ReverseHurl()
    _unjoin(_active(), hurl)
    assert hurl.calls[:2] == ["cs.init", "ss.bringup_init"]
    at_cs_delete = hurl.variables[hurl.calls.index("cs.members_member#reverse")]
    assert at_cs_delete["cs_xsrf_token"]
    assert at_cs_delete["ss_plr_xsrf_token"]


def test_a_completed_walk_leaves_no_credential_in_the_serialised_record(tmp_path):
    path = tmp_path / "retired.json"
    record = _active()
    job.unjoin(
        record, REAL_PACK_DIR, secrets=SECRETS,
        save=lambda rec: path.write_text(json.dumps(rec, indent=2)),
        run_hurl=ReverseHurl(), retry_interval=0,
    )
    assert record["state"] == "RETIRED"
    for value in SECRETS.values():
        if value != SECRETS["ss_admin_user"]:
            assert value not in path.read_text()


# -- Step 2: the two probes that do NOT signal absence with a 404 -------------


def test_the_two_empty_collection_probes_are_read_as_absence_not_as_a_404():
    """service.publish's descriptions list and cs.members_member's
    /clients?q= result both 200 forever; absence is an empty collection
    (PROBE_SERVICE_DELETE / PROBE_CS_MEMBER_DELETE's own comments). A guard
    written as "probe 404s => already gone" skips neither and re-issues both."""
    hurl = ReverseHurl(gone={"service.publish", "cs.members_member"})
    record = _unjoin(_active(), hurl)
    assert record["state"] == "RETIRED"
    assert "service.publish:awards-api" not in _reversed_ids(hurl)
    assert "cs.members_member" not in _reversed_ids(hurl)
    # ...and the four that DO 404 (or correlate) were still walked.
    assert len(_reversed_ids(hurl)) == 4


def test_a_transitional_deletion_in_progress_counts_as_already_unregistered():
    """PROBE_SS_CLIENT_REGISTER 200s with DELETION_IN_PROGRESS between the
    unregister and the client delete (unjoin.client_unregister.probe.json).
    Re-issuing the unregister there is pointless; the forward interpreter's
    REGISTERED is the only reading that means "still needs undoing"."""
    step = next(s for s in job.build_sequence(REAL_PACK_DIR, _payload()) if s.id == "ss.client_register")
    live = _raw("unjoin.client_unregister.probe")["body"]["status"]
    assert live == "DELETION_IN_PROGRESS"
    mid = _element(True, {"ss_ptsb_client_status": live}, [200])
    assert job._absent_client_registration(step, mid, {}) is True
    assert job._absent_client_registration(step, PROBE_PRESENT["ss.client_register"], {}) is False
    assert job._absent_client_registration(step, PROBE_ABSENT["ss.client_register"], {}) is True


def test_an_unanswerable_probe_reads_as_still_present_never_as_absent():
    """A probe that cannot run must never skip a reversal: attempting one that
    already happened is safe (404/409-as-already-gone), skipping one that did
    not is a member left half-retired."""
    dead = _element(False, statuses=[500])
    steps = {s.id.split(":")[0]: s for s in job.build_sequence(REAL_PACK_DIR, _payload())}
    for base, interpreter in job.REVERSAL_ABSENT.items():
        assert interpreter(steps[base], dead, {}) is False, base


def test_a_probe_that_answered_with_the_wrong_shape_is_not_read_as_absence_either():
    """The rule above has to hold for a probe that
    SUCCEEDED and returned something unexpected, not only for one that failed.
    A body of the wrong type is unreadable, not empty -- and reading it as
    empty is reading it as "already gone", which silently skips the reversal.
    Every capture-reading interpreter, against every shape a JSON body can
    take that is not the documented one."""
    steps = {s.id.split(":")[0]: s for s in job.build_sequence(REAL_PACK_DIR, _payload())}
    variables = {"ss_ptsb_awards_api_description_id": DESCRIPTION_ID}
    wrong = ['{"unexpected": "object"}', '"a string"', "123", "null", "[1, 2, 3]", "not json", ""]
    for capture, base in (
        ("ss_ptsb_service_descriptions", "service.publish"),
        ("ss_ptsb_token", "ss.sign_key_csr"),
        ("cs_member_delete_probe_clients", "cs.members_member"),
    ):
        for body in wrong:
            element = _element(True, {capture: body}, [200])
            assert job.REVERSAL_ABSENT[base](steps[base], element, variables) is False, (base, body)


# -- Step 3: attempt-and-retry for the client-delete window --------------------


def test_a_409_action_not_possible_on_the_client_delete_is_retried_not_failed():
    """#11 finding 3: DELETE /clients/{id} may be refused while the deletion
    propagates, and the window's size is NOT established -- so this attempts
    and retries rather than polling DELETION_IN_PROGRESS as a gate. The retry
    comes out of the SAME one-run budget the forward path uses."""
    busy = _element(False, statuses=[409], bodies='{"error":{"code":"action_not_possible"}}')
    hurl = ReverseHurl(overrides={"ss.client_add#reverse": [busy, busy, REVERSED_OK]})
    record = _unjoin(_active(), hurl)
    assert record["state"] == "RETIRED"
    assert hurl.calls.count("ss.client_add#reverse") == 3
    assert record["retry_budget_left"] == job.RETRY_BUDGET - 2


def test_a_409_that_is_not_action_not_possible_is_already_gone_not_a_retry():
    """service.acl's repeat is `409 accessright_not_found` (#11's repeat
    table). Same status as the retryable one, opposite meaning -- which is why
    _reversal_succeeded reads the error code, not the status."""
    revoked = _element(False, statuses=[409], bodies='{"error":{"code":"accessright_not_found"}}')
    hurl = ReverseHurl(overrides={"service.acl:awards-api:PROGRESSA/GOV/PNEA/EXAMS#reverse": revoked})
    record = _unjoin(_active(), hurl)
    assert record["state"] == "RETIRED"
    assert hurl.calls.count("service.acl:awards-api:PROGRESSA/GOV/PNEA/EXAMS#reverse") == 1


def test_a_reversal_that_never_succeeds_stops_the_walk_in_retiring_not_failed():
    """FAILED on this record would offer POST /requests/{id}/resume, which
    re-enters the FORWARD path. RETIRING with an error is the honest state:
    re-issue the DELETE."""
    busy = _element(False, statuses=[409], bodies='{"error":{"code":"action_not_possible"}}')
    hurl = ReverseHurl(overrides={"ss.client_add#reverse": busy})
    record = _unjoin(_active(), hurl)
    assert record["state"] == "RETIRING"
    assert record["error"]["step"] == "ss.client_add"
    assert "retry budget" in record["error"]["message"]
    # The two reversals before it still happened and are recorded as such.
    assert [e["step"] for e in record["reversal"]] == UNJOIN_IDS[:3]


# -- Step 4b: the SIGN-key orphan ---------------------------------------------


def test_the_sign_key_delete_names_the_id_correlated_by_owner_not_by_label():
    """The live token carries four keys all labelled "Sign key", one per member
    on the shared host. The id used must come from
    keys[].certificates[].owner_id (SS_SIGN_KEY_DELETE.hurl.tmpl), never from
    the label and never blind from the job context."""
    hurl = ReverseHurl()
    _unjoin(_active(), hurl)
    at_delete = hurl.variables[hurl.calls.index("ss.sign_key_csr#reverse")]
    assert at_delete["ss_ptsb_sign_key_id"] == PTSB_SIGN_KEY_ID
    others = {k["id"] for k in TOKEN_WITH_PTSB["keys"]} - {PTSB_SIGN_KEY_ID}
    assert others and at_delete["ss_ptsb_sign_key_id"] not in others


def test_a_hosted_members_sign_key_is_gone_afterwards_and_every_other_members_is_not():
    """Step 4b's assertion, made against the token the walk would read next:
    no key whose certificates[].owner_id is the departed member, and every
    other member's still there. The orphan #11 found is the whole reason this
    step exists.

    Asserted over the token the WALK leaves behind, derived from the key the
    walk actually asked X-Road to delete -- not over a hand-built constant,
    which asserts nothing about the walk at all."""
    hurl = ReverseHurl()
    _unjoin(_active(), hurl)
    deleted = hurl.variables[hurl.calls.index("ss.sign_key_csr#reverse")]["ss_ptsb_sign_key_id"]
    # One DELETE /keys/{key_id} removes the key AND its certificate together
    # (#11), so what GET /tokens/0 reads next is the live token minus exactly
    # the key the walk named.
    remaining = [key for key in TOKEN_WITH_PTSB["keys"] if key["id"] != deleted]
    owners = [cert.get("owner_id") for key in remaining for cert in key.get("certificates", [])]
    assert "PROGRESSA:GOV:PTSB" not in owners
    assert {"PROGRESSA:GOV:PNIA", "PROGRESSA:GOV:PLR", "PROGRESSA:GOV:MOEYS"} <= set(owners)
    # ...and that token is what the next probe reads as absence, so a re-issued
    # DELETE skips this step rather than deleting a second key.
    step = next(s for s in job.build_sequence(REAL_PACK_DIR, _payload()) if s.id == "ss.sign_key_csr")
    after = _element(True, {"ss_ptsb_token": json.dumps(dict(TOKEN_WITH_PTSB, keys=remaining))}, [200])
    assert job._absent_sign_key(step, after, {}) is True


def test_the_walk_refuses_to_delete_a_key_it_cannot_correlate():
    """Rather than fall back to the forward run's captured id: on a shared host
    a stale id is another agency's signing key."""
    blind = _element(True, {"ss_ptsb_token": json.dumps({"keys": []})}, [200])
    # An empty token reads as "already gone", so this uses one that answers
    # NEITHER -- a body the probe could not parse at all.
    unparseable = _element(True, {"ss_ptsb_token": "not json"}, [200])
    assert job._absent_sign_key(
        next(s for s in job.build_sequence(REAL_PACK_DIR, _payload()) if s.id == "ss.sign_key_csr"),
        blind, {},
    ) is True
    hurl = ReverseHurl(overrides={"ss.sign_key_csr#reverse-probe": unparseable})
    record = _unjoin(_active(), hurl)
    assert record["state"] == "RETIRING"
    assert record["error"]["step"] == "ss.sign_key_csr"
    assert "another agency's signing key" in record["error"]["message"]
    assert "ss.sign_key_csr#reverse" not in hurl.calls


# -- Step 4: the own-server member's Docker residue ----------------------------


def test_an_own_server_un_join_skips_the_sign_key_and_names_the_three_volumes():
    """An own-server member's key dies with its container and volumes, so Step
    4b does not apply to it -- what it leaves instead is Docker state this API
    deliberately cannot touch."""
    record = _run(
        _record(payload=_own_payload().model_dump(mode="json")), OwnServerHurl(), server_up=lambda dns: True
    )
    assert record["state"] == "ACTIVE"
    record["state"] = "RETIRING"
    hurl = ReverseHurl(payload=_own_payload())
    record = _unjoin(record, hurl)
    assert record["state"] == "RETIRED"
    assert "ss.sign_key_csr" not in _reversed_ids(hurl)
    instruction = record["retire_instruction"]
    assert instruction["container"] == "ss-ptsb"
    assert instruction["volumes"] == ["kp2-ptsb-db", "kp2-ptsb-conf", "kp2-ptsb-archive"]
    assert "docker volume rm" in instruction["message"]


def test_the_archive_is_exported_before_the_volume_holding_it_is_deleted():
    """The retention step. An instruction that deletes kp2-<key>-archive and
    only then mentions retention is an evidence gap with a footnote -- the
    export has to be a command, above the delete, or the operator pastes the
    block and the message log is gone."""
    instruction = job.retire_instruction(_own_payload())
    assert instruction["archive_export"] == "out/retired/kp2-ptsb-archive.tar.gz"
    message = instruction["message"]
    export_at = message.index("tar czf /to/kp2-ptsb-archive.tar.gz")
    delete_at = message.index("docker volume rm")
    assert export_at < delete_at, "the export must come BEFORE the delete, not after it"
    assert "-v kp2-ptsb-archive:/from" in message


def test_the_export_runs_the_image_this_pack_already_pins():
    """One digest, two consumers: an operator is handed a pinned image, and
    it is one the host already has because join-api itself is built FROM it.
    If the Dockerfile's pin moves, this instruction hands out a digest that
    is nowhere else in the pack."""
    dockerfile = (REAL_PACK_DIR / "apps" / "join-api" / "Dockerfile").read_text()
    assert f"FROM {job.TAR_IMAGE}" in dockerfile


def test_the_volume_names_are_the_ones_generate_py_actually_writes():
    """One naming rule, two consumers: if hurl/generate.py's compose.members.yml
    volume block is renamed, this instruction becomes wrong silently."""
    source = (REAL_PACK_DIR / "hurl" / "generate.py").read_text()
    for suffix in ("db", "conf", "archive"):
        assert f"{{key}}-{suffix}: {{{{name: kp2-{{key}}-{suffix}}}}}" in source


def test_a_hosted_member_gets_no_docker_instruction():
    """It owns no container and no volumes -- a hand-cleanup note here would be
    an instruction to do nothing."""
    assert job.retire_instruction(_payload()) is None


# -- Step 9: resumability ------------------------------------------------------


def test_a_walk_killed_halfway_resumes_without_re_attempting_what_the_probes_report_gone():
    """The headline resumability test. The kill is simulated the way a real one
    lands: the walk stops mid-way, and the SAME federation state (three
    reversals done) is what the resume's probes read."""
    stalled = _element(False, statuses=[500])
    first = ReverseHurl(overrides={"ss.client_add#reverse": stalled})
    record = _unjoin(_active(), first)
    assert record["state"] == "RETIRING"
    assert [e["step"] for e in record["reversal"]] == UNJOIN_IDS[:3]
    assert _reversed_ids(first) == UNJOIN_IDS[:4]  # the fourth was attempted and never took

    # The federation now has the first three undone -- which is exactly what
    # `gone` carries into the resume, and what its probes therefore report.
    resumed = ReverseHurl(gone={_base(i) for i in UNJOIN_IDS[:3]})
    record = _unjoin(record, resumed)
    assert record["state"] == "RETIRED"
    assert _reversed_ids(resumed) == UNJOIN_IDS[3:]
    # Every entry is still PROBED on the resume -- that is how it knows.
    assert len([c for c in resumed.calls if c.endswith("#reverse-probe")]) == len(UNJOIN_IDS)
    assert [e["outcome"] for e in record["reversal"]] == ["already absent"] * 3 + ["reversed"] * 3


def test_a_second_delete_on_a_fully_retired_member_is_a_no_op_walk():
    """Idempotent by construction: every probe reports absence, nothing is
    re-issued, and the record ends RETIRED again."""
    record = _unjoin(_active(), ReverseHurl())
    again = ReverseHurl(gone={_base(i) for i in UNJOIN_IDS})
    record = _unjoin(record, again)
    assert record["state"] == "RETIRED"
    assert _reversed_ids(again) == []


def test_a_record_with_no_progress_marker_is_walked_in_full_not_skipped():
    """last_completed_step is the forward path's marker; a record missing it
    must not be read as "nothing to undo". The probes are what decide."""
    record = _active()
    record.pop("last_completed_step")
    hurl = ReverseHurl()
    record = _unjoin(record, hurl)
    assert _reversed_ids(hurl) == UNJOIN_IDS


def test_a_step_the_forward_run_never_reached_is_not_reversed():
    """The mirror of the test above: a join that FAILED at ss.client_register
    never published a service or granted an ACL, and the walk must not try to
    undo either."""
    record = _run(_record(), FakeHurl({"ss.client_register": _FAILED}))
    assert record["last_completed_step"] == "ss.sign_key_csr"
    record["state"] = "RETIRING"
    hurl = ReverseHurl()
    record = _unjoin(record, hurl)
    assert record["state"] == "RETIRED"
    assert _reversed_ids(hurl) == ["ss.client_add", "ss.sign_key_csr", "cs.members_member"]
