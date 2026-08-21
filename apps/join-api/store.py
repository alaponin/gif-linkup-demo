"""apps/join-api/store.py -- the repository seam for the join API's
persistent state: request records, their audit trail, and issued tokens.
Two backends exist: SQLite (`out/join-store/join-store.sqlite3`, the
original -- see docs/plans/join-datastore-sqlite-plan.md) and Postgres (see
docs/plans/join-datastore-postgres-digitalocean-plan.md). Dispatch between
them is by **connection type**
(`isinstance(conn, sqlite3.Connection)` vs everything else, i.e. a psycopg
`Connection`) -- not a stored "current backend" global -- because every
function already receives an open connection, and the connection object
already carries that information.

**The join-api process is the sole writer while it is running.** Host-side
tools (scripts/member.sh drift, `python -m store`) open read-only where the
backend supports it: SQLite via `connect(readonly=True)` -- a
`file:...?mode=ro` URI connection, which SQLite refuses writes on at the
OS/file level, independent of anything this module does (plan §1.3);
Postgres by connecting as the `joinapi_ro` role (a GRANT-level restriction,
enforced by the server, not by this module).

Nothing in this module does the id/name charset checks (`_REQUEST_ID_RE`,
`_TOKEN_NAME_RE`) -- those stay in app.py as trust-boundary checks on
caller input, same division of responsibility as today. This module only
guarantees parameterised SQL.

**Import discipline**: `psycopg`/`yaml` are NOT imported at module level --
two existing host-side pure-SQLite consumers (`scripts/migrate-join-store.py`,
repo-root `tests/test_member_drift.py`) already `import store` expecting it
to stay usable with only the stdlib on a host that never touches Postgres.
Every function that needs `psycopg` or `yaml` does its own `import` as its
first line instead -- cheap after the first real import (cached in
sys.modules), and it means `import store` and every SQLite-path function
still work with neither package installed; only actually calling into a
Postgres-only path raises (a plain ModuleNotFoundError naming the missing
package, exactly when that path is used, not before).
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE requests (
  id           TEXT PRIMARY KEY,
  state        TEXT NOT NULL CHECK (state IN
               ('REJECTED','SUBMITTED','APPROVED','RUNNING','BLOCKED',
                'FAILED','ACTIVE','RETIRING','RETIRED')),
  submitted_at TEXT NOT NULL,
  submitted_by TEXT,
  member_key   TEXT,
  record       TEXT NOT NULL
);
CREATE INDEX requests_by_member ON requests (member_key, state, submitted_at);
CREATE INDEX requests_by_state  ON requests (state);

CREATE TABLE request_events (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT REFERENCES requests(id),
  at         TEXT NOT NULL,
  actor      TEXT NOT NULL,
  event      TEXT NOT NULL,
  detail     TEXT
);
CREATE TRIGGER request_events_no_update BEFORE UPDATE ON request_events
  BEGIN SELECT RAISE(ABORT, 'request_events is append-only'); END;
CREATE TRIGGER request_events_no_delete BEFORE DELETE ON request_events
  BEGIN SELECT RAISE(ABORT, 'request_events is append-only'); END;

CREATE TABLE tokens (
  name       TEXT PRIMARY KEY,
  sha256     TEXT NOT NULL,
  issued_at  TEXT NOT NULL,
  expires_at TEXT,
  revoked_at TEXT
);
"""

# The pragmas that apply to every connection, read-write or read-only alike
# (plan §1.1/§1.3). journal_mode=WAL and secure_delete are database-level
# settings (first writer wins, but re-asserting is harmless); busy_timeout
# and foreign_keys/trusted_schema are per-connection.
_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA secure_delete = ON",
    "PRAGMA trusted_schema = OFF",
    "PRAGMA busy_timeout = 5000",
)

# apps/join-api/migrations/*.sql -- Postgres-only (SQLite's schema is _SCHEMA
# above, applied inline). Resolved relative to this file, not the process's
# cwd, so `python -m store ...` works from any directory.
_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / "migrations"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_dsn(dsn: str) -> str:
    """Return `dsn` with its password replaced by '***', for safe use in
    exceptions, logs, and reprs -- a DSN's password must never reach any of
    those verbatim. Round-trips through psycopg's own conninfo parser
    (handles both `postgresql://` URL and `key=value` styles) rather than a
    hand-rolled regex, so it stays correct as the DSN grows sslmode/
    sslrootcert/etc. params. Unparseable input returns a fixed placeholder
    instead of guessing -- never echo something we can't confirm is safe.

    >>> _mask_dsn("postgresql://joinapi:s3cr3t@db:5432/join?sslmode=verify-full")
    'user=joinapi password=*** dbname=join host=db port=5432 sslmode=verify-full'
    >>> "s3cr3t" in _mask_dsn("postgresql://joinapi:s3cr3t@db:5432/join")
    False
    >>> _mask_dsn("host=db dbname=join user=joinapi")  # no password present
    'user=joinapi dbname=join host=db'
    >>> _mask_dsn("not a dsn ??")
    '<unparseable DSN>'
    """
    import psycopg  # lazy -- see module docstring's "Import discipline"
    try:
        parts = psycopg.conninfo.conninfo_to_dict(dsn)
    except Exception:
        return "<unparseable DSN>"
    if parts.get("password"):
        parts["password"] = "***"
    return psycopg.conninfo.make_conninfo(**parts)


