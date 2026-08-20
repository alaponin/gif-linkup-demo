"""The join API's rate limit and store quota (app.py's rate_limit /
STORE_QUOTA).

The clock is injected, never slept on: app.py's `_clock` is the one
indirection the limiter has, so refill is a variable in this file rather than
a wall-clock wait. A suite that sleeps to test a token bucket is a suite
nobody runs.

Same env-vars-before-import pattern as test_app_requests.py -- see that
file's own comment for why the module is loaded by path under a distinct
name.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

os.environ["PACK_DIR"] = "/tmp/join-api-test-pack-ratelimit"
os.environ["OUT_DIR"] = "/tmp/join-api-test-out-ratelimit"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["XROAD_TOKEN_PIN"] = "1234"
os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

_spec = importlib.util.spec_from_file_location(
    "join_api_app_ratelimit", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app_module)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import writer  # noqa: E402

REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]
CONSOLE_HEADER = "X-KP2-Console"
APPLICANT = {"Authorization": "Bearer test-applicant-token", CONSOLE_HEADER: "1"}
OPERATOR = {"Authorization": "Bearer test-operator-token", CONSOLE_HEADER: "1"}


def _conn():
    # store.init() (not just db_path()) so a test can seed the store before
    # any HTTP request has gone through the app and created the schema --
    # idempotent, so this is cheap even when the app has already done it.
    return app_module.store.connect(app_module.store.init(app_module.OUT_DIR))


class _Clock:
    """A monotonic clock that only moves when a test moves it."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(app_module, "_clock", fake)
    app_module._BUCKETS.clear()
    return fake


@pytest.fixture
def client(tmp_path, clock):
    pack = tmp_path / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    app_module.PACK_DIR = pack
    app_module.OUT_DIR = tmp_path / "out"
    return TestClient(app_module.app)


# A payload that fails validation on purpose: every one of these tests is
# about how MANY requests the endpoint accepts, not what it does with them,
# and a rejected submission takes the same rate-limit budget as an accepted
# one -- which is the point, since an attacker's submissions are all invalid.
BAD_PAYLOAD = {"code": "NOPE"}


def _submit(client, headers=APPLICANT):
    return client.post("/requests", json=BAD_PAYLOAD, headers=headers)


# -- the bucket ----------------------------------------------------------------


def test_the_burst_is_accepted_and_the_next_request_is_refused(client):
    for _ in range(app_module.RATE_LIMIT_CAPACITY):
        assert _submit(client).status_code == 201
    resp = _submit(client)
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"]


def test_the_refusal_says_when_to_come_back(client):
    """A 429 with no Retry-After tells a caller to guess, and a guessing
    caller retries immediately."""
    for _ in range(app_module.RATE_LIMIT_CAPACITY):
        _submit(client)
    resp = _submit(client)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1


def test_the_bucket_refills_over_time(client, clock):
    for _ in range(app_module.RATE_LIMIT_CAPACITY):
        _submit(client)
    assert _submit(client).status_code == 429
    # One token's worth of seconds at the configured refill rate.
    clock.advance(60.0 / app_module.RATE_LIMIT_REFILL_PER_MINUTE)
    assert _submit(client).status_code == 201
    assert _submit(client).status_code == 429


def test_refill_never_exceeds_the_burst_capacity(client, clock):
    """A bucket that keeps accruing while nobody calls is not a rate limit --
    an hour of quiet would buy an unbounded burst."""
    clock.advance(86_400)
    for _ in range(app_module.RATE_LIMIT_CAPACITY):
        assert _submit(client).status_code == 201
    assert _submit(client).status_code == 429


def test_each_credential_has_its_own_budget(client):
    """One applicant exhausting the limit must not lock the operator out of
    resuming a job -- and, once agencies hold their own tokens, must not lock
    out another agency."""
    for _ in range(app_module.RATE_LIMIT_CAPACITY):
        _submit(client)
    assert _submit(client).status_code == 429
    # The operator token satisfies require_applicant too, so this is the same
    # route with a different credential -- and it still has its own full
    # budget.
    assert _submit(client, headers=OPERATOR).status_code == 201


def test_resume_is_limited_too(client):
    """The other POST that drives the federation. 404 rather than 202 is
    fine: what is asserted is which status the LIMITER produces."""
    for _ in range(app_module.RATE_LIMIT_CAPACITY):
        assert client.post("/requests/nosuch/resume", headers=OPERATOR).status_code == 404
    assert client.post("/requests/nosuch/resume", headers=OPERATOR).status_code == 429


def test_reads_are_not_limited(client):
    """Discovery is what a body deciding whether to join uses; rate-limiting
    it would put the catalogue behind the people who already know."""
    for _ in range(app_module.RATE_LIMIT_CAPACITY * 2):
        assert client.get("/catalogue", headers=APPLICANT).status_code == 200


def test_an_unauthenticated_caller_never_reaches_the_bucket(client):
    """The limiter keys on the bearer token, so it has to run after auth --
    otherwise there is no key. Asserted so the dependency order is a
    decision."""
    for _ in range(app_module.RATE_LIMIT_CAPACITY * 2):
        assert client.post("/requests", json=BAD_PAYLOAD,
                           headers={CONSOLE_HEADER: "1"}).status_code == 401
    assert not app_module._BUCKETS


# -- the store quota -----------------------------------------------------------


def test_a_full_out_join_refuses_new_submissions_naming_the_remedy(client, monkeypatch):
    monkeypatch.setattr(app_module, "STORE_QUOTA", 3)
    conn = _conn()
    for i in range(3):
        app_module.store.save_request(
            conn, {"id": f"seed{i}", "state": "SUBMITTED", "submitted_at": "2026-01-01T00:00:00+00:00"},
            actor="system", event="test-seed",
        )
    resp = _submit(client)
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert "join store" in detail and "3" in detail


def test_the_quota_is_checked_before_validation_does_any_work(client, monkeypatch):
    """Refusing after fetching an applicant-controlled spec_url would make a
    full store an amplifier rather than a brake."""
    monkeypatch.setattr(app_module, "STORE_QUOTA", 0)

    def _boom(*args, **kwargs):
        raise AssertionError("validation ran despite a full store")

    monkeypatch.setattr(app_module.validate, "validate", _boom)
    assert _submit(client).status_code == 429


def test_below_the_quota_a_submission_is_accepted(client, monkeypatch):
    monkeypatch.setattr(app_module, "STORE_QUOTA", 2)
    app_module.store.save_request(
        _conn(), {"id": "seed", "state": "SUBMITTED", "submitted_at": "2026-01-01T00:00:00+00:00"},
        actor="system", event="test-seed",
    )
    assert _submit(client).status_code == 201
