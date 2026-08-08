"""A path parameter went straight into a
URL that addresses an X-Road service (get_exchange, get_exchange_negative)
or the mock registry directly (_identity_held_fields). No network, no
Docker -- PACK_DIR points at the existing test fixtures; rejected requests
never reach xroad.exchange() at all, so no mocking is needed for them.

Checked against a real HTTP client (TestClient's ASGI transport, and
separately confirmed against a real uvicorn process with curl) rather than
assumed: three of the six shapes this plan's Step 5 named never reach
get_exchange over real HTTP at all, and 404 instead of 400 --

  - a %2F-encoded traversal decodes to a literal "/" in ASGI
    scope["path"] before Starlette's router sees it (uvicorn/h11 decode
    per the ASGI spec), landing on a path with more segments than the
    single-segment `{nin}` pattern matches;
  - a bare ".." path segment gets collapsed by standard URL dot-segment
    normalisation (RFC 3986 -- every normal HTTP client does this,
    including browsers, not just httpx) before the request is even sent;
  - an empty segment (a trailing slash) doesn't match the default
    converter's one-or-more-characters requirement.

All three are safe outcomes -- the request never reaches our code -- just
not the 400 this plan assumed without checking. The validator itself is
exercised directly against all six shapes below regardless, proving the
defense-in-depth still holds for any caller that *does* reach it (a
different framework version, a different mount, a future route)."""
import csv
import os
import pathlib
import sys
import urllib.parse

os.environ["PACK_DIR"] = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "pack")
os.environ["OUT_DIR"] = "/tmp"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]
PERSONS_CSV = REAL_PACK_DIR / "apps" / "data" / "persons.csv"

client = TestClient(app.app, raise_server_exceptions=False)

# All six shapes this plan's Step 5 names -- exercised directly against the
# validator regardless of what any given transport does with them.
ALL_BAD_SHAPES = [
    "../../enrolment-api/enrolments/123",  # what a %2F traversal decodes to
    "..",                                   # a raw path segment
    "",                                     # empty
    "1" * 200,                              # 200-character string
    "11111111111x",                         # 11 digits plus a letter
    "١" * 11,                               # eleven non-ASCII digits
]

# Of those six, these three actually reach get_exchange over real HTTP --
# the other three are normalised or fail to route before our code runs
# (see test_three_shapes_never_reach_the_handler_over_http).
REACHES_HANDLER_OVER_HTTP = ["1" * 200, "11111111111x", "١" * 11]


def test_every_seeded_nin_passes_validation():
    with open(PERSONS_CSV, newline="", encoding="utf-8") as f:
        nins = [row["nin"] for row in csv.DictReader(f)]
    assert nins, "apps/data/persons.csv is empty -- nothing to prove"
    for nin in nins:
        assert app._validated_nin(nin) == nin


def _assert_rejected(fn, nin):
    try:
        fn(nin)
    except app.HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "nin must be 11 digits"
        if nin:
            assert nin not in str(exc.detail)
    else:
        raise AssertionError(f"{nin!r} should have been rejected")


def test_all_bad_shapes_rejected_by_validator_directly():
    for nin in ALL_BAD_SHAPES:
        _assert_rejected(app._validated_nin, nin)


def test_identity_held_fields_rejects_bad_nin_directly():
    """Module-level function a future caller could reach directly, not just
    via get_exchange -- the deliberate double-check this plan calls for."""
    for nin in ALL_BAD_SHAPES:
        _assert_rejected(app._identity_held_fields, nin)


# The CSRF guard (test_app_csrf.py) runs before
# these handlers do, so it needs satisfying here too -- these tests are
# about the NIN boundary, not the CSRF one.
CSRF_HEADERS = {"X-KP2-Console": "1"}


def test_get_exchange_rejects_bad_nin_over_http():
    for nin in REACHES_HANDLER_OVER_HTTP:
        resp = client.get(f"/api/exchange/{urllib.parse.quote(nin, safe='')}", headers=CSRF_HEADERS)
        assert resp.status_code == 400, (nin, resp.status_code, resp.text)
        assert nin not in resp.text


def test_get_exchange_negative_rejects_bad_nin_over_http():
    for nin in REACHES_HANDLER_OVER_HTTP:
        resp = client.get(f"/api/exchange/{urllib.parse.quote(nin, safe='')}/negative", headers=CSRF_HEADERS)
        assert resp.status_code == 400, (nin, resp.status_code, resp.text)
        assert nin not in resp.text


def test_three_shapes_never_reach_the_handler_over_http():
    """See the module docstring: verified 404 for all three, both via
    TestClient's ASGI transport and separately against a real uvicorn
    process with curl --path-as-is."""
    assert client.get("/api/exchange/..%2F..%2Fenrolment-api%2Fenrolments%2F123").status_code == 404
    assert client.get("/api/exchange/..").status_code == 404
    assert client.get("/api/exchange/").status_code == 404
