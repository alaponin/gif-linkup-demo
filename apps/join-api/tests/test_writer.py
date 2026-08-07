"""Tests for apps/join-api/writer.py (join-b Task 3).

Every test here works against a temporary COPY of the pack, never the real
checkout -- REAL_PACK_DIR is read from exactly once, as the copy source
(shutil.copytree/copy2 inside writer._copy_pack), which is the same "read
once to seed a copy" writer.dry_run_diff itself does in production. Nothing
here ever calls apply_real() or _write_member() with REAL_PACK_DIR as the
TARGET -- test_dry_run_diff_never_touches_the_real_checkout below asserts
that invariant directly.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import writer  # noqa: E402
from schema import JoinPayload  # noqa: E402

# apps/join-api/tests/test_writer.py -> tests -> join-api -> apps -> pack root
REAL_PACK_DIR = pathlib.Path(__file__).resolve().parents[3]


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


def _payload(**overrides) -> JoinPayload:
    base = dict(
        code="PTSB",
        name="Progressa Tertiary Scholarship Board",
        subsystem="SCHOLARSHIP",
        subsystem_description="Scholarship award management",
        security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
        backend={"auth": "network_allowlist"},
        member_requirements=_requirements(),
    )
    base.update(overrides)
    return JoinPayload(**base)


def _git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


# -- render_member_config -----------------------------------------------------


def test_render_member_config_matches_the_documented_shape():
    payload = _payload(
        services=[
            {
                "code": "awards-api",
                "spec_url": "http://app-ptsb:8000/spec.yaml",
                "access": ["PROGRESSA/GOV/PNEA/EXAMS"],
                "sla": _sla(),
            }
        ],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id", "status"]},
    )
    text = writer.render_member_config("ptsb", payload)
    assert text.splitlines()[0].startswith("# Member PTSB")
    doc = yaml.safe_load(text)
    assert doc["module"] == "ptsb"
    assert doc["building_block"] == "member-ptsb"
    assert doc["security_server"] == {"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"}
    assert doc["services"][0]["code"] == "awards-api"
    assert doc["services"][0]["access"] == ["PROGRESSA/GOV/PNEA/EXAMS"]
    assert doc["services"][0]["sla"]["signatory"] == "Head of IT"
    assert doc["semantic"]["fields"] == ["award_id", "status"]
    assert doc["backend"] == {"auth": "network_allowlist"}
    assert doc["member_requirements"]["technical_contact"] == "Jane Doe"
    # generate.py never reads these -- a copy here would drift (2.5.yaml).
    for absent in ("type", "enabled", "tls_verify"):
        assert absent not in doc


def test_render_member_config_includes_pattern_when_classified():
    """Wave 2 Task 1 Step 3 (G-04): pattern is optional on Semantic, but when
    a joining payload does set it, the rendered config must carry it -- not
    silently drop a field the schema now accepts."""
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "access": ["PROGRESSA/GOV/PNEA/EXAMS"]}],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id"],
                  "pattern": "digital_registries_lookup"},
    )
    doc = yaml.safe_load(writer.render_member_config("ptsb", payload))
    assert doc["semantic"]["pattern"] == "digital_registries_lookup"


def test_render_member_config_includes_lawful_basis_when_set():
    """Wave 2 Task 3 (K-02): lawful_basis is optional on Service, but when a
    joining payload does set it, the rendered config must carry it -- the
    same "don't silently drop a field the schema now accepts" rule
    test_render_member_config_includes_pattern_when_classified enforces for
    Semantic.pattern."""
    payload = _payload(
        services=[{
            "code": "awards-api",
            "spec_url": "http://app-ptsb:8000/spec.yaml",
            "lawful_basis": "[confirm: cite the decree article]",
        }],
    )
    doc = yaml.safe_load(writer.render_member_config("ptsb", payload))
    assert doc["services"][0]["lawful_basis"] == "[confirm: cite the decree article]"


def test_render_member_config_omits_lawful_basis_when_unset():
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml"}],
    )
    doc = yaml.safe_load(writer.render_member_config("ptsb", payload))
    assert "lawful_basis" not in doc["services"][0]


def test_render_member_config_omits_empty_optional_blocks():
    doc = yaml.safe_load(writer.render_member_config("ptsb", _payload()))
    assert "services" not in doc
    assert "semantic" not in doc
    assert "requested_access" not in doc


# -- render_manifest_entry / _insert_manifest_entry ---------------------------


def test_manifest_insert_leaves_the_frozen_identifiers_block_untouched():
    text = (REAL_PACK_DIR / "manifest.yaml").read_text()
    entry = writer.render_manifest_entry("ptsb", _payload())
    updated = writer._insert_manifest_entry(text, entry)

    doc = yaml.safe_load(updated)
    assert doc["identifiers"]["members"] == [
        "PROGRESSA/GOV/PNEA:EXAMS",
        "PROGRESSA/GOV/PLR:ENROLMENT",
        "PROGRESSA/GOV/PNIA:IDENTITY",
    ]
    assert doc["identity"]["members"]["ptsb"] == {
        "code": "PTSB",
        "name": "Progressa Tertiary Scholarship Board",
        "subsystem": "SCHOLARSHIP",
        "subsystem_description": "Scholarship award management",
        "origin": "joined",
    }
    assert doc["identity"]["members"]["pnia"]["origin"] == "canonical"

    # Text-surgery, not a round-trip: everything up to and including
    # "modules:\n" is byte-identical to the original, comments included.
    split = text.index("modules:\n")
    assert updated.startswith(text[:split])
    assert updated.endswith(text[split:])


def test_manifest_insert_fails_loudly_without_an_identity_members_key():
    with pytest.raises(RuntimeError):
        writer._insert_manifest_entry("identity:\n  owner:\n    code: X\n", "    x:\n      code: X\n")


# -- dry_run_diff --------------------------------------------------------------


def test_dry_run_diff_accepts_a_hosted_consumer_and_returns_a_diff():
    diff = writer.dry_run_diff(
        REAL_PACK_DIR, "ptsb", _payload(requested_access=["PROGRESSA/GOV/PNIA/IDENTITY"])
    )
    assert "configs/member-ptsb/ptsb.yaml" in diff
    assert 'module: "ptsb"' in diff
    assert "manifest.yaml" in diff
    assert "+    ptsb:" in diff


def test_dry_run_diff_never_touches_the_real_checkout():
    before = (REAL_PACK_DIR / "manifest.yaml").read_text()
    writer.dry_run_diff(REAL_PACK_DIR, "ptsb", _payload())
    after = (REAL_PACK_DIR / "manifest.yaml").read_text()
    assert before == after
    assert not (REAL_PACK_DIR / "configs" / "member-ptsb").exists()


def test_dry_run_diff_removes_its_temp_directory():
    tmp_root = pathlib.Path(tempfile.gettempdir())
    before = set(tmp_root.glob("kp2-join-dryrun-*"))
    writer.dry_run_diff(REAL_PACK_DIR, "ptsb", _payload())
    after = set(tmp_root.glob("kp2-join-dryrun-*"))
    assert after == before


def test_dry_run_diff_surfaces_generates_stderr_verbatim_on_failure():
    bad = _payload(
        security_server={"code": "SS-BAD", "dns_name": "ss-bad", "hosted_on": "ss-does-not-exist"}
    )
    with pytest.raises(writer.GenerateFailure) as exc_info:
        writer.dry_run_diff(REAL_PACK_DIR, "ptsb", bad)
    assert "hosted_on" in exc_info.value.stderr
    assert "ss-does-not-exist" in exc_info.value.stderr
    assert exc_info.value.returncode != 0


def test_writer_output_is_discovered_by_generates_own_discover_members(tmp_path):
    """task-3 brief step 6: "asserting generate.py accepts the result and
    discover_members() finds the new member" -- calling discover_members()
    directly, not just relying on dry_run_diff() not raising."""
    writer._copy_pack(REAL_PACK_DIR, tmp_path)
    writer._write_member(tmp_path, "ptsb", _payload())

    spec = importlib.util.spec_from_file_location(
        "writer_test_generate", tmp_path / "hurl" / "generate.py"
    )
    generate_module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(tmp_path / "hurl"))  # generate.py's own `import steps`
    try:
        spec.loader.exec_module(generate_module)
    finally:
        sys.path.remove(str(tmp_path / "hurl"))

    manifest = yaml.safe_load((tmp_path / "manifest.yaml").read_text())
    members = generate_module.discover_members(tmp_path, manifest["identity"])
    assert "ptsb" in members
    assert members["ptsb"]["security_server"]["code"] == "SS-PTSB"


# -- apply_real ----------------------------------------------------------------


def test_apply_real_refuses_when_the_checkout_is_dirty(tmp_path):
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    # No commit -- configs/ and manifest.yaml are all-untracked, i.e. dirty.

    with pytest.raises(writer.DirtyCheckoutError):
        writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)
    assert not (pack / "configs" / "member-ptsb").exists()


def test_apply_real_refuses_cleanly_when_the_git_check_itself_cannot_run(tmp_path):
    """Review finding (2026-08-02): repo_root not actually being a git repo
    (a structural problem: the pack copy ended up outside the monorepo, or
    parents[2] resolved somewhere wrong) used to raise a raw, unhandled
    subprocess.CalledProcessError out of _git_status_dirty -- a 500, not a
    clear refusal. Contrast with test_apply_real_refuses_when_the_checkout_is_dirty
    above: that repo_root IS a real (uninitialised-content) git repo; this
    one is not a git repo at all."""
    repo_root = tmp_path / "not-a-repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    # No `git init` -- repo_root has no .git at all.

    with pytest.raises(writer.GitCheckFailure):
        writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)
    assert not (pack / "configs" / "member-ptsb").exists()


def test_apply_real_refuses_cleanly_on_a_member_directory_collision(tmp_path):
    """Review finding (2026-08-02): validate.py's own collision check (S8
    check 3) already refuses a request whose key collides with an existing
    configs/member-<key>/ at submission time -- this reproduces the
    unlikely race where a directory for the same key appears between that
    check and approval, which used to raise a raw, unhandled
    FileExistsError out of _write_member's mkdir -- a 500, not a clear
    refusal."""
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "seed", cwd=repo_root,
    )
    (pack / "configs" / "member-ptsb").mkdir()  # the race: already there

    with pytest.raises(writer.MemberCollisionError):
        writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)


def test_apply_real_writes_for_real_once_the_copy_is_committed(tmp_path):
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "seed", cwd=repo_root,
    )

    writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)

    assert (pack / "configs" / "member-ptsb" / "ptsb.yaml").exists()
    manifest = yaml.safe_load((pack / "manifest.yaml").read_text())
    assert manifest["identity"]["members"]["ptsb"]["origin"] == "joined"
    # generate.py ran for real too -- topology.json is one of its outputs.
    assert (pack / "hurl" / "topology.json").exists()


# -- onboarding/<key>/ (Wave 4 Task 2, G-07) -----------------------------------


def test_render_gates_table_links_sla_when_the_member_has_services():
    text = writer.render_gates_table(True)
    assert "[`03-sla/`](03-sla/)" in text
    assert "not implemented in this demo" in text


def test_render_gates_table_names_the_consumer_only_absence():
    text = writer.render_gates_table(False)
    assert "consumer-only member has none" in text
    assert "[`03-sla/`]" not in text


def test_render_requirements_record_shows_every_stated_item():
    payload = _payload()
    text = writer.render_requirements_record(payload.member_requirements)
    assert "Jane Doe" in text
    assert "consent" in text
    assert "| yes |" in text


def test_render_requirements_record_names_where_an_unset_lawful_basis_is_satisfied():
    payload = _payload(member_requirements=_requirements(lawful_basis=None))
    text = writer.render_requirements_record(payload.member_requirements)
    assert "satisfied by this member's published services" in text


def test_render_sla_record_carries_every_term_and_the_signatory():
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml", "sla": _sla()}],
    )
    text = writer.render_sla_record(payload.services[0])
    assert "awards-api" in text
    assert "99.5% monthly uptime" in text
    assert "Signed by: Head of IT" in text


def test_render_registration_record_shows_hosting_and_acl():
    payload = _payload(requested_access=["PROGRESSA/GOV/PNIA/IDENTITY"])
    text = writer.render_registration_record(
        subsystem=payload.subsystem,
        security_server=payload.security_server,
        acl_subjects=["PROGRESSA/GOV/PNIA/IDENTITY"],
        request_id="abc123",
    )
    assert "SCHOLARSHIP" in text
    assert "hosted on `ss-plr`" in text
    assert "PROGRESSA/GOV/PNIA/IDENTITY" in text
    assert "`abc123`" in text


def test_render_registration_record_names_an_own_server_join_with_no_request_id():
    payload = _payload(security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "own_server": True})
    text = writer.render_registration_record(
        subsystem=payload.subsystem,
        security_server=payload.security_server,
        acl_subjects=[],
        request_id=None,
    )
    assert "runs its own Security Server" in text
    assert "registered by hand" in text


def test_apply_real_renders_the_onboarding_tree_for_a_provider(tmp_path):
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "seed", cwd=repo_root,
    )

    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "access": ["PROGRESSA/GOV/PNEA/EXAMS"], "sla": _sla()}],
    )
    writer.apply_real(pack, "ptsb", payload, repo_root=repo_root, request_id="req-123")

    onboarding = pack / "onboarding" / "ptsb"
    assert (onboarding / "00-gates.md").exists()
    assert (onboarding / "02-requirements.md").exists()
    assert (onboarding / "03-sla" / "awards-api.md").exists()
    registration = (onboarding / "05-registration.md").read_text()
    assert "req-123" in registration
    assert "PROGRESSA/GOV/PNEA/EXAMS" in registration


def test_apply_real_renders_no_sla_directory_for_a_consumer_only_member(tmp_path):
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "seed", cwd=repo_root,
    )

    writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root, request_id="req-456")

    onboarding = pack / "onboarding" / "ptsb"
    assert (onboarding / "00-gates.md").exists()
    assert not (onboarding / "03-sla").exists()


def test_apply_real_refuses_when_onboarding_is_dirty(tmp_path):
    """Step 4: the refusal-when-dirty check now watches onboarding/ too, not
    just configs/ and manifest.yaml."""
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "seed", cwd=repo_root,
    )
    (pack / "onboarding").mkdir()
    (pack / "onboarding" / "stray.md").write_text("uncommitted\n")

    with pytest.raises(writer.DirtyCheckoutError):
        writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)
    assert not (pack / "configs" / "member-ptsb").exists()