def db_path(out_dir: pathlib.Path) -> pathlib.Path:
    """out/join-store/join-store.sqlite3 -- a dedicated subdirectory under
    OUT_DIR, not out/ directly, so nothing else sharing out/'s :rw mount
    (the console container's own journal) ever needs write access to this
    file specifically."""
    return out_dir / "join-store" / "join-store.sqlite3"


def connect(target: pathlib.Path | str, *, readonly: bool = False):
    """Connection factory, dispatched by the **type** of `target`: a
    pathlib.Path opens SQLite (unchanged from before); a str opens Postgres,
    treated as a DSN passed straight to psycopg.connect(). Always pass
    whatever `init()` just returned -- that's what determines which backend
    you get, not any flag here. Row access is dict-like either way
    (sqlite3.Row / psycopg's dict_row).

    SQLite readonly=True opens via the `mode=ro` URI, which SQLite enforces
    at the OS/file level -- a write through it raises
    sqlite3.OperationalError regardless of anything this module does.

    check_same_thread=False (SQLite only): sqlite3's own default (True)
    raises sqlite3.ProgrammingError the moment a connection is touched from
    a thread other than the one that opened it. app.py's get_conn is a sync
    generator FastAPI dependency, and every route is a sync `def`, so
    FastAPI dispatches each request through anyio.to_thread.run_sync --
    which does not guarantee the connection is opened and used on the same
    worker thread (measured live: 8 concurrent clients, 80% failure rate).
    Safe to relax here because it only permits a connection to migrate
    threads, not to be used by two threads at once -- one request/flow of
    control still ever holds a given connection at a time.

    Postgres readonly=True additionally issues `SET SESSION CHARACTERISTICS
    AS TRANSACTION READ ONLY` -- belt-and-suspenders on top of whichever DSN
    (rw `joinapi` vs ro `joinapi_ro` role) the caller already chose; the role
    grant is the real enforcement, this just fails fast in-process too."""
    if isinstance(target, str):
        return _pg_connect(target, readonly=readonly)
    return _sqlite_connect(target, readonly=readonly)


def _sqlite_connect(path: pathlib.Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


def _pg_connect(dsn: str, *, readonly: bool = False) -> psycopg.Connection:
    """DSN is passed through to psycopg.connect() as-is -- sslmode=
    verify-full&sslrootcert=... included, never stripped or overridden here.
    Any connection failure is re-raised with the password masked (`raise ...
    from None` drops the original exception's __context__/__cause__ too, so
    it can't resurface via traceback formatting either), so a bad DSN never
    lands a plaintext password in a log line or traceback. The re-raised
    message DOES include type(exc).__name__ and str(exc) (and .sqlstate when
    the underlying error has one) -- libpq's connection-error text (DNS
    failure, auth failure, TLS/cert failure, firewall/trusted-sources
    rejection, ...) does not echo the password, only _mask_dsn(dsn) ever
    could, so surfacing it is safe *alongside* the masking, not instead of
    it. Against a real cluster, connection failure is the expected
    first-run failure mode -- without the real cause, every one of those is
    indistinguishable from every other one.

    Import discipline: `psycopg` is imported lazily right here, not at
    module level (see the module docstring) -- this function (and
    _mask_dsn, called below) are the only things in the Postgres connect
    path that need it.

    autocommit=True deliberately, unlike psycopg's own default: psycopg (like
    every PEP 249 DBAPI) opens an implicit transaction before ANY statement,
    including a bare SELECT -- not just DML, unlike sqlite3 (which only opens
    one for INSERT/UPDATE/DELETE). Every read function in this module
    (load_request, list_requests, ...) issues a bare conn.execute() with no
    `with conn.transaction():` around it; on a non-autocommit connection that
    leaves a transaction open, so a *later* write on the same connection
    (`with conn.transaction():` inside save_request, say) becomes a nested
    SAVEPOINT of that still-open outer transaction instead of a real
    top-level commit -- and closing the connection afterwards silently rolls
    the whole thing back (found live: a CLI amend-refresh that printed
    success and never touched the row). autocommit=True is the fix and is
    psycopg's own documented recommendation for exactly this shape of code;
    `with conn.transaction():` still works for real multi-statement
    atomicity (save_request's upsert + event insert, issue_token's
    check-then-insert) -- entering it takes the connection out of autocommit
    for the block and commits for real at the end."""
    import psycopg  # lazy -- see module docstring's "Import discipline"
    try:
        conn = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row, autocommit=True)
    except Exception as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        cause = f"{type(exc).__name__}: {exc}" + (f" [sqlstate={sqlstate}]" if sqlstate else "")
        raise RuntimeError(f"postgres connection failed ({cause}), dsn={_mask_dsn(dsn)}") from None
    if readonly:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return conn


