"""DELETE /members/{key} through FastAPI's TestClient.

Same throwaway-git-repo pack fixture as test_app_approve.py, and for the same
reason (writer.apply_real runs `git status --porcelain` against the enclosing
checkout). The reversal WALK is not run here -- _start_unjoin is replaced, and
job.unjoin() is driven directly against the recorded un-join fixtures in
test_job.py. What these tests are about is the endpoint: the canonical refusal,
which record a member key resolves to, and the scripts/member.sh handoff.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-retire"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-retire"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

_spec = importlib.util.spec_from_file_location(
    "join_api_app_retire", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app_module)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import writer  # noqa: E402

REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]
CONSOLE_HEADER = "X-KP2-Console"
APPLICANT = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}
OPERATOR = {"Authorization": "Bearer test-operator-token", CONSOLE_HEADER: "1"}
# approve now requires a decision_reference (test_app_approve.py).
DECISION = {"decision_reference": "[confirm: cite the Steering Committee minute reference and date]"}

started: list[str] = []


def _git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    started.clear()
    # Each test gets its own budget: the limiter's buckets are module-level
    # state and a suite is not a caller (app.py's rate_limit).
    app_module._BUCKETS.clear()
    repo_root = tmp_path / "repo"
    pack = repo_root / "a" / "b" / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    # writer._COPY_ITEMS is deliberately scoped to what hurl/generate.py reads
    # and does not include scripts/ -- widening it for a test would blur a
    # documented boundary, so the one script this endpoint shells out to is
    # copied here instead.
    shutil.copytree(REAL_PACK_DIR / "scripts", pack / "scripts")
    _git("init", "-q", cwd=repo_root)
    _git("config", "user.email", "test@example.invalid", cwd=repo_root)
    _git("config", "user.name", "test", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    _git("add", "-A", cwd=repo_root)
    _git("commit", "-q", "-m", "seed", cwd=repo_root)
    monkeypatch.setattr(app_module, "PACK_DIR", pack)
    monkeypatch.setattr(app_module, "OUT_DIR", tmp_path / "out")
    # scripts/member.sh runs a bare `python3` (correct in the container, where
    # there is exactly one). On a dev host there may be several, and the pack
    # documents a 3.9+ floor (hurl/README.md's "Host Python runtime") that a
    # system default can be below -- pin the one this suite itself runs under
    # rather than depend on which python3 a shell happens to resolve.
    monkeypatch.setenv("PATH", f"{pathlib.Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(app_module, "_start_job", lambda request_id: None)
    monkeypatch.setattr(app_module, "_start_unjoin", lambda request_id: started.append(request_id))
    return TestClient(app_module.app)


PAYLOAD = dict(
    code="PTSB",
    name="Progressa Tertiary Scholarship Board",
    subsystem="SCHOLARSHIP",
    subsystem_description="Scholarship award management",
    security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
    backend={"auth": "network_allowlist"},
    member_requirements={
        "has_security_server": True,
        "has_registered_identity": True,
        "standards_portfolio_adopted": True,
        "data_conformant": True,
        "lawful_basis": "consent",
        "technical_contact": "Jane Doe",
    },
)


def _joined(client, **payload_overrides) -> dict:
    """A real joined member: submitted, approved (so the config and the
    manifest entry exist on disk), then marked ACTIVE the way a completed job
    would leave it. The job itself never runs here."""
    payload = dict(PAYLOAD, **payload_overrides)
    record = client.post("/requests", json=payload, headers=APPLICANT).json()
    assert record["state"] == "SUBMITTED", record
    assert client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR).status_code == 202
    stored = app_module._load_request(record["id"])
    stored["state"] = "ACTIVE"
    stored["last_completed_step"] = "join.r1_verify"
    stored["verified"] = True
    app_module._save_request(stored)
    return stored


# -- Step 1: the canonical refusal, before anything else ----------------------


def test_a_canonical_member_is_refused_naming_the_frozen_cross_pack_contract(client):
    resp = client.request("DELETE", "/members/pnia", headers=OPERATOR)
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "KP3/KP4" in detail and "identifiers" in detail
    assert started == []


def test_the_canonical_refusal_comes_before_the_record_lookup(client):
    """"Refuse before doing anything" is the point: a canonical member has no
    join record either, and a 404 "never joined through this API" would be a
    true statement that hides the real reason and invites a retry."""
    resp = client.request("DELETE", "/members/pnea", headers=OPERATOR)
    assert resp.status_code == 403
    assert "canonical" in resp.json()["detail"]


def test_an_unknown_member_is_a_404(client):
    resp = client.request("DELETE", "/members/nobody", headers=OPERATOR)
    assert resp.status_code == 404
    assert "manifest.yaml" in resp.json()["detail"]


def test_a_key_outside_the_derivable_charset_never_reaches_the_filesystem(client):
    """The key becomes a manifest lookup and an argv element for
    scripts/member.sh, which rm -r's a path built from it."""
    for bad in ("pt.sb", "PTSB", "ptsb%2e%2e"):
        resp = client.request("DELETE", f"/members/{bad}", headers=OPERATOR)
        assert resp.status_code in (400, 404), bad
        assert started == []


