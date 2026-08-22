"""apps/console/app.py -- the read and write API for the KP2
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
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import console_logging as logging_setup
import journal as journal_mod
import truth as truth_mod
import xroad

PACK_DIR = pathlib.Path(os.environ.get("PACK_DIR", "/pack"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/out"))
ADMIN_USER = os.environ["XROAD_ADMIN_USER"]
ADMIN_PASSWORD = os.environ["XROAD_ADMIN_PASSWORD"]

# join tab: the join API's operator token stays server-side here,
# exactly like ADMIN_PASSWORD above -- read once at import, only ever used
# inside _join_api()'s Authorization header, never serialized into a
# response. join-api is on the same linkup network as every other
# console-adjacent service (docker-compose.yml), so this is a plain
# server-to-server call, same shape as xroad.AdminSession.
#
# Optional, unlike ADMIN_PASSWORD: a missing token disables the join tab
# only (_proxy_join renders the remedy), it does not stop the console.
JOIN_API_URL = os.environ.get("JOIN_API_URL", "http://join-api:8000")
JOIN_OPERATOR_TOKEN = os.environ.get("KP2_JOIN_OPERATOR_TOKEN", "")
JOIN_TOKEN_MISSING = (
    "KP2_JOIN_OPERATOR_TOKEN not set -- re-run scripts/gen-secrets.sh "
    "(no flags), then scripts/console.sh up"
)

# Only one ACL is mutable in this demo -- identity-api's
# grant to PNEA:EXAMS. enrolment-api stays untouched so a broken reset is
# always visible as an asymmetry between the two tabs, not hidden by symmetry.
MUTABLE_SERVICE = "identity-api"
HEARTBEAT_TIMEOUT_S = 120
WATCHDOG_POLL_S = 10

# JSON-lines to stdout, scrubbed of ADMIN_PASSWORD/JOIN_OPERATOR_TOKEN
# (console_logging.py's own docstring, imported here as `logging_setup` --
# apps/join-api/app.py has its own, different, same-named module; a bare
# `import logging_setup` in both would collide in sys.modules the moment
# both services' test suites load in one pytest session -- found live,
# closed by giving the two files distinct names) -- replaces the previous
# "uvicorn configures the root handlers, so this lands in `docker logs
# console` with no setup of our own" default, for consistency with
# apps/join-api's own structured logging (production-hardening-plan.md's
# E.1).
_LOG = logging_setup.configure("kp2.console", {"admin_password": ADMIN_PASSWORD, "join_operator_token": JOIN_OPERATOR_TOKEN})

# _mutate_acl is reached from `def` (not
# `async def`) endpoints, so FastAPI runs it in a threadpool -- two
# concurrent POSTs genuinely interleave without this. Serialises every
# path that reads-then-writes the journal or calls reset(): _mutate_acl,
# post_reset, and the watchdog/lifespan resets. Scope: this lock
# serialises mutations WITHIN ONE CONSOLE PROCESS. It is not a
# distributed lock and does not protect against two consoles pointed at
# one federation -- which this pack does not do and should not start
# doing.
_MUTATE_LOCK = threading.Lock()

# Shape confirmed against apps/data/persons.csv
# (scripts/gen_seed_data.py's nin() -- 11 digits, 0-9). \A/\Z rather than
# ^/$: $ matches before a trailing newline in Python, exactly the kind of
# near-miss this validator exists to stop.
NIN_RE = re.compile(r"\A[0-9]{11}\Z")


def _validated_nin(nin: str) -> str:
    if not NIN_RE.match(nin):
        raise HTTPException(400, "nin must be 11 digits")  # never echo the value back
    return nin


# A cross-origin <form method=POST> is sent by
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
# means the container needs restarting anyway (topology.json changing is a
# redeploy event, not something this demo tool hot-reloads).
TRUTH = truth_mod.load_truth(PACK_DIR)
JOURNAL = journal_mod.Journal(OUT_DIR / "console-acl-journal.json")
_last_heartbeat = time.time()

# member code -> that member's own config file, derived from manifest.yaml's
# module map (never hardcoded) -- so the inspector's semantic pane can show
# each provider's own semantic.fields list.
#
# A module's building_blocks: and member_configs: (falling back to config:
# for a module with exactly one member building block and no
# member_configs: key -- every module but the collapsed one below, today)
# are parallel lists (three one-member modules were collapsed into
# one three-member module; config: itself stays a single path there because
# the sibling ITU-Giga-KP-Plugin ship gate's check_pack.py does a plain
# os.path.exists(pack/config) per module and has no notion of a
# comma-joined list -- see manifest.yaml's comment on that module). For the
# member- prefixed entries in building_blocks, the Nth one's config is the
# Nth comma-separated path in member_configs:. A module with a single
# config for multiple members would silently point every one of them at the
# same file; a length mismatch is a manifest.yaml bug, not something to
# guess through.
_MANIFEST = yaml.safe_load((PACK_DIR / "manifest.yaml").read_text())
_CONFIG_BY_MEMBER: dict[str, str] = {}
for _module in _MANIFEST["modules"]:
    _member_bbs = [b for b in _module.get("building_blocks", []) if b.startswith("member-")]
    if not _member_bbs:
        continue
    _raw_configs = _module.get("member_configs", _module["config"])
    _configs = [c.strip() for c in _raw_configs.split(",")]
    if len(_configs) != len(_member_bbs):
        raise RuntimeError(
            f"manifest.yaml module {_module['id']!r}: {len(_member_bbs)} member "
            f"building_blocks but {len(_configs)} config path(s) -- must be 1:1 "
            "(add/fix member_configs:)"
        )
    for _bb, _cfg in zip(_member_bbs, _configs):
        _CONFIG_BY_MEMBER[_bb.removeprefix("member-").upper()] = _cfg


def _semantic_fields_for(member_code: str) -> list[str]:
    config_path = _CONFIG_BY_MEMBER.get(member_code)
    if not config_path:
        return []
    cfg = yaml.safe_load((PACK_DIR / config_path).read_text())
    return cfg.get("semantic", {}).get("fields", [])


# One session per Security Server for the life of the process, not one per
# API hit. Every hit used to POST /login again, and a login is a SERVER-SIDE
# admin-UI session: /api/acl alone logs into every Security Server, the page
# polls it every 30s, and a two-hour demo therefore opened hundreds of
# sessions on the same admin UIs whose concurrent-session behaviour
# runbook.md already warns about. AdminSession re-logs-in by itself on a 401,
# so a session the server expires is replaced on the next call rather than
# breaking it.
#
# The lock is held across the login (up to 10s) so two threadpool workers
# racing on a cold cache cannot each open one: serialising the rare cold path
# is cheaper than the duplicate sessions it prevents, and every later call
# takes the lock only long enough to read the dict.
_SESSIONS: dict[str, xroad.AdminSession] = {}
_SESSION_LOCK = threading.Lock()


def _admin_session(host: str) -> xroad.AdminSession:
    with _SESSION_LOCK:
        session = _SESSIONS.get(host)
        if session is None:
            session = _SESSIONS[host] = xroad.AdminSession(host, ADMIN_USER, ADMIN_PASSWORD)
        return session


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
    S16 lock and the off-event-loop wrapping only need stating
    once."""
    with _MUTATE_LOCK:
        return journal_mod.reset(JOURNAL, _admin_session, TRUTH.expected_acl, TRUTH.topology)


