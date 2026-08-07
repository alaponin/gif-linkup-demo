"""Regression test for a bug found live while investigating UX plan Task 8:
_mutate_acl() used to infer prior_state as "the opposite of the requested
action" instead of reading the actual live state. Calling grant() when
already granted (idempotent-safe at the X-Road layer, xroad.py's 409
handling) journalled a false transition, and reset()'s reversal then
corrupted the real ACL. No network, no Docker -- PACK_DIR points at the
existing test fixtures; the admin session is monkeypatched."""
import asyncio
import os
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

os.environ["PACK_DIR"] = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "pack")
os.environ["OUT_DIR"] = "/tmp"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

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


def test_health_answers_while_a_reset_is_in_progress(monkeypatch, tmp_path):
    """S17: reset() performs several blocking HTTPS logins -- run inline
    inside an async coroutine (the pre-fix watchdog/lifespan code), the
    single-threaded event loop stops entirely for that whole period, and
    NOTHING answers, /api/health included. This is the assertion that
    actually encodes the finding: a test that only checks
    asyncio.to_thread was called tests the fix, not the behaviour.

    Elapsed wall-clock time, not a cooperative asyncio timeout, is what
    catches a starved loop here -- confirmed live while writing this test:
    asyncio.wait_for's own timeout callback is scheduled on the SAME loop
    it would need to detect as stuck, so it cannot fire while the loop is
    starved; it only resolves once the rogue synchronous call finally
    returns, at which point the (by-then-unblocked) request completes
    "successfully" regardless of how long the loop was actually stuck.
    Measuring total elapsed time from before the reset starts is what
    actually distinguishes a live loop from a starved one that just
    happened to unstick before an unbounded wait gave up. Confirmed this
    test fails on the pre-fix inline call (15s elapsed, not under 1s)
    before keeping it."""
    app.JOURNAL = app.journal_mod.Journal(tmp_path / "journal.json")
    release = threading.Event()

    class BlockingSession:
        def read_subjects(self, client_id):
            release.wait(timeout=5)
            return []

    monkeypatch.setattr(app, "_admin_session", lambda host: BlockingSession())

    async def scenario():
        transport = httpx.ASGITransport(app=app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = time.monotonic()
            reset_task = asyncio.create_task(asyncio.to_thread(app._reset_locked))
            # A fixed, short head start -- enough for the offloaded thread
            # to reach the blocking read_subjects call when the loop is
            # actually free to run this sleep promptly; not contingent on
            # any signal from the reset itself, since a signal awaited
            # cooperatively would suffer the exact same starvation this
            # test is checking for.
            await asyncio.sleep(0.1)
            resp = await client.get("/api/health")
            elapsed = time.monotonic() - start

            release.set()
            await reset_task

            assert resp.status_code == 200
            assert elapsed < 1.0, f"health took {elapsed:.2f}s -- event loop was starved by the reset"

    asyncio.run(scenario())


def test_lifespan_shutdown_does_not_hang_with_a_dirty_journal(monkeypatch, tmp_path):
    """S17 Step 3: watchdog_task.cancel() (and the startup reset task's own
    .cancel()) cannot interrupt a thread already inside asyncio.to_thread --
    Python threads are not preemptible. Confirms shutdown itself does not
    wait for that thread: cancelling a task awaiting to_thread raises
    CancelledError at the await promptly, even though the underlying OS
    thread keeps running reset() to completion in the background, harmlessly,
    under the same lock.

    A fixed short sleep, not an Event some other step must remember to
    set: asyncio.run()'s own cleanup gathers every still-pending task
    before returning, including one it could not actually cancel (a
    concurrent.futures.Future already running cannot be cancelled, so the
    wrapped asyncio future just waits for it) -- an Event that needed
    releasing from outside would deadlock against that cleanup, found
    live while writing this test (it hung for 15s -- 5s per expected_acl
    service -- before this fix)."""
    app.JOURNAL = app.journal_mod.Journal(tmp_path / "journal.json")
    app.JOURNAL.append_pending(app.journal_mod.JournalEntry(
        ts=1.0, action="revoke", ss="ss-pnia", client_id="PROGRESSA:GOV:PNIA:IDENTITY",
        subject="PROGRESSA:GOV:PNEA:EXAMS", service_code="identity-api", prior_state="granted",
    ))

    class SlowSession:
        def read_subjects(self, client_id):
            time.sleep(0.1)
            return []

        def read_acl(self, client_id, subject_id):
            return []

        def grant(self, *a):
            pass

        def revoke(self, *a):
            pass

    monkeypatch.setattr(app, "_admin_session", lambda host: SlowSession())

    async def scenario():
        start = time.monotonic()
        async with app._lifespan(app.app):
            await asyncio.sleep(0.05)  # let the startup reset task actually start
        return time.monotonic() - start

    elapsed = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert elapsed < 1.0, f"shutdown took {elapsed:.2f}s -- cancel() waited on the blocked thread"
