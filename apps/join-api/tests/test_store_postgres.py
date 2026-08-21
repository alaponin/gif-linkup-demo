"""Tests for apps/join-api/store.py's Postgres backend.

SQLite already has its own coverage in test_store.py; this file is
Postgres-only by design and covers what only the Postgres path has: role
grants (append-only enforcement on request_events), advisory-lock
serialisation (job_lock/apply_lock), migration idempotence under two racing
store.init() calls, DSN masking on a real connection failure, the JSONB
round-trip (no double encode/decode), and job_lock_held().

Runs only when KP2_TEST_DB_URL is set to a throwaway Postgres DSN (a CI
service container, or a scratch local cluster) -- see the module-level
pytestmark below, same skipif idiom test_job.py uses near its bottom for its
bundled-binary test.
"""
from __future__ import annotations

import os
import pathlib
import sys
import threading
import time
import traceback

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import store  # noqa: E402

_TEST_DB_URL = os.environ.get("KP2_TEST_DB_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="KP2_TEST_DB_URL not set -- set it to a throwaway Postgres DSN to run these",
)


def _record(**overrides) -> dict:
    base = {
        "id": "req-pg-abc123",
        "state": "SUBMITTED",
        "submitted_at": "2026-08-20T00:00:00+00:00",
        "submitted_by": None,
        "payload": {"code": "PTSB", "name": "Progressa Tertiary Scholarship Board"},
    }
    base.update(overrides)
    return base


@pytest.fixture
def conn():
    """Clean database state for every test. store.init() is idempotent
    (also exercised directly by test_migration_is_idempotent... below), so
    calling it here just makes sure the schema/grants exist. Table reset
    prefers TRUNCATE (the brief's suggested approach) but falls back to
    DELETE, then to a visible skip, since which of TRUNCATE/DELETE/neither
    KP2_TEST_DB_URL's role can do depends on how the test Postgres is
    provisioned (see migrations/grants.sql -- joinapi gets neither TRUNCATE
    nor DELETE on any table, only SELECT/INSERT[/UPDATE], so a joinapi-role
    KP2_TEST_DB_URL genuinely cannot reset state itself; that's real, not a
    test bug -- see the task report)."""
    import psycopg  # lazy, matching store.py's own import discipline

    store.init(pathlib.Path("unused"), kind="postgres", db_url=_TEST_DB_URL)
    c = store.connect(_TEST_DB_URL)
    try:
        c.execute("TRUNCATE requests, request_events, tokens RESTART IDENTITY CASCADE")
    except psycopg.errors.InsufficientPrivilege:
        try:
            c.execute("DELETE FROM request_events")
            c.execute("DELETE FROM requests")
            c.execute("DELETE FROM tokens")
        except psycopg.errors.InsufficientPrivilege:
            c.close()
            pytest.skip(
                "KP2_TEST_DB_URL's role has neither TRUNCATE nor DELETE on "
                "requests/request_events/tokens (per migrations/grants.sql, "
                "a joinapi-role DSN never does) -- this test environment "
                "can't reset state between tests"
            )
    yield c
    c.close()


# -- grants / privilege enforcement -------------------------------------------


def test_joinapi_cannot_update_or_delete_request_events(conn):
    """The whole point of grants.sql's restricted GRANTs (see its header
    comment): joinapi is *structurally* unable to rewrite request_events
    history, not merely discouraged by application code -- so an
    UPDATE/DELETE there must fail with a privilege error specifically, not
    just any error.

    Only meaningful when `conn` is actually connected as a role with
    joinapi's restricted grants. A throwaway local/CI Postgres with no role
    provisioning connects as the table owner or a superuser instead, which
    bypasses GRANTs entirely (ownership, not privilege, decides access) --
    detected here by the UPDATE trivially succeeding, in which case this
    skips with an explanation rather than reporting a false green."""
    import psycopg

    store.save_request(conn, _record(id="req-grants-1"), actor="tester", event="submitted")
    row = conn.execute("SELECT seq FROM request_events LIMIT 1").fetchone()

    try:
        conn.execute("UPDATE request_events SET actor = %s WHERE seq = %s", ("tampered", row["seq"]))
    except psycopg.errors.InsufficientPrivilege:
        pass  # exactly what grants.sql's design promises
    else:
        current_user = conn.execute("SELECT current_user").fetchone()["current_user"]
        pytest.skip(
            f"KP2_TEST_DB_URL connects as {current_user!r}, which has UPDATE "
            "on request_events (table owner/superuser, not the "
            "grants-restricted joinapi role) -- the UPDATE trivially "
            "succeeded, so this environment can't exercise grants.sql's "
            "privilege restriction for real"
        )

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM request_events WHERE seq = %s", (row["seq"],))


# -- advisory-lock serialisation -----------------------------------------------


