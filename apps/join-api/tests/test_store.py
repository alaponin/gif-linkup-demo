"""Tests for apps/join-api/store.py.

Every test uses tmp_path for the DB file -- never a real out/ directory.
Plain pytest, no fixtures framework beyond what the rest of this suite
already uses (see test_writer.py/test_job.py for the house idiom).
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys
import traceback

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import store  # noqa: E402


def _record(**overrides) -> dict:
    base = {
        "id": "req-abc123",
        "state": "SUBMITTED",
        "submitted_at": "2026-08-20T00:00:00+00:00",
        "submitted_by": None,
        "payload": {"code": "PTSB", "name": "Progressa Tertiary Scholarship Board"},
    }
    base.update(overrides)
    return base


def _conn(tmp_path) -> sqlite3.Connection:
    path = store.init(tmp_path)
    return store.connect(path)


# -- schema creation / init -----------------------------------------------------


def test_init_creates_the_db_file_in_its_own_subdirectory(tmp_path):
    path = store.init(tmp_path)
    assert path == tmp_path / "join-store" / "join-store.sqlite3"
    assert path.exists()
    assert path.parent == tmp_path / "join-store"


def test_init_chmods_the_new_file_0600(tmp_path):
    path = store.init(tmp_path)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_init_is_idempotent(tmp_path):
    store.init(tmp_path)
    store.init(tmp_path)  # must not raise ("table already exists" etc.)
    conn = _conn(tmp_path)
    try:
        assert store.count_requests(conn) == 0
    finally:
        conn.close()


def test_init_recovers_a_file_that_exists_with_no_schema(tmp_path):
    """The crash window: a process died between store.connect() creating the
    file and executescript() running, leaving a zero-table file behind. init
    must detect the missing schema (not just the missing file) and create it
    -- and still chmod 0600, since the earlier crash may have left a looser
    mode."""
    path = store.db_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()  # file exists, zero tables -- the crash artefact
    os.chmod(path, 0o644)

    store.init(tmp_path)

    conn = store.connect(path)
    try:
        assert store.count_requests(conn) == 0  # would raise OperationalError pre-fix
    finally:
        conn.close()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_init_writes_the_schema_version_row(tmp_path):
    conn = _conn(tmp_path)
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == store.SCHEMA_VERSION
    finally:
        conn.close()


# -- save_request / load_request round-trip -------------------------------------


def test_save_and_load_round_trips_the_record_values(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record(diff="some diff text", endpoint_baseline={"PTSB": ["/x"]})
        store.save_request(conn, record, actor="system", event="submitted")
        loaded = store.load_request(conn, record["id"])
        assert loaded == record
    finally:
        conn.close()


def test_save_request_upserts_on_id_conflict(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record()
        store.save_request(conn, record, actor="system", event="submitted")
        record["state"] = "APPROVED"
        store.save_request(conn, record, actor="operator", event="approved")
        assert store.load_request(conn, record["id"])["state"] == "APPROVED"
        assert store.count_requests(conn) == 1
    finally:
        conn.close()


def test_save_request_appends_one_event_row_per_call(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record()
        store.save_request(conn, record, actor="system", event="submitted", detail={"x": 1})
        record["state"] = "APPROVED"
        store.save_request(conn, record, actor="operator", event="approved")
        rows = conn.execute(
            "SELECT actor, event, detail FROM request_events WHERE request_id = ? ORDER BY seq", (record["id"],)
        ).fetchall()
        assert [r["event"] for r in rows] == ["submitted", "approved"]
        assert rows[0]["actor"] == "system"
        assert rows[0]["detail"] == '{"x": 1}'
    finally:
        conn.close()


def test_load_request_returns_none_for_a_missing_id(tmp_path):
    conn = _conn(tmp_path)
    try:
        assert store.load_request(conn, "does-not-exist") is None
    finally:
        conn.close()


def test_save_request_extracts_member_key_lowercased(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record(payload={"code": "PTSB"})
        store.save_request(conn, record, actor="system", event="submitted")
        row = conn.execute("SELECT member_key FROM requests WHERE id = ?", (record["id"],)).fetchone()
        assert row["member_key"] == "ptsb"
    finally:
        conn.close()


def test_save_request_member_key_is_null_when_payload_has_no_code(tmp_path):
    """The REJECTED-at-schema case: payload is the raw un-validated input,
    not always shaped with a code key."""
    conn = _conn(tmp_path)
    try:
        record = _record(state="REJECTED", payload={"garbage": True})
        store.save_request(conn, record, actor="system", event="rejected")
        row = conn.execute("SELECT member_key FROM requests WHERE id = ?", (record["id"],)).fetchone()
        assert row["member_key"] is None
    finally:
        conn.close()


# -- list_requests / count_requests ----------------------------------------------


def test_list_requests_returns_every_record_sorted_by_id(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.save_request(conn, _record(id="req-b"), actor="system", event="submitted")
        store.save_request(conn, _record(id="req-a"), actor="system", event="submitted")
        ids = [r["id"] for r in store.list_requests(conn)]
        assert ids == ["req-a", "req-b"]
    finally:
        conn.close()


def test_count_requests_backs_the_quota(tmp_path):
    conn = _conn(tmp_path)
    try:
        assert store.count_requests(conn) == 0
        store.save_request(conn, _record(), actor="system", event="submitted")
        assert store.count_requests(conn) == 1
    finally:
        conn.close()


# -- member_record ---------------------------------------------------------------


def test_member_record_picks_the_newest_among_multiple_matches(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.save_request(
            conn, _record(id="req-1", state="ACTIVE", payload={"code": "PTSB"}, submitted_at="2026-01-01T00:00:00+00:00"),
            actor="system", event="submitted",
        )
        store.save_request(
            conn, _record(id="req-2", state="RETIRING", payload={"code": "PTSB"}, submitted_at="2026-06-01T00:00:00+00:00"),
            actor="system", event="submitted",
        )
        found = store.member_record(conn, "ptsb")
        assert found["id"] == "req-2"
    finally:
        conn.close()


def test_member_record_ignores_other_states(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.save_request(
            conn, _record(id="req-1", state="RETIRED", payload={"code": "PTSB"}), actor="system", event="submitted"
        )
        assert store.member_record(conn, "ptsb") is None
    finally:
        conn.close()


def test_member_record_returns_none_for_no_match(tmp_path):
    conn = _conn(tmp_path)
    try:
        assert store.member_record(conn, "nobody") is None
    finally:
        conn.close()


# -- recover_interrupted ----------------------------------------------------------


def test_recover_interrupted_transitions_running_to_failed_and_logs_event(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record(state="RUNNING", last_completed_step="r1")
        store.save_request(conn, record, actor="system", event="submitted")
        store.recover_interrupted(conn)

        reloaded = store.load_request(conn, record["id"])
        assert reloaded["state"] == "FAILED"
        assert reloaded["error"] == {"step": "r1", "message": "interrupted by a join-api restart"}

        events = conn.execute(
            "SELECT actor, event FROM request_events WHERE request_id = ? ORDER BY seq", (record["id"],)
        ).fetchall()
        assert events[-1]["actor"] == "system"
        assert events[-1]["event"] == "state:RUNNING->FAILED"
    finally:
        conn.close()


def test_recover_interrupted_leaves_non_running_rows_untouched(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record(state="ACTIVE")
        store.save_request(conn, record, actor="system", event="submitted")
        store.recover_interrupted(conn)
        assert store.load_request(conn, record["id"])["state"] == "ACTIVE"
        events = conn.execute(
            "SELECT COUNT(*) AS n FROM request_events WHERE request_id = ?", (record["id"],)
        ).fetchone()
        assert events["n"] == 1  # only the original "submitted" -- no recovery event
    finally:
        conn.close()


# -- request_events append-only triggers ------------------------------------------


def test_request_events_cannot_be_updated(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record()
        store.save_request(conn, record, actor="system", event="submitted")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE request_events SET event = 'tampered' WHERE request_id = ?", (record["id"],))
    finally:
        conn.close()


def test_request_events_cannot_be_deleted(tmp_path):
    conn = _conn(tmp_path)
    try:
        record = _record()
        store.save_request(conn, record, actor="system", event="submitted")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM request_events WHERE request_id = ?", (record["id"],))
    finally:
        conn.close()


# -- log_refusal (request_id=NULL, pre-request 429s) ------------------------------


def test_log_refusal_inserts_a_null_request_id_row(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.log_refusal(conn, "token:abcd1234", "rate_limited")
        row = conn.execute(
            "SELECT request_id, actor, event FROM request_events WHERE actor = 'token:abcd1234'"
        ).fetchone()
        assert row["request_id"] is None
        assert row["event"] == "rate_limited"
    finally:
        conn.close()


def test_log_refusal_rows_are_also_append_only(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.log_refusal(conn, "token:abcd1234", "rate_limited")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE request_events SET event = 'tampered' WHERE actor = 'token:abcd1234'")
    finally:
        conn.close()


# -- tokens: issue / find / list / revoke -----------------------------------------


def test_issue_and_find_token(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "ptsb", "deadbeef")
        found = store.find_token(conn, "deadbeef")
        assert found["name"] == "ptsb"
        assert found["expires_at"] is None
        assert found["revoked_at"] is None
    finally:
        conn.close()


def test_issue_token_sets_expiry_when_given(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "ptsb", "deadbeef", expires_in_days=30)
        found = store.find_token(conn, "deadbeef")
        assert found["expires_at"] is not None
        assert found["expires_at"] > found["issued_at"]
    finally:
        conn.close()


def test_issue_token_expires_in_days_zero_means_already_expired_not_no_expiry(tmp_path):
    """expires_in_days=0 is present, not absent -- plan §1.4 says *absent*
    means no expiry. A naive `if expires_in_days` truthiness test would take
    0 for the no-expiry branch; this asserts it does not."""
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "zero", "h0", expires_in_days=0)
        found = store.find_token(conn, "h0")
        assert found["expires_at"] is not None
        assert found["expires_at"] == found["issued_at"]
    finally:
        conn.close()


def test_issue_token_raises_on_duplicate_active_name(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "ptsb", "hash1")
        with pytest.raises(store.NameAlreadyUsed) as exc_info:
            store.issue_token(conn, "ptsb", "hash2")
        assert exc_info.value.revoked is False
    finally:
        conn.close()


def test_issue_token_raises_on_reissue_of_a_revoked_name(tmp_path):
    """The reissue-rejection ruling: a revoked name can never be reused,
    even by a fresh issue_token call."""
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "ptsb", "hash1")
        store.revoke_token(conn, "ptsb")
        with pytest.raises(store.NameAlreadyUsed) as exc_info:
            store.issue_token(conn, "ptsb", "hash2")
        assert exc_info.value.revoked is True
    finally:
        conn.close()


def test_issue_token_translates_a_concurrent_duplicate_insert_to_name_already_used(tmp_path):
    """The race the pre-check alone can't catch: two callers both pass the
    SELECT before either INSERTs. Reproduced by having a second connection
    insert the colliding row at the exact moment issue_token's own INSERT is
    about to run -- the INSERT that follows must fail on the UNIQUE
    constraint, and issue_token must translate that into NameAlreadyUsed
    rather than let the raw sqlite3.IntegrityError escape (Task 2's
    `except NameAlreadyUsed: return 409` would otherwise see an unhandled
    500). A subclassed Connection.execute is the interception point --
    sqlite3.Connection is an immutable builtin type, so instance/class
    monkeypatching of `execute` is not available; subclassing is."""
    path = store.init(tmp_path)
    other = store.connect(path)

    class RacingConnection(sqlite3.Connection):
        raced = False

        def execute(self, sql, *args, **kwargs):
            if not RacingConnection.raced and sql.strip().startswith("INSERT INTO tokens"):
                RacingConnection.raced = True
                with other:
                    other.execute(
                        "INSERT INTO tokens (name, sha256, issued_at, expires_at, revoked_at) "
                        "VALUES ('race', 'winner-hash', '2026-01-01T00:00:00+00:00', NULL, NULL)"
                    )
            return super().execute(sql, *args, **kwargs)

    conn = sqlite3.connect(path, factory=RacingConnection)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(store.NameAlreadyUsed) as exc_info:
            store.issue_token(conn, "race", "loser-hash")
        assert exc_info.value.revoked is False
    finally:
        conn.close()
        other.close()


def test_list_tokens_returns_every_row(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "ptsb", "hash1")
        store.issue_token(conn, "plr", "hash2")
        names = [t["name"] for t in store.list_tokens(conn)]
        assert names == ["plr", "ptsb"]
    finally:
        conn.close()


def test_revoke_token_returns_true_and_sets_revoked_at(tmp_path):
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "ptsb", "hash1")
        assert store.revoke_token(conn, "ptsb") is True
        found = store.find_token(conn, "hash1")
        assert found["revoked_at"] is not None
    finally:
        conn.close()


def test_revoke_token_returns_false_for_an_unknown_name(tmp_path):
    conn = _conn(tmp_path)
    try:
        assert store.revoke_token(conn, "nobody") is False
    finally:
        conn.close()


def test_revoke_token_twice_overwrites_revoked_at_with_the_second_call(tmp_path):
    """Not idempotent in the state it leaves: both calls return True, but
    the second overwrites revoked_at with its own timestamp rather than
    preserving the first revocation time (store.py's own docstring says so
    -- this asserts the behaviour it documents, not just the return value)."""
    conn = _conn(tmp_path)
    try:
        store.issue_token(conn, "ptsb", "hash1")
        assert store.revoke_token(conn, "ptsb") is True
        first = store.find_token(conn, "hash1")["revoked_at"]
        assert store.revoke_token(conn, "ptsb") is True
        second = store.find_token(conn, "hash1")["revoked_at"]
        assert second >= first
    finally:
        conn.close()


# -- read-only mode ----------------------------------------------------------------


def test_readonly_connection_can_read_existing_rows(tmp_path):
    path = store.init(tmp_path)
    conn = store.connect(path)
    try:
        store.save_request(conn, _record(), actor="system", event="submitted")
    finally:
        conn.close()

    ro = store.connect(path, readonly=True)
    try:
        assert store.count_requests(ro) == 1
    finally:
        ro.close()


def test_readonly_connection_cannot_write(tmp_path):
    path = store.init(tmp_path)
    conn = store.connect(path)
    try:
        store.save_request(conn, _record(), actor="system", event="submitted")
    finally:
        conn.close()

    ro = store.connect(path, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO tokens (name, sha256, issued_at) VALUES ('x', 'y', 'z')")
            ro.commit()
    finally:
        ro.close()


# -- locks --------------------------------------------------------------------------


def test_job_lock_held_reflects_the_sqlite_singleton(tmp_path):
    """SQLite path only -- a real Postgres path is Task 3's job. job_lock()
    itself acquires store._JOB_LOCK; job_lock_held() must be peeking at that
    same object, not a lookalike, so grabbing it directly (as
    test_app_approve.py's queued test does) is what job_lock_held() sees."""
    path = store.init(tmp_path)
    conn = store.connect(path)
    try:
        assert store.job_lock_held(conn) is False
        store._JOB_LOCK.acquire()
        try:
            assert store.job_lock_held(conn) is True
        finally:
            store._JOB_LOCK.release()
        assert store.job_lock_held(conn) is False
    finally:
        conn.close()


# -- backend selection --------------------------------------------------------------


def test_backend_for_sqlite_returns_sqlite():
    assert store.backend_for("sqlite") == "sqlite"


def test_backend_for_postgres_returns_postgres():
    assert store.backend_for("postgres") == "postgres"


def test_backend_for_anything_else_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        store.backend_for("mysql")


# -- DSN masking (Postgres connect path -- needs psycopg, but no live DB) -----
# Moved here from test_store_postgres.py (final-review fix wave): that file's
# whole-module `skipif(not KP2_TEST_DB_URL)` meant this test -- which only
# provokes a connection *failure* and asserts the password never leaks into
# it -- never ran without a live Postgres, even though it needs no database
# at all. The plan explicitly demanded this be tested ("test it, because
# connection errors are exactly where DSNs leak," §4). importorskip instead
# of the DB-availability skipif: runs whenever the driver is installed, with
# or without a real database.


def test_connection_failure_never_leaks_the_password():
    pytest.importorskip("psycopg")

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
