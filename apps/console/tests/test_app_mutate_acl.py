"""Regression test for a bug found live while investigating UX plan Task 8:
_mutate_acl() used to infer prior_state as "the opposite of the requested
action" instead of reading the actual live state. Calling grant() when
already granted (idempotent-safe at the X-Road layer, xroad.py's 409
handling) journalled a false transition, and reset()'s reversal then
corrupted the real ACL. No network, no Docker -- PACK_DIR points at the
existing test fixtures; the admin session is monkeypatched."""
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["PACK_DIR"] = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "full")
os.environ["OUT_DIR"] = "/tmp"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app  # noqa: E402


class _FakeSession:
    """Models one subject's grants on one client -- enough for _mutate_acl
    and journal.reset()'s verification, which reads read_subjects() (any
    grant at all) rather than read_acl() (which service codes)."""

    def __init__(self, live_service_codes, subject="PROGRESSA:GOV:PNEA:EXAMS"):
        self._live = live_service_codes
        self._subject = subject

    def read_acl(self, client_id, subject_id):
        return self._live

    def read_subjects(self, client_id):
        return [self._subject] if self._live else []

    def grant(self, client_id, subject_id, service_code):
        self._live = list({*self._live, service_code})

    def revoke(self, client_id, subject_id, service_code):
        self._live = [s for s in self._live if s != service_code]


class _SlowFakeSession(_FakeSession):
    """Sleeps inside grant/revoke to widen the read-modify-write window --
    enough for two real OS threads to interleave without the lock."""

    def __init__(self, *args, delay=0.05, **kwargs):
        super().__init__(*args, **kwargs)
        self._delay = delay

    def grant(self, client_id, subject_id, service_code):
        time.sleep(self._delay)
        super().grant(client_id, subject_id, service_code)

    def revoke(self, client_id, subject_id, service_code):
        time.sleep(self._delay)
        super().revoke(client_id, subject_id, service_code)


def test_grant_when_already_granted_journals_correct_prior_state(monkeypatch, tmp_path):
    app.JOURNAL = app.journal_mod.Journal(tmp_path / "journal.json")
    fake = _FakeSession(["identity-api"])
    monkeypatch.setattr(app, "_admin_session", lambda host: fake)

    result = app._mutate_acl("grant")

    assert result == {"ok": True, "action": "grant", "service_code": "identity-api"}
    entries = app.JOURNAL.entries()
    assert len(entries) == 1
    assert entries[0]["prior_state"] == "granted"  # the true live state, not "revoked"


def test_revoke_when_already_revoked_journals_correct_prior_state(monkeypatch, tmp_path):
    app.JOURNAL = app.journal_mod.Journal(tmp_path / "journal.json")
    fake = _FakeSession([])
    monkeypatch.setattr(app, "_admin_session", lambda host: fake)

    app._mutate_acl("revoke")

    entries = app.JOURNAL.entries()
    assert entries[0]["prior_state"] == "revoked"  # the true live state, not "granted"


def test_redundant_grant_then_reset_leaves_correct_final_state(monkeypatch, tmp_path):
    """The exact bug trigger: grant while already granted, then reset --
    must NOT flip the real state to revoked. reset() verifies every
    expected_acl service, so each host needs its own fake session matching
    the fixture's real configured state (identity/enrolment granted,
    pemis not)."""
    app.JOURNAL = app.journal_mod.Journal(tmp_path / "journal.json")
    sessions = {
        "ss-pnia": _FakeSession(["identity-api"]),
        "ss-plr": _FakeSession(["enrolment-api"]),
        "ss-moeys": _FakeSession([]),
    }
    monkeypatch.setattr(app, "_admin_session", lambda host: sessions[host])

    app._mutate_acl("grant")
    result = app.journal_mod.reset(
        app.JOURNAL, lambda host: sessions[host], app.TRUTH.expected_acl, app.TRUTH.topology
    )

    assert result["ok"] is True
    assert "identity-api" in sessions["ss-pnia"].read_acl(None, None)


def test_concurrent_mutations_do_not_lose_a_journal_entry(monkeypatch, tmp_path):
    """S16: append_pending/mark_applied are read-modify-write with no lock
    of their own, and _mutate_acl is reached from a `def` (not `async def`)
    endpoint, so FastAPI runs it in a threadpool -- two concurrent POSTs
    genuinely interleave. Two real OS threads, not asyncio tasks, because
    the race is a threading race: FastAPI's threadpool, not the event
    loop, is what makes two _mutate_acl calls run concurrently."""
    app.JOURNAL = app.journal_mod.Journal(tmp_path / "journal.json")
    fake = _SlowFakeSession(["identity-api"])
    monkeypatch.setattr(app, "_admin_session", lambda host: fake)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(app._mutate_acl, "revoke"), pool.submit(app._mutate_acl, "grant")]
        for f in futures:
            f.result()

    assert len(app.JOURNAL.entries()) == 2