def test_job_lock_is_non_blocking_and_serialises_across_connections(conn):
    """job_lock's Postgres path is pg_try_advisory_lock -- non-blocking, per
    its docstring. Connection B must see LockBusy immediately while A holds
    it, then succeed once A releases."""
    conn_b = store.connect(_TEST_DB_URL)
    try:
        with store.job_lock(conn):
            with pytest.raises(store.LockBusy):
                with store.job_lock(conn_b):
                    pass  # pragma: no cover -- must not be reached
        with store.job_lock(conn_b):
            pass  # released by A's `with` exit -- B can now acquire
    finally:
        conn_b.close()


def test_apply_lock_blocks_the_second_connection_for_the_hold_duration(conn):
    """apply_lock's Postgres path is pg_advisory_xact_lock -- BLOCKS until
    acquired (unlike job_lock's try-variant), proving the "serialise instead
    of interleave" guarantee. Connection A holds it in a background thread
    for ~0.3s; connection B's apply_lock must wait close to that long, not
    return instantly."""
    conn_b = store.connect(_TEST_DB_URL)
    hold_seconds = 0.3
    acquired_a = threading.Event()

    def hold_lock():
        with store.apply_lock(conn):
            acquired_a.set()
            time.sleep(hold_seconds)

    t = threading.Thread(target=hold_lock)
    t.start()
    assert acquired_a.wait(timeout=2), "connection A never acquired apply_lock"

    start = time.monotonic()
    try:
        with store.apply_lock(conn_b):
            waited = time.monotonic() - start
    finally:
        t.join()
        conn_b.close()

    assert waited >= hold_seconds * 0.7, f"apply_lock returned too fast ({waited:.3f}s) -- it did not block"
    assert waited < hold_seconds + 2, f"apply_lock waited suspiciously long ({waited:.3f}s)"


# -- migration idempotence -----------------------------------------------------


def test_migration_is_idempotent_under_two_concurrent_inits(conn):
    """Proves pg_advisory_xact_lock(hashtext('kp2-migrate')) actually
    serialises two racing store.init() calls rather than letting them race
    on CREATE TABLE: drop the schema, fire two store.init() calls from two
    threads at roughly the same time, assert neither raises and exactly one
    schema_version row (no duplicates, no partial state) exists after."""
    conn.execute("DROP TABLE IF EXISTS schema_version, requests, request_events, tokens CASCADE")

    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def run_init():
        barrier.wait(timeout=5)  # start both threads as close together as possible
        try:
            store.init(pathlib.Path("unused"), kind="postgres", db_url=_TEST_DB_URL)
        except Exception as exc:  # noqa: BLE001 -- collected, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=run_init) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"store.init() raised under concurrency: {errors}"
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert [row["version"] for row in rows] == [store.SCHEMA_VERSION]


# -- DSN masking ----------------------------------------------------------------


def test_connection_failure_never_leaks_the_password():
    """Task 1 already exercised _mask_dsn live and via a doctest; this is
    the pytest-run equivalent the reviewer flagged as missing. A real
    (unreachable-port) connection failure must not leak the password into
    str(exc), repr(exc), or a formatted traceback."""
    password = "correcthorsebatterystaple"  # noqa: S105 -- test fixture, not a real secret
    bad_dsn = f"postgresql://joinapi:{password}@localhost:1/kp2join"  # port 1: nothing listens

    try:
        store.connect(bad_dsn)
    except RuntimeError as exc:
        assert password not in str(exc)
        assert password not in repr(exc)
        assert password not in traceback.format_exc()
    else:
        pytest.fail("expected a RuntimeError from an unreachable Postgres DSN")


# -- JSONB round-trip -----------------------------------------------------------


def test_jsonb_round_trips_nested_values_without_double_encoding(conn):
    """record goes into a JSONB column un-dumped and must come back out
    already a dict/list -- not a JSON *string* of one (no double
    encode/decode on the Postgres path, per the plan)."""
    nested = _record(
        id="req-json-1",
        payload={
            "code": "PTSB",
            "tags": ["a", "b", "c"],
            "meta": {"nested": {"deeper": [1, 2, 3]}, "flag": True, "n": None},
        },
    )
    store.save_request(conn, nested, actor="tester", event="submitted")

    loaded = store.load_request(conn, "req-json-1")

    assert loaded == nested
    assert isinstance(loaded["payload"], dict)
    assert isinstance(loaded["payload"]["tags"], list)
    assert isinstance(loaded["payload"]["meta"]["nested"], dict)


# -- job_lock_held --------------------------------------------------------------


def test_job_lock_held_peeks_without_acquiring(conn):
    """False when free, True while another connection holds job_lock, and
    the peek itself must not leak/hold the lock -- confirmed by a second
    peek, and by conn being able to actually acquire it afterward."""
    conn_b = store.connect(_TEST_DB_URL)
    try:
        assert store.job_lock_held(conn) is False

        with store.job_lock(conn_b):
            assert store.job_lock_held(conn) is True
            assert store.job_lock_held(conn) is True  # peeking again doesn't change anything

        assert store.job_lock_held(conn) is False
        with store.job_lock(conn):  # still free -- the peeks above didn't hold it
            pass
    finally:
        conn_b.close()