def init(out_dir: pathlib.Path, *, kind: str = "sqlite", db_url: str | None = None) -> pathlib.Path | str:
    """Make the store ready to connect() to and return the value connect()
    needs: a pathlib.Path for SQLite, the DSN string (`db_url`, unchanged)
    for Postgres. Idempotent -- safe to call every process startup, and (for
    Postgres) safe under two processes racing to start at once.

    kind/db_url mirror how app.py already resolves the backend today:
    `deployment.yaml`'s `datastore.kind` (default "sqlite") and the
    KP2_JOIN_DB_URL environment variable. Passing neither keeps every
    existing call site (`store.init(out_dir)`) working exactly as before.

    SQLite: create the DB file and full schema if absent (unchanged
    behavior -- see the SQLite-only docstring below).
    Postgres: apply apps/join-api/migrations/*.sql that haven't been
    recorded in `schema_version` yet, inside
    `pg_advisory_xact_lock(hashtext('kp2-migrate'))` so a second process
    racing to start at the same time blocks until the first commits, then
    finds its own migrations already applied and does nothing."""
    if kind == "sqlite":
        return _sqlite_init(out_dir)
    if kind == "postgres":
        if not db_url:
            raise ValueError("init(kind='postgres') needs db_url (KP2_JOIN_DB_URL)")
        _pg_init(db_url)
        return db_url
    raise NotImplementedError(f"datastore.kind={kind!r} is not implemented")


def _sqlite_init(out_dir: pathlib.Path) -> pathlib.Path:
    """Gated on SCHEMA presence, not file presence: connect() below creates
    an empty file as a side effect of merely opening it, so a file-existence
    check would see that empty file on the next call and skip
    executescript() forever -- the crash-between-create-and-schema window
    (process dies right after this function's own connect() call, before
    the file had tables). sqlite_master is queried for it directly."""
    path = db_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    was_new = not path.exists()  # only for the chmod decision below
    conn = _sqlite_connect(path)
    try:
        has_schema = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
        ).fetchone() is not None
        if not has_schema:
            conn.executescript(_SCHEMA)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
    finally:
        conn.close()
    # 0600 whenever this call is the one that (re)created the schema, not
    # just on a brand-new file -- a half-created file from an earlier crash
    # existed (and may already carry a looser mode) but never got chmod'd.
    if was_new or not has_schema:
        os.chmod(path, 0o600)  # the container umask isn't trusted (plan §3)
    return path


def _pg_init(db_url: str) -> None:
    conn = _pg_connect(db_url)  # autocommit=True; the `with conn.transaction():`
    # below is what makes the migrate-check-and-apply one real transaction.
    try:
        with conn.transaction():
            # Transaction-scoped: blocks a racing second process until this
            # one commits (releasing the lock), then that process re-checks
            # schema_version and finds everything already applied.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('kp2-migrate'))")
            exists = conn.execute(
                "SELECT to_regclass('public.schema_version') IS NOT NULL AS exists"
            ).fetchone()["exists"]
            applied = set()
            if exists:
                applied = {row["version"] for row in conn.execute("SELECT version FROM schema_version").fetchall()}
            # Numeric-prefixed files only ("001_init.sql", ...) -- these are
            # the once-ever, schema_version-gated table-creation migrations.
            for migration in sorted(_MIGRATIONS_DIR.glob("[0-9]*_*.sql")):
                version = int(migration.name.split("_", 1)[0])
                if version in applied:
                    continue
                conn.execute(migration.read_text())  # no params -> psycopg's simple
                # query protocol, which (unlike execute() with params) allows
                # several ;-separated statements -- including dollar-quoted
                # DO blocks -- in one call, same as sqlite3.executescript().
                conn.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))
            # grants.sql: NOT numbered, NOT gated by schema_version -- run on
            # every store.init() call, every process startup, so GRANTs that
            # couldn't apply yet (roles not provisioned) are retried instead
            # of being permanently skipped once table creation is recorded as
            # applied. See grants.sql's own header for the full reasoning.
            grants_file = _MIGRATIONS_DIR / "grants.sql"
            if grants_file.exists():
                conn.execute(grants_file.read_text())
    finally:
        conn.close()


def _member_key(record: dict) -> str | None:
    payload = record.get("payload")
    if isinstance(payload, dict) and "code" in payload:
        return str(payload["code"]).lower()
    return None