# -- auth and the request boundary --------------------------------------------


def test_an_applicant_cannot_un_join(client):
    _joined(client)
    resp = client.request("DELETE", "/members/ptsb", headers=APPLICANT)
    assert resp.status_code == 403
    assert started == []


def test_un_join_requires_the_request_boundary_header(client):
    _joined(client)
    resp = client.request("DELETE", "/members/ptsb", headers={"Authorization": "Bearer test-operator-token"})
    assert resp.status_code == 403
    assert started == []


# -- Step 2: which record a member key resolves to ----------------------------


def test_a_joined_member_goes_retiring_and_starts_the_walk(client):
    record = _joined(client)
    resp = client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "RETIRING"
    assert body["id"] == record["id"]
    assert started == [record["id"]]
    assert app_module._load_request(record["id"])["state"] == "RETIRING"


def test_a_joined_member_with_no_active_record_is_a_404_that_says_why(client):
    """A member added by hand via prompts/member.md has config and a manifest
    entry but no step sequence to walk backwards -- scripts/member.sh remove is
    the answer there, and the message says so."""
    _joined(client)
    stored = app_module._member_record("ptsb")
    stored["state"] = "FAILED"
    app_module._save_request(stored)
    resp = client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    assert resp.status_code == 404
    assert "scripts/member.sh remove" in resp.json()["detail"]


def test_the_newest_matching_record_wins_when_a_member_joined_more_than_once(client):
    """Nothing enforces one record per member (scripts/member.sh drift makes
    the same choice for the same reason)."""
    first = _joined(client)
    stale = dict(first, id="older", submitted_at="2000-01-01T00:00:00+00:00")
    app_module._save_request(stale)
    assert app_module._member_record("ptsb")["id"] == first["id"]


def test_re_issuing_the_delete_on_a_retiring_record_resumes_it(client):
    """The whole resume story for un-joining: the walk is probe-guarded, so a
    second DELETE re-walks and skips what is gone. POST /requests/{id}/resume
    is deliberately NOT this -- that one re-enters the forward path."""
    record = _joined(client)
    assert client.request("DELETE", "/members/ptsb", headers=OPERATOR).status_code == 202
    resp = client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    assert resp.status_code == 202
    assert started == [record["id"], record["id"]]


def test_the_forward_resume_endpoint_will_not_touch_a_retiring_record(client):
    record = _joined(client)
    client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    resp = client.post(f"/requests/{record['id']}/resume", headers=OPERATOR)
    assert resp.status_code == 409


# -- Step 4/8: the Docker instruction reaches the operator on the way IN -------


def test_an_own_server_member_is_told_about_its_container_and_volumes_immediately(client):
    """Not only in the final record: an operator who never comes back for it
    would otherwise leave three named volumes behind."""
    _joined(client, security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "own_server": True})
    body = client.request("DELETE", "/members/ptsb", headers=OPERATOR).json()
    instruction = body["retire_instruction"]
    assert instruction["container"] == "ss-ptsb"
    assert instruction["volumes"] == ["kp2-ptsb-db", "kp2-ptsb-conf", "kp2-ptsb-archive"]


def test_a_hosted_member_is_told_nothing_about_docker(client):
    _joined(client)
    body = client.request("DELETE", "/members/ptsb", headers=OPERATOR).json()
    assert body["retire_instruction"] is None


# -- Step 5: the scripts/member.sh handoff ------------------------------------


def _stub_walk(monkeypatch, state="RETIRED"):
    def fake_unjoin(record, pack_dir, *, secrets, save, **kwargs):
        record["state"] = state
        if state == "RETIRED":
            # Matches real job.unjoin(): retired_at is set exactly when state
            # becomes RETIRED (job.py's own "record['retired_at'] = _now()"
            # right after "record['state'] = 'RETIRED'").
            record["retired_at"] = "2026-08-08T00:00:00+00:00"
        save(record)
        return record

    monkeypatch.setattr(app_module.job, "unjoin", fake_unjoin)


