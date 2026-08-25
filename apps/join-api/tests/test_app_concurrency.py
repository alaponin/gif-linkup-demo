"""C1 regression: concurrent requests must never 500 with
sqlite3.ProgrammingError ("SQLite objects created in a thread can only be
used in that same thread").

store.connect() used to open with sqlite3's own default
(check_same_thread=True). app.py's get_conn is a sync generator FastAPI
dependency, and every route is a sync `def`, so FastAPI dispatches each
request through anyio.to_thread.run_sync -- which does NOT guarantee the
connection is opened and used on the same worker thread. Measured against
the real running app before the fix: 8 concurrent clients x 160 requests ->
128/160 (80%) failed with sqlite3.ProgrammingError.

A serial TestClient (every other apps/join-api/tests/test_app_*.py file)
can never catch this class of bug: TestClient runs the whole ASGI call
chain on the one thread pytest is already running on, so the dependency and
the endpoint body always land on the same thread by construction -- there
is no thread pool in play at all. This test starts a REAL uvicorn server
(a real thread pool, real sockets) and fires genuinely concurrent requests
at it, which is the only way to reproduce anyio's actual scheduling
behaviour for sync routes.

Confirmed live, by hand, while writing this fix: with store.py's connect()
reverted to plain sqlite3.connect(path) (no check_same_thread=False), this
test fails most runs with sqlite3.ProgrammingError surfacing as HTTP 500 on
several of the concurrent responses. With the fix in place it passes
every run."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-concurrency"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-concurrency"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import store  # noqa: E402

# Loaded by path under a distinct module name -- same reason
# test_app_health.py does (a plain `import app` would reuse whichever
# apps/*/tests session already claimed that name in sys.modules).
_spec = importlib.util.spec_from_file_location(
    "join_api_app_concurrency", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

import uvicorn  # noqa: E402

PORT = 18879
REQUEST_ID = "concurrency-check"
CONCURRENCY = 8
REQUESTS_PER_WORKER = 10


def _seed() -> None:
    conn = store.connect(store.init(app.OUT_DIR))
    store.save_request(conn, {
        "id": REQUEST_ID,
        "state": "ACTIVE",
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "payload": {"code": "PTSB"},
    }, actor="system", event="test-seed")
    conn.close()


def _fetch(_: int) -> int:
    """GET /requests/{id} -- an ordinary read route. The bearer token here
    is the static applicant token, so require_applicant returns
    without ever opening a connection -- the route's own
    db: Depends(get_conn), read by _load_request (store.load_request), is
    now the ONLY connection this request opens, and it is still resolved on
    one FastAPI worker thread and used from whichever thread the event loop
    schedules the handler on next: exactly the shape that crosses threads
    under real concurrency."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/requests/{REQUEST_ID}",
        headers={"Authorization": "Bearer test-applicant-token", "X-KP2-Console": "1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_concurrent_reads_never_500_on_a_cross_thread_sqlite_connection():
    _seed()
    config = uvicorn.Config(app.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn did not start in time"

        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            statuses = list(pool.map(_fetch, range(CONCURRENCY * REQUESTS_PER_WORKER)))
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    failures = [s for s in statuses if s != 200]
    assert not failures, (
        f"{len(failures)}/{len(statuses)} concurrent GET /requests/{{id}} calls failed "
        f"(status codes seen: {sorted(set(failures))}) -- see this file's docstring for C1"
    )