def save_request(conn, record: dict, *, actor: str, event: str,
                  detail: dict | None = None) -> None:
    """Upsert the `requests` row and append one `request_events` row, in one
    transaction. SQLite: `record` round-trips through json.dumps/json.loads
    -- values round-trip byte-for-byte where it matters (what json.loads
    returns), not the on-disk text layout. Postgres: `record` goes into a
    JSONB column un-dumped (wrapped in psycopg's Jsonb marker) and comes
    back out already a dict -- no json.dumps/loads anywhere on that path,
    per the plan (don't double-encode/decode)."""
    if isinstance(conn, sqlite3.Connection):
        with conn:
            conn.execute(
                """
                INSERT INTO requests (id, state, submitted_at, submitted_by, member_key, record)
                VALUES (:id, :state, :submitted_at, :submitted_by, :member_key, :record)
                ON CONFLICT(id) DO UPDATE SET
                    state=excluded.state, submitted_at=excluded.submitted_at,
                    submitted_by=excluded.submitted_by, member_key=excluded.member_key,
                    record=excluded.record
                """,
                {
                    "id": record["id"],
                    "state": record["state"],
                    "submitted_at": record["submitted_at"],
                    "submitted_by": record.get("submitted_by"),
                    "member_key": _member_key(record),
                    "record": json.dumps(record),
                },
            )
            conn.execute(
                "INSERT INTO request_events (request_id, at, actor, event, detail) VALUES (?, ?, ?, ?, ?)",
                (record["id"], _now(), actor, event, json.dumps(detail) if detail is not None else None),
            )
        return
    import psycopg  # lazy -- see module docstring's "Import discipline"
    Jsonb = psycopg.types.json.Jsonb
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO requests (id, state, submitted_at, submitted_by, member_key, record)
            VALUES (%(id)s, %(state)s, %(submitted_at)s, %(submitted_by)s, %(member_key)s, %(record)s)
            ON CONFLICT (id) DO UPDATE SET
                state=excluded.state, submitted_at=excluded.submitted_at,
                submitted_by=excluded.submitted_by, member_key=excluded.member_key,
                record=excluded.record
            """,
            {
                "id": record["id"],
                "state": record["state"],
                "submitted_at": record["submitted_at"],
                "submitted_by": record.get("submitted_by"),
                "member_key": _member_key(record),
                "record": Jsonb(record),
            },
        )
        conn.execute(
            "INSERT INTO request_events (request_id, at, actor, event, detail) VALUES (%s, %s, %s, %s, %s)",
            (record["id"], _now(), actor, event, Jsonb(detail) if detail is not None else None),
        )


def log_refusal(conn, actor: str, event: str, detail: dict | None = None) -> None:
    """One request_events row with request_id=NULL, for a 429 (rate-limit or
    store-quota) refusal that happens before any request row exists to
    attach it to -- plan §1.5. This is why request_events.request_id has no
    NOT NULL (SQLite) / has no `NOT NULL` and a plain (nullable) FK
    (Postgres): neither engine enforces a foreign key check on a NULL
    value, so this insert is safe against the REFERENCES requests(id)
    constraint on both backends."""
    if isinstance(conn, sqlite3.Connection):
        with conn:
            conn.execute(
                "INSERT INTO request_events (request_id, at, actor, event, detail) VALUES (NULL, ?, ?, ?, ?)",
                (_now(), actor, event, json.dumps(detail) if detail is not None else None),
            )
        return
    import psycopg  # lazy -- see module docstring's "Import discipline"
    Jsonb = psycopg.types.json.Jsonb
    with conn.transaction():
        conn.execute(
            "INSERT INTO request_events (request_id, at, actor, event, detail) VALUES (NULL, %s, %s, %s, %s)",
            (_now(), actor, event, Jsonb(detail) if detail is not None else None),
        )


def load_request(conn, request_id: str) -> dict | None:
    """The id charset check (_REQUEST_ID_RE) is a caller responsibility --
    this function just queries."""
    if isinstance(conn, sqlite3.Connection):
        row = conn.execute("SELECT record FROM requests WHERE id = ?", (request_id,)).fetchone()
        return json.loads(row["record"]) if row else None
    row = conn.execute("SELECT record FROM requests WHERE id = %s", (request_id,)).fetchone()
    return row["record"] if row else None  # jsonb decodes to dict automatically


def list_requests(conn) -> list[dict]:
    """Every record, sorted by id for deterministic order -- callers that
    need submission order re-sort by submitted_at themselves, same as
    today's route."""
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute("SELECT record FROM requests ORDER BY id").fetchall()
        return [json.loads(row["record"]) for row in rows]
    rows = conn.execute("SELECT record FROM requests ORDER BY id").fetchall()
    return [row["record"] for row in rows]


def count_requests(conn) -> int:
    """Same query, unbranched -- works against either backend unmodified
    (dict-like row access both ways)."""
    return conn.execute("SELECT COUNT(*) AS n FROM requests").fetchone()["n"]


def member_record(conn, key: str) -> dict | None:
    """Newest ACTIVE-or-RETIRING record for `key` (already lowercased by the
    caller), via the requests_by_member index."""
    if isinstance(conn, sqlite3.Connection):
        row = conn.execute(
            """
            SELECT record FROM requests
            WHERE member_key = ? AND state IN ('ACTIVE', 'RETIRING')
            ORDER BY submitted_at DESC LIMIT 1
            """,
            (key,),
        ).fetchone()
        return json.loads(row["record"]) if row else None
    row = conn.execute(
        """
        SELECT record FROM requests
        WHERE member_key = %s AND state IN ('ACTIVE', 'RETIRING')
        ORDER BY submitted_at DESC LIMIT 1
        """,
        (key,),
    ).fetchone()
    return row["record"] if row else None


