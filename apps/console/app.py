"""apps/console/app.py -- read (Task 4) and write (Task 5) API for the KP2
demonstration console. The browser talks only to this service; this is the
only thing in the demo that talks to X-Road (see the plan's Architecture
section). Credentials come from the environment (.env via Docker Compose),
read here once, never returned in a response or logged.
"""
from __future__ import annotations

import csv
import dataclasses
import os
import pathlib

import httpx
from fastapi import FastAPI, HTTPException

import truth as truth_mod
import xroad

PACK_DIR = pathlib.Path(os.environ.get("PACK_DIR", "/pack"))
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "/out"))
ADMIN_USER = os.environ["XROAD_ADMIN_USER"]
ADMIN_PASSWORD = os.environ["XROAD_ADMIN_PASSWORD"]
JOURNAL_PATH = OUT_DIR / "console-acl-journal.json"

app = FastAPI(title="KP2 demonstration console")

# Loaded once at startup, not per-request: a stale Truth after a redeploy
# means the container needs restarting anyway (deployment.yaml/topology.json
# changing is a redeploy event, not something this demo tool hot-reloads).
TRUTH = truth_mod.load_truth(PACK_DIR)


def _admin_session(host: str) -> xroad.AdminSession:
    return xroad.AdminSession(host, ADMIN_USER, ADMIN_PASSWORD)


def _subsystem_for_service(service_code: str) -> dict:
    for subsystem in TRUTH.topology["subsystems"]:
        if any(svc["code"] == service_code for svc in subsystem["services"]):
            return subsystem
    raise HTTPException(404, f"no subsystem publishes service {service_code!r}")


def _journal_is_dirty() -> bool:
    if not JOURNAL_PATH.exists():
        return False
    return JOURNAL_PATH.read_text().strip() not in ("", "[]")


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
        application[field.name] = {"value": values.get(field.name), "source": source}

    return {
        "credential_application": application,
        "calls": [dataclasses.asdict(r) for r in results],
    }


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