def _log_task_exception(task: asyncio.Task) -> None:
    """done-callback for the fire-and-forget startup reset."""
    if not task.cancelled() and task.exception() is not None:
        _LOG.error("startup reset failed: %r -- the watchdog reconciles from here", task.exception())


async def _watchdog() -> None:
    """Belt and braces: a demo that silently leaves the
    ACL revoked and makes acceptance.sh fail an hour later for an
    unrelated-looking reason is exactly the kind of thing that discredits
    the pack. Reset after HEARTBEAT_TIMEOUT_S with no page heartbeat.

    reset() performs several blocking HTTPS logins at up to 10s timeout
    each -- without to_thread, the whole ASGI event loop stops for
    that period: /api/health and /api/heartbeat cannot answer, so the
    page's own heartbeat cannot land, so the watchdog's own timeout logic
    is being starved by the watchdog."""
    global _last_heartbeat
    while True:
        await asyncio.sleep(WATCHDOG_POLL_S)
        if time.time() - _last_heartbeat > HEARTBEAT_TIMEOUT_S and JOURNAL.is_dirty():
            try:
                await asyncio.to_thread(_reset_locked)
            except Exception:  # noqa: BLE001 -- see below
                # reset() logs into every Security Server (xroad.py raises on
                # a failed login), so one unreachable server used to end this
                # task for the lifetime of the process -- silently, since
                # nothing awaits it. The journal then stays dirty forever and
                # acceptance.sh refuses with "the federation is mid-demo":
                # exactly the failure this watchdog exists to prevent. Log
                # and stay in the loop; the next poll retries.
                _LOG.exception("watchdog reset failed -- retrying in %ss", WATCHDOG_POLL_S)
                continue
            _last_heartbeat = time.time()


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    startup_reset_task = None
    if JOURNAL.is_dirty():
        # Non-blocking, a deliberate choice between two real
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
        # Fire-and-forget: nothing awaits this task, so an exception inside it
        # is swallowed by asyncio and never printed. The watchdog still
        # reconciles afterwards, but only a log line says why the journal was
        # still dirty when it did.
        startup_reset_task.add_done_callback(_log_task_exception)
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
    xroad.SHARED_CLIENT.close()
    # The consumer hop's own pool, separate since docs/production-delta.md
    # row 19 stopped it sharing SHARED_CLIENT's trust decision -- and so a
    # second thing to close, or the leak this shutdown exists to prevent
    # just moved to the other client.
    xroad.EXCHANGE_CLIENT.close()


