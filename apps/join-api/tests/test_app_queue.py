"""GET /requests (the operator queue) and
POST /requests/{id}/reject -- both part of the original API surface but
never built alongside submit/approve, and both genuinely needed by the console's join
tab (the pending queue, and reject-with-a-reason). Same fixture pattern as
test_app_approve.py: a temp copy of the pack inside a throwaway git repo,
because _record_view's uncommitted flag runs `git status --porcelain`."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-queue"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-queue"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

_spec = importlib.util.spec_from_file_location(
    "join_api_app_queue", pathlib.Path(__file__).resolve().parent.parent / "app.py"
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


# -- GET /requests (the queue) -------------------------------------------------


def test_list_requires_operator_token(client):
    _submit(client)
    resp = client.get("/requests", headers=APPLICANT)
    assert resp.status_code == 403
    resp = client.get("/requests", headers=OPERATOR)
    assert resp.status_code == 200


def test_list_is_empty_with_no_requests(client):
    resp = client.get("/requests", headers=OPERATOR)
    assert resp.json() == {"requests": []}


def test_list_carries_the_diff_and_a_step_summary(client):
    record = _submit(client)
    resp = client.get("/requests", headers=OPERATOR)
    body = resp.json()["requests"]
    assert len(body) == 1
    assert body[0]["id"] == record["id"]
    assert "configs/member-ptsb" in body[0]["diff"]
    # step ids come from job.build_sequence -- every step's actor is
    # "operator" for a hosted join (job.py's own docstring), but the
    # mechanism reads the field rather than hardcoding it.
    assert body[0]["steps"]
    assert all(s["actor"] == "operator" for s in body[0]["steps"])
    assert any(s["id"] == "cs.init" for s in body[0]["steps"])


def test_list_newest_first(client):
    first = _submit(client)
    second_payload = dict(
        code="PHIB",
        name="Progressa Housing Board",
        subsystem="HOUSING",
        subsystem_description="Housing records",
        security_server={"code": "SS-PHIB", "dns_name": "ss-phib", "hosted_on": "ss-plr"},
        backend={"auth": "none"},
        member_requirements={
            "has_security_server": True,
            "has_registered_identity": True,
            "standards_portfolio_adopted": True,
            "data_conformant": True,
            "lawful_basis": "consent",
            "technical_contact": "Jane Doe",
        },
    )
    second = client.post("/requests", json=second_payload, headers=APPLICANT).json()
    assert second["state"] == "SUBMITTED"

    body = client.get("/requests", headers=OPERATOR).json()["requests"]
    assert [r["id"] for r in body] == [second["id"], first["id"]]


def test_active_record_carries_the_uncommitted_flag(client):
    """A known gap, made visible: approved-and-written but not yet
    committed. apply_real() itself writes the files without committing --
    a fresh queue read right after approval must say so."""
    record = _submit(client)
    approve = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert approve.status_code == 202

    stored = app_module._load_request(record["id"])
    stored["state"] = "ACTIVE"
    app_module._save_request(stored)

    body = client.get("/requests", headers=OPERATOR).json()["requests"]
    entry = next(r for r in body if r["id"] == record["id"])
    assert entry["uncommitted"] is True  # written by approve, never committed

    _git("add", "-A", cwd=app_module.PACK_DIR.resolve().parents[2])
    _git("commit", "-q", "-m", "commit the join", cwd=app_module.PACK_DIR.resolve().parents[2])

    body_after = client.get("/requests", headers=OPERATOR).json()["requests"]
    entry_after = next(r for r in body_after if r["id"] == record["id"])
    assert entry_after["uncommitted"] is False


def test_uncommitted_check_failure_reads_as_unknown_not_committed(client, monkeypatch):
    """_live_uncommitted used to return False --
    "not dirty" -- on ANY git failure (missing binary, permission error,
    unexpected layout), which is exactly the value that suppresses the
    console's warning box. That would have silently swallowed the precise
    failure this check exists to catch (git missing from the image -- the
    same bug found live and fixed in the
    Dockerfile). None means "could not tell" and must never collapse to
    False. Faked as a subprocess raising, not a real missing git, so this
    test needs no image build and runs in --fast."""
    record = _submit(client)
    approve = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert approve.status_code == 202

    stored = app_module._load_request(record["id"])
    stored["state"] = "ACTIVE"
    app_module._save_request(stored)

    def boom(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(app_module.subprocess, "run", boom)

    body = client.get("/requests", headers=OPERATOR).json()["requests"]
    entry = next(r for r in body if r["id"] == record["id"])
    assert entry["uncommitted"] is None  # not False -- the check failed, it did not pass


def test_uncommitted_check_nonzero_git_exit_also_reads_as_unknown(client, monkeypatch):
    """A second, distinct failure mode: git runs but exits non-zero (e.g.
    "not a git repository") -- without check=True this would have returned
    an empty stdout, read as bool(False)="clean", the exact same
    wrong-direction bug via a different door."""
    record = _submit(client)
    approve = client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    assert approve.status_code == 202

    stored = app_module._load_request(record["id"])
    stored["state"] = "ACTIVE"
    app_module._save_request(stored)

    import subprocess as real_subprocess

    def fails(args, **kwargs):
        raise real_subprocess.CalledProcessError(128, args, output="", stderr="fatal: not a git repository")

    monkeypatch.setattr(app_module.subprocess, "run", fails)

    body = client.get("/requests", headers=OPERATOR).json()["requests"]
    entry = next(r for r in body if r["id"] == record["id"])
    assert entry["uncommitted"] is None


def test_rejected_record_carries_no_steps(client):
    resp = client.post(
        "/requests",
        json=dict(
            code="PDGA",
            name="x",
            subsystem="MGMT",
            subsystem_description="x",
            security_server={"code": "SS-X", "dns_name": "ss-x", "hosted_on": "ss-plr"},
            backend={"auth": "none"},
            member_requirements={
                "has_security_server": True,
                "has_registered_identity": True,
                "standards_portfolio_adopted": True,
                "data_conformant": True,
                "lawful_basis": "consent",
                "technical_contact": "Jane Doe",
            },
        ),
        headers=APPLICANT,
    )
    assert resp.json()["state"] == "REJECTED"
    body = client.get("/requests", headers=OPERATOR).json()["requests"]
    assert body[0]["state"] == "REJECTED"
    assert "steps" not in body[0]


# -- POST /requests/{id}/reject ------------------------------------------------


def test_reject_requires_operator_token(client):
    record = _submit(client)
    resp = client.post(f"/requests/{record['id']}/reject", json={"reason": "no"}, headers=APPLICANT)
    assert resp.status_code == 403


def test_reject_sets_rejected_with_the_given_reason(client):
    record = _submit(client)
    resp = client.post(f"/requests/{record['id']}/reject", json={"reason": "wrong subsystem"}, headers=OPERATOR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "REJECTED"
    assert body["rejection"] == {"check": "operator", "message": "wrong subsystem"}


def test_reject_without_a_reason_still_succeeds(client):
    record = _submit(client)
    resp = client.post(f"/requests/{record['id']}/reject", headers=OPERATOR)
    assert resp.status_code == 200
    assert resp.json()["rejection"]["message"] == "(no reason given)"


def test_reject_only_from_submitted(client):
    record = _submit(client)
    client.post(f"/requests/{record['id']}/approve", json=DECISION, headers=OPERATOR)
    resp = client.post(f"/requests/{record['id']}/reject", json={"reason": "too late"}, headers=OPERATOR)
    assert resp.status_code == 409


def test_reject_unknown_id_is_404(client):
    resp = client.post("/requests/does-not-exist/reject", json={"reason": "x"}, headers=OPERATOR)
    assert resp.status_code == 404


def test_reject_never_starts_a_job(client):
    record = _submit(client)
    client.post(f"/requests/{record['id']}/reject", json={"reason": "no"}, headers=OPERATOR)
    assert started == []
