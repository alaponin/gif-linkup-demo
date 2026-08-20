"""apps/join-api/store.py -- the repository seam for the join API's
persistent state: request records, their audit trail, and issued tokens.
One SQLite database (`out/join-store/join-store.sqlite3`) replaces the
three file-backed stores app.py used to own directly (`out/join/*.json`,
`out/join-tokens.json`, and the `glob`-as-count that backed STORE_QUOTA).
See docs/plans/join-datastore-sqlite-plan.md for the design rationale.

**The join-api process is the sole writer while it is running.** Host-side
tools (scripts/member.sh drift) open read-only via `connect(readonly=True)`
-- a `file:...?mode=ro` URI connection, which SQLite refuses writes on at
the OS/file level, independent of anything this module does. See plan
§1.3.

Nothing in this module does the id/name charset checks (`_REQUEST_ID_RE`,
`_TOKEN_NAME_RE`) -- those stay in app.py as trust-boundary checks on
caller input, same division of responsibility as today. This module only
guarantees parameterised SQL.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path(out_dir: pathlib.Path) -> pathlib.Path:
    """out/join-store/join-store.sqlite3 -- a dedicated subdirectory under
    OUT_DIR, not out/ directly, so nothing else sharing out/'s :rw mount
    (the console container's own journal) ever needs write access to this
    file specifically."""
    return out_dir / "join-store" / "join-store.sqlite3"


def connect(path: pathlib.Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Connection factory. Row access is dict-like (sqlite3.Row). A
    readonly=True connection opens via the `mode=ro` URI, which SQLite
    enforces at the OS/file level -- a write through it raises
    sqlite3.OperationalError regardless of anything this module does."""
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


def init(out_dir: pathlib.Path) -> pathlib.Path:
    """Create the DB file and full schema if absent. Idempotent -- safe to
    call every process startup. Returns the DB path."""
    path = db_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    conn = connect(path)
    try:
        if is_new:
            conn.executescript(_SCHEMA)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
    finally:
        conn.close()
    if is_new:
        os.chmod(path, 0o600)  # the container umask isn't trusted (plan §3)
    return path


def _member_key(record: dict) -> str | None:
    payload = record.get("payload")
    if isinstance(payload, dict) and "code" in payload:
        return str(payload["code"]).lower()
    return None


def save_request(conn: sqlite3.Connection, record: dict, *, actor: str, event: str,
                  detail: dict | None = None) -> None:
    """Upsert the `requests` row and append one `request_events` row, in one
    transaction. `record` round-trips through json.dumps/json.loads --
    values round-trip byte-for-byte where it matters (what json.loads
    returns), not the on-disk text layout."""
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


def log_refusal(conn: sqlite3.Connection, actor: str, event: str, detail: dict | None = None) -> None:
    """One request_events row with request_id=NULL, for a 429 (rate-limit or
    store-quota) refusal that happens before any request row exists to
    attach it to -- plan §1.5. This is why request_events.request_id has no
    NOT NULL: SQLite does not enforce a foreign key check on a NULL value,
    so this insert is safe against the REFERENCES requests(id) constraint."""
    with conn:
        conn.execute(
            "INSERT INTO request_events (request_id, at, actor, event, detail) VALUES (NULL, ?, ?, ?, ?)",
            (_now(), actor, event, json.dumps(detail) if detail is not None else None),
        )


def load_request(conn: sqlite3.Connection, request_id: str) -> dict | None:
    """The id charset check (_REQUEST_ID_RE) is a caller responsibility --
    this function just queries."""
    row = conn.execute("SELECT record FROM requests WHERE id = ?", (request_id,)).fetchone()
    return json.loads(row["record"]) if row else None


def list_requests(conn: sqlite3.Connection) -> list[dict]:
    """Every record, sorted by id for deterministic order -- callers that
    need submission order re-sort by submitted_at themselves, same as
    today's route."""
    rows = conn.execute("SELECT record FROM requests ORDER BY id").fetchall()
    return [json.loads(row["record"]) for row in rows]


def count_requests(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM requests").fetchone()["n"]


def member_record(conn: sqlite3.Connection, key: str) -> dict | None:
    """Newest ACTIVE-or-RETIRING record for `key` (already lowercased by the
    caller), via the requests_by_member index."""
    row = conn.execute(
        """
        SELECT record FROM requests
        WHERE member_key = ? AND state IN ('ACTIVE', 'RETIRING')
        ORDER BY submitted_at DESC LIMIT 1
        """,
        (key,),
    ).fetchone()
    return json.loads(row["record"]) if row else None


def recover_interrupted(conn: sqlite3.Connection) -> None:
    """Every row still RUNNING becomes FAILED, in one transaction, with one
    request_events row per transitioned record. Run once at startup by the
    caller (Task 2) -- not at import time."""
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


class NameAlreadyUsed(Exception):
    """Raised by issue_token when `name` already has a row in `tokens`,
    active or revoked -- reissuing a revoked name is rejected (plan §1.4's
    "require a fresh name" ruling); `.revoked` tells the caller which case
    it is, since app.py's 409 wording distinguishes them."""

    def __init__(self, name: str, *, revoked: bool):
        super().__init__(f"token name already used: {name!r} (revoked={revoked})")
        self.name = name
        self.revoked = revoked


def issue_token(conn: sqlite3.Connection, name: str, sha256: str, *,
                 expires_in_days: int | None = None) -> None:
    existing = conn.execute("SELECT revoked_at FROM tokens WHERE name = ?", (name,)).fetchone()
    if existing is not None:
        raise NameAlreadyUsed(name, revoked=existing["revoked_at"] is not None)
    issued_at = datetime.now(timezone.utc)
    expires_at = (issued_at + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
    with conn:
        conn.execute(
            "INSERT INTO tokens (name, sha256, issued_at, expires_at, revoked_at) VALUES (?, ?, ?, ?, NULL)",
            (name, sha256, issued_at.isoformat(), expires_at),
        )


def find_token(conn: sqlite3.Connection, sha256: str) -> dict | None:
    """Returns the matching row or None -- does not decide revoked/expired
    rejection itself (that's require_applicant's job, Task 2)."""
    row = conn.execute(
        "SELECT name, issued_at, expires_at, revoked_at FROM tokens WHERE sha256 = ?", (sha256,)
    ).fetchone()
    return dict(row) if row else None


def list_tokens(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT name, issued_at, expires_at, revoked_at FROM tokens ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def revoke_token(conn: sqlite3.Connection, name: str) -> bool:
    """True if a row was found (and updated), False if no row with that name
    exists. Revoking an already-revoked name is a no-op returning True --
    simpler than preserving the first revocation timestamp, and which
    timestamp survives a double-revoke isn't load-bearing here."""
    with conn:
        row = conn.execute("SELECT name FROM tokens WHERE name = ?", (name,)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ?", (_now(), name))
        return True


# -- backend selection (plan §1.6) --------------------------------------------
# One real implementation exists today (sqlite); postgres is a future plan
# (docs/plans/join-datastore-postgres-digitalocean-plan.md). This is the seam
# Task 2 calls, not a plugin system -- there is nothing to select FROM yet.

def backend_for(kind: str) -> str:
    if kind != "sqlite":
        raise NotImplementedError(f"datastore.kind={kind!r} is not implemented (only 'sqlite' exists today)")
    return kind
