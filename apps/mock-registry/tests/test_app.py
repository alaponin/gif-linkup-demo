"""Unit tests for apps/mock-registry/app.py's purpose-limitation filter (T5.1).

DECLARED_FIELDS is computed from the OpenAPI spec's response schema and
filters every response through it -- the mechanism behind "purpose
limitation is a property of the published contract, proved by absence"
(app.py's own docstring). It had no tests of its own; scripts/acceptance.sh
check 2.6.3 is the only thing that has ever exercised it, inside a
six-to-fifteen-minute live run.

ENTITY/CSV_FILE/SPEC_FILE/KEY_FIELD are pointed at the REAL
apps/data/persons.csv and apps/specs/pnia-identity.openapi.yaml, not a
fixture copy (Design decision 2, 2026-08-01-kp2-testing-gaps.md) -- using
the real files means this test breaks if the spec and the seed data ever
diverge, which is itself worth catching. app.py reads all four env vars at
import time, so they have to be set before import -- the same pattern
apps/console/tests/test_app_mutate_acl.py already uses.
"""
import csv
import importlib.util
import os
import pathlib

PACK_DIR = pathlib.Path(__file__).resolve().parent.parent.parent.parent
os.environ["ENTITY"] = "persons"
os.environ["CSV_FILE"] = str(PACK_DIR / "apps" / "data" / "persons.csv")
os.environ["SPEC_FILE"] = str(PACK_DIR / "apps" / "specs" / "pnia-identity.openapi.yaml")
os.environ["KEY_FIELD"] = "nin"

# Loaded by path under a distinct module name, not `sys.path.insert` +
# `import app` -- apps/console/tests/test_app_mutate_acl.py already claims
# the plain name "app" in sys.modules, and when verify.sh runs both test
# directories in one pytest session, a second `import app` here would
# silently reuse the console's cached module instead of loading this one.
_spec = importlib.util.spec_from_file_location(
    "mock_registry_app", pathlib.Path(__file__).resolve().parent.parent / "app.py"
)
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app.app)

# persons.csv carries these three; pnia-identity.openapi.yaml declares none
# of them (Design decision 2) -- the withheld set this whole filter exists
# to enforce.
WITHHELD_FIELDS = ("mother_name", "birth_registration_no", "residence_address")


def _seed_nin() -> str:
    with open(os.environ["CSV_FILE"], newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))["nin"]


def test_lookup_returns_exactly_the_declared_field_set():
    nin = _seed_nin()
    resp = client.get(f"/v1/persons/{nin}")
    assert resp.status_code == 200
    assert set(resp.json().keys()) == set(app.DECLARED_FIELDS)  # exact, not a subset


def test_withheld_fields_appear_nowhere_in_the_response_not_even_the_raw_text():
    nin = _seed_nin()
    resp = client.get(f"/v1/persons/{nin}")
    body = resp.json()
    for field in WITHHELD_FIELDS:
        assert field not in body
    # Checked against the raw text too -- a field withheld from the parsed
    # model but leaking through an error message or a stray debug key would
    # still pass a parsed-only assertion.
    for field in WITHHELD_FIELDS:
        assert field not in resp.text


def test_unknown_key_returns_404_with_no_record_data():
    resp = client.get("/v1/persons/00000000000")
    assert resp.status_code == 404
    assert "given_name" not in resp.text


def test_held_fields_returns_exactly_the_undeclared_names():
    nin = _seed_nin()
    resp = client.get(f"/v1/persons/{nin}/held-fields")
    assert resp.status_code == 200
    assert set(resp.json()["held"]) == set(WITHHELD_FIELDS)


def test_held_fields_never_exposes_the_values_themselves():
    nin = _seed_nin()
    with open(os.environ["CSV_FILE"], newline="", encoding="utf-8") as f:
        row = next(r for r in csv.DictReader(f) if r["nin"] == nin)
    resp = client.get(f"/v1/persons/{nin}/held-fields")
    for field in WITHHELD_FIELDS:
        assert row[field] not in resp.text, f"{field}'s value leaked into /held-fields"


def test_held_fields_on_unknown_key_returns_404():
    resp = client.get("/v1/persons/00000000000/held-fields")
    assert resp.status_code == 404


def test_health_reports_the_record_count_matching_the_csv():
    with open(os.environ["CSV_FILE"], newline="", encoding="utf-8") as f:
        expected = sum(1 for _ in csv.DictReader(f))
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"] == "persons"
    assert body["count"] == expected


def test_every_declared_field_exists_as_a_csv_column():
    """Pins the coupling, not the behaviour (T5.1 Step 3): if the spec ever
    declares a field the seed data does not carry, app.py's lookup()
    (`if f in RECORDS[key]`) silently omits it from every response -- a
    real, currently-undetectable failure mode. Checked against the CSV
    header directly, not RECORDS, so an empty CSV would still catch a
    missing column."""
    with open(os.environ["CSV_FILE"], newline="", encoding="utf-8") as f:
        header = set(next(csv.reader(f)))
    missing = set(app.DECLARED_FIELDS) - header
    assert not missing, f"spec declares fields the CSV has no column for: {missing}"
