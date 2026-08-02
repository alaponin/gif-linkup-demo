"""apps/join-api/app.py -- the KP2 member-join API. Task 1 (this file's
first commit) is the skeleton: liveness, the credentials this service will
drive the admin API with, and the same request-boundary guard
apps/console/app.py uses. Task 3 added POST /requests and GET /requests/{id}
-- validation (validate.py) and config-diff computation (writer.py's dry-run
mode) run synchronously at submission. Task 4 (this commit) adds the operator
side: POST /requests/{id}/approve writes the config for real
(writer.apply_real) and starts the job (job.py) on a background thread, one
at a time; POST /requests/{id}/resume re-runs a FAILED one from its
last_completed_step. See
docs/superpowers/specs/2026-08-01-member-join-api-design.md.

Credentials come from the environment (.env via Docker Compose), read here
once, never returned in a response or logged -- same rule as
apps/console/app.py (see that file's own docstring)."""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import secrets
import sys
import threading

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
    (docs/reviews/2026-07-28-branch-review.md finding S2). Same idea, applied
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
# _require_console_origin (request-boundary plan S12/S13): a required custom
# header a cross-origin request cannot set without triggering a CORS
# preflight this app never answers with permission, plus an Origin check
# when the browser sends one. Spec §7: "the same request-boundary guard the
# console already applies".
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


# Bearer-token auth (spec §7, decision 10): two roles, applicant and
# operator, each its own token from scripts/gen-secrets.sh. Deliberately no
# per-request ownership -- spec §7 ("Applicant request scoping") and §16.4
# explain why: in a demo where one person plays both roles it is machinery
# guarding a boundary nobody crosses, and restoring it later (a
# `submitted_by` field and one comparison) is cheap if the module is ever
# run with genuinely separate applicant/operator actors. The *asymmetry* is
# the teaching point (decision 10): an applicant cannot approve.
def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(401, "missing or malformed Authorization: Bearer <token> header")
    return value


def require_applicant(request: Request) -> str:
    """Applicant may read any request (spec §7) -- any valid token, applicant
    or operator, satisfies this dependency. Used by read/submit routes."""
    token = _bearer_token(request)
    if secrets.compare_digest(token, OPERATOR_TOKEN):
        return "operator"
    if secrets.compare_digest(token, APPLICANT_TOKEN):
        return "applicant"
    raise HTTPException(403, "token does not match either configured role")


def require_operator(request: Request) -> str:
    """Approve, reject and resume are operator-only (spec §7) -- the
    applicant token is rejected here, not just left unchecked."""
    token = _bearer_token(request)
    if secrets.compare_digest(token, OPERATOR_TOKEN):
        return "operator"
    raise HTTPException(403, "operator token required for this endpoint")


app = FastAPI(title="KP2 member-join API")


@app.get("/health")
def health():
    return {"status": "ok"}


# -- request persistence (spec S5.4) -----------------------------------------
# out/join/<request-id>.json, the same OUT_DIR convention apps/console/
# journal.py already uses for out/console-acl-journal.json. One file per
# request, carrying every state it has been through (spec S4's seven, minus
# BLOCKED, which no hosted join can reach) and, since Task 4, the job's own
# record: last_completed_step, the non-secret captures (context), verified,
# queued, retry_budget_left, and {step, message} on FAILED.

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


def _load_manifest() -> dict:
    return yaml.safe_load((PACK_DIR / "manifest.yaml").read_text())


def _load_join_policy() -> dict:
    """configs/x-road-bus/2.7.yaml's join: block only (spec S8) -- not the
    whole file, mirroring validate.py's own ValidationContext.policy."""
    doc = yaml.safe_load((PACK_DIR / "configs" / "x-road-bus" / "2.7.yaml").read_text()) or {}
    return doc.get("join") or {}


