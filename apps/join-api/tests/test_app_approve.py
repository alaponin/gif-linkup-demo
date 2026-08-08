"""POST /requests/{id}/approve and /resume through FastAPI's
TestClient. The pack is a temp copy inside a throwaway git repo, three levels
down, because writer.apply_real() runs `git status --porcelain` against the
enclosing checkout (spec S9) and app.py lets it default repo_root the way
docker-compose.yml lays the real one out.

The job itself is not run here -- app_module._start_job is replaced, so these
tests are about the endpoints (state transitions, the operator-only asymmetry,
the queued indicator). job.run() is tested directly, against recorded
fixtures, in test_job.py.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-approve"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-approve"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

_spec = importlib.util.spec_from_file_location(
    "join_api_app_approve", pathlib.Path(__file__).resolve().parent.parent / "app.py"
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

# Every approve call now needs a decision_reference -- the
# minute identifier and date the demo cannot supply a real one for, in the
# pack's own [confirm: ...] register.
DECISION = {"decision_reference": "[confirm: cite the Steering Committee minute reference and date]"}


def _git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    pack = repo_root / "a" / "b" / "pack"  # apply_real defaults repo_root to parents[2]
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "user.email", "test@example.invalid", cwd=repo_root)
    _git("config", "user.name", "test", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    _git("add", "-A", cwd=repo_root)
    _git("commit", "-q", "-m", "seed", cwd=repo_root)
    monkeypatch.setattr(app_module, "PACK_DIR", pack)
    monkeypatch.setattr(app_module, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(app_module, "_start_job", lambda request_id: started.append(request_id))
    return TestClient(app_module.app)


started: list[str] = []


@pytest.fixture(autouse=True)
def _clear_started():
    started.clear()


def _submit(client) -> dict:
    payload = dict(
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
    resp = client.post("/requests", json=payload, headers=APPLICANT)
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "SUBMITTED"
    return resp.json()


def test_approve_writes_the_config_for_real_and_starts_the_job(client):
    record = _submit(client)
    resp = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "APPROVED"
    assert body["queued"] is False
    assert body["decision_reference"] == DECISION["decision_reference"]
    assert started == [record["id"]]
    # spec S9: config is written on APPROVED, before any live mutation.
    assert (app_module.PACK_DIR / "configs" / "member-ptsb" / "ptsb.yaml").exists()
    assert "ptsb" in (app_module.PACK_DIR / "manifest.yaml").read_text()

    # Step 2: persisted, and surfaced verbatim on a follow-up GET (raw-dict
    # return, apps/join-api/app.py:325-340) -- no separate wiring needed.
    follow_up = client.get(f"/requests/{record['id']}", headers=OPERATOR)
    assert follow_up.status_code == 200
    assert follow_up.json()["decision_reference"] == DECISION["decision_reference"]


def test_approve_without_a_decision_reference_is_rejected(client):
    """The admission gate is the field, not a second
    login. Missing entirely -- no body at all."""
    record = _submit(client)
    resp = client.post(f"/requests/{record['id']}/approve", headers=OPERATOR)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "decision_reference" in detail
    assert "Steering Committee" in detail
    assert "5.3" in detail
    # No write happened -- the check runs before the config is touched.
    assert not (app_module.PACK_DIR / "configs" / "member-ptsb").exists()
    assert app_module._load_request(record["id"])["state"] == "SUBMITTED"


def test_approve_with_a_blank_decision_reference_is_rejected(client):
    """Whitespace-only is not a reference either."""
    record = _submit(client)
    resp = client.post(
        f"/requests/{record['id']}/approve", json={"decision_reference": "   "}, headers=OPERATOR
    )
    assert resp.status_code == 400
    assert not (app_module.PACK_DIR / "configs" / "member-ptsb").exists()


def test_an_applicant_cannot_approve(client):
    """Decision 10's teaching point: the asymmetry, not per-request scoping."""
    record = _submit(client)
    resp = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=APPLICANT)
    assert resp.status_code == 403
    assert not (app_module.PACK_DIR / "configs" / "member-ptsb").exists()


