#!/usr/bin/env python3
"""scripts/migrate-join-store.py -- one-shot, idempotent migration of the
join API's file-backed state (out/join/*.json request records,
out/join-tokens.json issued credentials) into the SQLite store
apps/join-api/store.py owns. Host-side, stdlib only.
See docs/plans/join-datastore-sqlite-plan.md §2 for the design rationale.

app.py's own startup check (its module-level migration-refusal block)
refuses to boot if out/join/*.json files still sit beside a DB that holds
none of them, naming this script as the remedy. Run it once, before
starting join-api against the new store:

    python3 scripts/migrate-join-store.py

Safe to re-run: every insert is INSERT OR IGNORE (never store.save_request,
which upserts -- a stale on-disk JSON file must never clobber a row the
live system has already moved forward), and the archive step is a no-op
once the source files are gone.
"""
from __future__ import annotations

import json
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


def _import_requests(conn: sqlite3.Connection, join_dir: pathlib.Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    paths = sorted(join_dir.glob("*.json"))
    imported = 0
    for path in paths:
        record = json.loads(path.read_text())
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
    print(f"requests: imported {imported} new of {len(paths)} file(s) found")


def _import_tokens(conn: sqlite3.Connection, tokens_path: pathlib.Path) -> None:
    entries = json.loads(tokens_path.read_text())
    imported = 0
    with conn:
        for entry in entries:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO tokens (name, sha256, issued_at, expires_at, revoked_at) "
                "VALUES (?, ?, ?, NULL, NULL)",
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


def main() -> None:
    _refuse_if_join_api_running()

    out_dir = PACK_DIR / "out"
    db_path = store.init(out_dir)
    conn = store.connect(db_path)
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
        print(f"totals: {n_requests} request(s), {n_tokens} token(s) in {db_path}")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"PRAGMA integrity_check: {integrity}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
