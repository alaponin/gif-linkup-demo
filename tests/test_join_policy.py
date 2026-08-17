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


def test_the_committed_federation_policy_is_explicit():
    core = yaml.safe_load((PACK / "configs/x-road-bus/federation-core.yaml").read_text())
    approval = core["policy"]["management_request_approval"]
    assert approval == "explicit", (
        "automatic is a demonstration setting; a clone of this repository must "
        "not inherit a federation that approves registrations with no human in "
        "the loop"
    )


def test_the_committed_join_policy_yaml_passes_against_the_real_manifest():
    config = yaml.safe_load((PACK / "configs/x-road-bus/join-policy.yaml").read_text())
    manifest = yaml.safe_load((PACK / "manifest.yaml").read_text())
    check_join_policy(config, manifest)  # does not raise


def test_exactly_four_keys_are_recognised():
    assert JOIN_POLICY_KEYS == {
        "member_class", "default_hosting", "allowed_methods", "spec_url_hosts",
    }


# -- spec_url_hosts: the fourth key -------------------------------------------
#
# It is the allowlist apps/join-api/validate.py judges an applicant's spec_url
# against before fetching it from a container that holds the federation's
# admin credentials. Its VALUE is validate.py's to apply; its SHAPE is checked
# here, because a bare string would silently make every character of a
# hostname an "allowed host" and fail nothing.


def test_the_committed_policy_lists_the_hosts_compose_actually_runs():
    """An allowlist naming hosts that do not exist rejects every real join;
    one that has drifted from the mock backends rejects the pack's own
    fixture. Both are caught here rather than at the next live join."""
    config = yaml.safe_load((PACK / "configs/x-road-bus/join-policy.yaml").read_text())
    compose = (PACK / "docker-compose.yml").read_text()
    hosts = config["join"]["spec_url_hosts"]
    assert hosts, "an empty allowlist means validate.py refuses every join"
    for host in hosts:
        assert f"container_name: {host}" in compose, (
            f"join.spec_url_hosts names {host!r}, which docker-compose.yml does not run"
        )


def test_a_spec_url_hosts_that_is_a_bare_string_is_a_hard_failure():
    with pytest.raises(SystemExit, match="spec_url_hosts"):
        check_join_policy({"join": {"spec_url_hosts": "app-ptsb"}}, GOV_MANIFEST)


def test_an_empty_spec_url_hosts_list_is_a_hard_failure():
    """Empty is not "allow everything" and not "allow nothing by accident" --
    it is a misconfiguration, said at generate time. validate.py fails closed
    on it too, at request time, for a policy file this never saw."""
    with pytest.raises(SystemExit, match="spec_url_hosts"):
        check_join_policy({"join": {"spec_url_hosts": []}}, GOV_MANIFEST)


def test_a_non_string_entry_in_spec_url_hosts_is_a_hard_failure():
    with pytest.raises(SystemExit, match="spec_url_hosts"):
        check_join_policy({"join": {"spec_url_hosts": ["app-ptsb", 8000]}}, GOV_MANIFEST)


def test_a_well_formed_spec_url_hosts_passes():
    check_join_policy({"join": {"spec_url_hosts": ["app-ptsb"]}}, GOV_MANIFEST)


def test_an_absent_spec_url_hosts_passes_generate_time():
    """Absent is not a generate-time failure: the key is optional here, and
    validate.py fails closed at request time rather than letting an
    unrestricted fetch through. Stated as a test so "absent is allowed here"
    is a decision, not an oversight."""
    check_join_policy({"join": {"member_class": "GOV"}}, GOV_MANIFEST)


def test_an_undeclared_fourth_key_is_a_hard_failure():
    with pytest.raises(SystemExit, match="max_services"):
        check_join_policy({"join": {"member_class": "GOV", "max_services": 4}}, GOV_MANIFEST)


def test_approval_is_no_longer_a_recognised_key():
    """The worked example of the rule this file enforces: approval mode is a
    property of how the Central Server was deployed, not of a join request,
    so this file is the wrong scope for it regardless of value -- a
    resurrected `approval` key is rejected exactly like any other
    undeclared one."""
    with pytest.raises(SystemExit, match="approval"):
        check_join_policy({"join": {"member_class": "GOV", "approval": "explicit"}}, GOV_MANIFEST)


def test_an_empty_join_block_passes():
    check_join_policy({"join": {}}, GOV_MANIFEST)  # nothing declared, nothing to contradict


def test_a_missing_join_block_passes():
    check_join_policy({}, GOV_MANIFEST)


def test_member_class_disagreeing_with_the_federation_is_a_hard_failure():
    with pytest.raises(SystemExit, match="member_class"):
        check_join_policy({"join": {"member_class": "PRIVATE"}}, GOV_MANIFEST)


def test_member_class_agreeing_with_the_federation_passes():
    check_join_policy({"join": {"member_class": "GOV"}}, GOV_MANIFEST)