def test_a_completed_walk_delegates_the_config_half_to_member_sh(client, monkeypatch):
    """Step 5: member.sh remove already deletes the directory, strips the
    manifest entry, refuses a canonical member and regenerates. Called, not
    reimplemented."""
    record = _joined(client)
    _stub_walk(monkeypatch)
    client.request("DELETE", "/members/ptsb", headers=OPERATOR)

    app_module._run_unjoin(record["id"])

    stored = app_module._load_request(record["id"])
    assert stored["state"] == "RETIRED", stored.get("error")
    assert stored["config_removed"] is True
    assert not (app_module.PACK_DIR / "configs" / "member-ptsb").exists()
    manifest = (app_module.PACK_DIR / "manifest.yaml").read_text()
    assert "\n    ptsb:\n" not in manifest

    # The onboarding folder survives config removal, and now carries
    # 99-retirement.md, written by this endpoint (not by scripts/member.sh
    # remove, which only ran the config half above).
    onboarding = app_module.PACK_DIR / "onboarding" / "ptsb"
    assert onboarding.is_dir()
    retirement = (onboarding / "99-retirement.md").read_text()
    assert "2026-08-08T00:00:00+00:00" in retirement
    assert record["id"] in retirement
    assert "REVERSAL_ORDER" in retirement


def test_the_global_constraint_holds_after_the_round_trip(client, monkeypatch):
    """docs/decisions/xroad-770-notes.md #11's own closing finding, asserted rather than
    trusted: a join followed by an un-join leaves hurl/topology.json
    byte-identical to what it was before -- same member set, same allocation,
    always. Compared against the pre-join bytes rather than a golden file so
    it holds for whatever profile deployment.yaml currently names."""
    topology = app_module.PACK_DIR / "hurl" / "topology.json"
    before = topology.read_bytes()
    record = _joined(client)
    assert topology.read_bytes() != before, "the join should have changed the topology"
    _stub_walk(monkeypatch)
    client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    app_module._run_unjoin(record["id"])
    assert topology.read_bytes() == before


def test_a_second_delete_does_not_re_run_member_sh_on_a_completed_retirement(client, monkeypatch):
    """scripts/member.sh remove is NOT idempotent -- it exits non-zero on a
    member whose directory is already gone (member.sh's own "nothing to
    remove"). Two DELETEs queue on _JOB_LOCK, and the second one's walk is a
    clean no-op over probes that all report absence -- so without a guard the
    second run would rewrite a finished retirement back to RETIRING with a
    config.remove error, and the console would show a fully un-joined member
    as stuck. runbook.md explicitly invites the re-issue."""
    record = _joined(client)
    _stub_walk(monkeypatch)
    client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    app_module._run_unjoin(record["id"])
    assert app_module._load_request(record["id"])["config_removed"] is True

    calls: list[list[str]] = []
    real_run = app_module.subprocess.run
    monkeypatch.setattr(
        app_module.subprocess, "run", lambda args, **kw: calls.append(args) or real_run(args, **kw)
    )
    app_module._run_unjoin(record["id"])  # the queued second DELETE

    assert calls == [], "member.sh was re-run on an already-removed member"
    stored = app_module._load_request(record["id"])
    assert stored["state"] == "RETIRED"
    assert stored["error"] is None


def test_a_member_sh_failure_leaves_the_record_retiring_and_says_so(client, monkeypatch):
    """The federation no longer holds the member but the working tree still
    does -- RETIRED would be a lie, and the error names the step."""
    record = _joined(client)
    _stub_walk(monkeypatch)
    client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    # Remove the config out from under member.sh: it fails with "nothing to
    # remove", the same way it would for any member it cannot retire.
    shutil.rmtree(app_module.PACK_DIR / "configs" / "member-ptsb")
    app_module._run_unjoin(record["id"])

    stored = app_module._load_request(record["id"])
    assert stored["state"] == "RETIRING"
    assert stored["error"]["step"] == "config.remove"
    assert "member.sh" in stored["error"]["message"]


def test_member_sh_is_never_run_when_the_walk_did_not_finish(client, monkeypatch):
    """Removing the config while the federation still holds the member is the
    orphan trap docs/decisions/xroad-770-notes.md #7 records."""
    record = _joined(client)
    _stub_walk(monkeypatch, state="RETIRING")
    client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    app_module._run_unjoin(record["id"])
    assert (app_module.PACK_DIR / "configs" / "member-ptsb").exists()


def test_a_crashing_walk_records_the_failure_instead_of_leaving_it_silent(client, monkeypatch):
    record = _joined(client)

    def boom(*args, **kwargs):
        raise RuntimeError(f"kaboom {app_module.TOKEN_PIN}")

    monkeypatch.setattr(app_module.job, "unjoin", boom)
    client.request("DELETE", "/members/ptsb", headers=OPERATOR)
    app_module._run_unjoin(record["id"])

    stored = app_module._load_request(record["id"])
    assert stored["state"] == "RETIRING"
    assert "kaboom" in stored["error"]["message"]
    # Scrubbed like every other persisted error message.
    assert app_module.TOKEN_PIN not in json.dumps(stored)
