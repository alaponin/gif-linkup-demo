"""Unit tests for apps/join-api/schema.py. The one property
that matters most: JoinPayload has no origin field at all, and extra="forbid"
means a payload that tries to smuggle one in fails to parse rather than
having it silently discarded."""
from __future__ import annotations

import pathlib
import sys

import pydantic
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from schema import BackendAuth, ExchangePattern, JoinPayload  # noqa: E402


def _requirements(**overrides) -> dict:
    base = {
        "has_security_server": True,
        "has_registered_identity": True,
        "standards_portfolio_adopted": True,
        "data_conformant": True,
        "lawful_basis": "consent",
        "technical_contact": "Jane Doe",
    }
    base.update(overrides)
    return base


def _consume_only(**overrides) -> dict:
    base = {
        "code": "PTSB",
        "name": "Progressa Tertiary Scholarship Board",
        "subsystem": "SCHOLARSHIP",
        "subsystem_description": "Scholarship award management",
        "security_server": {"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
        "backend": {"auth": "network_allowlist"},
        "member_requirements": _requirements(),
    }
    base.update(overrides)
    return base


def test_consume_only_payload_parses_with_no_services():
    payload = JoinPayload(**_consume_only())
    assert payload.services == []
    assert payload.requested_access == []
    assert payload.semantic is None


def test_publishing_payload_parses():
    payload = JoinPayload(**_consume_only(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id", "status"]},
    ))
    assert payload.services[0].code == "awards-api"
    assert payload.semantic.entity == "award"


def test_origin_field_is_rejected_not_silently_dropped():
    """The whole safeguard: there is no path through this schema by which a
    payload-supplied "origin" reaches anything downstream."""
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(origin="canonical"))


def test_unknown_field_is_rejected():
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(not_a_real_field=True))


def test_missing_backend_is_rejected():
    raw = _consume_only()
    del raw["backend"]
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**raw)


def test_invalid_backend_auth_value_is_rejected():
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(backend={"auth": "shared_api_key"}))


def test_backend_auth_enum_has_exactly_the_three_spec_values():
    assert {m.value for m in BackendAuth} == {"none", "network_allowlist", "proxy_injected"}


def test_semantic_pattern_defaults_to_none():
    """Optional -- required would reject every
    existing config until all are classified against ExchangePattern."""
    payload = JoinPayload(**_consume_only(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id", "status"]},
    ))
    assert payload.semantic.pattern is None


def test_semantic_pattern_accepts_a_declared_value():
    payload = JoinPayload(**_consume_only(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id"],
                  "pattern": "digital_registries_lookup"},
    ))
    assert payload.semantic.pattern == ExchangePattern.digital_registries_lookup


def test_semantic_pattern_rejects_a_value_outside_the_enum():
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(
            services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                       "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
            semantic={"entity": "award", "key": "award_id", "fields": ["award_id"],
                      "pattern": "not_a_real_pattern"},
        ))


def test_hosted_on_defaults_to_none():
    raw = _consume_only()
    raw["security_server"] = {"code": "SS-PTSB", "dns_name": "ss-ptsb"}
    payload = JoinPayload(**raw)
    assert payload.security_server.hosted_on is None


def test_lawful_basis_defaults_to_none():
    """Optional -- no config file and no resolution
    check, so a service that omits it must still parse (docs/conventions.md
    does not gate the join payload; this field is recorded, not enforced)."""
    payload = JoinPayload(**_consume_only(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml"}],
    ))
    assert payload.services[0].lawful_basis is None


def test_lawful_basis_accepts_free_text():
    payload = JoinPayload(**_consume_only(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "lawful_basis": "[confirm: cite the decree article]"}],
    ))
    assert payload.services[0].lawful_basis == "[confirm: cite the decree article]"


# -- member_requirements -------------------------------------------------


def test_member_requirements_is_required():
    """Every join answers 5.2's checklist, provider or consumer -- unlike
    semantic and lawful_basis, there is no default here."""
    raw = _consume_only()
    del raw["member_requirements"]
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**raw)


def test_member_requirements_rejects_unknown_field():
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(member_requirements=_requirements(not_a_real_field=True)))


def test_member_requirements_lawful_basis_defaults_to_none():
    """Optional here too (Step 3): a provider leaves it unset and relies on
    its services' own lawful_basis instead."""
    payload = JoinPayload(**_consume_only(member_requirements=_requirements(lawful_basis=None)))
    assert payload.member_requirements.lawful_basis is None


# -- sla -------------------------------------------------------------------


def _sla(**overrides) -> dict:
    base = {
        "availability": "99.5% monthly uptime",
        "response_time": "4 business hours, P1",
        "support_hours": "Mon-Fri 08:00-18:00 ICT",
        "incident_response": "P1 acknowledged within 1 hour",
        "change_notice": "5 business days for planned changes",
        "signatory": "Head of IT",
    }
    base.update(overrides)
    return base


