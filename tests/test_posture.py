"""Tests for deployment.yaml's `posture:` key (security-review-remediation-
plan.md Phase A, H3) -- apps/join-api/app.py's own startup resolution of it
into the three join_workflow switches (commit_gate, enforce_ownership,
require_https_spec_url), and the acknowledge_permissive escape hatch.

Each test imports apps/join-api/app.py fresh, by path under a distinct
module name -- the same technique
apps/join-api/tests/test_app_startup.py uses (module-level code in app.py
runs at import time, so a fresh import per deployment.yaml scenario is the
only way to exercise it), pointed at a throwaway PACK_DIR built from
tmp_path. Not added to sys.modules, so nothing here can collide with the
"join_api_app"/"join_api_app_startup" modules apps/join-api/tests/ itself
loads in the same pytest session.
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import pathlib

import pytest

PACK = pathlib.Path(__file__).resolve().parent.parent
APP_PY = PACK / "apps" / "join-api" / "app.py"

_counter = itertools.count()


def _import_app(tmp_path, deployment_yaml: str | None):
    """Write `deployment_yaml` (or nothing, for the absent-file case) into a
    fresh throwaway PACK_DIR, point every env var app.py's import-time code
    needs at it, and import app.py fresh. Raises whatever app.py itself
    raises at import time (RuntimeError, for a posture/join_workflow
    refusal)."""
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir(parents=True)
    if deployment_yaml is not None:
        (pack_dir / "deployment.yaml").write_text(deployment_yaml)
    os.environ["PACK_DIR"] = str(pack_dir)
    os.environ["OUT_DIR"] = str(tmp_path / "out")
    os.environ["XROAD_ADMIN_USER"] = "xrd"
    os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
    os.environ["XROAD_TOKEN_PIN"] = "1234"
    os.environ["KP2_JOIN_APPLICANT_TOKEN"] = "test-applicant-token"
    os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

    spec = importlib.util.spec_from_file_location(f"join_api_app_posture_{next(_counter)}", APP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_posture_implies_all_three_safe_values_with_no_keys_present(tmp_path):
    module = _import_app(tmp_path, "posture: production\n")
    assert module._COMMIT_GATE == "required"
    assert module._ENFORCE_OWNERSHIP is True
    assert module._REQUIRE_HTTPS_SPEC_URL is True
    # Phase C, M1: posture: production implies hurl_insecure=False -- Hurl's
    # --insecure TLS to the admin API is not allowed without an explicit
    # acknowledgement, same idiom, same acknowledge_permissive list.
    assert module._HURL_INSECURE_ALLOWED is False


def test_production_posture_refuses_an_unacknowledged_explicit_permissive_value(tmp_path):
    deployment_yaml = """
posture: production
join_workflow:
  enforce_ownership: false
"""
    with pytest.raises(RuntimeError, match="enforce_ownership"):
        _import_app(tmp_path, deployment_yaml)


def test_production_posture_admits_the_same_value_once_acknowledged(tmp_path):
    deployment_yaml = """
posture: production
join_workflow:
  enforce_ownership: false
  acknowledge_permissive: [enforce_ownership]
"""
    module = _import_app(tmp_path, deployment_yaml)
    assert module._ENFORCE_OWNERSHIP is False
    # The other two switches are untouched by this acknowledgement -- still
    # implied by posture: production.
    assert module._COMMIT_GATE == "required"
    assert module._REQUIRE_HTTPS_SPEC_URL is True


def test_a_scalar_acknowledge_permissive_is_a_hard_failure_not_a_substring_match(tmp_path):
    """Reviewer finding on ab84b75: `key in (block.get(...) or [])` used `in`
    on whatever YAML produced -- a scalar string acknowledges via Python's
    substring `in`, so `acknowledge_permissive: enforce_ownership` (a scalar,
    not a list) or even a typo like `commit_gates_off` would silently
    acknowledge `commit_gate`. Must refuse instead."""
    deployment_yaml = """
posture: production
join_workflow:
  enforce_ownership: false
  acknowledge_permissive: enforce_ownership
"""
    with pytest.raises(RuntimeError, match="acknowledge_permissive"):
        _import_app(tmp_path, deployment_yaml)


def test_a_non_list_acknowledge_permissive_is_a_hard_failure_not_a_traceback(tmp_path):
    """acknowledge_permissive: true used to raise a bare
    TypeError: argument of type 'bool' is not iterable -- must be the pack's
    own RuntimeError idiom instead."""
    deployment_yaml = """
posture: production
join_workflow:
  enforce_ownership: false
  acknowledge_permissive: true
"""
    with pytest.raises(RuntimeError, match="acknowledge_permissive"):
        _import_app(tmp_path, deployment_yaml)


def test_demo_posture_and_absent_posture_reproduce_todays_behaviour(tmp_path):
    explicit_demo = _import_app(tmp_path, "posture: demo\n")
    assert explicit_demo._COMMIT_GATE == "advisory"
    assert explicit_demo._ENFORCE_OWNERSHIP is False
    assert explicit_demo._REQUIRE_HTTPS_SPEC_URL is False
    assert explicit_demo._HURL_INSECURE_ALLOWED is True

    # No deployment.yaml at all -- app.py's own FileNotFoundError fallback,
    # which many unit tests already rely on. Must resolve identically to an
    # explicit posture: demo, and must not raise: the fallback stays, only
    # its silence is what this task removes.
    absent_file = _import_app(tmp_path / "no-deployment-yaml", None)
    assert absent_file._COMMIT_GATE == "advisory"
    assert absent_file._ENFORCE_OWNERSHIP is False
    assert absent_file._REQUIRE_HTTPS_SPEC_URL is False
    assert absent_file._HURL_INSECURE_ALLOWED is True


def test_production_posture_refuses_unacknowledged_hurl_insecure(tmp_path):
    deployment_yaml = """
posture: production
join_workflow:
  hurl_insecure: true
"""
    with pytest.raises(RuntimeError, match="hurl_insecure"):
        _import_app(tmp_path, deployment_yaml)


def test_production_posture_admits_hurl_insecure_once_acknowledged(tmp_path):
    deployment_yaml = """
posture: production
join_workflow:
  hurl_insecure: true
  acknowledge_permissive: [hurl_insecure]
"""
    module = _import_app(tmp_path, deployment_yaml)
    assert module._HURL_INSECURE_ALLOWED is True
    # The other three switches are untouched -- still implied by
    # posture: production.
    assert module._COMMIT_GATE == "required"
    assert module._ENFORCE_OWNERSHIP is True
    assert module._REQUIRE_HTTPS_SPEC_URL is True


def test_a_non_bool_hurl_insecure_is_a_hard_failure(tmp_path):
    deployment_yaml = """
posture: demo
join_workflow:
  hurl_insecure: "yes"
"""
    with pytest.raises(RuntimeError, match="hurl_insecure"):
        _import_app(tmp_path, deployment_yaml)


def test_absent_deployment_yaml_logs_a_loud_warning_naming_the_posture(tmp_path, capsys):
    _import_app(tmp_path, None)
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "deployment.yaml not found" in out
    assert "posture: demo" in out


def test_an_unknown_posture_value_is_a_hard_failure(tmp_path):
    with pytest.raises(RuntimeError, match="posture"):
        _import_app(tmp_path, "posture: hardened\n")
