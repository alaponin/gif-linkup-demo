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

import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import secrets
import subprocess
import sys
import threading
import time

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request

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
import schema  # noqa: E402
import validate  # noqa: E402
import writer  # noqa: E402

PACK_DIR = pathlib.Path(os.environ.get("PACK_DIR", "/pack"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/out"))

# Held server-side only, exactly like apps/console's ADMIN_USER/ADMIN_PASSWORD.
# This is the one place credentials enter the process; job.py receives them as
# an explicit argument (JOB_SECRETS below) rather than reading the environment
# itself, so the module that shells out to Hurl has exactly the values it was
# handed and nothing else.
ADMIN_USER = os.environ.get("XROAD_ADMIN_USER", "xrd")
ADMIN_PASSWORD = os.environ["XROAD_ADMIN_PASSWORD"]
TOKEN_PIN = os.environ["XROAD_TOKEN_PIN"]


def _required_token(name: str) -> str:
    """scripts/lib-stack.sh refuses to run while XROAD_TOKEN_PIN or
    XROAD_ADMIN_PASSWORD are still a placeholder from .env.example
    on purpose. Same idea, applied
    here rather than in lib-stack.sh: only join-api cares about these two
    tokens, and every other script that sources lib-stack.sh (console.sh,
    member.sh, ...) has no reason to fail over a secret it never uses."""
    value = os.environ.get(name, "")
    if not value or "CHANGEME" in value:
        raise RuntimeError(
            f"join-api: {name} is unset or still the .env.example placeholder. "
            "Run scripts/gen-secrets.sh to generate a real .env."
        )
    return value


APPLICANT_TOKEN = _required_token("KP2_JOIN_APPLICANT_TOKEN")
OPERATOR_TOKEN = _required_token("KP2_JOIN_OPERATOR_TOKEN")

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
# operator, each its own token from scripts/gen-secrets.sh. Deliberately no
# per-request ownership: in a demo where one person plays both roles it is
# machinery guarding a boundary nobody crosses, and restoring it later (a
# `submitted_by` field and one comparison) is cheap if the module is ever
# run with genuinely separate applicant/operator actors. The *asymmetry* is
# the teaching point: an applicant cannot approve.
def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(401, "missing or malformed Authorization: Bearer <token> header")
    return value


def require_applicant(request: Request) -> str:
    """Applicant may read any request -- any valid token, applicant
    or operator, satisfies this dependency. Used by read/submit routes."""
    token = _bearer_token(request)
    if secrets.compare_digest(token, OPERATOR_TOKEN):
        return "operator"
    if secrets.compare_digest(token, APPLICANT_TOKEN):
        return "applicant"
    raise HTTPException(403, "token does not match either configured role")


def require_operator(request: Request) -> str:
    """Approve, reject and resume are operator-only -- the
    applicant token is rejected here, not just left unchecked."""
    token = _bearer_token(request)
    if secrets.compare_digest(token, OPERATOR_TOKEN):
        return "operator"
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

# The other half of the same worry: out/join/ is a directory of files on local
# disk, which is what this pack has instead of a datastore. A submission is
# refused once that directory holds this many records -- the remedy is naming
# in the message, because there is no eviction policy here and inventing one
# would be pretending the directory is a database.
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


def rate_limit(request: Request) -> None:
    """One bucket per bearer token, so the applicant and operator credentials
    cannot exhaust each other's budget -- and, once per-agency tokens exist,
    neither can two agencies. Applied to the two POSTs that drive the
    federation (submit, resume). Reads stay unlimited: discovery
    (GET /catalogue) is what a body deciding whether to join uses, and
    approve/reject are already operator-gated and 409-guarded by state."""
    wait = _take_token(hashlib.sha256(_bearer_token(request).encode()).hexdigest())
    if wait is None:
        return
    raise HTTPException(
        429,
        f"rate limit: this credential may make {RATE_LIMIT_REFILL_PER_MINUTE} of these "
        f"requests per minute (burst {RATE_LIMIT_CAPACITY}). Retry in "
        f"{math.ceil(wait)}s, or slow the caller down -- this endpoint registers "
        "federation members.",
        headers={"Retry-After": str(max(1, math.ceil(wait)))},
    )


app = FastAPI(title="KP2 member-join API")


@app.get("/health")
def health():
    return {"status": "ok"}


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
# out/join/<request-id>.json, the same OUT_DIR convention apps/console/
# journal.py already uses for out/console-acl-journal.json. One file per
# request, carrying every state it has been through (seven, including
# BLOCKED: an own-server join waits in it for the member's own
# Security Server) and the job's own record: last_completed_step, the
# non-secret captures (context), verified, queued, retry_budget_left,
# {step, message} on FAILED, and {step, server, message} on BLOCKED.

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def _requests_dir() -> pathlib.Path:
    d = OUT_DIR / "join"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_request(record: dict) -> None:
    # Atomic on POSIX, same pattern as journal.py's _write: a temp file
    # beside the target, renamed on -- a reader never sees a partial write.
    path = _requests_dir() / f"{record['id']}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2))
    os.replace(tmp, path)


def _load_request(request_id: str) -> dict | None:
    # request_id came off the URL path and becomes a filename below --
    # reject anything outside secrets.token_urlsafe's own charset before it
    # ever reaches the filesystem (trust-boundary input, not a cosmetic
    # check: a path-traversal-shaped id must 404, not read outside out/join/).
    if not _REQUEST_ID_RE.fullmatch(request_id):
        return None
    path = _requests_dir() / f"{request_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _recover_interrupted_jobs() -> None:
    """A job's record can be stuck at RUNNING forever if the process running
    it stops mid-job -- scripts/join.sh down, a rebuild, or even
    acceptance.sh's own 2.7 section (which brings join-api up and back down
    around its checks). job.run() itself already resumes correctly from any
    record carrying last_completed_step (see its own docstring), but
    resume_request only accepts FAILED or BLOCKED -- never RUNNING, so two
    runners can never land on one live job -- so a record left at RUNNING is
    otherwise
    unrecoverable through this API except by hand-editing out/join/<id>.json.

    Run once, at import time: this process is,
    by construction, not the one that was running that job -- if it were
    still running, this module would not be re-executing from the top. Every
    record still marked RUNNING therefore belongs to a run that died with
    this process (or an earlier one) and never got to report itself FAILED;
    rewriting it to FAILED here makes the existing FAILED-only resume path
    able to pick it back up."""
    for path in sorted(_requests_dir().glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("state") != "RUNNING":
            continue
        record["state"] = "FAILED"
        record["error"] = {
            "step": record.get("last_completed_step"),
            "message": "interrupted by a join-api restart",
        }
        _save_request(record)


_recover_interrupted_jobs()


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
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_applicant),
    _rate: None = Depends(rate_limit),
) -> dict:
    """Validate synchronously (eleven per-request checks plus
    lawful_basis, sla_required and spec_url_origin, additions beyond those
    eleven -- validate.py's own module docstring: check 5 moved to
    generate-time),
    then either persist
    a REJECTED record or -- on success -- write the candidate config to a
    throwaway copy of the pack, run its generate.py, and persist a SUBMITTED
    record carrying the resulting diff. Either way: 201 (the
    applicant retrieves the outcome via GET /requests/{id}, there is no
    separate failure status here). A malformed body (bad JSON, wrong types,
    an unrecognised key) is check 1 ("schema") -- validate() itself does
    `JoinPayload(**raw)` and turns a pydantic.ValidationError into
    RejectionError("schema", ...), so nothing here hand-rolls that check."""
    # The disk-backed store's own ceiling, checked before validation does any
    # work (or any fetching). out/join/ is a directory of files, not a
    # datastore: nothing evicts, nothing rotates, and a request record is
    # evidence, so the honest response to a full one is to refuse and say
    # where to look -- not to invent a retention policy for it here.
    held = sum(1 for _ in _requests_dir().glob("*.json"))
    if held >= STORE_QUOTA:
        raise HTTPException(
            429,
            f"out/join/ already holds {held} request records, the limit this "
            f"service accepts ({STORE_QUOTA}). Nothing is evicted automatically -- "
            "each record is the evidence of a decision. Archive or remove the "
            "settled ones (REJECTED and RETIRED) from out/join/ and retry.",
        )
    request_id = secrets.token_urlsafe(8)
    submitted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

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
            "payload": raw,
            "rejection": {"check": exc.check, "message": exc.message},
        }
        _save_request(record)
        return record

    key = payload.code.lower()
    try:
        diff = writer.dry_run_diff(PACK_DIR, key, payload)
    except writer.GenerateFailure as exc:
        # Every one of the fourteen per-request checks passed, but generate.py itself still
        # refused the result (e.g. check_join_policy's static cross-check) --
        # a real, if rarer, rejection. Surfaced the same way: a REJECTED
        # record, never a bare 500 -- submission always
        # returns 201 -- stderr is passed through verbatim.
        record = {
            "id": request_id,
            "state": "REJECTED",
            "submitted_at": submitted_at,
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
        _save_request(record)
        return record

    record = {
        "id": request_id,
        "state": "SUBMITTED",
        "submitted_at": submitted_at,
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
    _save_request(record)
    return record


@app.get("/requests/{request_id}")
def get_request(
    request_id: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_applicant),
) -> dict:
    """The whole record, which also carries last_completed_step, the job
    context's captures, verified, and the failing step + last error when
    FAILED. Deliberately the RAW record
    (test_app_requests.py asserts GET round-trips POST's response
    byte-for-byte) -- the derived, operator-only view (_record_view below)
    lives on GET /requests instead, not here."""
    record = _load_request(request_id)
    if record is None:
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
    validation) -- the console renders no step list rather than erroring."""
    try:
        return [
            {"id": s.id, "actor": s.actor, "kind": s.kind}
            for s in job.build_sequence(pack_dir, payload)
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
) -> dict:
    """The operator queue: every persisted request, newest first,
    each enriched via _record_view. Operator-only, unlike GET /requests/{id}
    -- an applicant reads its own outcome by id (the "own request
    only" restriction was dropped, but the queue-wide view is still an operator tool)."""
    records = []
    for path in sorted(_requests_dir().glob("*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    records.sort(key=lambda r: r.get("submitted_at", ""), reverse=True)
    return {"requests": [_record_view(r) for r in records]}


# -- approval and the job -----------------------------------------------------
# One job at a time, others queue. threading.Lock, not a queue or a worker
# pool, for the same reason apps/console/app.py's _MUTATE_LOCK is one: this is
# one process, and two joins converging the same federation concurrently would
# interleave management-request approvals on the Central Server. A request
# whose thread is waiting on the lock reports queued: true.
_JOB_LOCK = threading.Lock()

# writer.apply_real is transactional (it restores every path it writes on any
# failure), and that guarantee is only true one approval at a time: two
# concurrent approvals interleave their writes to manifest.yaml, and then one
# rollback reverts the other's entry. Not _JOB_LOCK -- that one is held for a
# whole running job, and an approval must not wait minutes behind a live
# federation walk to write four files.
_APPLY_LOCK = threading.Lock()

JOB_SECRETS = {
    "ss_admin_user": ADMIN_USER,
    "ss_admin_password": ADMIN_PASSWORD,
    "token_pin": TOKEN_PIN,
}


def _run_job(request_id: str) -> None:
    with _JOB_LOCK:
        record = _load_request(request_id)
        if record is None:  # deleted while queued
            return
        try:
            job.run(record, PACK_DIR, secrets=JOB_SECRETS, save=_save_request)
        except Exception as exc:  # noqa: BLE001 -- a crashed job must not leave RUNNING forever
            record["state"] = "FAILED"
            record["error"] = {
                "step": record.get("last_completed_step"),
                "message": job.scrub(f"{type(exc).__name__}: {exc}", JOB_SECRETS),
            }
            _save_request(record)


def _start_job(request_id: str) -> None:
    threading.Thread(target=_run_job, args=(request_id,), daemon=True).start()


@app.post("/requests/{request_id}/approve", status_code=202)
def approve_request(
    request_id: str,
    body: dict | None = None,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
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
    record = _load_request(request_id)
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
        with _APPLY_LOCK:
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
        _save_request(record)
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
        _save_request(record)
        raise HTTPException(409, f"hurl/generate.py rejected the written config:\n{stderr}") from exc

    record["state"] = "APPROVED"
    record["approved_at"] = approved_at
    record["decision_reference"] = decision_reference
    record["queued"] = _JOB_LOCK.locked()
    _save_request(record)
    _start_job(request_id)
    return record


@app.post("/requests/{request_id}/resume", status_code=202)
def resume_request(
    request_id: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
    _rate: None = Depends(rate_limit),
) -> dict:
    """Re-run from last_completed_step. Only from FAILED or BLOCKED --
    resuming a RUNNING job would put two runners on one
    federation, and resuming an ACTIVE one has nothing left to do.

    BLOCKED is the exit chosen over a callback endpoint: the
    operator runs scripts/join-agent.sh, then resumes, and job.run() polls the
    now-existing Security Server and carries on. A resume that finds it still
    absent re-enters BLOCKED rather than failing, as many times as it takes --
    which is why this is not a state that expires."""
    record = _load_request(request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    if record["state"] not in ("FAILED", "BLOCKED"):
        raise HTTPException(409, f"request {request_id} is {record['state']}, not FAILED or BLOCKED")
    record["queued"] = _JOB_LOCK.locked()
    _save_request(record)
    _start_job(request_id)
    return record


@app.post("/requests/{request_id}/reject")
def reject_request(
    request_id: str,
    body: dict | None = None,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
) -> dict:
    """Operator rejection with a reason (the console's operator tab).
    Only from SUBMITTED -- once a request is APPROVED the config is already
    written and a job may be running or done; rejecting at that point isn't
    "this join should not happen", it's un-joining, which is DELETE
    /members/{key} (Plan C, not this endpoint)."""
    record = _load_request(request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    if record["state"] != "SUBMITTED":
        raise HTTPException(409, f"request {request_id} is {record['state']}, not SUBMITTED")
    reason = (body or {}).get("reason") or "(no reason given)"
    record["state"] = "REJECTED"
    record["rejection"] = {"check": "operator", "message": reason}
    record["rejected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _save_request(record)
    return record


# -- un-joining -----------------------------------------------------------------
# The reverse of everything above: DELETE /members/{key} walks the member's
# completed steps backwards (job.unjoin), then delegates the config-and-manifest
# half to scripts/member.sh remove.
#
# Keyed by member KEY, not by request id -- "retire PTSB" is the operator's
# question, and the request id that joined it is an implementation detail
# nobody kept. The record it walks is found the same way scripts/member.sh
# drift already finds a member's join-time baseline (_member_record below).

# The key becomes a manifest lookup AND an argv element for member.sh, which
# does `rm -r "$PACK_DIR/configs/member-$key"`. subprocess is called with a
# list (no shell), so this is not about quoting -- it is about a `..` or a `/`
# turning that rm into one outside configs/. Same charset validate.py's
# key_derivation check already constrains a joining member's key to.
_MEMBER_KEY_RE = re.compile(r"[a-z0-9]+")


def _member_record(key: str) -> dict | None:
    """The job record to walk backwards for `key`: the newest ACTIVE or
    RETIRING one whose payload code matches. Same discovery scripts/member.sh
    drift does for the same reason -- nothing indexes out/join/ by member, and
    nothing enforces one record per member, so pick the newest rather than
    assume. RETIRING is included so that re-issuing the DELETE resumes an
    interrupted walk instead of 404ing on a member whose record has already
    left ACTIVE."""
    best = None
    for path in sorted(_requests_dir().glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("state") not in ("ACTIVE", "RETIRING"):
            continue
        if (record.get("payload") or {}).get("code", "").lower() != key:
            continue
        if best is None or record.get("submitted_at", "") > best.get("submitted_at", ""):
            best = record
    return best


def _run_unjoin(request_id: str) -> None:
    with _JOB_LOCK:
        record = _load_request(request_id)
        if record is None:
            return
        try:
            job.unjoin(record, PACK_DIR, secrets=JOB_SECRETS, save=_save_request)
        except Exception as exc:  # noqa: BLE001 -- same contract as _run_job's
            record["error"] = {
                "step": record.get("last_reversed_step"),
                "message": job.scrub(f"{type(exc).__name__}: {exc}", JOB_SECRETS),
            }
            _save_request(record)
            return
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
            # runbook.md explicitly invites: two queue on _JOB_LOCK, and the
            # second one's walk is a clean no-op over probes that all
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
        _save_request(record)


def _start_unjoin(request_id: str) -> None:
    threading.Thread(target=_run_unjoin, args=(request_id,), daemon=True).start()


@app.delete("/members/{key}", status_code=202)
def retire_member(
    key: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
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

    record = _member_record(key)
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
    record["queued"] = _JOB_LOCK.locked()
    _save_request(record)
    _start_unjoin(record["id"])
    return record