def recover_interrupted(conn) -> None:
    """Every row still RUNNING becomes FAILED, in one transaction, with one
    request_events row per transitioned record. Run once at startup by the
    caller (Task 2) -- not at import time."""
    if isinstance(conn, sqlite3.Connection):
        with conn:
            rows = conn.execute("SELECT record FROM requests WHERE state = 'RUNNING'").fetchall()
            at = _now()
            for row in rows:
                record = json.loads(row["record"])
                record["state"] = "FAILED"
                record["error"] = {
                    "step": record.get("last_completed_step"),
                    "message": "interrupted by a join-api restart",
                }
                conn.execute(
                    "UPDATE requests SET state = ?, record = ? WHERE id = ?",
                    ("FAILED", json.dumps(record), record["id"]),
                )
                conn.execute(
                    "INSERT INTO request_events (request_id, at, actor, event, detail) VALUES (?, ?, ?, ?, ?)",
                    (record["id"], at, "system", "state:RUNNING->FAILED", None),
                )
        return
    import psycopg  # lazy -- see module docstring's "Import discipline"
    Jsonb = psycopg.types.json.Jsonb
    with conn.transaction():
        rows = conn.execute("SELECT record FROM requests WHERE state = 'RUNNING'").fetchall()
        at = _now()
        for row in rows:
            record = row["record"]
            record["state"] = "FAILED"
            record["error"] = {
                "step": record.get("last_completed_step"),
                "message": "interrupted by a join-api restart",
            }
            conn.execute(
                "UPDATE requests SET state = %s, record = %s WHERE id = %s",
                ("FAILED", Jsonb(record), record["id"]),
            )
            conn.execute(
                "INSERT INTO request_events (request_id, at, actor, event, detail) VALUES (%s, %s, %s, %s, %s)",
                (record["id"], at, "system", "state:RUNNING->FAILED", None),
            )


class NameAlreadyUsed(Exception):
    """Raised by issue_token when `name` already has a row in `tokens`,
    active or revoked -- reissuing a revoked name is rejected (plan §1.4's
    "require a fresh name" ruling); `.revoked` tells the caller which case
    it is, since app.py's 409 wording distinguishes them."""

    def __init__(self, name: str, *, revoked: bool):
        super().__init__(f"token name already used: {name!r} (revoked={revoked})")
        self.name = name
        self.revoked = revoked


def _pg_stringify_timestamps(row: dict, *keys: str) -> dict:
    """Postgres TIMESTAMPTZ columns come back from psycopg as tz-aware
    datetime objects; the SQLite backend (and every existing caller)
    expects ISO-8601 strings. This is the one seam where store.py hides
    that backend difference -- "the API still emits ISO-8601 strings
    unchanged; conversion happens in store.py, not by changing what callers
    see." astimezone(UTC) first so the offset is always +00:00 regardless
    of the connection's session timezone, matching _now()'s own output."""
    out = dict(row)
    for key in keys:
        if out.get(key) is not None:
            out[key] = out[key].astimezone(timezone.utc).isoformat()
    return out


def issue_token(conn, name: str, sha256: str, *,
                 expires_in_days: int | None = None) -> None:
    # Cheap pre-check for the common case -- gives .revoked without a second
    # round trip. Not the actual safety net: two concurrent callers can both
    # pass this before either INSERTs, so the INSERT below carries its own
    # UNIQUE-constraint fallback, translated to the same exception type,
    # rather than letting a raw sqlite3.IntegrityError/psycopg.IntegrityError
    # reach Task 2's `except NameAlreadyUsed` handler.
    if isinstance(conn, sqlite3.Connection):
        existing = conn.execute("SELECT revoked_at FROM tokens WHERE name = ?", (name,)).fetchone()
        if existing is not None:
            raise NameAlreadyUsed(name, revoked=existing["revoked_at"] is not None)
        issued_at = datetime.now(timezone.utc)
        # expires_in_days=0 is present, not absent -- plan §1.4 says *absent*
        # means no expiry, so this must not fall into the same branch as None.
        expires_at = (issued_at + timedelta(days=expires_in_days)).isoformat() if expires_in_days is not None else None
        try:
            with conn:
                conn.execute(
                    "INSERT INTO tokens (name, sha256, issued_at, expires_at, revoked_at) VALUES (?, ?, ?, ?, NULL)",
                    (name, sha256, issued_at.isoformat(), expires_at),
                )
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT revoked_at FROM tokens WHERE name = ?", (name,)).fetchone()
            raise NameAlreadyUsed(name, revoked=row is not None and row["revoked_at"] is not None) from None
        return
    import psycopg  # lazy -- see module docstring's "Import discipline"
    existing = conn.execute("SELECT revoked_at FROM tokens WHERE name = %s", (name,)).fetchone()
    if existing is not None:
        raise NameAlreadyUsed(name, revoked=existing["revoked_at"] is not None)
    issued_at = datetime.now(timezone.utc)
    expires_at = (issued_at + timedelta(days=expires_in_days)) if expires_in_days is not None else None
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO tokens (name, sha256, issued_at, expires_at, revoked_at) VALUES (%s, %s, %s, %s, NULL)",
                (name, sha256, issued_at, expires_at),
            )
    except psycopg.errors.UniqueViolation:
        row = conn.execute("SELECT revoked_at FROM tokens WHERE name = %s", (name,)).fetchone()
        raise NameAlreadyUsed(name, revoked=row is not None and row["revoked_at"] is not None) from None


