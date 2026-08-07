"""Unit tests for apps/console/truth.py. No network, no Docker -- fixtures
under apps/console/tests/fixtures/ stand in for a real pack directory."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from truth import load_truth  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def test_topology_resolves():
    truth = load_truth(FIXTURES / "full")
    assert {s["host"] for s in truth.topology["security_servers"]} == {
        "ss-pdga", "ss-pnea", "ss-plr", "ss-pnia", "ss-moeys",
    }
    # PNIA and PLR (the negative check's caller since Wave 3 Task 1) are
    # both self-hosted -- resolved from topology.json's hosted_on, not from
    # once-only-exchange.yaml's static entrypoint field (module docstring).
    assert truth.negative_check_entrypoint == "http://ss-plr:8080"
    assert truth.consumer_entrypoint == "http://ss-pnea:8080"


def test_form_fields_cover_citizen_and_bus_exactly():
    truth = load_truth(FIXTURES / "full")
    by_name = {f.name: f.source for f in truth.form_fields}
    assert by_name["nin"] == "citizen"
    assert by_name["given_name"] == "PNIA"
    assert by_name["school"] == "PLR"
    assert len(truth.form_fields) == 10  # 1 citizen + 9 prefilled


def test_layers_aggregate_across_both_calls():
    truth = load_truth(FIXTURES / "full")
    assert set(truth.layers) == {"technical", "legal", "organisational", "semantic"}
    assert "cross-server" in truth.layers["technical"]
    assert "semantic map" in truth.layers["semantic"]


def test_expected_acl_normalised_to_colon_form():
    truth = load_truth(FIXTURES / "full")
    assert truth.expected_acl["identity-api"] == ["PROGRESSA:GOV:PNEA:EXAMS"]
    assert truth.expected_acl["enrolment-api"] == ["PROGRESSA:GOV:PNEA:EXAMS"]
    assert truth.expected_acl["pemis-api"] == []


def test_inconsistent_fixture_raises():
    with pytest.raises(RuntimeError, match="does not equal"):
        load_truth(FIXTURES / "inconsistent")