@app.post("/requests", status_code=201)
def submit_request(
    raw: dict,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_applicant),
) -> dict:
    """Validate synchronously (spec S8's twelve checks), then either persist
    a REJECTED record or -- on success -- write the candidate config to a
    throwaway copy of the pack, run its generate.py, and persist a SUBMITTED
    record carrying the resulting diff. Either way: 201 (spec S7 -- the
    applicant retrieves the outcome via GET /requests/{id}, there is no
    separate failure status here). A malformed body (bad JSON, wrong types,
    an unrecognised key) is check 1 ("schema") -- validate() itself does
    `JoinPayload(**raw)` and turns a pydantic.ValidationError into
    RejectionError("schema", ...), so nothing here hand-rolls that check."""
    request_id = secrets.token_urlsafe(8)
    submitted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        payload = validate.validate(
            raw,
            manifest=_load_manifest(),
            policy=_load_join_policy(),
            existing_servers=validate.load_existing_security_servers(PACK_DIR),
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
        # Every twelve S8 checks passed, but generate.py itself still
        # refused the result (e.g. check_join_policy's static cross-check) --
        # a real, if rarer, rejection. Surfaced the same way: a REJECTED
        # record, never a bare 500, per spec S7's "submission always
        # returns 201" (task-3 brief step 3: stderr passed through verbatim).
        record = {
            "id": request_id,
            "state": "REJECTED",
            "submitted_at": submitted_at,
            "payload": raw,
            "rejection": {
                "check": "generate_dry_run",
                "message": f"hurl/generate.py rejected this join (exit {exc.returncode}):\n{exc.stderr}",
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
    }
    _save_request(record)
    return record


@app.get("/requests/{request_id}")
def get_request(
    request_id: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_applicant),
) -> dict:
    """The whole record, which since Task 4 also carries last_completed_step,
    the job context's captures, verified, and the failing step + last error
    when FAILED (spec S7's row for this endpoint)."""
    record = _load_request(request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    return record


# -- approval and the job (spec S4, S5) --------------------------------------
# One job at a time, others queue. threading.Lock, not a queue or a worker
# pool, for the same reason apps/console/app.py's _MUTATE_LOCK is one: this is
# one process, and two joins converging the same federation concurrently would
# interleave management-request approvals on the Central Server. A request
# whose thread is waiting on the lock reports queued: true.
_JOB_LOCK = threading.Lock()

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
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
) -> dict:
    """Operator approval: write the config for real (spec S9 -- on APPROVED,
    before any live mutation), then start the job. 202, not 200: the job runs
    past this response and the applicant polls GET /requests/{id}."""
    record = _load_request(request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    if record["state"] != "SUBMITTED":
        raise HTTPException(409, f"request {request_id} is {record['state']}, not SUBMITTED")

    payload = schema.JoinPayload(**record["payload"])
    try:
        writer.apply_real(PACK_DIR, payload.code.lower(), payload)
    except writer.DirtyCheckoutError as exc:
        raise HTTPException(409, str(exc)) from exc
    except writer.GenerateFailure as exc:
        # The config was written but generate.py refused it -- the working
        # tree now needs a human, so this is FAILED, not a rejection.
        record["state"] = "FAILED"
        record["error"] = {"step": "config.write", "message": exc.stderr}
        _save_request(record)
        raise HTTPException(409, f"hurl/generate.py rejected the written config:\n{exc.stderr}") from exc

    record["state"] = "APPROVED"
    record["approved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record["queued"] = _JOB_LOCK.locked()
    _save_request(record)
    _start_job(request_id)
    return record


@app.post("/requests/{request_id}/resume", status_code=202)
def resume_request(
    request_id: str,
    _origin: None = Depends(_require_console_origin),
    _role: str = Depends(require_operator),
) -> dict:
    """Re-run from last_completed_step. Only from FAILED (spec S7) -- resuming
    a RUNNING job would put two runners on one federation, and resuming an
    ACTIVE one has nothing left to do."""
    record = _load_request(request_id)
    if record is None:
        raise HTTPException(404, f"no join request {request_id!r}")
    if record["state"] != "FAILED":
        raise HTTPException(409, f"request {request_id} is {record['state']}, not FAILED")
    record["queued"] = _JOB_LOCK.locked()
    _save_request(record)
    _start_job(request_id)
    return record
