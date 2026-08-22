"""apps/join-api/app.py -- the KP2 member-join API: liveness, the
credentials this service drives the admin API with, and the same
request-boundary guard apps/console/app.py uses. POST /requests and GET
/requests/{id} run validation (validate.py) and config-diff computation
(writer.py's dry-run mode) synchronously at submission. On the operator
side, POST /requests/{id}/approve writes the config for real
(writer.apply_real) and starts the job (job.py) on a background thread, one
at a time; POST /requests/{id}/resume re-runs a FAILED one from its
last_completed_step. DELETE /members/{key} is the other direction: it walks
a completed job backwards (job.unjoin) and then delegates the config half
to scripts/member.sh remove. GET /catalogue reads rather than writes: what
is published on this instance, for a body that has just joined or is
deciding whether to.

Credentials come from the environment (.env via Docker Compose), read here
once, never returned in a response or logged -- same rule as
apps/console/app.py (see that file's own docstring)."""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import math
import os
import pathlib
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

# schema.py/validate.py/writer.py live beside this file. A bare `import
# validate` (which itself does `from schema import ...`) only resolves if
# this directory is on sys.path -- true when uvicorn runs `app:app` from
# WORKDIR /app (mirrors apps/console/app.py's bare `import journal`), but
# NOT when a test loads this file via importlib.util.spec_from_file_location
# under a distinct module name (apps/join-api/tests/test_app_health.py's own
# comment explains why that trick is used instead of `import app` +
# sys.path.insert). Inserting this file's own directory here makes the
# import work either way, without requiring every test file to do it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import job  # noqa: E402
import logging_setup  # noqa: E402
import schema  # noqa: E402
import store  # noqa: E402
import validate  # noqa: E402
import writer  # noqa: E402

