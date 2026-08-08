"""Unit tests for hurl/generate.py's check_join_policy() (join-b) -- the same
"a declared key the code does not apply is a hard failure" rule
check_policy() already applies to the bus policy, extended to
configs/x-road-bus/join-policy.yaml's join: block, plus the
join.member_class vs identity.member_class consistency assertion that moved
here from apps/join-api/validate.py's per-request check 5 -- see
validate.py's comment where _check_member_class used to be.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "hurl"))
from generate import JOIN_POLICY_KEYS, check_join_policy  # noqa: E402

PACK = pathlib.Path(__file__).resolve().parent.parent

GOV_MANIFEST = {"identity": {"member_class": "GOV"}}


def test_the_committed_join_policy_yaml_passes_against_the_real_manifest():
    config = yaml.safe_load((PACK / "configs/x-road-bus/join-policy.yaml").read_text())
    manifest = yaml.safe_load((PACK / "manifest.yaml").read_text())
    check_join_policy(config, manifest)  # does not raise


def test_exactly_four_keys_are_recognised():
    assert JOIN_POLICY_KEYS == {"member_class", "approval", "default_hosting", "allowed_methods"}


def test_an_undeclared_fifth_key_is_a_hard_failure():
    with pytest.raises(SystemExit, match="max_services"):
        check_join_policy({"join": {"member_class": "GOV", "max_services": 4}}, GOV_MANIFEST)


def test_an_empty_join_block_passes():
    check_join_policy({"join": {}}, GOV_MANIFEST)  # nothing declared, nothing to contradict


def test_a_missing_join_block_passes():
    check_join_policy({}, GOV_MANIFEST)


def test_member_class_disagreeing_with_the_federation_is_a_hard_failure():
    with pytest.raises(SystemExit, match="member_class"):
        check_join_policy({"join": {"member_class": "PRIVATE"}}, GOV_MANIFEST)


def test_member_class_agreeing_with_the_federation_passes():
    check_join_policy({"join": {"member_class": "GOV"}}, GOV_MANIFEST)
