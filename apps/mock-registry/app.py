"""KP2 mock registry — the information system behind a Security Server.

One generic app, parameterised by env:
  ENTITY     collection name (persons | enrolments | school-records)
  CSV_FILE   seed data (loaded at startup; seed.sh restarts to reload)
  SPEC_FILE  curated OpenAPI 3 spec served at /spec.yaml (what X-Road parses)
  KEY_FIELD  lookup key (nin)

Endpoints: GET /v1/{ENTITY}/{key}              -> record (spec-declared
                                                   fields only) | 404
           GET /v1/{ENTITY}/{key}/held-fields  -> field names the CSV
                                                   carries but the spec
                                                   doesn't declare | 404
           GET /v1/health                      -> {status, entity, count}
           GET /spec.yaml                       -> the OpenAPI description
                                                    for Add REST

The response is filtered to exactly the OpenAPI spec's declared fields, not
the whole CSV row: purpose limitation is a property of the published
contract, proved by absence -- a field the CSV carries but
the spec never declares is held, never sent, and /held-fields exposes only
its name (never its value) so the demo console can show what was withheld
without ever transporting it.

Demo stand-in: replaced later by a real system (e.g. a Joget DX app) behind the
same OpenAPI contract — the X-Road configuration does not change.
"""
import csv
import os

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

ENTITY = os.environ["ENTITY"]
CSV_FILE = os.environ["CSV_FILE"]
SPEC_FILE = os.environ["SPEC_FILE"]
KEY_FIELD = os.environ.get("KEY_FIELD", "nin")

with open(CSV_FILE, newline="", encoding="utf-8") as f:
    RECORDS = {row[KEY_FIELD]: row for row in csv.DictReader(f)}

with open(SPEC_FILE) as f:
    _spec = yaml.safe_load(f)
# Exactly one path per spec (this generic app serves one entity each) --
# its 200 response schema's declared properties are the contract.
# Mirrors apps/join-api/validate.py's contract_fields() expression exactly,
# on purpose -- this container cannot import join-api's code, and the two
# computing the same set independently is why a live response silently
# diverging from its own contract went unnoticed for as long as it did: the
# provider and the contract could not disagree. Do not factor this out into
# a shared library -- that would hide the very coupling the check this
# enables exists to break.
_response_schema = next(iter(_spec["paths"].values()))["get"]["responses"]["200"]["content"]["application/json"]["schema"]
DECLARED_FIELDS = list(_response_schema["properties"].keys())

app = FastAPI(title=f"Progressa mock registry: {ENTITY}")


@app.get("/v1/health")
def health():
    return {"status": "ok", "entity": ENTITY, "count": len(RECORDS)}


@app.get("/spec.yaml")
def spec():
    return FileResponse(SPEC_FILE, media_type="application/yaml")


@app.get("/v1/" + ENTITY + "/{key}/held-fields")
def held_fields(key: str):
    if key not in RECORDS:
        raise HTTPException(status_code=404, detail=f"{ENTITY[:-1]} not found")
    return {"held": [f for f in RECORDS[key] if f not in DECLARED_FIELDS]}


@app.get("/v1/" + ENTITY + "/{key}")
def lookup(key: str):
    if key not in RECORDS:
        raise HTTPException(status_code=404, detail=f"{ENTITY[:-1]} not found")
    return {f: RECORDS[key][f] for f in DECLARED_FIELDS if f in RECORDS[key]}