app = FastAPI(title="KP2 demonstration console", lifespan=_lifespan)


# EVERY /api/* route below carries _require_console_origin except this one.
# /api/health is the single deliberate exemption: the Dockerfile HEALTHCHECK,
# scripts/console.sh and verify.sh's console smoke all curl it with no
# header, and it reads nothing and changes nothing. A new route without the
# guard fails apps/console/tests/test_app_csrf.py's route sweep -- opting IN
# per route is what left /api/acl (an admin login and a read on every
# Security Server, per hit, reachable cross-origin via <img>) and
# /api/heartbeat (a cross-origin simple-form POST postpones the watchdog
# reset indefinitely) open.
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/topology", dependencies=[Depends(_require_console_origin)])
def get_topology():
    """The Truth topology plus a live reachability probe per server, so the
    page can show honestly that the federation is up before anyone types a
    NIN."""
    servers = []
    for ss in TRUTH.topology["security_servers"]:
        try:
            # The shared pool, not a client per server per poll: this
            # endpoint is polled every 30s and never closed one of them.
            resp = xroad.SHARED_CLIENT.get(
                f"https://{ss['host']}:{ss['ui_port']}", timeout=3.0
            )
            reachable = resp.status_code == 200
        except httpx.HTTPError:
            reachable = False
        servers.append({**ss, "reachable": reachable})
    return {**TRUTH.topology, "security_servers": servers}


