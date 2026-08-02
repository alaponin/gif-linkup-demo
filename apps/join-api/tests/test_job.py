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


def _run(record: dict, hurl: FakeHurl, *, r1=None, saves: list | None = None) -> dict:
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
        retry_interval=0,
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
