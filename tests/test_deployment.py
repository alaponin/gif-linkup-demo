"""Unit tests for hurl/generate.py's check_join_workflow() -- deployment.yaml's
join_workflow: block (docs/production-delta.md row 33), admitted the same way
check_policy()/check_join_policy() admit their own files' blocks: a value the
code does not recognise is a generate-time hard failure, not a silent
default.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "hurl"))
from generate import check_join_workflow  # noqa: E402

PACK = pathlib.Path(__file__).resolve().parent.parent


def test_the_committed_deployment_yaml_passes():
    deployment = yaml.safe_load((PACK / "deployment.yaml").read_text())
    check_join_workflow(deployment)  # does not raise
    assert deployment["join_workflow"]["commit_gate"] == "advisory", (
        "docker-local's committed default must stay advisory -- demo.sh and "
        "exercises.md's default happy path assume nothing gates a join"
    )


def test_the_committed_deployment_yaml_states_a_posture_explicitly():
    """posture: demo (the docker-local default) must be spelled out in the
    committed file, not left to app.py's own default -- the point of the key
    is that a reviewer reading deployment.yaml sees the deployment's posture
    as one word, without inferring it from the absence of a line."""
    deployment = yaml.safe_load((PACK / "deployment.yaml").read_text())
    assert deployment.get("posture") == "demo"


def test_a_missing_join_workflow_block_defaults_to_advisory():
    check_join_workflow({})  # does not raise


def test_required_is_admitted():
    check_join_workflow({"join_workflow": {"commit_gate": "required"}})  # does not raise


def test_an_invented_commit_gate_value_is_a_hard_failure():
    with pytest.raises(SystemExit, match="commit_gate"):
        check_join_workflow({"join_workflow": {"commit_gate": "enforced"}})


# enforce_ownership (row 28), require_https_spec_url (row 18) and
# hurl_insecure (row 19) landed in the same join_workflow: block in separate
# rounds of work, and the first two didn't extend this check when they
# landed -- so a typo passed generate
# time and was caught only by apps/join-api/app.py's own startup
# validation, inside a container, after a deploy. All three are booleans in
# app.py; these assert generate.py agrees.
@pytest.mark.parametrize("key", ["enforce_ownership", "require_https_spec_url", "hurl_insecure"])
def test_the_boolean_switches_admit_both_booleans(key):
    check_join_workflow({"join_workflow": {key: True}})   # does not raise
    check_join_workflow({"join_workflow": {key: False}})  # does not raise


# "true" is the one that matters: YAML quoting turns the posture ON into a
# string, app.py rejects it -- but only after generate.py has already written
# a whole federation's worth of output believing the switch is fine.
@pytest.mark.parametrize("key", ["enforce_ownership", "require_https_spec_url", "hurl_insecure"])
@pytest.mark.parametrize("bad", ["true", "yes", 1, None])
def test_a_non_boolean_switch_value_is_a_hard_failure(key, bad):
    with pytest.raises(SystemExit, match=key):
        check_join_workflow({"join_workflow": {key: bad}})