def test_sla_defaults_to_none_on_a_service():
    payload = JoinPayload(**_consume_only(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml"}],
    ))
    assert payload.services[0].sla is None


def test_sla_accepts_the_full_block():
    payload = JoinPayload(**_consume_only(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "sla": _sla()}],
    ))
    assert payload.services[0].sla.availability == "99.5% monthly uptime"
    assert payload.services[0].sla.signatory == "Head of IT"


def test_sla_rejects_unknown_field():
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(
            services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                       "sla": _sla(not_a_real_field=True)}],
        ))


# -- E.2: string max_length / list max_length bounds -----------------------
# security-review-remediation-plan.md Phase E.2's own table, applied
# verbatim. Every bound below: accepts at exactly n, rejects at n+1 -- the
# brief's own instruction, not "somewhere past n".

_SERVICE = {"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml"}


def _services(n: int) -> list[dict]:
    """n distinct, otherwise-valid Service entries -- for the `services`
    list-length bound, not the `code`/`spec_url` string bounds (their own
    tests below use one service each)."""
    return [{"code": f"svc-{i}", "spec_url": "http://app-ptsb:8000/spec.yaml"} for i in range(n)]


def test_code_max_length_is_64():
    JoinPayload(**_consume_only(code="X" * 64))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(code="X" * 65))


def test_subsystem_max_length_is_64():
    JoinPayload(**_consume_only(subsystem="X" * 64))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(subsystem="X" * 65))


def test_security_server_code_max_length_is_64():
    raw = _consume_only()
    raw["security_server"]["code"] = "X" * 64
    JoinPayload(**raw)
    raw["security_server"]["code"] = "X" * 65
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**raw)


def test_name_max_length_is_200():
    JoinPayload(**_consume_only(name="X" * 200))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(name="X" * 201))


def test_security_server_dns_name_max_length_is_200():
    raw = _consume_only()
    raw["security_server"]["dns_name"] = "X" * 200
    JoinPayload(**raw)
    raw["security_server"]["dns_name"] = "X" * 201
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**raw)


def test_hosted_on_max_length_is_200():
    raw = _consume_only()
    raw["security_server"]["hosted_on"] = "X" * 200
    JoinPayload(**raw)
    raw["security_server"]["hosted_on"] = "X" * 201
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**raw)


def test_spec_url_max_length_is_2048():
    base = "http://app-ptsb:8000/" + "s" * (2048 - len("http://app-ptsb:8000/"))
    assert len(base) == 2048
    JoinPayload(**_consume_only(services=[{"code": "awards-api", "spec_url": base}]))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(services=[{"code": "awards-api", "spec_url": base + "x"}]))


def test_subsystem_description_max_length_is_2000():
    JoinPayload(**_consume_only(subsystem_description="X" * 2000))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(subsystem_description="X" * 2001))


def test_service_lawful_basis_max_length_is_500():
    JoinPayload(**_consume_only(services=[{**_SERVICE, "lawful_basis": "X" * 500}]))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(services=[{**_SERVICE, "lawful_basis": "X" * 501}]))


def test_member_requirements_lawful_basis_max_length_is_500():
    JoinPayload(**_consume_only(member_requirements=_requirements(lawful_basis="X" * 500)))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(member_requirements=_requirements(lawful_basis="X" * 501)))


def test_technical_contact_max_length_is_500():
    JoinPayload(**_consume_only(member_requirements=_requirements(technical_contact="X" * 500)))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(member_requirements=_requirements(technical_contact="X" * 501)))


@pytest.mark.parametrize("field", [
    "availability", "response_time", "support_hours",
    "incident_response", "change_notice", "signatory",
])
def test_sla_field_max_length_is_500(field):
    JoinPayload(**_consume_only(services=[{**_SERVICE, "sla": _sla(**{field: "X" * 500})}]))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(services=[{**_SERVICE, "sla": _sla(**{field: "X" * 501})}]))


def test_services_max_length_is_50_items():
    JoinPayload(**_consume_only(services=_services(50)))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(services=_services(51)))


def test_access_max_length_is_200_items():
    JoinPayload(**_consume_only(services=[{**_SERVICE, "access": [f"a{i}" for i in range(200)]}]))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(services=[{**_SERVICE, "access": [f"a{i}" for i in range(201)]}]))


def test_requested_access_max_length_is_200_items():
    JoinPayload(**_consume_only(requested_access=[f"a{i}" for i in range(200)]))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(requested_access=[f"a{i}" for i in range(201)]))


def test_semantic_fields_max_length_is_200_items():
    JoinPayload(**_consume_only(
        services=[_SERVICE],
        semantic={"entity": "award", "key": "award_id", "fields": [f"f{i}" for i in range(200)]},
    ))
    with pytest.raises(pydantic.ValidationError):
        JoinPayload(**_consume_only(
            services=[_SERVICE],
            semantic={"entity": "award", "key": "award_id", "fields": [f"f{i}" for i in range(201)]},
        ))