def test_approving_twice_is_a_conflict_not_a_second_write(client):
    record = _submit(client)
    assert client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR).status_code == 202
    resp = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert resp.status_code == 409
    assert "APPROVED" in resp.json()["detail"]


def test_approve_reports_queued_when_another_job_holds_the_lock(client):
    """One active job, others queue, and the API says so."""
    record = _submit(client)
    app_module._JOB_LOCK.acquire()
    try:
        body = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR).json()
    finally:
        app_module._JOB_LOCK.release()
    assert body["queued"] is True


def test_resume_is_only_possible_from_failed(client):
    record = _submit(client)
    resp = client.post(f"/requests/{record['id']}/resume", headers=OPERATOR)
    assert resp.status_code == 409

    stored = app_module._load_request(record["id"])
    stored["state"] = "FAILED"
    stored["last_completed_step"] = "ss.client_add"
    app_module._save_request(stored)
    resp = client.post(f"/requests/{record['id']}/resume", headers=OPERATOR)
    assert resp.status_code == 202
    assert started == [record["id"]]


def test_resume_is_also_the_exit_from_blocked(client):
    """BLOCKED leaves through this same endpoint --
    no callback route, no work-order endpoint (spec S6.1). The operator runs
    scripts/join-agent.sh, then resumes, and job.run() polls the server it
    just stood up."""
    record = _submit(client)
    stored = app_module._load_request(record["id"])
    stored["state"] = "BLOCKED"
    stored["last_completed_step"] = "cs.anchor"
    app_module._save_request(stored)
    resp = client.post(f"/requests/{record['id']}/resume", headers=OPERATOR)
    assert resp.status_code == 202
    assert started == [record["id"]]


def test_a_generate_failure_is_scrubbed_before_it_is_returned_or_persisted(client, monkeypatch):
    """apply_real's generate.py subprocess reads .env, so its stderr can
    carry a credential -- and this one string goes into both the response and
    out/join/<id>.json."""
    record = _submit(client)
    pin = app_module.TOKEN_PIN

    def boom(*args, **kwargs):
        raise writer.GenerateFailure(f"Traceback ... XROAD_TOKEN_PIN={pin}\n", 1)

    monkeypatch.setattr(app_module.writer, "apply_real", boom)
    resp = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert resp.status_code == 409
    assert pin not in resp.text
    assert pin not in (app_module._requests_dir() / f"{record['id']}.json").read_text()
    assert app_module._load_request(record["id"])["state"] == "FAILED"


def test_a_git_check_failure_is_a_409_not_a_500(client, monkeypatch):
    """writer._git_status_dirty used to let a
    structural git failure escape as a raw, unhandled exception -- a 500.
    apply_real now raises writer.GitCheckFailure for that case, and
    approve_request maps it to the same clear 409 shape as a genuinely
    dirty checkout."""
    record = _submit(client)

    def boom(*args, **kwargs):
        raise writer.GitCheckFailure("could not check whether the pack is a clean checkout: boom")

    monkeypatch.setattr(app_module.writer, "apply_real", boom)
    resp = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert resp.status_code == 409
    assert "could not check" in resp.json()["detail"]
    assert started == []


def test_a_member_directory_collision_is_a_409_not_a_500(client, monkeypatch):
    """_write_member's FileExistsError (the
    validated-key-collides-anyway race) used to escape apply_real as a raw,
    unhandled exception -- a 500. approve_request now maps
    writer.MemberCollisionError to a clear 409."""
    record = _submit(client)

    def boom(*args, **kwargs):
        raise writer.MemberCollisionError("configs/member-ptsb/ already exists")

    monkeypatch.setattr(app_module.writer, "apply_real", boom)
    resp = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    assert started == []


def test_a_dirty_checkout_refuses_the_approval(client):
    """spec S9's mitigation: a join must never stack on uncommitted work of
    unclear provenance."""
    record = _submit(client)
    (app_module.PACK_DIR / "manifest.yaml").write_text(
        (app_module.PACK_DIR / "manifest.yaml").read_text() + "\n# local edit\n"
    )
    resp = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert resp.status_code == 409
    assert "uncommitted" in resp.json()["detail"]
    assert started == []
