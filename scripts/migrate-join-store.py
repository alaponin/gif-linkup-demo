#!/usr/bin/env python3
"""scripts/migrate-join-store.py -- one-shot, idempotent migration of the
join API's file-backed state (out/join/*.json request records,
out/join-tokens.json issued credentials) into the store apps/join-api/
store.py owns -- SQLite by default, or Postgres when deployment.yaml's
datastore.kind: postgres (see docs/plans/join-datastore-sqlite-plan.md §2
for the original SQLite design, and
docs/plans/join-datastore-postgres-digitalocean-plan.md §3: "same
scripts/migrate-join-store.py ... pointed at Postgres by KP2_JOIN_DB_URL,
run via docker compose run so the driver is present"). Host-side; needs
PyYAML for deployment.yaml (same as every other host script that reads it,
e.g. scripts/lib-core.sh's yq_get) and, on the Postgres path, psycopg --
neither is required on the (default, more common) SQLite path.

app.py's own startup check (its module-level migration-refusal block)
refuses to boot if out/join/*.json files still sit beside a DB that holds
none of them, naming this script as the remedy. Run it once, before
starting join-api against the new store:

    python3 scripts/migrate-join-store.py

Safe to re-run: every insert is INSERT OR IGNORE (SQLite) / ON CONFLICT DO
NOTHING (Postgres) -- never store.save_request, which upserts on both
backends -- a stale on-disk JSON file must never clobber a row the live
system has already moved forward. The archive step is a no-op once the
source files are gone.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

PACK_DIR = pathlib.Path(__file__).resolve().parents[1]

# apps/join-api/ is not a package (same reason app.py inserts its own
# directory onto sys.path -- see that file's own comment); this script
# needs store.py's connect()/init() and nothing else from that directory.
sys.path.insert(0, str(PACK_DIR / "apps" / "join-api"))
import store  # noqa: E402


def _refuse_if_join_api_running() -> None:
    """Single-writer rule (plan §1.3): a host-side write is only safe while
    join-api is not also writing. Docker being unavailable, or the compose
    project simply not being up, is NOT a refusal -- only an actually
    RUNNING join-api container blocks this script."""
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(PACK_DIR / "docker-compose.yml"),
             "ps", "join-api", "--status", "running", "-q"],
            cwd=PACK_DIR, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return  # docker/compose not available here -- not this script's problem
    if result.returncode == 0 and result.stdout.strip():
        raise SystemExit(
            "migrate-join-store.py: join-api is running -- it is the sole writer "
            "while it is running (plan §1.3). Stop it first: "
            "docker compose stop join-api"
        )


def _import_requests(conn, join_dir: pathlib.Path) -> None:
    """conn is whatever store.connect() returned -- a sqlite3.Connection or
    a psycopg.Connection -- dispatched by type, same convention store.py's
    own functions use throughout."""
    now = datetime.now(timezone.utc).isoformat()
    paths = sorted(join_dir.glob("*.json"))
    imported = 0
    is_sqlite = isinstance(conn, sqlite3.Connection)
    if not is_sqlite:
        import psycopg  # lazy -- only the Postgres path needs it
        Jsonb = psycopg.types.json.Jsonb
    for path in paths:
        record = json.loads(path.read_text())
        if is_sqlite:
            with conn:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO requests "
                    "(id, state, submitted_at, submitted_by, member_key, record) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (record["id"], record["state"], record["submitted_at"],
                     record.get("submitted_by"), store._member_key(record), json.dumps(record)),
                )
                if cursor.rowcount:  # newly inserted -- not a re-run seeing an id already present
                    conn.execute(
                        "INSERT INTO request_events (request_id, at, actor, event, detail) "
                        "VALUES (?, ?, 'system', 'imported', NULL)",
                        (record["id"], now),
                    )
                    imported += 1
        else:
            with conn.transaction():
                cursor = conn.execute(
                    "INSERT INTO requests "
                    "(id, state, submitted_at, submitted_by, member_key, record) "
                    "VALUES (%(id)s, %(state)s, %(submitted_at)s, %(submitted_by)s, %(member_key)s, %(record)s) "
                    "ON CONFLICT (id) DO NOTHING",
                    {
                        "id": record["id"],
                        "state": record["state"],
                        "submitted_at": record["submitted_at"],
                        "submitted_by": record.get("submitted_by"),
                        "member_key": store._member_key(record),
                        "record": Jsonb(record),
                    },
                )
                if cursor.rowcount:
                    conn.execute(
                        "INSERT INTO request_events (request_id, at, actor, event, detail) "
                        "VALUES (%s, %s, 'system', 'imported', NULL)",
                        (record["id"], now),
                    )
                    imported += 1
    print(f"requests: imported {imported} new of {len(paths)} file(s) found")


def _import_tokens(conn, tokens_path: pathlib.Path) -> None:
    entries = json.loads(tokens_path.read_text())
    imported = 0
    if isinstance(conn, sqlite3.Connection):
        with conn:
            for entry in entries:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO tokens (name, sha256, issued_at, expires_at, revoked_at) "
                    "VALUES (?, ?, ?, NULL, NULL)",
                    (entry["name"], entry["sha256"], entry["issued_at"]),
                )
                imported += cursor.rowcount
    else:
        with conn.transaction():
            for entry in entries:
                cursor = conn.execute(
                    "INSERT INTO tokens (name, sha256, issued_at, expires_at, revoked_at) "
                    "VALUES (%s, %s, %s, NULL, NULL) ON CONFLICT (name) DO NOTHING",
                    (entry["name"], entry["sha256"], entry["issued_at"]),
                )
                imported += cursor.rowcount
    print(f"tokens: imported {imported} new of {len(entries)} entrie(s) found")


def _archive(join_dir: pathlib.Path, tokens_path: pathlib.Path) -> None:
    files = list(join_dir.glob("*.json")) if join_dir.is_dir() else []
    if tokens_path.is_file():
        files.append(tokens_path)
    if not files:
        print("archive: nothing to do (no source files left)")
        return
    dest = PACK_DIR / "out" / "join-migrated" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.move(str(path), str(dest / path.name))
    print(f"archive: moved {len(files)} file(s) to {dest}")


def _resolve_backend() -> tuple[str, str | None]:
    """Same resolution app.py uses at its own startup: deployment.yaml's
    datastore.kind (default "sqlite"), and -- only when that is "postgres"
    -- KP2_JOIN_DB_URL, refused the same _required_token-style way app.py
    refuses an unset/placeholder DSN (see app.py's own _required_token)."""
    import yaml  # lazy -- only this function needs it
    try:
        deployment_doc = yaml.safe_load((PACK_DIR / "deployment.yaml").read_text()) or {}
    except FileNotFoundError:
        deployment_doc = {}
    kind = (deployment_doc.get("datastore") or {}).get("kind", "sqlite")
    if kind != "postgres":
        return kind, None
    db_url = os.environ.get("KP2_JOIN_DB_URL", "")
    if not db_url or "CHANGEME" in db_url:
        raise SystemExit(
            "migrate-join-store.py: deployment.yaml's datastore.kind is 'postgres' but "
            "KP2_JOIN_DB_URL is unset or still the .env.example placeholder -- run this "
            "via `docker compose run --rm -T -e KP2_JOIN_DB_URL join-api "
            "python scripts/migrate-join-store.py` with a real DSN."
        )
    return kind, db_url


def main() -> None:
    _refuse_if_join_api_running()

    kind, db_url = _resolve_backend()
    out_dir = PACK_DIR / "out"
    target = store.init(out_dir, kind=kind, db_url=db_url)
    conn = store.connect(target)
    try:
        join_dir = out_dir / "join"
        if join_dir.is_dir():
            _import_requests(conn, join_dir)
        else:
            print("requests: out/join/ does not exist -- nothing to do")

        tokens_path = out_dir / "join-tokens.json"
        if tokens_path.is_file():
            _import_tokens(conn, tokens_path)
        else:
            print("tokens: out/join-tokens.json does not exist -- nothing to do")

        _archive(join_dir, tokens_path)

        n_requests = store.count_requests(conn)
        n_tokens = len(store.list_tokens(conn))
        print(f"totals: {n_requests} request(s), {n_tokens} token(s) in {target}")
        if isinstance(conn, sqlite3.Connection):
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            print(f"PRAGMA integrity_check: {integrity}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