@app.get("/api/learners", dependencies=[Depends(_require_console_origin)])
def get_learners():
    """A handful of seeded NINs: several present in both registries, and --
    labelled as such -- one present in PNIA but absent from PLR, which is
    acceptance.sh check 2.6.5's clean-404 case. Read from the same CSVs
    seed.sh regenerates. No names here: the name
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

    Guarded too, even though a read
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

    # The file acceptance.sh writes for this NIN, if it has been run. Only
    # ever reported, never written: writing pack outputs is acceptance.sh's
    # act, and the console is not in the acceptance path.
    artifact = OUT_DIR / f"application-{nin}.json"

    return {
        "credential_application": application,
        "calls": [dataclasses.asdict(r) for r in results],
        "layers": TRUTH.layers,
        "layer_sources": _layer_sources(),
        "client_header": TRUTH.exchange["headers"]["X-Road-Client"],
        "identity_held_fields": _identity_held_fields(nin),
        "semantic_fields": semantic_fields,
        "artifact_hint": f"out/application-{nin}.json" if artifact.exists() else None,
    }


def _identity_held_fields(nin: str) -> list[str]:
    """Field names PNIA's own record carries but its published contract
    doesn't send -- read directly from the mock, off the bus entirely,
    never through xroad.py. Never the values."""
    # Validated again even though get_exchange already validated its nin --
    # deliberate double-check: this is a
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
    unauthorised client through ITS OWN Security Server -- it
    must be routed this way, or the denial comes from a consumer SS
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


CATALOGUE_MISSING = (
    "onboarding/catalogue.yaml has not been rendered -- run scripts/render-onboarding.sh"
)


@app.get("/api/catalogue", dependencies=[Depends(_require_console_origin)])
def get_catalogue():
    """What is published on this bus, straight from the register's own
    generated onboarding/catalogue.yaml -- no join-api, so the tab works
    with `scripts/join.sh down`, which is its normal state."""
    catalogue = truth_mod.load_catalogue(PACK_DIR)
    if catalogue is None:
        return {"error": CATALOGUE_MISSING}
    return {
        **catalogue,
        "source": "onboarding/catalogue.yaml",
        "api_form": "GET :8091/catalogue (applicant token)",
    }


def _layer_sources() -> dict[str, list[str]]:
    """Per EIF layer, where in the pack that layer's claim actually lives:
    a file path and the string it holds. Empty lists if the catalogue has
    not been rendered -- the panes render without the footer."""
    catalogue = truth_mod.load_catalogue(PACK_DIR)
    if catalogue is None:
        return {}

    sources: dict[str, list[str]] = {"legal": [], "organisational": [], "semantic": [], "technical": []}
    for svc in catalogue.get("services", []):
        provider = svc.get("provider", {})
        key, code = provider.get("key", "?"), provider.get("code", "?")
        semantic = svc.get("semantic", {})
        sources["legal"].append(
            f"configs/member-{key}/{key}.yaml -- {code} {svc.get('service_code')} lawful_basis: "
            f"{svc.get('lawful_basis', '(not stated)')}"
        )
        sources["organisational"].append(
            f"{svc.get('entry')} -- {svc.get('service_code')} ACL names: "
            f"{', '.join(svc.get('access') or []) or '(nobody)'}"
        )
        sources["semantic"].append(
            f"configs/semantic/semantic-map.yaml -- {svc.get('service_code')} entity "
            f"{semantic.get('entity')}, anchored on {semantic.get('anchor')}"
        )
    sources["technical"].append(
        "configs/x-road-bus/once-only-exchange.yaml -- the calls, their r1 paths and "
        f"X-Road-Client {TRUTH.exchange['headers']['X-Road-Client']}"
    )
    return sources


@app.get("/api/acl", dependencies=[Depends(_require_console_origin)])
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
    """Only MUTABLE_SERVICE is ever mutated here."""
    subsystem = _subsystem_for_service(MUTABLE_SERVICE)
    subjects = TRUTH.expected_acl[MUTABLE_SERVICE]
    if not subjects:
        raise HTTPException(400, f"{MUTABLE_SERVICE} has no expected subject to mutate")
    subject = subjects[0]

    # Held across the read that establishes prior_state, the journal write,
    # the live call, AND mark_applied -- releasing it earlier would
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
        # the real state -- calling this endpoint twice with the
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
    truth.expected_acl exactly -- never a silent 'reset ok'."""
    global _last_heartbeat
    _last_heartbeat = time.time()
    result = _reset_locked()
    if not result["ok"]:
        raise HTTPException(409, result)
    return result


@app.post("/api/heartbeat", dependencies=[Depends(_require_console_origin)])
def post_heartbeat():
    global _last_heartbeat
    _last_heartbeat = time.time()
    return {"ok": True}


# -- join tab --------------------------------------------------------
# A thin, read-mostly proxy onto the REAL apps/join-api/app.py -- never the
# journal, never _MUTATE_LOCK: a join is not an ACL mutation this console
# tracks, so nothing below touches JOURNAL or _reset_locked,
# and scripts/acceptance.sh's dirty-journal refusal keeps meaning exactly
# what it already means. The operator token stays server-side
# (JOIN_OPERATOR_TOKEN above); the browser only ever sees join-api's own
# response bodies, which never carry it.
#
# Route paths verified directly against apps/join-api/app.py's actual routes,
# not the documented-but-inaccurate "/api/join" base path (a discrepancy
# already found and left alone) -- join-api's real routes have no prefix:
# POST/GET /requests, POST /requests/{id}/{approve,resume,reject}.
_JOIN_REQUEST_ID_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def _validated_join_request_id(request_id: str) -> str:
    # Mirrors join-api's own _REQUEST_ID_RE (a defence THAT side already
    # has) -- checked here too because this value is about to become part
    # of an outbound URL this process builds, not just an inbound one.
    if not _JOIN_REQUEST_ID_RE.match(request_id):
        raise HTTPException(400, "request id contains characters no join-api id can ever have")
    return request_id


