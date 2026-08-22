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


def test_a_missing_join_workflow_block_defaults_to_advisory():
    check_join_workflow({})  # does not raise


def test_required_is_admitted():
    check_join_workflow({"join_workflow": {"commit_gate": "required"}})  # does not raise


def test_an_invented_commit_gate_value_is_a_hard_failure():
    with pytest.raises(SystemExit, match="commit_gate"):
        check_join_workflow({"join_workflow": {"commit_gate": "enforced"}})
