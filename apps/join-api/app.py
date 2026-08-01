"""apps/join-api/app.py -- the KP2 member-join API. Task 1 (this file's
first commit) is the skeleton: liveness, the credentials this service will
drive the admin API with, and the same request-boundary guard
apps/console/app.py uses. Later tasks (2-6, see
docs/superpowers/specs/2026-08-01-member-join-api-design.md) add validation,
config writing, the step engine, and the endpoints these credentials and
this guard actually protect.

Credentials come from the environment (.env via Docker Compose), read here
once, never returned in a response or logged -- same rule as
apps/console/app.py (see that file's own docstring)."""
from __future__ import annotations

import os
import pathlib
import secrets

from fastapi import FastAPI, HTTPException, Request

PACK_DIR = pathlib.Path(os.environ.get("PACK_DIR", "/pack"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/out"))

# Held server-side only, exactly like apps/console's ADMIN_USER/ADMIN_PASSWORD.
# Not read by any endpoint in this task -- the step engine that drives the
# admin API with these arrives in a later task -- but this is the one place
# credentials enter the process, so they are read here now rather than adding
# them to docker-compose.yml's environment block again later (that file is a
# one-time touchpoint across this whole plan, see its join-api comment).
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
