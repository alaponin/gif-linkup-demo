"""Unit tests for apps/join-api/validate.py -- join-b Task 2's twelve
checks (spec S8). Pure functions over fixture dicts for checks 2-8 and 12;
checks 9-11 read the fixture OpenAPI documents under fixtures/specs/.

Check 9 (backend reachability) is the one place this suite does real I/O,
deliberately: a local http.server thread stands in for "inside the linkup
network" (task-2 brief point 6), and fixtures/specs/unreachable.yaml's
servers.url (127.0.0.1:1, a privileged port nothing listens on) is a real
connection attempt that fails fast, not a mocked-away one -- mocking it
would defeat the point of the check.
"""
from __future__ import annotations

import http.server
import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import validate as validate_module  # noqa: E402
from validate import RejectionError, ValidationContext, validate  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "specs"
REACHABLE_PORT = 18765


class _OKHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- stdlib method name
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # silence -- keep pytest -q output clean
        pass


@pytest.fixture(scope="module", autouse=True)
def _reachable_backend():
    """Stands in for "the linkup network" for every test in this module --
    clean.yaml, has_delete.yaml and bad_service_code.yaml all name this
    port as their servers.url."""
    server = http.server.HTTPServer(("127.0.0.1", REACHABLE_PORT), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield
    server.shutdown()
    thread.join()


def _fetch_fixture(name: str):
    text = (FIXTURES / name).read_text()
    return lambda url: text


MANIFEST = {
    "identity": {
        "instance": "PROGRESSA",
        "member_class": "GOV",
        "owner": {"code": "PDGA"},
        "members": {
            "moeys": {"code": "MOEYS", "subsystem": "PEMIS", "origin": "canonical"},
            "pnea": {"code": "PNEA", "subsystem": "EXAMS", "origin": "canonical"},
            "plr": {"code": "PLR", "subsystem": "ENROLMENT", "origin": "canonical"},
            "pnia": {"code": "PNIA", "subsystem": "IDENTITY", "origin": "canonical"},
        },
    }
}

POLICY = {
    "member_class": "GOV",
    "approval": "explicit",
    "default_hosting": "hosted_on",
    "allowed_methods": ["GET"],
}

# Deliberately does not include every canonical member -- only plr and pnia
# have "existing config on disk" in this fixture set, which is what lets
# test_not_canonical_rejects_the_owner_code below isolate check 4 (not
# canonical) from check 3 (collision): PDGA is canonical but has no entry
# here, so a PDGA join reaches check 4 rather than failing check 3 first.
EXISTING_SERVERS = {
    "plr": {"code": "SS-PLR", "dns_name": "ss-plr", "hosted_on": None},
    "pnia": {"code": "SS-PNIA", "dns_name": "ss-pnia", "hosted_on": None},
}


def _payload(**overrides) -> dict:
    base = {
        "code": "PTSB",
        "name": "Progressa Tertiary Scholarship Board",
        "subsystem": "SCHOLARSHIP",
        "subsystem_description": "Scholarship award management",
        "security_server": {"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
        "backend": {"auth": "network_allowlist"},
    }
    base.update(overrides)
    return base


def _run(raw: dict, *, manifest=MANIFEST, policy=POLICY, existing_servers=EXISTING_SERVERS, **kw):
    # validate() returns (payload, ValidationContext) since Task 5 (the
    # context's fetched_specs feeds module 2.7's join-time drift baseline) --
    # every caller in this file wants just the payload.
    payload, _ctx = validate(raw, manifest=manifest, policy=policy, existing_servers=existing_servers, **kw)
    return payload


def _rejects(raw: dict, check: str, **kw):
    with pytest.raises(RejectionError) as exc_info:
        _run(raw, **kw)
    assert exc_info.value.check == check, (
        f"expected check {check!r} to fail, got {exc_info.value.check!r}: "
        f"{exc_info.value.message}"
    )
    return exc_info.value


# -- happy paths ---------------------------------------------------------------


def test_consume_only_hosted_join_passes_every_check():
    payload = _run(_payload(requested_access=["PROGRESSA/GOV/PNIA/IDENTITY"]))
    assert payload.code == "PTSB"


def test_publishing_hosted_join_passes_every_check():
    payload = _run(_payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id", "status"]},
    ), fetch_spec=_fetch_fixture("clean.yaml"))
    assert payload.services[0].code == "awards-api"


# -- check 1: schema (pydantic) -----------------------------------------------


def test_schema_failure_is_reported_as_check_schema():
    raw = _payload()
    del raw["backend"]
    _rejects(raw, "schema")


# -- check 2: key derivation ---------------------------------------------------


def test_key_derivation_rejects_a_code_that_lowers_to_an_invalid_key():
    # "-" is not a bad identifier character (check 12 tolerates it) but does
    # break the [a-z0-9]+ key the directory name and manifest map key need.
    _rejects(_payload(code="PT-SB"), "key_derivation")


# -- check 3: collision --------------------------------------------------------


def test_collision_rejects_an_existing_member_directory():
    servers = dict(EXISTING_SERVERS, acme={"code": "SS-ACME", "dns_name": "ss-acme", "hosted_on": None})
    _rejects(_payload(code="ACME"), "collision", existing_servers=servers)


def test_collision_rejects_a_reused_dns_name():
    _rejects(_payload(security_server={"code": "SS-PTSB", "dns_name": "ss-plr", "hosted_on": "ss-pnia"}),
              "collision")


def test_collision_rejects_a_reused_security_server_code():
    _rejects(_payload(security_server={"code": "SS-PLR", "dns_name": "ss-ptsb", "hosted_on": "ss-pnia"}),
              "collision")


# -- check 4: not canonical ----------------------------------------------------


def test_not_canonical_rejects_the_owner_code_even_though_its_not_an_identity_members_entry():
    """task-2 brief point 4: PDGA is canonical via identity.owner.code, not
    an identity.members entry -- check 4 must still catch it. (The other
    four canonical codes are always caught by check 3, collision, first --
    each is already an identity.members entry, so there is no payload for
    which check 4 alone is the one that fires for them.)"""
    _rejects(_payload(code="PDGA", security_server={"code": "SS-X", "dns_name": "ss-x", "hosted_on": "ss-plr"}),
              "not_canonical")


# Check 5 (member class) has no per-request test here -- join-b Task 2
# review finding 2: the payload schema has no member_class field, so there
# was nothing about a submitted request for that check to evaluate. The
# join.member_class vs identity.member_class consistency assertion moved to
# hurl/generate.py's check_join_policy() (tests/test_join_policy.py).


# -- check 6: hosting -----------------------------------------------------------


def test_hosting_rejects_a_request_that_asks_for_neither_hosting_nor_its_own_server():
    """join.default_hosting: hosted_on means "a join defaults to hosting;
    own_server must be asked for" (configs/x-road-bus/2.7.yaml's own comment).
    Since join-c Task 3 an own-server join is a real code path, so the reason
    this is still a rejection is the fail-safe, not the missing feature: a
    payload that simply forgot hosted_on must not silently become an
    own-server join that then waits in BLOCKED for a server nobody agreed to
    stand up."""
    raw = _payload()
    raw["security_server"] = {"code": "SS-PTSB", "dns_name": "ss-ptsb"}
    error = _rejects(raw, "hosting")
    assert "own_server" in error.message


def test_hosting_accepts_an_explicit_own_server_request():
    """The other half of the same policy key: asked for explicitly, an own
    Security Server is admissible (join-c plan Task 3)."""
    raw = _payload()
    raw["security_server"] = {"code": "SS-PTSB", "dns_name": "ss-ptsb", "own_server": True}
    payload = _run(raw)
    assert payload.security_server.own_server is True
    assert payload.security_server.hosted_on is None


def test_hosting_rejects_a_request_that_asks_for_both():
    raw = _payload()
    raw["security_server"] = {"code": "SS-PTSB", "dns_name": "ss-ptsb",
                              "hosted_on": "ss-plr", "own_server": True}
    _rejects(raw, "hosting")


def test_hosting_still_validates_the_host_when_own_server_is_false():
    """own_server: false is not a way past the host checks below -- it is the
    default, and a hosted request is exactly what it describes."""
    raw = _payload()
    raw["security_server"] = {"code": "SS-PTSB", "dns_name": "ss-ptsb",
                              "hosted_on": "ss-nope", "own_server": False}
    _rejects(raw, "hosting")


def test_hosting_rejects_an_unknown_host():
    raw = _payload()
    raw["security_server"]["hosted_on"] = "ss-does-not-exist"
    _rejects(raw, "hosting")


def test_hosting_rejects_a_hosting_chain():
    servers = dict(EXISTING_SERVERS, moeys={"code": "SS-MOEYS", "dns_name": "ss-moeys", "hosted_on": "ss-plr"})
    raw = _payload()
    raw["security_server"]["hosted_on"] = "ss-moeys"
    _rejects(raw, "hosting", existing_servers=servers)


# -- check 7: ACL sanity -------------------------------------------------------


def test_acl_sanity_rejects_an_access_target_that_is_not_a_real_subsystem():
    raw = _payload(services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": ["PROGRESSA/GOV/NOPE/NOTHING"]}])
    _rejects(raw, "acl_sanity")


def test_acl_sanity_rejects_a_self_grant():
    raw = _payload(services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": ["PROGRESSA/GOV/PTSB/SCHOLARSHIP"]}])
    _rejects(raw, "acl_sanity")


def test_acl_sanity_rejects_an_unresolvable_requested_access():
    raw = _payload(requested_access=["PROGRESSA/GOV/NOPE/NOTHING"])
    _rejects(raw, "acl_sanity")


# -- check 8: purpose limitation ------------------------------------------------


def test_purpose_limitation_rejects_a_publish_with_access_and_no_semantic():
    raw = _payload(services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}])
    _rejects(raw, "purpose_limitation")


def test_purpose_limitation_allows_a_publish_with_empty_access_and_no_semantic():
    payload = _run(_payload(services=[{"code": "awards-api",
                                         "spec_url": "http://app-ptsb:8000/spec.yaml", "access": []}]),
                    fetch_spec=_fetch_fixture("clean.yaml"))
    assert payload.services[0].access == []


# -- check 9: backend reachability ----------------------------------------------


def test_backend_reachability_rejects_an_unreachable_servers_url():
    raw = _payload(services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
                    semantic={"entity": "award", "key": "award_id", "fields": ["award_id"]})
    err = _rejects(raw, "backend_reachability", fetch_spec=_fetch_fixture("unreachable.yaml"))
    assert "127.0.0.1:1" in err.message


def test_backend_reachability_rejects_when_the_spec_cannot_be_fetched_at_all():
    def _boom(url):
        raise RuntimeError("connection refused")

    raw = _payload(services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": []}])
    _rejects(raw, "backend_reachability", fetch_spec=_boom)


# -- check 10: allowed methods --------------------------------------------------


def test_allowed_methods_rejects_a_delete_operation():
    raw = _payload(services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
                    semantic={"entity": "award", "key": "award_id", "fields": ["award_id"]})
    err = _rejects(raw, "allowed_methods", fetch_spec=_fetch_fixture("has_delete.yaml"))
    assert "DELETE" in err.message


# -- check 11: backend auth declared --------------------------------------------


def test_backend_auth_declared_passes_for_every_schema_enum_value():
    """schema.py already guarantees backend.auth is a valid enum member by
    the time a ValidationContext exists -- this asserts the check function
    itself is correct on that guarantee, not that it can fail via the
    public validate() entry point (it cannot: an invalid value is caught
    as check 1, schema, first)."""
    from schema import Backend, BackendAuth, JoinPayload

    raw = _payload()
    payload = JoinPayload(**raw)
    for value in BackendAuth:
        payload = payload.model_copy(update={"backend": Backend(auth=value)})
        ctx = ValidationContext(payload=payload, manifest=MANIFEST, policy=POLICY,
                                  existing_servers=EXISTING_SERVERS)
        assert validate_module._check_backend_auth_declared(ctx) is None


# -- check 12: identifier characters --------------------------------------------


def test_a_space_in_code_is_caught_earlier_by_key_derivation():
    """Every character check 12 rejects (whitespace, '/', ':', ';', '%',
    control characters) already breaks key_derivation's [a-z0-9]+ key
    regex, so a bad `code` is always caught by check 2 first -- there is no
    payload for which check 12 alone is the one that fires for `code`.
    Check 12 is exercised below on `subsystem` and a service code instead,
    neither of which key_derivation looks at."""
    _rejects(_payload(code="PT SB"), "key_derivation")


def test_identifier_characters_rejects_a_slash_in_subsystem():
    _rejects(_payload(subsystem="SCHOLAR/SHIP"), "identifier_characters")


def test_identifier_characters_rejects_a_bad_service_code_last_after_9_10_11_pass():
    raw = _payload(services=[{"code": "awards api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": []}])
    _rejects(raw, "identifier_characters", fetch_spec=_fetch_fixture("bad_service_code.yaml"))


def test_identifier_characters_accepts_a_dotted_service_code():
    """join-b Task 2 review finding 1 banned '.' after "awards.list" (a
    service code copied from a third-party tool's human-facing API name)
    slipped through every other check -- but X-Road >=7.3.0's actual
    allowlist (a-zA-Z0-9'()+,-.=?, XRDDEV-1960) permits '.'. That finding
    was solving the wrong problem: "awards.list" is a valid X-Road
    identifier and this pack must not reject it. Deliberately asserting
    ACCEPTANCE here, not rejection -- do not "fix" this back to a reject,
    that would resurrect the false-reject bug Task 1 (G-01) corrected."""
    raw = _payload(services=[{"code": "awards.list", "spec_url": "http://app-ptsb:8000/spec.yaml",
                                "access": []}])
    payload = _run(raw, fetch_spec=_fetch_fixture("bad_service_code.yaml"))
    assert payload.services[0].code == "awards.list"


# Table-driven per Task 1 (G-01) Step 4: X-Road >=7.3.0's identifier
# allowlist (a-zA-Z0-9'()+,-.=?, XRDDEV-1960) disagrees with this pack's old
# denylist in both directions -- see validate.py's comment above
# _bad_identifier for the full story. Exercised against `subsystem`
# specifically (not `code`): a bad `code` is always caught earlier by check
# 2, key_derivation (see test_a_space_in_code_is_caught_earlier_by_key_
# derivation above), so `code` can never isolate check 12 on its own.
_ACCEPTED_IDENTIFIERS = [
    "PTSB",
    "SS-PTSB",
    "PT.SB",
    # The dotted-service-code case is covered on its own above
    # (test_identifier_characters_accepts_a_dotted_service_code) since it
    # needs a service code, not a subsystem, to isolate check 12 -- listed
    # here too because it is also a plain "dot in an identifier" case.
    "PTSB.X",
]

_REJECTED_IDENTIFIERS = [
    "MOE_YS",  # underscore: outside the allowlist
    "PTSB_2",  # underscore: outside the allowlist
    "P&B",
    "PT#B",
    "PT@B",
    "PT$B",
    "PT~B",
    "PT*B",
    "PTSB!",
    'PT"B',
    "PT\\B",
    "PT<B",
    "PT[B",
    "PT{B",
]


@pytest.mark.parametrize("subsystem", _ACCEPTED_IDENTIFIERS)
def test_identifier_characters_accepts_allowlisted_subsystems(subsystem):
    payload = _run(_payload(subsystem=subsystem))
    assert payload.subsystem == subsystem


@pytest.mark.parametrize("subsystem", _REJECTED_IDENTIFIERS)
def test_identifier_characters_rejects_non_allowlisted_subsystems(subsystem):
    _rejects(_payload(subsystem=subsystem), "identifier_characters")
