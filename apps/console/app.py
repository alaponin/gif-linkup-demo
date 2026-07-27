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
import time

import httpx
from fastapi import FastAPI, HTTPException
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

# Loaded once at startup, not per-request: a stale Truth after a redeploy
# means the container needs restarting anyway (deployment.yaml/topology.json
# changing is a redeploy event, not something this demo tool hot-reloads).
TRUTH = truth_mod.load_truth(PACK_DIR)
JOURNAL = journal_mod.Journal(OUT_DIR / "console-acl-journal.json")
_last_heartbeat = time.time()


def _admin_session(host: str) -> xroad.AdminSession:
    return xroad.AdminSession(host, ADMIN_USER, ADMIN_PASSWORD)


def _subsystem_for_service(service_code: str) -> dict:
    for subsystem in TRUTH.topology["subsystems"]:
        if any(svc["code"] == service_code for svc in subsystem["services"]):
            return subsystem
    raise HTTPException(404, f"no subsystem publishes service {service_code!r}")


def _journal_is_dirty() -> bool:
    return JOURNAL.is_dirty()


async def _watchdog() -> None:
    """Belt and braces (design decision 3): a demo that silently leaves the
    ACL revoked and makes acceptance.sh fail an hour later for an
    unrelated-looking reason is exactly the kind of thing that discredits
    the pack. Reset after HEARTBEAT_TIMEOUT_S with no page heartbeat."""
    global _last_heartbeat
    while True:
        await asyncio.sleep(WATCHDOG_POLL_S)
        if time.time() - _last_heartbeat > HEARTBEAT_TIMEOUT_S and JOURNAL.is_dirty():
            journal_mod.reset(JOURNAL, _admin_session, TRUTH.expected_acl, TRUTH.topology)
            _last_heartbeat = time.time()


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    if JOURNAL.is_dirty():
        journal_mod.reset(JOURNAL, _admin_session, TRUTH.expected_acl, TRUTH.topology)
    watchdog_task = asyncio.create_task(_watchdog())
    yield
    watchdog_task.cancel()


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
    seed.sh regenerates."""
    with open(PACK_DIR / "apps/data/persons.csv", newline="") as f:
        persons = {row["nin"]: row for row in csv.DictReader(f)}
    with open(PACK_DIR / "apps/data/enrolments.csv", newline="") as f:
        enrolled_nins = {row["nin"] for row in csv.DictReader(f)}

    both = sorted(nin for nin in persons if nin in enrolled_nins)[:4]
    pnia_only = sorted(nin for nin in persons if nin not in enrolled_nins)[:1]

    learners = [
        {
            "nin": nin,
            "name": f"{persons[nin]['given_name']} {persons[nin]['family_name']}",
            "case": "happy path",
        }
        for nin in both
    ] + [
        {
            "nin": nin,
            "name": f"{persons[nin]['given_name']} {persons[nin]['family_name']}",
            "case": "no enrolment record (clean 404)",
        }
        for nin in pnia_only
    ]
    return {"learners": learners}


@app.get("/api/exchange/{nin}")
def get_exchange(nin: str):
    """The assembled application with per-field provenance -- the same shape
    acceptance.sh already writes to out/application-{nin}.json -- plus the
    per-call technical detail the inspector tab renders."""
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
            "label": field.label,
            "group": field.group,
        }

    return {
        "credential_application": application,
        "calls": [dataclasses.asdict(r) for r in results],
        "layers": TRUTH.layers,
    }


@app.get("/api/exchange/{nin}/negative")
def get_exchange_negative(nin: str):
    """The negative check (Module 5.6): the same calls, run as the
    unauthorised client through ITS OWN Security Server -- confirmed live
    this must be routed this way, or the denial comes from a consumer SS
    rejecting a client it doesn't host rather than from the provider's ACL."""
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
    session = _admin_session(subsystem["hosted_on"])
    prior_state = "granted" if action == "revoke" else "revoked"

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


@app.post("/api/acl/revoke")
def post_acl_revoke():
    return _mutate_acl("revoke")


@app.post("/api/acl/grant")
def post_acl_grant():
    return _mutate_acl("grant")


@app.post("/api/reset")
def post_reset():
    """Reverses the journal newest-first and verifies the result equals
    truth.expected_acl exactly -- never a silent 'reset ok' (Task 5 Step 2)."""
    global _last_heartbeat
    _last_heartbeat = time.time()
    result = journal_mod.reset(JOURNAL, _admin_session, TRUTH.expected_acl, TRUTH.topology)
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
