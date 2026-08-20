"""Tests for scripts/migrate-join-store.py -- the one-shot import of the
join API's old file-backed state (out/join/*.json, out/join-tokens.json)
into apps/join-api/store.py's SQLite database.
See docs/plans/join-datastore-sqlite-plan.md §2.

Like tests/test_mkfixture.py, this runs the script as a subprocess -- its
own module docstring says "no CLI flags", and PACK_DIR is derived from
`__file__` (not overridable via env), so the only way to exercise the real
entry point is to give it a real pack layout to resolve against. Each test
builds a throwaway pack directory: scripts/migrate-join-store.py and
apps/join-api/store.py copied in (so `pathlib.Path(__file__).resolve()
.parents[1]` inside the copy resolves to the throwaway root, exactly as it
would for the real script), plus out/join/*.json and out/join-tokens.json
fixtures.

The join-api-running refusal is stubbed, not exercised against a live
stack: a fake `docker` executable is put on PATH ahead of the real one. It
answers unconditionally (this script invokes `docker` exactly once, in one
shape), so no argv parsing is needed on the fake side.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

PACK = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_SRC = PACK / "scripts" / "migrate-join-store.py"
STORE_SRC = PACK / "apps" / "join-api" / "store.py"

REQUESTS = [
    {
        "id": "req-aaaaaaaa",
        "state": "ACTIVE",
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "submitted_by": "applicant:agency-a",
        "payload": {"code": "AGENCY-A"},
    },
    {
        "id": "req-bbbbbbbb",
        "state": "SUBMITTED",
        "submitted_at": "2026-01-02T00:00:00+00:00",
        "submitted_by": None,
    },
]
TOKENS = [
    {"name": "agency-a", "sha256": "a" * 64, "issued_at": "2026-01-01T00:00:00+00:00"},
    {"name": "agency-b", "sha256": "b" * 64, "issued_at": "2026-01-02T00:00:00+00:00"},
]


def _make_pack(tmp_path: pathlib.Path, *, with_source_files: bool = True) -> pathlib.Path:
    """A throwaway pack root: just enough real structure for
    migrate-join-store.py to resolve PACK_DIR and import store.py against."""
    pack = tmp_path / "pack"
    (pack / "scripts").mkdir(parents=True)
    (pack / "apps" / "join-api").mkdir(parents=True)
    shutil.copy(SCRIPT_SRC, pack / "scripts" / "migrate-join-store.py")
    shutil.copy(STORE_SRC, pack / "apps" / "join-api" / "store.py")
    if with_source_files:
        join_dir = pack / "out" / "join"
        join_dir.mkdir(parents=True)
        for record in REQUESTS:
            (join_dir / f"{record['id']}.json").write_text(json.dumps(record))
        (pack / "out" / "join-tokens.json").write_text(json.dumps(TOKENS))
    return pack


def _fake_docker(tmp_path: pathlib.Path, *, running: bool) -> pathlib.Path:
    """A `docker` stand-in on its own PATH entry, so it is found ahead of
    any real `docker` -- see module docstring for why this is stubbed
    rather than requiring an actual compose stack."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "docker"
    fake.write_text("#!/bin/sh\n" + ("echo fake-container-id\n" if running else "exit 0\n"))
    fake.chmod(0o755)
    return bin_dir


def _run(pack: pathlib.Path, *, docker_bin: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if docker_bin is not None:
        env["PATH"] = f"{docker_bin}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [sys.executable, str(pack / "scripts" / "migrate-join-store.py")],
        cwd=pack, capture_output=True, text=True, env=env, timeout=30,
    )


def _db(pack: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(pack / "out" / "join-store" / "join-store.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


def test_fresh_migration_imports_rows_and_archives_source_files(tmp_path):
    pack = _make_pack(tmp_path)
    result = _run(pack)
    assert result.returncode == 0, result.stderr

    conn = _db(pack)
    rows = conn.execute("SELECT id, state, member_key FROM requests ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"id": "req-aaaaaaaa", "state": "ACTIVE", "member_key": "agency-a"},
        {"id": "req-bbbbbbbb", "state": "SUBMITTED", "member_key": None},
    ]
    events = conn.execute("SELECT request_id, actor, event FROM request_events ORDER BY request_id").fetchall()
    assert [dict(e) for e in events] == [
        {"request_id": "req-aaaaaaaa", "actor": "system", "event": "imported"},
        {"request_id": "req-bbbbbbbb", "actor": "system", "event": "imported"},
    ]
    tokens = conn.execute("SELECT name, sha256 FROM tokens ORDER BY name").fetchall()
    assert [dict(t) for t in tokens] == [
        {"name": "agency-a", "sha256": "a" * 64},
        {"name": "agency-b", "sha256": "b" * 64},
    ]

    # evidence archived, not deleted
    assert not (pack / "out" / "join-tokens.json").exists()
    assert list((pack / "out" / "join").glob("*.json")) == []
    archived = list((pack / "out" / "join-migrated").glob("*/"))
    assert len(archived) == 1
    archived_names = {p.name for p in archived[0].iterdir()}
    assert archived_names == {"req-aaaaaaaa.json", "req-bbbbbbbb.json", "join-tokens.json"}


def test_second_run_is_a_no_op(tmp_path):
    pack = _make_pack(tmp_path)
    first = _run(pack)
    assert first.returncode == 0, first.stderr

    second = _run(pack)
    assert second.returncode == 0, second.stderr
    assert "nothing to do" in second.stdout  # both the requests/tokens paths and the archive step

    conn = _db(pack)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 2
    # no duplicate 'imported' events from the re-run
    assert conn.execute(
        "SELECT COUNT(*) FROM request_events WHERE event = 'imported'"
    ).fetchone()[0] == 2
    # the re-run archived nothing new
    assert len(list((pack / "out" / "join-migrated").glob("*/"))) == 1


def test_refuses_when_join_api_is_running(tmp_path):
    pack = _make_pack(tmp_path)
    docker_bin = _fake_docker(tmp_path, running=True)
    result = _run(pack, docker_bin=docker_bin)

    assert result.returncode != 0
    assert "join-api is running" in result.stderr
    assert "docker compose stop join-api" in result.stderr
    # refused before touching anything
    assert not (pack / "out" / "join-store").exists()
    assert (pack / "out" / "join" / "req-aaaaaaaa.json").exists()


def test_proceeds_when_docker_reports_join_api_not_running(tmp_path):
    pack = _make_pack(tmp_path)
    docker_bin = _fake_docker(tmp_path, running=False)
    result = _run(pack, docker_bin=docker_bin)

    assert result.returncode == 0, result.stderr
    assert _db(pack).execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2
