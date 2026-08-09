"""Unit tests for scripts/render_onboarding.py. Not a
consistency test against hardcoded expected content -- that would guard a
duplication the simplification pass deliberately removed (canonical members'
onboarding records are generated, never hand-authored, so there is nothing
to compare them against). This exercises render_onboarding.py's own two
branch points instead: own_server inference from an absent hosted_on, and
consumes: mapping onto requested_access for a consumer-only member.

Every test works against a temporary COPY of the pack (mirrors apps/join-api/
tests/test_writer.py's own REAL_PACK_DIR discipline) -- the real checkout is
read from, never written to.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable


def _copy_pack(dest: pathlib.Path) -> pathlib.Path:
    shutil.copytree(PACK / "configs", dest / "configs")
    shutil.copy2(PACK / "manifest.yaml", dest / "manifest.yaml")
    shutil.copytree(PACK / "apps" / "join-api", dest / "apps" / "join-api")
    return dest


def _run(pack_dir: pathlib.Path, key: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(PACK / "scripts" / "render_onboarding.py"), str(pack_dir), key],
        capture_output=True, text=True,
    )


def test_renders_a_providers_onboarding_tree_with_an_sla_per_service(tmp_path):
    pack = _copy_pack(tmp_path / "pack")
    proc = _run(pack, "pnia")
    assert proc.returncode == 0, proc.stderr
    onboarding = pack / "onboarding" / "pnia"
    assert (onboarding / "00-gates.md").exists()
    assert (onboarding / "02-requirements.md").exists()
    assert (onboarding / "03-sla" / "identity-api.md").exists()
    entry = (onboarding / "04-catalogue" / "identity-api.md").read_text()
    assert "`PROGRESSA/GOV/PNIA/IDENTITY/identity-api`" in entry
    assert (onboarding / "04-catalogue" / "../03-sla/identity-api.md").resolve().exists()
    # configs/member-pnia/pnia.yaml's semantic: block reaches the entry --
    # without it every canonical service would render as unclassified while
    # its own config says otherwise.
    assert "`person`" in entry and "anchor: CEDS" in entry
    assert "`digital_registries_lookup`" in entry
    registration = (onboarding / "05-registration.md").read_text()
    # security_server.hosted_on is absent in configs/member-pnia/pnia.yaml --
    # own_server must be inferred True, not left False (which would render
    # "hosted on `None`").
    assert "runs its own Security Server" in registration
    assert "None" not in registration


def test_renders_a_consumer_only_members_onboarding_tree_with_no_sla_directory(tmp_path):
    pack = _copy_pack(tmp_path / "pack")
    proc = _run(pack, "pnea")
    assert proc.returncode == 0, proc.stderr
    onboarding = pack / "onboarding" / "pnea"
    assert (onboarding / "00-gates.md").exists()
    assert not (onboarding / "03-sla").exists()
    assert not (onboarding / "04-catalogue").exists()
    # configs/member-pnea/pnea.yaml's consumes: list maps onto
    # requested_access, and from there into the ACL subjects column.
    registration = (onboarding / "05-registration.md").read_text()
    assert "PROGRESSA/GOV/PNIA/IDENTITY/identity-api" in registration


def test_refuses_a_member_with_no_member_requirements_block(tmp_path):
    pack = _copy_pack(tmp_path / "pack")
    cfg_path = pack / "configs" / "member-pnia" / "pnia.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    del cfg["member_requirements"]
    cfg_path.write_text(yaml.safe_dump(cfg))

    proc = _run(pack, "pnia")
    assert proc.returncode != 0
    assert "member_requirements" in proc.stderr


def test_rerendering_is_safe_and_replaces_the_prior_output(tmp_path):
    """render_onboarding_tree()'s mkdir is not exist_ok (mirrors
    _write_member's own collision guard) -- render_onboarding.py clears its
    own prior output first specifically so a second run over an edited
    config does not collide with the first."""
    pack = _copy_pack(tmp_path / "pack")
    assert _run(pack, "pnia").returncode == 0
    assert _run(pack, "pnia").returncode == 0
    assert (pack / "onboarding" / "pnia" / "00-gates.md").exists()
