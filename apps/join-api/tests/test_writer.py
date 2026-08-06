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


def _payload(**overrides) -> JoinPayload:
    base = dict(
        code="PTSB",
        name="Progressa Tertiary Scholarship Board",
        subsystem="SCHOLARSHIP",
        subsystem_description="Scholarship award management",
        security_server={"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
        backend={"auth": "network_allowlist"},
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
    assert doc["semantic"]["fields"] == ["award_id", "status"]
    assert doc["backend"] == {"auth": "network_allowlist"}
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
        "PROGRESSA/GOV/MOEYS:PEMIS",
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
