"""Unit tests for apps/join-api/schema.py (join-b Task 2). The one property
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
    """Optional (Wave 2 Task 1 Step 3, G-04) -- required would reject every
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
    """Optional (Wave 2 Task 3, K-02) -- no config file and no resolution
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


# -- member_requirements (Wave 4 Task 1, K-01) ---------------------------------


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


# -- sla (Wave 4 Task 1, K-01) --------------------------------------------------


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