PACK_DIR = pathlib.Path(os.environ.get("PACK_DIR", "/pack"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/out"))

# -- the store connection --------------------------------------------------
# sqlite3.Connection objects are not safe to share across threads
# (check_same_thread=True), and this process has two execution contexts that
# touch the store: FastAPI's request handlers, and the background daemon
# threads _start_job/_start_unjoin launch. Each gets its own connection,
# opened where it runs, never handed across a thread boundary (see
# store.py's own docstring).
#
# _conn() resolves against the CURRENT OUT_DIR on every call, not a path
# cached at import time: this file's own test fixtures reassign
# app_module.OUT_DIR per test for isolation (the same pattern the old
# file-backed _requests_dir() relied on by re-reading the OUT_DIR global on
# every call), and store.init() is idempotent, so re-running it here is the
# cheap way to keep that working for the store too.


def _conn(*, readonly: bool = False):
    return store.connect(
        store.init(OUT_DIR, kind=_DATASTORE_KIND, db_url=KP2_JOIN_DB_URL),
        readonly=readonly,
    )


def get_conn():
    """FastAPI dependency: one connection per request, closed on the way
    out."""
    conn = _conn()
    try:
        yield conn
    finally:
        conn.close()


# Held server-side only, exactly like apps/console's ADMIN_USER/ADMIN_PASSWORD.
# This is the one place credentials enter the process; job.py receives them as
# an explicit argument (JOB_SECRETS below) rather than reading the environment
# itself, so the module that shells out to Hurl has exactly the values it was
# handed and nothing else.
ADMIN_USER = os.environ.get("XROAD_ADMIN_USER", "xrd")
ADMIN_PASSWORD = os.environ["XROAD_ADMIN_PASSWORD"]
TOKEN_PIN = os.environ["XROAD_TOKEN_PIN"]

# Moved up from beside "the operator queue" section (where it stayed for
# most of this file's history) to right after the three raw values it is
# built from: logging_setup.configure() below needs it immediately, to scrub
# every record this process emits from the very first line -- see
# "-- structured logging (E.1) --" a few lines down. Nothing between the old
# and new position reads JOB_SECRETS before job.run()/job.unjoin() are
# actually called (both post-approval), so the move changes nothing else.
JOB_SECRETS = {
    "ss_admin_user": ADMIN_USER,
    "ss_admin_password": ADMIN_PASSWORD,
    "token_pin": TOKEN_PIN,
}

# -- structured logging (E.1, docs/production-delta.md row 34) --------------
# JSON-lines to stdout, stdlib `logging` only -- logging_setup.py's own
# docstring has the full design. Every record this logger emits passes
# through job.scrub(..., JOB_SECRETS) first (logging_setup.ScrubFilter),
# the identical guard app.py already applies to subprocess output before
# persisting it (JOB_SECRETS' own comment below, `job.scrub` calls at this
# file's GenerateFailure/RollbackFailure handlers) -- logs are the same
# class of sink, and get the same treatment, tested the same way
# (tests/test_app_health.py: a log record built from a real secret value
# must not contain it once formatted).
_LOG = logging_setup.configure("kp2.join-api", JOB_SECRETS)


def _required_token(name: str, *, allow_disabled: bool = False) -> str:
    """scripts/lib-stack.sh refuses to run while XROAD_TOKEN_PIN or
    XROAD_ADMIN_PASSWORD are still a placeholder from .env.example
    on purpose. Same idea, applied
    here rather than in lib-stack.sh: only join-api cares about these two
    tokens, and every other script that sources lib-stack.sh (console.sh,
    member.sh, ...) has no reason to fail over a secret it never uses.

    `allow_disabled=True` is KP2_JOIN_APPLICANT_TOKEN's own switch (row 28,
    docs/production-delta.md): the sentinel string "disabled" -- matched
    case-insensitively and with surrounding whitespace stripped (.env is
    shell-sourced; a trailing space or a capitalised "Disabled" is an easy
    typo to introduce and must not fail OPEN into that literal string
    becoming a live, guessable shared token) -- returns the single
    canonical value "disabled", never the raw/un-normalised text, for
    require_applicant to compare against with == and skip the shared-token
    comparison entirely. Not empty/absent, which docker-compose.yml's `:-`
    default already passes through, so absence would silently mean
    "disabled" the moment an operator forgot the .env line. Every other
    caller (KP2_JOIN_OPERATOR_TOKEN) has that same sentinel (any spelling
    of it) refused outright -- the operator credential can never be
    disabled this way."""
    value = os.environ.get(name, "")
    if value.strip().lower() == "disabled":
        if allow_disabled:
            return "disabled"
        raise RuntimeError(
            f"join-api: {name} is set to the sentinel string 'disabled', which "
            "only KP2_JOIN_APPLICANT_TOKEN may be. Set a real token (run "
            "scripts/gen-secrets.sh)."
        )
    if not value or "CHANGEME" in value:
        raise RuntimeError(
            f"join-api: {name} is unset or still the .env.example placeholder. "
            "Run scripts/gen-secrets.sh to generate a real .env."
        )
    return value


APPLICANT_TOKEN = _required_token("KP2_JOIN_APPLICANT_TOKEN", allow_disabled=True)
OPERATOR_TOKEN = _required_token("KP2_JOIN_OPERATOR_TOKEN")

# -- store backend selection (plan §1.6) ---------------------------------------
# deployment.yaml's datastore.kind: the seam that makes `kind: postgres` fail
# loudly at import time (store.backend_for raises NotImplementedError for
# anything else) instead of silently being ignored. Missing file defaults to
# "sqlite" -- many unit tests point PACK_DIR at a throwaway fixture directory
# with no deployment.yaml, same as manifest.yaml/join-policy.yaml being read
# lazily rather than required at import time.
#
# Read here, next to the other credentials, NOT down by the startup sweep
# where this used to live: _conn()/get_conn() (defined above, first called by
# the startup sweep a few lines down) need _DATASTORE_KIND/KP2_JOIN_DB_URL
# already resolved the first time either runs.
try:
    _deployment_doc = yaml.safe_load((PACK_DIR / "deployment.yaml").read_text()) or {}
except FileNotFoundError:
    _deployment_doc = {}
_DATASTORE_KIND = (_deployment_doc.get("datastore") or {}).get("kind", "sqlite")
store.backend_for(_DATASTORE_KIND)

# deployment.yaml's join_workflow.commit_gate (docs/production-delta.md row
# 33): "advisory" (default, docker-local) is today's behaviour -- the
# console shows the live-but-uncommitted flag, nothing gates. "required"
# (the droplet target) makes approve_request stamp this value onto the
# record, which job.py reads back at run() time to decide whether to plan a
# config.commit gate step -- job.py itself never reads deployment.yaml (same
# split _DATASTORE_KIND above makes). hurl/generate.py's check_join_workflow
# admits the same two values at generation time; this is the join-api
# process's own copy of that same refusal, so a bad value fails loudly at
# startup here too rather than silently defaulting to "advisory".
_COMMIT_GATE = (_deployment_doc.get("join_workflow") or {}).get("commit_gate", "advisory")
if _COMMIT_GATE not in ("advisory", "required"):
    raise RuntimeError(
        f"join-api: deployment.yaml join_workflow.commit_gate {_COMMIT_GATE!r} is not "
        "'advisory' or 'required'."
    )

# deployment.yaml's join_workflow.enforce_ownership (docs/production-delta.md
# row 28): False (default, docker-local) is today's behaviour -- any
# applicant or operator credential may read any request record. True (the
# droplet target) turns on the one comparison this module used to promise
# was missing -- see _owns_record below, applied to GET /requests/{id}. Read
# and validated here, next to _COMMIT_GATE, for the same reason: a bad value
# fails loudly at startup rather than an app.py route silently treating a
# typo as "false".
_ENFORCE_OWNERSHIP = (_deployment_doc.get("join_workflow") or {}).get("enforce_ownership", False)
if not isinstance(_ENFORCE_OWNERSHIP, bool):
    raise RuntimeError(
        f"join-api: deployment.yaml join_workflow.enforce_ownership {_ENFORCE_OWNERSHIP!r} is not "
        "true or false."
    )
# Same _required_token refusal APPLICANT_TOKEN/OPERATOR_TOKEN get above,
# applied to the Postgres DSN: unset or still the .env.example CHANGEME
# placeholder must fail loudly at startup, not hand psycopg a string it will
# only fail to parse (or, worse, connect with) later. None on the SQLite
# path -- KP2_JOIN_DB_URL is Postgres-only, and _conn() only reads it when
# _DATASTORE_KIND == "postgres" (store.init() ignores db_url for "sqlite").
KP2_JOIN_DB_URL = _required_token("KP2_JOIN_DB_URL") if _DATASTORE_KIND == "postgres" else None

# Request-boundary guard -- copied verbatim from apps/console/app.py's
# _require_console_origin: a required custom
# header a cross-origin request cannot set without triggering a CORS
# preflight this app never answers with permission, plus an Origin check
# when the browser sends one.
CONSOLE_HEADER = "x-kp2-console"


def _require_console_origin(request: Request) -> None:
    if request.headers.get(CONSOLE_HEADER) != "1":
        raise HTTPException(
            403,
            f"missing required header {CONSOLE_HEADER}: 1 -- this endpoint "
            "refuses requests without it (add the header and retry)",
        )
    origin = request.headers.get("origin")
    if origin is not None:
        host = request.headers.get("host", "")
        if origin not in (f"http://{host}", f"https://{host}"):
            raise HTTPException(403, f"Origin {origin!r} does not match this API's own host {host!r}")
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site is not None and sec_fetch_site != "same-origin":
        raise HTTPException(403, f"Sec-Fetch-Site {sec_fetch_site!r} is not same-origin")


# Bearer-token auth: two roles, applicant and
# operator, each its own token from scripts/gen-secrets.sh, plus any number
# of named applicant credentials the operator issues (POST /tokens below).
# Per-request OWNERSHIP -- the one comparison this comment used to say was
# deliberately missing -- now exists, behind deployment.yaml's
# join_workflow.enforce_ownership (default false, docker-local: in a demo
# where one person plays both roles it is machinery guarding a boundary
# nobody crosses). See _owns_record below, applied to GET /requests/{id}.
# The *asymmetry* stays the teaching point regardless: an applicant cannot
# approve.
def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(401, "missing or malformed Authorization: Bearer <token> header")
    return value


# -- issued applicant tokens ---------------------------------------------------
#
# One shared KP2_JOIN_APPLICANT_TOKEN for every applicant is a demo shortcut
# with a real cost: nothing on a request says WHICH agency submitted it, and
# revoking one agency's access revokes everyone's
# (docs/production-delta.md). The operator can now issue a named credential
# per agency instead. The shared token stays -- it is the console's
# server-side credential and the zero-setup demo path -- so this is an
# addition to the token model, not a replacement of it. mTLS, which is what
# that delta row actually asks for in production, is still out of scope.
#
# Tokens live in the same SQLite store as request records now (store.py's
# `tokens` table) -- see the store wiring below. Keeping them out of
# out/join/ used to matter because that directory was globbed for request
# records; with one DB and two separate tables a token row can never be
# miscounted as a request row by construction.
_TOKEN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_applicant(request: Request, db: sqlite3.Connection = Depends(get_conn)) -> str:
    """Applicant may read any request -- any valid token, applicant
    or operator, satisfies this dependency. Used by read/submit routes.

    Three credentials, tried in that order: the operator token, the shared
    applicant token, then any token the operator has issued to a named
    agency. An issued token resolves to "applicant:<name>", which is what
    POST /requests records as submitted_by. A row that is revoked or expired
    (plan §1.4) is treated as no match, same as a token nobody ever issued --
    falls through to the 403 below, exactly like today's
    revocation-takes-effect-on-next-request behaviour, with expiry added."""
    token = _bearer_token(request)
    if secrets.compare_digest(token, OPERATOR_TOKEN):
        return "operator"
    # APPLICANT_TOKEN == "disabled" (row 28, docs/production-delta.md):
    # the shared credential is off, and the comparison against it is
    # skipped entirely rather than attempted -- every applicant call must
    # arrive on an issued per-agency credential instead (below). The
    # console is unaffected: its join tab holds the *operator* token
    # server-side (docker-compose.yml's own comment), never this one.
    if APPLICANT_TOKEN != "disabled" and secrets.compare_digest(token, APPLICANT_TOKEN):
        return "applicant"
    row = store.find_token(db, _token_digest(token))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if row is not None and row["revoked_at"] is None and (
        row["expires_at"] is None or row["expires_at"] > now
    ):
        return f"applicant:{row['name']}"
    # Auth failure: the token DIGEST PREFIX only, never the raw token --
    # same rule and same helper (_refusal_actor, defined below in this
    # file) the 429 refusal path already applies to a bearer token that
    # must never reach a log line or a persisted record whole.
    _LOG.info("auth.rejected", extra={"extra_fields": {"role": "applicant", "actor": _refusal_actor(request)}})
    raise HTTPException(403, "token does not match either configured role")


def require_operator(request: Request) -> str:
    """Approve, reject and resume are operator-only -- the
    applicant token is rejected here, not just left unchecked."""
    token = _bearer_token(request)
    if secrets.compare_digest(token, OPERATOR_TOKEN):
        return "operator"
    _LOG.info("auth.rejected", extra={"extra_fields": {"role": "operator", "actor": _refusal_actor(request)}})
    raise HTTPException(403, "operator token required for this endpoint")


# -- rate limit and store quota ------------------------------------------------
#
# The join API can register federation members, and had no limit of any kind
# on how often it would be asked to (docs/production-delta.md). What follows
# is the demo-sized answer: an in-process token bucket per bearer token, no
# slowapi, no Redis, nothing shared between replicas -- because there are no
# replicas. The production side of that row stays open on purpose: a
# distributed quota and abuse monitoring are not this.
#
# The numbers live here, next to their use, the same way job.py keeps
# RETRY_BUDGET -- NOT in configs/x-road-bus/join-policy.yaml. A rate limit is
# a property of this service instance (its memory, its disk), not of a join,
# so that file would be the wrong scope for it regardless of value, exactly
# as its own comment says of approval mode.
#
# 30/minute is deliberately generous: scripts/acceptance.sh's 2.7 section
# submits nothing at all (it reads /health and /catalogue), and exercises.md's
# join/un-join loop submits a handful, so nothing this pack itself does comes
# near the limit. It bounds a script gone wrong, not a demonstration.
RATE_LIMIT_CAPACITY = 30
RATE_LIMIT_REFILL_PER_MINUTE = 30

# The other half of the same worry: the join store holds every request
# record ever submitted, and nothing evicts. A submission is refused once it
# holds this many records -- the remedy is naming in the message, because
# there is no eviction policy here and inventing one would be pretending
# this is a production-sized retention story.
STORE_QUOTA = 200

# token sha256 -> (tokens left, monotonic time that count was computed at).
# Keyed by digest rather than by the credential itself so a traceback, a
# repr() or a memory dump of this dict does not carry a live token.
_BUCKETS: dict[str, tuple[float, float]] = {}
_BUCKET_LOCK = threading.Lock()
# Indirected so tests can drive refill without sleeping. Nothing else
# reassigns it.
_clock = time.monotonic


def _take_token(bucket_key: str) -> float | None:
    """None if the caller may proceed; otherwise the seconds until it may."""
    per_second = RATE_LIMIT_REFILL_PER_MINUTE / 60.0
    now = _clock()
    with _BUCKET_LOCK:
        left, last = _BUCKETS.get(bucket_key, (float(RATE_LIMIT_CAPACITY), now))
        left = min(float(RATE_LIMIT_CAPACITY), left + (now - last) * per_second)
        if left < 1.0:
            _BUCKETS[bucket_key] = (left, now)
            return (1.0 - left) / per_second
        _BUCKETS[bucket_key] = (left - 1.0, now)
        return None


# How much of a bearer token's sha256 digest is worth recording against a
# 429 refusal (plan §1.5): enough to correlate repeat offenders across
# request_events rows, never the full digest and never the plaintext -- a
# hash is still a credential-shaped secret (this file's own /tokens rule).
_REFUSAL_ACTOR_DIGEST_LEN = 12


def _refusal_actor(request: Request) -> str:
    return hashlib.sha256(_bearer_token(request).encode()).hexdigest()[:_REFUSAL_ACTOR_DIGEST_LEN]


def rate_limit(request: Request, db: sqlite3.Connection = Depends(get_conn)) -> None:
    """One bucket per bearer token, so the applicant and operator credentials
    cannot exhaust each other's budget -- and, once per-agency tokens exist,
    neither can two agencies. Applied to the two POSTs that drive the
    federation (submit, resume). Reads stay unlimited: discovery
    (GET /catalogue) is what a body deciding whether to join uses, and
    approve/reject are already operator-gated and 409-guarded by state."""
    wait = _take_token(hashlib.sha256(_bearer_token(request).encode()).hexdigest())
    if wait is None:
        return
    _refuse(db, actor=_refusal_actor(request), event="rate_limit")
    _metric_inc("rate_limited_total")
    _LOG.info("rate_limit.refused", extra={"extra_fields": {"actor": _refusal_actor(request), "retry_after_s": max(1, math.ceil(wait))}})
    raise HTTPException(
        429,
        f"rate limit: this credential may make {RATE_LIMIT_REFILL_PER_MINUTE} of these "
        f"requests per minute (burst {RATE_LIMIT_CAPACITY}). Retry in "
        f"{math.ceil(wait)}s, or slow the caller down -- this endpoint registers "
        "federation members.",
        headers={"Retry-After": str(max(1, math.ceil(wait)))},
    )


app = FastAPI(title="KP2 member-join API")


# -- request-id middleware (E.1) ----------------------------------------------
# One id per HTTP request, generated the same way join request ids are
# (secrets.token_urlsafe), set on logging_setup.request_id_ctx for the
# duration of the call (every _LOG.info(...) issued while handling this
# request picks it up automatically -- JsonFormatter reads the same
# contextvar) and returned as X-Request-Id. `_save()`/`_refuse()` below also
# stamp it into request_events.detail, so a JSON log line and its
# audit-table row can be joined on this one value -- distinct from
# `join_id` (the join request's own record id, stable across the several
# HTTP calls -- submit, approve, resume -- and the background job/unjoin
# threads a single join spans; see `_job_log`).
@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    request_id = secrets.token_urlsafe(8)
    token = logging_setup.request_id_ctx.set(request_id)
    try:
        response = await call_next(request)
    finally:
        logging_setup.request_id_ctx.reset(token)
    response.headers["X-Request-Id"] = request_id
    return response


def _save(conn, record: dict, *, actor: str, event: str, detail: dict | None = None) -> None:
    """store.save_request, plus the request-id middleware's id stamped into
    detail -- the log/audit join key described above. `None` for the
    background job/unjoin threads (no HTTP request is in flight there);
    those events are already correlated by `join_id` instead."""
    merged = dict(detail or {})
    merged["request_id"] = logging_setup.request_id_ctx.get()
    store.save_request(conn, record, actor=actor, event=event, detail=merged)


def _refuse(conn, *, actor: str, event: str) -> None:
    store.log_refusal(conn, actor=actor, event=event, detail={"request_id": logging_setup.request_id_ctx.get()})


def _job_log(join_id: str, event: str, **fields) -> None:
    """The `log=` callable threaded into job.run()/job.unjoin() (job.py's
    own seam -- see its module docstring on why job.py itself never imports
    `logging`). Every call carries `join_id` -- the join request's own
    record id -- so a live join's whole lifecycle (submit, approve, every
    job step, the final ACTIVE/FAILED/BLOCKED) greps as one value, spanning
    the several separate HTTP requests and the background thread the job
    runs on, none of which share one X-Request-Id."""
    _LOG.info(event, extra={"extra_fields": {"join_id": join_id, **fields}})


@app.get("/health")
def health():
    return {"status": "ok"}


# -- Prometheus text-format metrics (E.2, docs/production-delta.md row 34) --
# Hand-rolled -- no prometheus_client (Global constraints: no new Python
# dependency for E.1/E.2). This is a *surface*, not a monitoring system:
# nothing scrapes it by default, no alerting, no retention -- runbook.md's
# scrape-config note says so, and so does the row this closes half of.
# Gated the same way every other operator-only route is (Depends(require_operator)),
# not a new auth path: Prometheus scrapes with a bearer header, which is
# exactly what that dependency already checks. Deliberately NOT behind
# _require_console_origin -- that guard exists for a BROWSER's cross-origin
# request (the custom header, the Origin/Sec-Fetch-Site checks); a
# Prometheus server is a plain server-to-server scrape that sends none of
# those, and gating on them would make this endpoint unreachable by the
# thing it exists for.
def _render_metrics(db: sqlite3.Connection) -> str:
    lines: list[str] = []
    lines.append("# HELP kp2_join_requests Join requests currently in each state.")
    lines.append("# TYPE kp2_join_requests gauge")
    for state, n in sorted(store.count_requests_by_state(db).items()):
        lines.append(f'kp2_join_requests{{state="{state}"}} {n}')
    lines.append("# HELP kp2_join_store_requests Records held in the join store (store.count_requests).")
    lines.append("# TYPE kp2_join_store_requests gauge")
    lines.append(f'kp2_join_store_requests{{backend="{_DATASTORE_KIND}"}} {store.count_requests(db)}')
    lines.append("# HELP kp2_join_store_quota The store's refusal ceiling (STORE_QUOTA) -- not a usage value.")
    lines.append("# TYPE kp2_join_store_quota gauge")
    lines.append(f"kp2_join_store_quota {STORE_QUOTA}")
    lines.append("# HELP kp2_join_rate_limited_total 429 refusals (per-minute rate limit + store quota) since process start.")
    lines.append("# TYPE kp2_join_rate_limited_total counter")
    lines.append(f'kp2_join_rate_limited_total {_METRICS.get("rate_limited_total", 0)}')
    lines.append("# HELP kp2_join_job_steps_total Job/unjoin steps completed, by outcome, since process start.")
    lines.append("# TYPE kp2_join_job_steps_total counter")
    lines.append(f'kp2_join_job_steps_total{{outcome="success"}} {_METRICS.get("job_steps_completed_total", 0)}')
    lines.append(f'kp2_join_job_steps_total{{outcome="failed"}} {_METRICS.get("job_steps_failed_total", 0)}')
    lines.append("# HELP kp2_join_job_duration_seconds Wall-clock duration of a completed job/unjoin run.")
    lines.append("# TYPE kp2_join_job_duration_seconds summary")
    lines.append(f'kp2_join_job_duration_seconds_sum {_METRICS.get("job_duration_seconds_sum", 0.0)}')
    lines.append(f'kp2_join_job_duration_seconds_count {_METRICS.get("job_duration_seconds_count", 0)}')
    return "\n".join(lines) + "\n"


@app.get("/metrics")
def metrics(
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> PlainTextResponse:
    return PlainTextResponse(_render_metrics(db), media_type="text/plain; version=0.0.4")


# -- token administration (operator only) --------------------------------------


@app.post("/tokens", status_code=201)
def issue_token(
    body: dict,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Issue a named applicant credential for one agency.

    The plaintext is in this response and nowhere else: only
    {name, sha256, issued_at, expires_at} is persisted, and the value is
    never logged -- the same rule this module's docstring states for the
    admin credentials. An operator who loses it revokes the name and issues
    a new one; there is no retrieval endpoint, because a store that can
    return a credential is a store that can leak one.

    `expires_in_days` is optional (plan §1.4): absent means no expiry, same
    as today's default."""
    agency = (body or {}).get("agency")
    if not isinstance(agency, str) or not _TOKEN_NAME_RE.fullmatch(agency):
        raise HTTPException(
            400,
            "agency is required and must be 1-64 characters of letters, digits, "
            "'-' or '_' (it names the credential and becomes a URL path segment "
            "on DELETE /tokens/{name})",
        )
    expires_in_days = (body or {}).get("expires_in_days")
    if expires_in_days is not None and (not isinstance(expires_in_days, int) or isinstance(expires_in_days, bool) or expires_in_days <= 0):
        raise HTTPException(400, "expires_in_days must be a positive integer when present")
    value = secrets.token_urlsafe(24)
    try:
        store.issue_token(db, agency, _token_digest(value), expires_in_days=expires_in_days)
    except store.NameAlreadyUsed as exc:
        if exc.revoked:
            raise HTTPException(
                409,
                f"{agency!r} was already used and revoked -- choose a different name "
                "(revoked names cannot be reissued; the issuance stays on the books as evidence)",
            ) from exc
        raise HTTPException(
            409,
            f"a token is already issued to {agency!r} -- revoke it first "
            f"(DELETE /tokens/{agency}) rather than issuing a second one, so "
            "one name always means one live credential",
        ) from exc
    return {"agency": agency, "token": value,
            "note": "shown once -- this API stores only its sha256"}


@app.get("/tokens")
def list_tokens(
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Who holds a credential, since when, and whether it has been revoked
    (plan §1.4). Never the hashes: a hash is still a credential-shaped
    secret, and an offline guess against a 24-byte token is only impossible
    while the hash is not in hand."""
    return {"tokens": [
        {"agency": entry["name"], "issued_at": entry["issued_at"], "revoked_at": entry["revoked_at"]}
        for entry in store.list_tokens(db)
    ]}


@app.delete("/tokens/{agency}")
def revoke_token(
    agency: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Revocation. Takes effect on the next request -- require_applicant
    reads the store rather than caching it. The join requests this agency
    already submitted are untouched: they are the record of a decision, and
    they keep naming it in submitted_by. A soft-delete (revoked_at set), not
    a row removal -- the issuance stays on the books as evidence (plan
    §1.4)."""
    if not store.revoke_token(db, agency):
        raise HTTPException(404, f"no token issued to {agency!r}")
    return {"agency": agency, "revoked": True}


@app.get("/catalogue")
def get_catalogue(
    _origin: None = Depends(_require_console_origin),
    # The applicant credential, not the operator one. The reader who needs a
    # service catalogue is a body that has just joined, or is deciding
    # whether to; gating that behind the operator credential would put
    # discovery back behind the people who already know what is published.
    _role: str = Depends(require_applicant),
) -> dict:
    """Every service published on this instance, as JSON -- the same derived
    data onboarding/catalogue.yaml carries, read from the member configs at
    request time. No write path, and it never talks to X-Road.

    What this answers is which services were registered here, not which ones
    a given caller may invoke: the response says so in a field of its own so
    that a client rendering it cannot leave the caveat out by accident."""
    return writer.catalogue_data(PACK_DIR)


# -- request persistence -------------------------------------------------------
# The join-api's own SQLite store (apps/join-api/store.py) now owns every
# request record; this section keeps only the id charset check, a
# trust-boundary guard on caller input that stays in app.py by design
# (store.py's own docstring, plan §1.2) -- request_id comes off the URL path
# and would otherwise reach a query unchecked.

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def _load_request(db: sqlite3.Connection, request_id: str) -> dict | None:
    """The charset check + store.load_request(), together -- request_id
    comes off a URL path in every caller, so this pairing is repeated at
    every /requests/{id} route rather than trusted to remember on its own."""
    if not _REQUEST_ID_RE.fullmatch(request_id):
        return None
    return store.load_request(db, request_id)


# A job's record can be stuck at RUNNING forever if the process running it
# stops mid-job -- scripts/join.sh down, a rebuild, or even acceptance.sh's
# own 2.7 section (which brings join-api up and back down around its
# checks). job.run() itself already resumes correctly from any record
# carrying last_completed_step (see its own docstring), but resume_request
# only accepts FAILED or BLOCKED -- never RUNNING, so two runners can never
# land on one live job -- so a record left at RUNNING is otherwise
# unrecoverable through this API. store.recover_interrupted() sweeps every
# such row to FAILED, in one transaction, once at import time: this process
# is, by construction, not the one that was running that job -- if it were
# still running, this module would not be re-executing from the top.
#
# Kept at module level, self-invoking on import, exactly like the file-backed
# sweep it replaces -- NOT a FastAPI @app.on_event("startup") handler.
# apps/join-api/tests/test_app_startup.py depends on "importing this module
# runs the sweep" (it loads app.py via importlib.util.spec_from_file_location
# specifically to trigger this side effect), asserting on state immediately
# after exec_module, before any TestClient exists.
with contextlib.closing(_conn()) as _startup_conn:
    store.recover_interrupted(_startup_conn)

    # Migration refusal (plan §2): if out/join/*.json request files still
    # exist beside a DB that holds none, this process must refuse to start
    # rather than silently serve out of an empty store while evidence sits
    # unmigrated next to it. scripts/migrate-join-store.py (a later task)
    # is the remedy; this check only needs to name it.
    _stale_requests_dir = OUT_DIR / "join"
    if (
        _stale_requests_dir.is_dir()
        and any(_stale_requests_dir.glob("*.json"))
        and store.count_requests(_startup_conn) == 0
    ):
        raise RuntimeError(
            f"{_stale_requests_dir} still holds *.json request records that have not "
            f"been migrated into the SQLite store ({store.db_path(OUT_DIR)}). Run "
            "scripts/migrate-join-store.py before starting join-api, so this process "
            "never silently half-reads two stores."
        )


def _load_manifest() -> dict:
    return yaml.safe_load((PACK_DIR / "manifest.yaml").read_text())


def _load_join_policy() -> dict:
    """configs/x-road-bus/join-policy.yaml's join: block only -- not
    the whole file, mirroring validate.py's own ValidationContext.policy."""
    doc = yaml.safe_load((PACK_DIR / "configs" / "x-road-bus" / "join-policy.yaml").read_text()) or {}
    return doc.get("join") or {}


@app.post("/requests", status_code=201)
def submit_request(
    raw: dict,
    request: Request,
    _origin: None = Depends(_require_console_origin),
    role: str = Depends(require_applicant),
    _rate: None = Depends(rate_limit),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Validate synchronously (eleven per-request checks plus
    lawful_basis, sla_required, spec_url_origin and allowed_backend_auth,
    additions beyond those eleven -- validate.py's own module docstring:
    check 5 moved to generate-time),
    then either persist
    a REJECTED record or -- on success -- write the candidate config to a
    throwaway copy of the pack, run its generate.py, and persist a SUBMITTED
    record carrying the resulting diff. Either way: 201 (the
    applicant retrieves the outcome via GET /requests/{id}, there is no
    separate failure status here). A malformed body (bad JSON, wrong types,
    an unrecognised key) is check 1 ("schema") -- validate() itself does
    `JoinPayload(**raw)` and turns a pydantic.ValidationError into
    RejectionError("schema", ...), so nothing here hand-rolls that check."""
    # The store's own ceiling, checked before validation does any work (or
    # any fetching). Nothing evicts, nothing rotates, and a request record is
    # evidence, so the honest response to a full store is to refuse and say
    # where to look -- not to invent a retention policy for it here.
    held = store.count_requests(db)
    if held >= STORE_QUOTA:
        _refuse(db, actor=_refusal_actor(request), event="quota")
        _metric_inc("rate_limited_total")
        raise HTTPException(
            429,
            f"the join store already holds {held} request records, the limit this "
            f"service accepts ({STORE_QUOTA}). Nothing is evicted automatically -- "
            "each record is the evidence of a decision. Archive or remove the "
            "settled ones (REJECTED and RETIRED) and retry.",
        )
    request_id = secrets.token_urlsafe(8)
    submitted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Which agency's credential this arrived on, when it arrived on an issued
    # one. None for the shared applicant token and for the operator, because
    # neither identifies anybody -- recording "applicant" there would be a
    # field that looks like attribution and is not. This is the field this
    # module's auth comment says per-request ownership would need; it is
    # recorded, not yet enforced on -- the demo's one-person-two-roles
    # ergonomics stay.
    submitted_by = role.split(":", 1)[1] if role.startswith("applicant:") else None

    try:
        payload, vctx = validate.validate(
            raw,
            manifest=_load_manifest(),
            policy=_load_join_policy(),
            existing_servers=validate.load_existing_security_servers(PACK_DIR),
            semantic_map=validate.load_semantic_map(PACK_DIR),
        )
    except validate.RejectionError as exc:
        record = {
            "id": request_id,
            "state": "REJECTED",
            "submitted_at": submitted_at,
            "submitted_by": submitted_by,
            "payload": raw,
            "rejection": {"check": exc.check, "message": exc.message},
        }
        _save(db, record, actor=role, event="rejected", detail={"check": exc.check})
        return record

    key = payload.code.lower()
    try:
        diff = writer.dry_run_diff(PACK_DIR, key, payload)
    except writer.GenerateFailure as exc:
        # Every one of the fifteen per-request checks passed, but generate.py itself still
        # refused the result (e.g. check_join_policy's static cross-check) --
        # a real, if rarer, rejection. Surfaced the same way: a REJECTED
        # record, never a bare 500 -- submission always
        # returns 201 -- stderr is passed through verbatim.
        record = {
            "id": request_id,
            "state": "REJECTED",
            "submitted_at": submitted_at,
            "submitted_by": submitted_by,
            "payload": raw,
            "rejection": {
                "check": "generate_dry_run",
                # Scrubbed for the same reason the approve endpoint scrubs its
                # copy: dry_run_diff's pack copy includes .env, which
                # generate.py reads, so a traceback out of that subprocess
                # could carry a credential into a persisted record.
                "message": f"hurl/generate.py rejected this join (exit {exc.returncode}):\n"
                + job.scrub(exc.stderr, JOB_SECRETS),
            },
        }
        _save(db, record, actor=role, event="rejected", detail={"check": "generate_dry_run"})
        return record

    record = {
        "id": request_id,
        "state": "SUBMITTED",
        "submitted_at": submitted_at,
        "submitted_by": submitted_by,
        "payload": payload.model_dump(mode="json"),
        "diff": diff,
        # The join-time drift baseline: each published service's
        # endpoint set, as check 9 (_check_backend_reachability) already
        # fetched and parsed it into vctx.fetched_specs. scripts/member.sh
        # drift re-fetches the *current* spec later and diffs its paths
        # against this -- the whole point being that this baseline is
        # captured once, at join time, and never re-derived.
        "endpoint_baseline": {
            code: sorted((spec_doc or {}).get("paths", {}).keys())
            for code, spec_doc in vctx.fetched_specs.items()
        },
        # A service's declared and required response fields, captured once
        # here (check 9 already parsed the spec) and read back by job.py's
        # r1 step at approval time -- never re-fetched, so the record
        # verifies against the contract the member was ADMITTED on, not
        # against whatever spec_url serves after approval.
        "contract_fields": {
            code: {"declared": sorted(declared), "required": sorted(required)}
            for code, (declared, required) in vctx.contract_fields.items()
        },
    }
    _save(db, record, actor=role, event="submitted")
    return record


def _owns_record(role: str, record: dict) -> bool:
    """True if `role` (require_applicant's return value) may read `record`
    under join_workflow.enforce_ownership: true (docs/production-delta.md
    row 28). The operator reads everything. An issued applicant:<name>
    credential reads only the record it submitted -- name equals
    submitted_by, nothing looser. The shared applicant token reads only a
    record with submitted_by: null -- the ones nothing else could have
    submitted; role can only BE the bare "applicant" value when the shared
    token matched in require_applicant, which cannot happen at all once
    APPLICANT_TOKEN == "disabled" (require_applicant skips that comparison
    entirely), so this branch is already conditioned on the shared token
    still being enabled without a separate check for it here.

    Ownership enforcement is only meaningful once the shared token is also
    disabled: with it still enabled, every hand-typed applicant call shares
    one identity, so this only ever protects per-agency (issued-token)
    records from each other, nothing more."""
    if role == "operator":
        return True
    if role.startswith("applicant:"):
        return role.split(":", 1)[1] == record.get("submitted_by")
    return record.get("submitted_by") is None


@app.get("/requests/{request_id}")
def get_request(
    request_id: str,
    _origin: None = Depends(_require_console_origin),
    role: str = Depends(require_applicant),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """The whole record, which also carries last_completed_step, the job
    context's captures, verified, and the failing step + last error when
    FAILED. Deliberately the RAW record
    (test_app_requests.py asserts GET round-trips POST's response
    byte-for-byte) -- the derived, operator-only view (_record_view below)
    lives on GET /requests instead, not here.

    Not owning a record that exists 404s exactly like a request id that
    does not exist at all (_owns_record, join_workflow.enforce_ownership) --
    the same message, the same status: no existence oracle, matching the
    path-traversal rule's 404 posture elsewhere in this module
    (test_get_request_id_path_traversal_never_reaches_the_filesystem)."""
    record = _load_request(db, request_id)
    if record is None or (_ENFORCE_OWNERSHIP and not _owns_record(role, record)):
        raise HTTPException(404, f"no join request {request_id!r}")
    return record


# -- the operator queue ("GET /requests -- the queue, filterable by
# state. Each entry carries the config diff ... computed at submission.")
# This listing endpoint and reject below are both genuinely needed by the
# console's operator tab (the pending queue, and reject-with-a-reason) and
# are pure additions to the API surface, not a
# change to any existing route.


def _step_summary(pack_dir: pathlib.Path, payload: schema.JoinPayload) -> list[dict] | None:
    """The ordered step sequence (id + actor + kind) for this payload's join
    -- job.py builds this from the payload at RUN time and never persists it
    (there is nothing on disk to read), so the console's progress list
    (the console's progress list, "coloured by its actor") recomputes it here instead.
    Cheap: yaml reads and string rendering, no network, no subprocess. None
    on failure (e.g. a REJECTED request's payload didn't survive schema
    validation) -- the console renders no step list rather than erroring.

    commit_gate=_COMMIT_GATE == "required" -- the module-level flag read from
    deployment.yaml at startup, not a per-record value -- so a request still
    SUBMITTED (no commit_gate stamped on it yet, see approve_request) shows
    the config.commit step in its preview exactly as it will actually run."""
    try:
        return [
            {"id": s.id, "actor": s.actor, "kind": s.kind}
            for s in job.build_sequence(pack_dir, payload, commit_gate=_COMMIT_GATE == "required")
        ]
    except Exception:  # noqa: BLE001 -- best-effort enrichment, never fatal to the queue view
        return None


def _live_uncommitted(key: str) -> bool | None:
    """A known gap, made visible: an ACTIVE member's config can be
    live on the running federation before anyone has committed
    configs/member-<key>/ and manifest.yaml to git. join-api is the only
    service in this pack with the enclosing .git mounted (docker-compose.yml's
    comment on this service's volumes) -- apps/console's own mount is
    curated read-only and has no .git at all, so this fact has to be
    computed here, not there. Same git-status shape as writer._git_status_dirty,
    scoped to this one member rather than the whole configs/ tree.

    Best-effort, but fails toward SHOWING the warning, not hiding it:
    returning False ("not dirty") on any exception would be exactly the
    value that suppresses the console's "Live but uncommitted" box, silently
    swallowing the precise failure this function exists to catch -- if
    `git` were ever missing from this image again (the bug that was also
    fixed in the Dockerfile), the one warning that should tell an operator
    the safety check itself is broken would instead just not render. None
    means "could not check" and is truthy-adjacent in the console (renders
    its own, honestly-worded box) -- never coerced to False."""
    try:
        repo_root = PACK_DIR.resolve().parents[2]
        rel = PACK_DIR.resolve().relative_to(repo_root)
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain",
             str(rel / "configs" / f"member-{key}"), str(rel / "manifest.yaml")],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001 -- "could not tell", not "definitely clean"
        return None


def _record_view(record: dict) -> dict:
    """Augments a persisted record with the two facts the console's queue
    needs that nothing on disk stores: the step sequence (for the progress
    list) and, for an ACTIVE record, the live-but-uncommitted flag."""
    view = dict(record)
    if record.get("state") != "REJECTED":
        try:
            payload = schema.JoinPayload(**record["payload"])
            view["steps"] = _step_summary(PACK_DIR, payload)
        except Exception:  # noqa: BLE001
            view["steps"] = None
    if record.get("state") == "ACTIVE":
        view["uncommitted"] = _live_uncommitted(record["payload"]["code"].lower())
    return view


@app.get("/requests")
def list_requests(
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """The operator queue: every persisted request, newest first,
    each enriched via _record_view. Operator-only, unlike GET /requests/{id}
    -- an applicant reads its own outcome by id (the "own request
    only" restriction was dropped, but the queue-wide view is still an operator tool)."""
    records = sorted(store.list_requests(db), key=lambda r: r.get("submitted_at", ""), reverse=True)
    return {"requests": [_record_view(r) for r in records]}


# -- approval and the job -----------------------------------------------------
# One job at a time, others queue. store.job_lock(conn)/store.apply_lock(conn)
# now own the actual lock objects (moved out of this file -- see store.py's
# own docstrings for the full design, including the failure-contract
# asymmetry between the SQLite and Postgres backends: apply_lock blocks on
# both backends, job_lock blocks on SQLite but is non-blocking on Postgres).
# Neither is aliased here -- every call site goes through
# store.job_lock(conn)/store.apply_lock(conn) directly (test code that needs
# the SQLite singleton reaches it as store._JOB_LOCK, same as store.py's own
# functions do).

def _blocking_job_lock(conn):
    """store.job_lock() blocks on SQLite (a plain threading.Lock) but is
    non-blocking on Postgres (pg_try_advisory_lock, raises LockBusy
    immediately) -- see store.py's own docstring. This wrapper reproduces
    the "one job at a time, others queue" behavior on both backends: on
    SQLite the loop body runs exactly once (job_lock() never raises
    LockBusy there); on Postgres it retries until the lock is free.

    The retry loop wraps only ACQUIRING the lock (job_lock's __enter__ --
    the only place it ever raises LockBusy; see its own docstring/source:
    Postgres raises it before yielding, never after). `yield` -- the
    wrapped body's actual work -- deliberately sits outside that
    except-LockBusy scope: nesting it inside (an earlier version did) is
    unreachable today (nothing in the wrapped body raises LockBusy) but
    would produce a confusing `RuntimeError: generator didn't stop after
    throw()` the moment something in there did -- catching it here would
    make this generator try to `yield` a second time, which contextlib
    doesn't allow."""
    @contextlib.contextmanager
    def _cm():
        while True:
            lock_cm = store.job_lock(conn)
            try:
                lock_cm.__enter__()
            except store.LockBusy:
                time.sleep(0.5)
                continue
            break
        try:
            yield
        except BaseException:
            lock_cm.__exit__(*sys.exc_info())
            raise
        else:
            lock_cm.__exit__(None, None, None)
    return _cm()


# -- in-process metrics (E.2) -------------------------------------------------
# A counter dict, hand-rolled -- no prometheus_client (Global constraints:
# no new Python dependency for E.1/E.2). Per-process, cleared by a restart,
# exactly like the rate limiter's own _BUCKETS a few sections up -- the same
# trade, for the same reason: there is one process and no replica to
# reconcile against. "Requests by state" and "store quota usage" are
# deliberately NOT kept here -- those are read live off the store at scrape
# time (store.count_requests_by_state/count_requests, GET /metrics below),
# reusing the existing SELECT COUNT(*) pattern rather than a second,
# driftable tally of the same fact.
_METRICS: dict[str, float] = {
    "rate_limited_total": 0,
    "job_steps_completed_total": 0,
    "job_steps_failed_total": 0,
    "job_duration_seconds_sum": 0.0,
    "job_duration_seconds_count": 0,
}
_METRICS_LOCK = threading.Lock()


def _metric_inc(name: str, amount: float = 1) -> None:
    with _METRICS_LOCK:
        _METRICS[name] = _METRICS.get(name, 0) + amount


def _metric_observe_duration(name: str, seconds: float) -> None:
    with _METRICS_LOCK:
        _METRICS[f"{name}_sum"] = _METRICS.get(f"{name}_sum", 0.0) + seconds
        _METRICS[f"{name}_count"] = _METRICS.get(f"{name}_count", 0) + 1


def _job_log_with_metrics(join_id: str):
    """job.run()/job.unjoin()'s `log=` argument: every event is a JSON log
    line (_job_log) AND, for the two events that name a step outcome,
    a bump of the matching counter above -- one call site, so the log and
    the metric can never read a different event stream."""

    def log(event: str, **fields) -> None:
        _job_log(join_id, event, **fields)
        if event in ("job.step.end", "unjoin.step.end"):
            outcome = fields.get("outcome")
            if outcome == "success" or outcome == "reversed":
                _metric_inc("job_steps_completed_total")
            elif outcome == "failed":
                _metric_inc("job_steps_failed_total")

    return log


def _run_job(request_id: str) -> None:
    # This is a background thread, never the request thread -- it opens and
    # closes its own connection rather than sharing the request-scoped one
    # FastAPI's Depends(get_conn) hands a route handler, since
    # sqlite3.Connection objects aren't safe to share across threads.
    #
    # Connection first, lock second -- INVERTED from this file's own earlier
    # comment here ("_JOB_LOCK first, connection second: a queued job ...
    # must not hold an idle connection open for however long that wait
    # takes"), because the lock now needs a live connection to operate
    # through (the Postgres advisory-lock calls run *through* conn -- see
    # store.job_lock()'s docstring). On the SQLite path this really does mean
    # a connection now sits open, idle, for however long _blocking_job_lock's
    # retry loop takes -- a real, intentional behavior change from before,
    # accepted as the trade for one backend-agnostic call site: SQLite
    # connections are cheap and local, this isn't a resource concern in
    # practice.
    with contextlib.closing(_conn()) as conn:
        with _blocking_job_lock(conn):
            record = _load_request(conn, request_id)
            if record is None:  # deleted while queued
                return
            t0 = time.monotonic()
            try:
                job.run(record, PACK_DIR, secrets=JOB_SECRETS,
                        save=lambda r: _save(conn, r, actor="system", event="job"),
                        log=_job_log_with_metrics(request_id))
            except Exception as exc:  # noqa: BLE001 -- a crashed job must not leave RUNNING forever
                record["state"] = "FAILED"
                record["error"] = {
                    "step": record.get("last_completed_step"),
                    "message": job.scrub(f"{type(exc).__name__}: {exc}", JOB_SECRETS),
                }
                _save(conn, record, actor="system", event="state:*->FAILED")
                _job_log(request_id, "job.finished", state="FAILED", error=record["error"]["message"])
            finally:
                _metric_observe_duration("job_duration_seconds", time.monotonic() - t0)


def _start_job(request_id: str) -> None:
    threading.Thread(target=_run_job, args=(request_id,), daemon=True).start()


@app.post("/requests/{request_id}/approve", status_code=202)
def approve_request(
    request_id: str,
    body: dict | None = None,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Operator approval: write the config for real (on APPROVED,
    before any live mutation), then start the job. 202, not 200: the job runs
    past this response and the applicant polls GET /requests/{id}.

    Manual approval puts one operator's bearer token where Ref Model §5.3
    puts the Steering Committee -- a RACI mismatch the onboarding path's own
    gap analysis names. The fix is not a second login (a
    committee doesn't hold an API token); it's requiring the call to name
    the decision it is actuating. `decision_reference` is untyped like
    reject_request's `body`, not a schema.py model -- this is evidence, not
    another auth layer, so a required non-empty string is the whole check."""
    record = _load_request(db, request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    if record["state"] != "SUBMITTED":
        raise HTTPException(409, f"request {request_id} is {record['state']}, not SUBMITTED")
    decision_reference = (body or {}).get("decision_reference")
    if not isinstance(decision_reference, str) or not decision_reference.strip():
        raise HTTPException(
            400,
            "decision_reference is required: admission is accountable to the "
            "Steering Committee, not the operator (Ref Model §5.3) -- this "
            "endpoint actuates that decision, it does not make it, so it "
            "must be told the minute identifier and date it is acting on.",
        )
    decision_reference = decision_reference.strip()
    # Computed here, before apply_real -- which writes onboarding/<key>/01-
    # admission.md, needing both -- rather than read off `record` afterwards
    # (writer.apply_real's own docstring names this ordering trap: record's
    # own approved_at/decision_reference fields are not assigned until after
    # apply_real returns, below).
    approved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    payload = schema.JoinPayload(**record["payload"])
    try:
        with store.apply_lock(db):
            writer.apply_real(
                PACK_DIR, payload.code.lower(), payload,
                request_id=request_id, decision_reference=decision_reference,
                approved_at=approved_at,
            )
    except writer.DirtyCheckoutError as exc:
        raise HTTPException(409, str(exc)) from exc
    except writer.GitCheckFailure as exc:
        # Could not tell whether the checkout is clean -- refuse the same as
        # if it were dirty (writer.GitCheckFailure's own docstring), a clear
        # 409 rather than a raw 500.
        raise HTTPException(409, str(exc)) from exc
    except writer.MemberCollisionError as exc:
        # A member directory for this key appeared between validation and
        # approval (a race, however unlikely) -- also a clear 409, not a
        # raw 500.
        raise HTTPException(409, str(exc)) from exc
    except writer.RollbackFailure as exc:
        # The rare one: the write failed AND apply_real could not put the
        # pack back, so the tree really does need a human. FAILED, not a
        # rejection, and scrubbed for the same reason as GenerateFailure
        # below -- the message can quote a subprocess that read .env.
        message = job.scrub(str(exc), JOB_SECRETS)
        record["state"] = "FAILED"
        record["error"] = {"step": "config.write", "message": message}
        record["decision_reference"] = decision_reference
        _save(db, record, actor="operator", event="state:SUBMITTED->FAILED")
        raise HTTPException(500, message) from exc
    except writer.GenerateFailure as exc:
        # The config was written and generate.py refused it. apply_real has
        # already restored the pack, so nothing is left behind -- this stays
        # FAILED rather than a rejection because an approval that got this
        # far is a decision that was actuated and did not take, which the
        # operator's queue has to show.
        # Scrubbed, like every other error path here: apply_real's generate.py
        # subprocess reads .env, so a traceback out of it could carry the
        # admin password or the token PIN, and this string is both persisted
        # and returned.
        stderr = job.scrub(exc.stderr, JOB_SECRETS)
        record["state"] = "FAILED"
        record["error"] = {"step": "config.write", "message": stderr}
        record["decision_reference"] = decision_reference
        _save(db, record, actor="operator", event="state:SUBMITTED->FAILED")
        raise HTTPException(409, f"hurl/generate.py rejected the written config:\n{stderr}") from exc

    record["state"] = "APPROVED"
    record["approved_at"] = approved_at
    record["decision_reference"] = decision_reference
    # Stamped now, like approved_at/decision_reference above -- not re-read
    # from deployment.yaml at run() time (a RUNNING/BLOCKED job can outlive a
    # config edit; the record should say what was decided when this request
    # was approved, not what the file happens to say when the job later
    # resumes). job.py reads this back to decide whether to plan the
    # config.commit gate step (docs/production-delta.md row 33).
    record["commit_gate"] = _COMMIT_GATE
    record["queued"] = store.job_lock_held(db)
    _save(db, record, actor="operator", event="state:SUBMITTED->APPROVED")
    _start_job(request_id)
    return record


@app.post("/requests/{request_id}/resume", status_code=202)
def resume_request(
    request_id: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    _rate: None = Depends(rate_limit),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Re-run from last_completed_step. Only from FAILED or BLOCKED --
    resuming a RUNNING job would put two runners on one
    federation, and resuming an ACTIVE one has nothing left to do.

    BLOCKED is the exit chosen over a callback endpoint: the
    operator runs scripts/join-agent.sh, then resumes, and job.run() polls the
    now-existing Security Server and carries on. A resume that finds it still
    absent re-enters BLOCKED rather than failing, as many times as it takes --
    which is why this is not a state that expires."""
    record = _load_request(db, request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    if record["state"] not in ("FAILED", "BLOCKED"):
        raise HTTPException(409, f"request {request_id} is {record['state']}, not FAILED or BLOCKED")
    record["queued"] = store.job_lock_held(db)
    _save(db, record, actor="operator", event="resume")
    _start_job(request_id)
    return record


@app.post("/requests/{request_id}/reject")
def reject_request(
    request_id: str,
    body: dict | None = None,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Operator rejection with a reason (the console's operator tab).
    Only from SUBMITTED -- once a request is APPROVED the config is already
    written and a job may be running or done; rejecting at that point isn't
    "this join should not happen", it's un-joining, which is DELETE
    /members/{key} (Plan C, not this endpoint)."""
    record = _load_request(db, request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    if record["state"] != "SUBMITTED":
        raise HTTPException(409, f"request {request_id} is {record['state']}, not SUBMITTED")
    reason = (body or {}).get("reason") or "(no reason given)"
    record["state"] = "REJECTED"
    record["rejection"] = {"check": "operator", "message": reason}
    record["rejected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _save(db, record, actor="operator", event="rejected")
    return record


@app.post("/requests/{request_id}/refreshes")
def add_refresh(
    request_id: str,
    body: dict,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Append a drift-refresh amendment to a request record (plan §1.3).
    `scripts/member.sh refresh` tries this endpoint first, and falls back to
    a direct DB write only when join-api is not running -- this is the
    single-writer path that fallback exists beside, not a replacement for
    it. Evidence-appending, not a state transition: no state-machine gate,
    mirroring the direct-write fallback's own lack of one."""
    record = _load_request(db, request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    endpoints = (body or {}).get("endpoints")
    if not isinstance(endpoints, dict):
        raise HTTPException(400, "endpoints is required and must be an object of {service_code: [path, ...]}")
    record.setdefault("refreshes", []).append({
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "endpoints": endpoints,
    })
    _save(db, record, actor="operator", event="refresh")
    return record


# -- un-joining -----------------------------------------------------------------
# The reverse of everything above: DELETE /members/{key} walks the member's
# completed steps backwards (job.unjoin), then delegates the config-and-manifest
# half to scripts/member.sh remove.
#
# Keyed by member KEY, not by request id -- "retire PTSB" is the operator's
# question, and the request id that joined it is an implementation detail
# nobody kept. The record it walks is found the same way scripts/member.sh
# drift already finds a member's join-time baseline (store.member_record()).

# The key becomes a manifest lookup AND an argv element for member.sh, which
# does `rm -r "$PACK_DIR/configs/member-$key"`. subprocess is called with a
# list (no shell), so this is not about quoting -- it is about a `..` or a `/`
# turning that rm into one outside configs/. Same charset validate.py's
# key_derivation check already constrains a joining member's key to.
_MEMBER_KEY_RE = re.compile(r"[a-z0-9]+")

# store.member_record(db, key) replaces the hand-rolled newest-ACTIVE/RETIRING
# scan that used to live here -- one indexed query (requests_by_member) over
# the same discovery scripts/member.sh drift does for the same reason.


def _run_unjoin(request_id: str) -> None:
    # Background thread, own connection -- connection first, lock second, see
    # _run_job's comment for why that ordering is inverted from before.
    with contextlib.closing(_conn()) as conn:
        with _blocking_job_lock(conn):
            record = _load_request(conn, request_id)
            if record is None:
                return
            t0 = time.monotonic()
            try:
                job.unjoin(record, PACK_DIR, secrets=JOB_SECRETS,
                           save=lambda r: _save(conn, r, actor="system", event="unjoin"),
                           log=_job_log_with_metrics(request_id))
            except Exception as exc:  # noqa: BLE001 -- same contract as _run_job's
                record["error"] = {
                    "step": record.get("last_reversed_step"),
                    "message": job.scrub(f"{type(exc).__name__}: {exc}", JOB_SECRETS),
                }
                _save(conn, record, actor="system", event="state:*->RETIRING(error)")
                _job_log(request_id, "unjoin.finished", state="RETIRING(error)", error=record["error"]["message"])
                return
            finally:
                _metric_observe_duration("job_duration_seconds", time.monotonic() - t0)
            if record.get("state") != "RETIRED":
                return  # the walk stopped; record["error"] says where. DELETE again to resume.

            # The federation-side retirement just completed, so this is where
            # the retirement record gets written -- not scripts/member.sh remove
            # below, which is config removal only. Idempotent (same content
            # every time): a repeat DELETE that reaches here just rewrites the
            # same file, which is cheap and simpler than guarding it.
            key = record["payload"]["code"].lower()
            (PACK_DIR / "onboarding" / key / writer.RETIREMENT_FILE).write_text(
                writer.render_retirement_record(key, record["retired_at"], record["id"])
            )

            if record.get("config_removed"):
                # Already done by an earlier run of this walk. scripts/member.sh
                # remove is NOT idempotent -- it exits non-zero on a member whose
                # directory is already gone -- so re-running it here would rewrite
                # a completed retirement back to RETIRING with a config.remove
                # error. Reachable whenever a second DELETE is issued, which
                # runbook.md explicitly invites: two queue on the job lock, and
                # the second one's walk is a clean no-op over probes that all
                # report absence.
                return

            # Step 5: the config-and-manifest half, delegated rather than
            # reimplemented -- member.sh remove already deletes the directory,
            # strips identity.members.<key>, refuses a canonical member and
            # regenerates. Last, after the federation no longer holds the member:
            # regenerating first would rewrite hurl/topology.json out from under
            # a walk that has not finished.
            proc = subprocess.run(
                [str(PACK_DIR / "scripts" / "member.sh"), "remove", key],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                record["state"] = "RETIRING"
                record["error"] = {
                    "step": "config.remove",
                    "message": f"the federation no longer holds {key}, but scripts/member.sh remove "
                    f"{key} failed (exit {proc.returncode}):\n{job.scrub(proc.stderr or proc.stdout, JOB_SECRETS)}",
                }
            else:
                record["config_removed"] = True
                # The instance catalogue is derived from the member configs, so
                # it drops this member's services by regeneration -- there is no
                # entry to delete, and nothing to forget to delete. Its own
                # onboarding/<key>/ record stays as evidence of what was revoked.
                writer.write_catalogue(PACK_DIR)
            _save(conn, record, actor="system",
                                event="config_removed" if record.get("config_removed") else "state:RETIRED->RETIRING")


def _start_unjoin(request_id: str) -> None:
    threading.Thread(target=_run_unjoin, args=(request_id,), daemon=True).start()


@app.delete("/members/{key}", status_code=202)
def retire_member(
    key: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    db: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Un-join a member: reverse its live federation presence, then remove its
    config. 202 and the record, like approve -- the walk runs past this
    response and the operator polls GET /requests/{id} (or the console's
    queue). States ACTIVE -> RETIRING -> RETIRED.

    Re-issuing this on a RETIRING record RESUMES the walk. That is the whole
    resume story for un-joining: every reversal is guarded by a probe, so a
    walk killed halfway re-runs from the top and skips what is already gone
    (job.unjoin's own docstring). There is deliberately no
    DELETE-specific resume endpoint, and POST /requests/{id}/resume is not it
    -- that one re-enters the FORWARD path."""
    if not _MEMBER_KEY_RE.fullmatch(key):
        raise HTTPException(400, f"{key!r} is not a member key")

    # Step 1: refuse a canonical member BEFORE anything else -- before the
    # record lookup, before a single call to the federation. scripts/member.sh
    # remove makes the same refusal for the config half; this is the same
    # check against the same field, made early enough that a canonical member
    # is never half-retired from the live bus and then blocked at the config
    # step.
    members = (_load_manifest().get("identity") or {}).get("members") or {}
    entry = members.get(key)
    if entry is None:
        raise HTTPException(404, f"no member {key!r} in manifest.yaml")
    if entry.get("origin", "canonical") != "joined":
        raise HTTPException(
            403,
            f"'{key}' is a canonical member and cannot be un-joined. The canonical four are the "
            "frozen KP3/KP4 cross-pack contract (manifest.yaml's identifiers: block) -- other packs "
            "build against those exact identifiers, so a demonstration un-join must never change "
            "them. Only a member with origin: joined can leave.",
        )

    record = store.member_record(db, key)
    if record is None:
        raise HTTPException(
            404,
            f"no ACTIVE or RETIRING join record for '{key}' -- it was never joined through this API "
            "(e.g. added by hand via prompts/member.md), so there is no step sequence to walk "
            "backwards. Remove its config with scripts/member.sh remove and, if it is live, retire "
            "it by hand or purge the federation.",
        )

    record["state"] = "RETIRING"
    record["error"] = None
    # Emitted on the way IN, not only on completion (Step 8): an own-server
    # un-join is not finished by this API, and the operator has to be told
    # what is left for them whether or not they come back for the final record.
    record["retire_instruction"] = job.retire_instruction(schema.JoinPayload(**record["payload"]))
    record["queued"] = store.job_lock_held(db)
    _save(db, record, actor="operator", event="state:ACTIVE->RETIRING")
    _start_unjoin(record["id"])
    return record