def _join_api(method: str, path: str, **kwargs) -> httpx.Response:
    """Server-to-server call to the real join-api -- same
    shape as _admin_session()'s calls to X-Road's admin API. join-api is on
    the same linkup network as every other console-adjacent service
    (docker-compose.yml), reachable at JOIN_API_URL without leaving the
    Docker network. Sends the same request-boundary header join-api itself
    requires (apps/join-api/app.py's _require_console_origin) -- this call
    never carries an Origin header, so that half of the guard is a no-op
    here, which is correct: this IS the trusted server-to-server leg."""
    headers = {"Authorization": f"Bearer {JOIN_OPERATOR_TOKEN}", CONSOLE_HEADER: "1"}
    headers.update(kwargs.pop("headers", None) or {})
    return httpx.request(method, f"{JOIN_API_URL}{path}", headers=headers, timeout=10.0, **kwargs)


def _proxy_join(method: str, path: str, **kwargs) -> dict:
    """join-api's JSON body verbatim on success. join-api is profile "demo",
    like the console itself, and not always running (scripts/join.sh
    up/down) -- that is a fact for the tab to render (the queue view
    treats a body with no "requests" key as "no join API reachable"), not a
    500 the console throws at its own caller. Same for a missing operator
    token: the tab renders the remedy, the other tabs are unaffected."""
    if not JOIN_OPERATOR_TOKEN:
        return {"error": JOIN_TOKEN_MISSING}
    try:
        resp = _join_api(method, path, **kwargs)
    except httpx.HTTPError as exc:
        return {"error": f"join-api unreachable: {exc}"}
    try:
        body = resp.json()
    except ValueError:
        return {"error": f"join-api returned a non-JSON response (HTTP {resp.status_code})"}
    if resp.status_code >= 400:
        return {"error": body.get("detail", body) if isinstance(body, dict) else body}
    return body


@app.get("/api/join/requests", dependencies=[Depends(_require_console_origin)])
def get_join_requests():
    """The pending queue: every request, each already
    carrying its config diff, its step sequence coloured by actor, and (for
    an ACTIVE record) the live-but-uncommitted flag -- all computed by
    join-api's own GET /requests (apps/join-api/app.py's _record_view)."""
    return _proxy_join("GET", "/requests")


@app.post("/api/join/requests/{request_id}/approve", dependencies=[Depends(_require_console_origin)])
def post_join_approve(request_id: str, body: dict | None = None):
    """Forwards decision_reference the same way post_join_reject
    already forwards reason -- join-api is where the required-field check
    lives, this is just a pass-through."""
    request_id = _validated_join_request_id(request_id)
    return _proxy_join("POST", f"/requests/{request_id}/approve", json=body or {})


@app.post("/api/join/requests/{request_id}/resume", dependencies=[Depends(_require_console_origin)])
def post_join_resume(request_id: str):
    """Recovers a FAILED job from its last_completed_step (job.py's own
    resume logic) -- the console's Resume button, not a new mechanism."""
    request_id = _validated_join_request_id(request_id)
    return _proxy_join("POST", f"/requests/{request_id}/resume")


@app.post("/api/join/requests/{request_id}/reject", dependencies=[Depends(_require_console_origin)])
def post_join_reject(request_id: str, body: dict | None = None):
    request_id = _validated_join_request_id(request_id)
    return _proxy_join("POST", f"/requests/{request_id}/reject", json=body or {})


class _RevalidatingStatic(StaticFiles):
    """StaticFiles, but every asset revalidates.

    FileResponse sends Last-Modified and no Cache-Control, which lets a
    browser apply HEURISTIC freshness and serve app.js from cache without
    asking. Across a `console.sh up --build` that means fresh index.html
    with a stale app.js -- a new tab's button rendering against a script
    that has no loader for it, which reads as a dead tab and survives a
    plain reload. (JSON responses carry no Last-Modified, so they were
    never heuristically cached; this is a static-asset fault only.)

    no-cache, not no-store: revalidate every time, but the ETag
    StaticFiles already emits still makes the answer a bodyless 304.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Mounted last so it never shadows an /api/* route -- StaticFiles(html=True)
# serves static/index.html for "/" and any other unmatched path.
app.mount("/", _RevalidatingStatic(directory=pathlib.Path(__file__).parent / "static", html=True), name="static")