def find_token(conn, sha256: str) -> dict | None:
    """Returns the matching row or None -- does not decide revoked/expired
    rejection itself (that's require_applicant's job, Task 2)."""
    if isinstance(conn, sqlite3.Connection):
        row = conn.execute(
            "SELECT name, issued_at, expires_at, revoked_at FROM tokens WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return dict(row) if row else None
    row = conn.execute(
        "SELECT name, issued_at, expires_at, revoked_at FROM tokens WHERE sha256 = %s", (sha256,)
    ).fetchone()
    return _pg_stringify_timestamps(row, "issued_at", "expires_at", "revoked_at") if row else None


def list_tokens(conn) -> list[dict]:
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute("SELECT name, issued_at, expires_at, revoked_at FROM tokens ORDER BY name").fetchall()
        return [dict(row) for row in rows]
    rows = conn.execute("SELECT name, issued_at, expires_at, revoked_at FROM tokens ORDER BY name").fetchall()
    return [_pg_stringify_timestamps(row, "issued_at", "expires_at", "revoked_at") for row in rows]


def revoke_token(conn, name: str) -> bool:
    """True if a row was found (and updated), False if no row with that name
    exists. Revoking an already-revoked name still returns True and
    overwrites revoked_at with this call's timestamp -- simpler than
    preserving the first revocation time, and which timestamp survives a
    double-revoke isn't load-bearing here."""
    if isinstance(conn, sqlite3.Connection):
        with conn:
            row = conn.execute("SELECT name FROM tokens WHERE name = ?", (name,)).fetchone()
            if row is None:
                return False
            conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ?", (_now(), name))
            return True
    with conn.transaction():
        row = conn.execute("SELECT name FROM tokens WHERE name = %s", (name,)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE tokens SET revoked_at = %s WHERE name = %s", (_now(), name))
        return True


# -- locks (plan: Postgres provisioning task) ---------------------------------
# Moved here from app.py's own module-level _JOB_LOCK/_APPLY_LOCK -- Task 2
# removes those two declarations from app.py and calls job_lock(conn)/
# apply_lock(conn) instead. _BUCKET_LOCK (rate limiting) is NOT one of these
# -- it stays in-process, in app.py, unchanged; it was never about
# serialising access to the store.

class LockBusy(Exception):
    """Raised by job_lock() on the Postgres backend when
    pg_try_advisory_lock finds the lock already held by another session.
    Non-blocking by design (unlike the SQLite backend's queueing
    threading.Lock) -- see job_lock()'s docstring for why, and what a
    caller needs to do differently per backend."""


# SQLite-only: module-level singletons, one process, one meaning each --
# moved verbatim from app.py's _JOB_LOCK/_APPLY_LOCK (same threading.Lock
# objects, same semantics, just re-exposed as context managers here).
_JOB_LOCK = threading.Lock()
_APPLY_LOCK = threading.Lock()


@contextlib.contextmanager
def job_lock(conn):
    """One job running at a time.

    SQLite: `with job_lock(conn):` blocks until the module-level _JOB_LOCK
    is free, then holds it for the block's duration -- app.py's existing
    "queue" behavior (a waiting caller reports queued: true), unchanged,
    just moved here from app.py's own _JOB_LOCK.

    Postgres: pg_try_advisory_lock(hashtext('kp2-job')) -- NON-BLOCKING.
    Raises LockBusy immediately if another session already holds it, rather
    than blocking the caller's thread on a database round trip. This is a
    deliberate asymmetry with the SQLite path (see plan/report): app.py's
    _run_job today always blocks (a background thread queues behind a
    running job); Task 2 decides what a Postgres LockBusy means for that
    call site (e.g. retry/backoff to reproduce the queueing behavior, or
    surface it as "busy, try again") -- store.py only provides the
    non-blocking primitive the plan asks for."""
    if isinstance(conn, sqlite3.Connection):
        with _JOB_LOCK:
            yield
        return
    got = conn.execute("SELECT pg_try_advisory_lock(hashtext('kp2-job')) AS ok").fetchone()["ok"]
    if not got:
        raise LockBusy("kp2-job lock is held by another session")
    try:
        yield
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext('kp2-job')) AS ok")


@contextlib.contextmanager
def apply_lock(conn):
    """One approval written at a time (writer.apply_real's transactional
    guarantee only holds one approval at a time -- plan/app.py comment on
    _APPLY_LOCK).

    SQLite: `with apply_lock(conn):` blocks until the module-level
    _APPLY_LOCK is free, same as app.py's existing _APPLY_LOCK, just moved
    here.

    Postgres: pg_advisory_xact_lock(hashtext('kp2-apply')) -- BLOCKS until
    acquired (unlike job_lock's try-variant), and is transaction-scoped:
    released automatically at commit/rollback, not at this context manager's
    exit as such. Concretely, `apply_lock` opens that transaction itself
    (`with conn.transaction():`) and keeps it open for the whole `with
    apply_lock(conn):` block -- connections default to autocommit=True (see
    _pg_connect), so without this the lock would be released the instant
    after it was acquired (each autocommit statement is its own
    single-statement transaction). Do the protected work on this same `conn`
    inside the `with` block (e.g. store.save_request(conn, ...) -- its own
    internal `with conn.transaction():` nests as a savepoint of this one)
    and let apply_lock's exit commit it all atomically; don't call
    conn.commit()/conn.rollback() yourself inside the block.

    **Failure-contract asymmetry callers must know about**: on SQLite,
    apply_lock is a mutex only -- each write inside the `with` block commits
    on its own (every store.py write function commits itself, e.g.
    save_request's own `with conn:`), so if something raises partway
    through, whatever was already written stays written. On Postgres,
    apply_lock is a mutex PLUS one enclosing transaction -- an exception
    anywhere in the `with apply_lock(conn):` block rolls back *everything*
    written inside it, not just the step that failed. Same lock name, same
    call shape, different all-or-nothing guarantee. Task 2 (app.py's
    approval-apply critical section, the one caller of apply_lock) needs to
    know which behavior it's relying on."""
    if isinstance(conn, sqlite3.Connection):
        with _APPLY_LOCK:
            yield
        return
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('kp2-apply'))")
        yield


