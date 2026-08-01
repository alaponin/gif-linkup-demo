"""apps/console/app.py -- read (Task 4) and write (Task 5) API for the KP2
demonstration console. The browser talks only to this service; this is the
only thing in the demo that talks to X-Road (see the plan's Architecture
section). Credentials come from the environment (.env via Docker Compose),
read here once, never returned in a response or logged.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import dataclasses
import os
import pathlib
import re
import threading
import time

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

import journal as journal_mod
import truth as truth_mod
import xroad

PACK_DIR = pathlib.Path(os.environ.get("PACK_DIR", "/pack"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/out"))
ADMIN_USER = os.environ["XROAD_ADMIN_USER"]
ADMIN_PASSWORD = os.environ["XROAD_ADMIN_PASSWORD"]

# Design decision 4: only one ACL is mutable in this demo -- identity-api's
# grant to PNEA:EXAMS. enrolment-api stays untouched so a broken reset is
# always visible as an asymmetry between the two tabs, not hidden by symmetry.
MUTABLE_SERVICE = "identity-api"
HEARTBEAT_TIMEOUT_S = 120
WATCHDOG_POLL_S = 10

# Journal integrity plan (S16): _mutate_acl is reached from `def` (not
# `async def`) endpoints, so FastAPI runs it in a threadpool -- two
# concurrent POSTs genuinely interleave without this. Serialises every
# path that reads-then-writes the journal or calls reset(): _mutate_acl,
# post_reset, and the watchdog/lifespan resets. Scope: this lock
# serialises mutations WITHIN ONE CONSOLE PROCESS. It is not a
# distributed lock and does not protect against two consoles pointed at
# one federation -- which this pack does not do and should not start
# doing.
_MUTATE_LOCK = threading.Lock()

# Request-boundary plan (S12): shape confirmed against apps/data/persons.csv
# (scripts/gen_seed_data.py's nin() -- 11 digits, 0-9). \A/\Z rather than
# ^/$: $ matches before a trailing newline in Python, exactly the kind of
# near-miss this validator exists to stop.
NIN_RE = re.compile(r"\A[0-9]{11}\Z")


def _validated_nin(nin: str) -> str:
    if not NIN_RE.match(nin):
        raise HTTPException(400, "nin must be 11 digits")  # never echo the value back
    return nin


# Request-boundary plan (S13): a cross-origin <form method=POST> is sent by
# the browser regardless of CORS -- CORS only stops the attacker reading the
# response, and the attacker does not need to read it, the side effect IS
# the attack. Loopback bind is not a control either: the browser is on the
# same host as the console. Two independent checks, since they fail in
# different ways and neither is expensive:
#   - a required custom header a cross-origin form cannot set (setting a
#     custom header from another origin triggers a CORS preflight, which
#     this app never answers with permission -- the browser refuses to send
#     the real request);
#   - Origin, when present, must match this request's own Host.
#     Sec-Fetch-Site: same-origin is a useful second signal on modern
#     browsers, but its ABSENCE is inconclusive (not every client sends it)
#     -- only its presence with a non-same-origin value counts against the
#     request.
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
            raise HTTPException(403, f"Origin {origin!r} does not match this console's own host {host!r}")
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site is not None and sec_fetch_site != "same-origin":
        raise HTTPException(403, f"Sec-Fetch-Site {sec_fetch_site!r} is not same-origin")

# Loaded once at startup, not per-request: a stale Truth after a redeploy
# means the container needs restarting anyway (deployment.yaml/topology.json
# changing is a redeploy event, not something this demo tool hot-reloads).
TRUTH = truth_mod.load_truth(PACK_DIR)
JOURNAL = journal_mod.Journal(OUT_DIR / "console-acl-journal.json")
_last_heartbeat = time.time()

# member code -> that member's own config file, derived from manifest.yaml's
# module map (never hardcoded) -- so the inspector's semantic pane can show
# each provider's own semantic.fields list (UX plan Task 6, Step 2).
_MANIFEST = yaml.safe_load((PACK_DIR / "manifest.yaml").read_text())
_CONFIG_BY_MEMBER: dict[str, str] = {}
for _module in _MANIFEST["modules"]:
    for _bb in _module.get("building_blocks", []):
        if _bb.startswith("member-"):
            _CONFIG_BY_MEMBER[_bb.removeprefix("member-").upper()] = _module["config"]


def _semantic_fields_for(member_code: str) -> list[str]:
    config_path = _CONFIG_BY_MEMBER.get(member_code)
    if not config_path:
        return []
    cfg = yaml.safe_load((PACK_DIR / config_path).read_text())
    return cfg.get("semantic", {}).get("fields", [])


def _admin_session(host: str) -> xroad.AdminSession:
    return xroad.AdminSession(host, ADMIN_USER, ADMIN_PASSWORD)


def _subsystem_for_service(service_code: str) -> dict:
    for subsystem in TRUTH.topology["subsystems"]:
        if any(svc["code"] == service_code for svc in subsystem["services"]):
            return subsystem
    raise HTTPException(404, f"no subsystem publishes service {service_code!r}")


def _journal_is_dirty() -> bool:
    return JOURNAL.is_dirty()


def _reset_locked() -> dict:
    """The one place that calls journal_mod.reset() -- post_reset, the
    watchdog, and the lifespan's startup reset all go through this, so the
    S16 lock and (Task 3) the off-event-loop wrapping only need stating
    once."""
    with _MUTATE_LOCK:
        return journal_mod.reset(JOURNAL, _admin_session, TRUTH.expected_acl, TRUTH.topology)


async def _watchdog() -> None:
    """Belt and braces (design decision 3): a demo that silently leaves the
    ACL revoked and makes acceptance.sh fail an hour later for an
    unrelated-looking reason is exactly the kind of thing that discredits
    the pack. Reset after HEARTBEAT_TIMEOUT_S with no page heartbeat.

    reset() performs several blocking HTTPS logins at up to 10s timeout
    each (S17) -- without to_thread, the whole ASGI event loop stops for
    that period: /api/health and /api/heartbeat cannot answer, so the
    page's own heartbeat cannot land, so the watchdog's own timeout logic
    is being starved by the watchdog."""
    global _last_heartbeat
    while True:
        await asyncio.sleep(WATCHDOG_POLL_S)
        if time.time() - _last_heartbeat > HEARTBEAT_TIMEOUT_S and JOURNAL.is_dirty():
            await asyncio.to_thread(_reset_locked)
            _last_heartbeat = time.time()


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    startup_reset_task = None
    if JOURNAL.is_dirty():
        # Non-blocking, a deliberate choice (S17 Step 2) between two real
        # alternatives: blocking startup until the reset completes means
        # /api/health cannot answer until every reset HTTP call finishes
        # (several, at up to 10s timeout each) -- very likely what
        # verify.sh --full's "console health check still failing 30s after
        # console.sh up" retry loop was silently papering over. A console
        # that briefly reports healthy while a startup reset reconciles a
        # dirty journal in the background is acceptable for a demo tool
        # that is explicitly outside the acceptance path (Global
        # Constraints): the mutate lock and the watchdog still enforce the
        # invariant either way, and blocking here doesn't make the
        # underlying federation any less dirty -- it only makes the
        # console slower to admit it is up.
        startup_reset_task = asyncio.create_task(asyncio.to_thread(_reset_locked))
    watchdog_task = asyncio.create_task(_watchdog())
    yield
    watchdog_task.cancel()
    # Cancelling a task awaiting asyncio.to_thread raises CancelledError at
    # the await promptly -- it does not and cannot stop the underlying OS
    # thread already running reset() (Python threads are not preemptible),
    # but shutdown itself does not hang on it (confirmed in
    # test_app_mutate_acl.py). The thread finishes reset() in the background,
    # harmlessly, under the same lock.
    if startup_reset_task is not None:
        startup_reset_task.cancel()


app = FastAPI(title="KP2 demonstration console", lifespan=_lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok", "profile": TRUTH.profile}


@app.get("/api/topology")
def get_topology():
    """The Truth topology plus a live reachability probe per server, so the
    page can show honestly that the federation is up before anyone types a
    NIN."""
    servers = []
    for ss in TRUTH.topology["security_servers"]:
        try:
            resp = httpx.Client(verify=False, timeout=3.0).get(
                f"https://{ss['host']}:{ss['ui_port']}"
            )
            reachable = resp.status_code == 200
        except httpx.HTTPError:
            reachable = False
        servers.append({**ss, "reachable": reachable})
    return {**TRUTH.topology, "security_servers": servers}


@app.get("/api/learners")
def get_learners():
    """A handful of seeded NINs: several present in both registries, and --
    labelled as such -- one present in PNIA but absent from PLR, which is
    acceptance.sh check 2.6.5's clean-404 case. Read from the same CSVs
    seed.sh regenerates. No names here (UX plan Task 2, Step 1): the name
    arriving from PNIA over the bus is the demonstration's payoff, and a
    chip that already shows it spoils that before the exchange runs."""
    with open(PACK_DIR / "apps/data/persons.csv", newline="") as f:
        nins = {row["nin"] for row in csv.DictReader(f)}
    with open(PACK_DIR / "apps/data/enrolments.csv", newline="") as f:
        enrolled_nins = {row["nin"] for row in csv.DictReader(f)}

    both = sorted(nin for nin in nins if nin in enrolled_nins)[:4]
    pnia_only = sorted(nin for nin in nins if nin not in enrolled_nins)[:1]

    learners = (
        [{"nin": nin, "case": "has an enrolment record"} for nin in both]
        + [{"nin": nin, "case": "no enrolment record"} for nin in pnia_only]
    )
    return {"learners": learners}


@app.get("/api/exchange/{nin}", dependencies=[Depends(_require_console_origin)])
def get_exchange(nin: str):
    """The assembled application with per-field provenance -- the same shape
    acceptance.sh already writes to out/application-{nin}.json -- plus the
    per-call technical detail the inspector tab renders.

    Guarded too (request-boundary plan S13 Step 5), even though a read
    doesn't mutate the ACL: it does cause the console to issue real,
    authenticated calls over the X-Road bus, so a cross-origin
    `<img src>` (no fetch, no CORS preflight needed for a plain GET) could
    make the federation do work on an attacker's behalf. Guarding costs
    nothing extra here -- the page's own api() helper already sends the
    required header on every call, GET included."""
    nin = _validated_nin(nin)
    results = xroad.exchange(
        TRUTH.consumer_entrypoint,
        TRUTH.exchange["calls"],
        nin,
        TRUTH.exchange["headers"]["X-Road-Client"],
    )

    values: dict[str, str] = {"nin": nin}
    for result in results:
        if isinstance(result.body, dict):
            values.update(result.body)

    application = {}
    for field in TRUTH.form_fields:
        source = "citizen" if field.source == "citizen" else f"{field.source} over the bus"
        application[field.name] = {
            "value": values.get(field.name),
            "source": source,
            "member_code": field.source,  # raw code ("citizen" or e.g. "PNIA") --
            "label": field.label,         # `source` above is display text, not
            "group": field.group,         # a key: match calls/topology on this instead
        }

    semantic_fields = {
        member_code: _semantic_fields_for(member_code)
        for member_code in {f.source for f in TRUTH.form_fields if f.source != "citizen"}
    }

    return {
        "credential_application": application,
        "calls": [dataclasses.asdict(r) for r in results],
        "layers": TRUTH.layers,
        "client_header": TRUTH.exchange["headers"]["X-Road-Client"],
        "identity_held_fields": _identity_held_fields(nin),
        "semantic_fields": semantic_fields,
    }


def _identity_held_fields(nin: str) -> list[str]:
    """Field names PNIA's own record carries but its published contract
    doesn't send -- read directly from the mock, off the bus entirely,
    never through xroad.py (UX plan Task 5, Step 3). Never the values."""
    # Validated again even though get_exchange already validated its nin --
    # deliberate double-check (request-boundary plan S12): this is a
    # module-level function a future caller could reach directly, not just
    # via get_exchange, and it builds its own URL below.
    nin = _validated_nin(nin)
    try:
        resp = httpx.get(f"{TRUTH.identity_mock_base_url}/persons/{nin}/held-fields", timeout=3.0)
        resp.raise_for_status()
        return resp.json()["held"]
    except httpx.HTTPError:
        return []


@app.get("/api/exchange/{nin}/negative", dependencies=[Depends(_require_console_origin)])
def get_exchange_negative(nin: str):
    """The negative check (Module 5.6): the same calls, run as the
    unauthorised client through ITS OWN Security Server -- confirmed live
    this must be routed this way, or the denial comes from a consumer SS
    rejecting a client it doesn't host rather than from the provider's ACL.

    Guarded for the same reason as get_exchange above: it issues real bus
    calls too, and leaving it unguarded while guarding get_exchange would
    just hand an attacker the sibling endpoint instead."""
    nin = _validated_nin(nin)
    negative = TRUTH.exchange["negative_check"]
    results = xroad.exchange(
        TRUTH.negative_check_entrypoint,
        TRUTH.exchange["calls"],
        nin,
        negative["unauthorised_client"],
    )
    return {"calls": [dataclasses.asdict(r) for r in results], "expect": negative["expect"]}


@app.get("/api/acl")
def get_acl():
    """Configured vs live grants for every service the exchange depends on,
    plus whether the journal is dirty (a demo left mid-permission-toggle)."""
    services = {}
    for service_code, configured in TRUTH.expected_acl.items():
        subsystem = _subsystem_for_service(service_code)
        session = _admin_session(subsystem["hosted_on"])
        live = session.read_subjects(subsystem["id"])
        services[service_code] = {
            "client_id": subsystem["id"],
            "hosted_on": subsystem["hosted_on"],
            "configured": configured,
            "live": live,
        }
    return {"services": services, "dirty": _journal_is_dirty()}


def _mutate_acl(action: str) -> dict:
    """Only MUTABLE_SERVICE (design decision 4) is ever mutated here."""
    subsystem = _subsystem_for_service(MUTABLE_SERVICE)
    subjects = TRUTH.expected_acl[MUTABLE_SERVICE]
    if not subjects:
        raise HTTPException(400, f"{MUTABLE_SERVICE} has no expected subject to mutate")
    subject = subjects[0]

    # Held across the read that establishes prior_state, the journal write,
    # the live call, AND mark_applied (S16) -- releasing it earlier would
    # let a second mutation read a prior_state the first has already
    # invalidated but not yet applied, the exact interleave that loses a
    # journal entry: A reads [], B reads [], A writes [x], B writes [y] --
    # x is gone from the journal even though its live mutation happened.
    with _MUTATE_LOCK:
        session = _admin_session(subsystem["hosted_on"])

        # prior_state must come from the actual live state, never inferred as
        # "the opposite of the requested action" -- grant()/revoke() are both
        # idempotent-safe at the X-Road layer (xroad.py's 409 handling: calling
        # grant when already granted, or revoke when already revoked, is a
        # success no-op). If prior_state assumed a transition happened when it
        # didn't, reset()'s reversal would apply the wrong action and corrupt
        # the real state -- confirmed live: calling this endpoint twice with the
        # same action left the journal permanently dirty and unable to verify.
        prior_state = "granted" if MUTABLE_SERVICE in session.read_acl(subsystem["id"], subject) else "revoked"

        # Journalled BEFORE the live call -- a crash between this write and the
        # next leaves enough on disk for reset() to reverse (journal.py docstring).
        idx = JOURNAL.append_pending(journal_mod.JournalEntry(
            ts=time.time(), action=action, ss=subsystem["hosted_on"],
            client_id=subsystem["id"], subject=subject, service_code=MUTABLE_SERVICE,
            prior_state=prior_state,
        ))
        if action == "revoke":
            session.revoke(subsystem["id"], subject, MUTABLE_SERVICE)
        else:
            session.grant(subsystem["id"], subject, MUTABLE_SERVICE)
        JOURNAL.mark_applied(idx)
        return {"ok": True, "action": action, "service_code": MUTABLE_SERVICE}


@app.post("/api/acl/revoke", dependencies=[Depends(_require_console_origin)])
def post_acl_revoke():
    return _mutate_acl("revoke")


@app.post("/api/acl/grant", dependencies=[Depends(_require_console_origin)])
def post_acl_grant():
    return _mutate_acl("grant")


@app.post("/api/reset", dependencies=[Depends(_require_console_origin)])
def post_reset():
    """Reverses the journal newest-first and verifies the result equals
    truth.expected_acl exactly -- never a silent 'reset ok' (Task 5 Step 2)."""
    global _last_heartbeat
    _last_heartbeat = time.time()
    result = _reset_locked()
    if not result["ok"]:
        raise HTTPException(409, result)
    return result


@app.post("/api/heartbeat")
def post_heartbeat():
    global _last_heartbeat
    _last_heartbeat = time.time()
    return {"ok": True}


# Mounted last so it never shadows an /api/* route -- StaticFiles(html=True)
# serves static/index.html for "/" and any other unmatched path.
app.mount("/", StaticFiles(directory=pathlib.Path(__file__).parent / "static", html=True), name="static")