def job_lock_held(conn) -> bool:
    """Non-blocking peek: true if job_lock(conn) is currently held by
    someone else, without acquiring it. Backs app.py's `queued` field
    (approve_request/resume_request/retire_member), which used to read
    app.py's own _JOB_LOCK.locked() directly -- job_lock() moved here in
    Task 2, so the peek moves with it.

    SQLite: _JOB_LOCK.locked() -- the same module-level singleton job_lock()
    itself acquires on that path.

    Postgres: try to acquire the *same* key job_lock() uses
    (pg_try_advisory_lock(hashtext('kp2-job'))) -- if it succeeds, the lock
    was free, so release it again immediately (pg_advisory_unlock) and
    report False; if it fails, someone else holds it, report True. Never
    holds the lock itself -- this is a peek, not a lock/unlock pair a caller
    is meant to nest anything inside."""
    if isinstance(conn, sqlite3.Connection):
        return _JOB_LOCK.locked()
    got = conn.execute("SELECT pg_try_advisory_lock(hashtext('kp2-job')) AS ok").fetchone()["ok"]
    if not got:
        return True
    conn.execute("SELECT pg_advisory_unlock(hashtext('kp2-job')) AS ok")
    return False


# -- backend selection (plan §1.6) --------------------------------------------
# The seam Task 2 calls at startup to fail fast on an unsupported
# datastore.kind, and store.init()/store.connect() use kind the same way.

def backend_for(kind: str) -> str:
    if kind not in ("sqlite", "postgres"):
        raise NotImplementedError(f"datastore.kind={kind!r} is not implemented (only 'sqlite'/'postgres' exist)")
    return kind


# -- `python -m store` CLI (host-side, no HTTP, no auth -- run on the host or
# in the join-api container, never exposed) ----------------------------------
# Mirrors scripts/member.sh's existing cmd_drift, which already opens the
# SQLite store directly (`sqlite3.connect(f"file:{db}?mode=ro", uri=True)`,
# bypassing store.py) rather than through the running join-api process --
# same idea here, generalised to whichever backend datastore.kind names, and
# routed through store.py this time so the Postgres path (which has no
# equivalent "open the file directly" option) works too. Deliberately does
# NOT call init()/apply migrations: these commands assume join-api's own
# startup already created/migrated the schema, exactly like member.sh drift
# assumes the SQLite file already exists (`[ -f "$db" ] || fail ...`).


def _resolve_backend() -> tuple[str, str | None, pathlib.Path, pathlib.Path]:
    """PACK_DIR/OUT_DIR default the same as app.py; datastore.kind comes
    from deployment.yaml the same way app.py resolves it at startup (missing
    file -> "sqlite", same as many of app.py's own test fixtures)."""
    import yaml  # lazy -- see module docstring's "Import discipline"
    pack_dir = pathlib.Path(os.environ.get("PACK_DIR", "/pack"))
    out_dir = pathlib.Path(os.environ.get("OUT_DIR", "/out"))
    try:
        deployment_doc = yaml.safe_load((pack_dir / "deployment.yaml").read_text()) or {}
    except FileNotFoundError:
        deployment_doc = {}
    kind = (deployment_doc.get("datastore") or {}).get("kind", "sqlite")
    db_url = os.environ.get("KP2_JOIN_DB_URL")
    return kind, db_url, pack_dir, out_dir


def _cli_connect(*, readonly: bool):
    """Postgres: KP2_JOIN_DB_URL_RO (the joinapi_ro role's DSN) when
    readonly and set, else KP2_JOIN_DB_URL -- `dump-records`/`check` prefer
    the read-only role where an operator has provisioned one; `amend-refresh`
    always needs the read-write DSN and never looks at _RO. SQLite: the
    existing readonly=True mode=ro URI connection."""
    kind, db_url, pack_dir, out_dir = _resolve_backend()
    if kind == "postgres":
        target = db_url
        if readonly:
            target = os.environ.get("KP2_JOIN_DB_URL_RO") or db_url
        if not target:
            print("KP2_JOIN_DB_URL is not set", file=sys.stderr)
            raise SystemExit(1)
        return connect(target, readonly=readonly)
    path = db_path(out_dir)
    if not path.exists():
        print(f"no join store at {path} -- join-api has not run yet", file=sys.stderr)
        raise SystemExit(1)
    return connect(path, readonly=readonly)


def _cmd_dump_records(_args) -> None:
    """Every request record, one JSON object per line (JSONL/NDJSON) on
    stdout -- not a single JSON array, so a consumer (Task 5's export
    scripts, or just `grep`/`jq -c`) can stream it without holding the whole
    dump in memory, and a partial/truncated read still yields whole
    records."""
    conn = _cli_connect(readonly=True)
    try:
        for record in list_requests(conn):
            print(json.dumps(record))
    finally:
        conn.close()


def _cmd_amend_refresh(args) -> None:
    """Load record `id`, shallow-merge the given JSON object on top of it
    (existing fields not named in the payload are kept -- a merge, not a
    replace, so a caller can patch one field without re-sending the whole
    record), and save it back via save_request(actor="host-script",
    event="refresh")."""
    conn = _cli_connect(readonly=False)
    try:
        record = load_request(conn, args.id)
        if record is None:
            print(f"no request {args.id!r} in the store", file=sys.stderr)
            raise SystemExit(1)
        try:
            patch = json.loads(args.json)
        except json.JSONDecodeError as exc:
            print(f"invalid JSON payload: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        if not isinstance(patch, dict):
            print("JSON payload must be a JSON object", file=sys.stderr)
            raise SystemExit(1)
        record.update(patch)
        save_request(conn, record, actor="host-script", event="refresh")
        print(f"amended {args.id}")
    finally:
        conn.close()


def _cmd_check(_args) -> None:
    """The §6.1 reconciliation: every ACTIVE/RETIRING record's member_key
    against manifest.yaml's identity.members. Two findings, always exit 0
    (a finding is evidence, not a tool error):
      - store records (state ACTIVE or RETIRING) whose member_key matches no
        manifest.yaml identity.members entry at all;
      - manifest.yaml identity.members entries with origin: joined that
        match no ACTIVE (not RETIRING -- a retiring member's manifest entry
        outliving its last ACTIVE record is expected, not drift) record.
    "clean" when both lists are empty."""
    import yaml  # lazy -- see module docstring's "Import discipline"
    _kind, _db_url, pack_dir, _out_dir = _resolve_backend()
    conn = _cli_connect(readonly=True)
    try:
        records = list_requests(conn)
    finally:
        conn.close()

    manifest = yaml.safe_load((pack_dir / "manifest.yaml").read_text()) or {}
    members = (manifest.get("identity") or {}).get("members") or {}
    manifest_codes = {str(entry.get("code", "")).lower() for entry in members.values() if entry.get("code")}

    tracked = [r for r in records if r.get("state") in ("ACTIVE", "RETIRING")]
    active_record_codes = {_member_key(r) for r in records if r.get("state") == "ACTIVE" and _member_key(r)}

    orphan_records = [r for r in tracked if _member_key(r) not in manifest_codes]
    orphan_manifest = [
        (key, entry) for key, entry in members.items()
        if entry.get("origin") == "joined" and str(entry.get("code", "")).lower() not in active_record_codes
    ]

    if not orphan_records and not orphan_manifest:
        print("clean")
        return

    print("records with no matching manifest entry:")
    if orphan_records:
        for r in orphan_records:
            print(f"  - {r.get('id')} (member_key={_member_key(r)!r}, state={r.get('state')})")
    else:
        print("  (none)")

    print("manifest 'joined' entries with no matching ACTIVE record:")
    if orphan_manifest:
        for key, entry in orphan_manifest:
            print(f"  - {key} (code={entry.get('code')!r})")
    else:
        print("  (none)")


def _build_arg_parser():
    import argparse
    parser = argparse.ArgumentParser(prog="python -m store", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dump-records", help="print every request record as JSON, one per line")

    p_amend = sub.add_parser("amend-refresh", help="merge a JSON patch into one record by id")
    p_amend.add_argument("id")
    p_amend.add_argument("json")

    sub.add_parser("check", help="reconcile store records against manifest.yaml")

    return parser


if __name__ == "__main__":
    _parser = _build_arg_parser()
    _args = _parser.parse_args()
    {
        "dump-records": _cmd_dump_records,
        "amend-refresh": _cmd_amend_refresh,
        "check": _cmd_check,
    }[_args.command](_args)
