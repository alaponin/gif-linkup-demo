"""Tests for apps/join-api/writer.py.

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
import shutil
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
    """Pattern is optional on Semantic, but when
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
    """lawful_basis is optional on Service, but when a
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
    """Asserting generate.py accepts the result and
    discover_members() finds the new member -- calling discover_members()
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
    """repo_root not actually being a git repo
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
    """validate.py's own collision check already refuses a request whose key collides with an existing
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


def test_apply_real_re_joins_a_retired_member_over_its_retirement_record(tmp_path):
    """The pack's own exercise loop: join, un-join, join the same member
    again. An un-join keeps onboarding/<key>/ -- it writes 99-retirement.md
    INTO it -- so the second join used to hit a bare mkdir, raise
    FileExistsError AFTER configs/ and manifest.yaml were already written,
    and wedge every later approval behind the dirty-checkout guard. The
    retired tree is replaced, not merged: a re-joined member must not carry
    a retirement record for the membership that ended."""
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    # Committed with the seed: the retirement record is what an un-join left
    # behind and the operator committed -- apply_real's own dirty check
    # refuses anything else before it looks at the tree at all.
    retired = pack / "onboarding" / "ptsb"
    retired.mkdir(parents=True)
    (retired / writer.RETIREMENT_FILE).write_text("retired earlier\n")
    (retired / "00-gates.md").write_text("stale\n")
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "seed", cwd=repo_root,
    )

    writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)

    assert not (retired / writer.RETIREMENT_FILE).exists()
    assert (retired / "00-gates.md").read_text() != "stale\n"
    assert (pack / "configs" / "member-ptsb" / "ptsb.yaml").exists()


def test_apply_real_refuses_cleanly_on_a_leftover_onboarding_tree(tmp_path):
    """A directory that is NOT a retired member's -- no 99-retirement.md, so
    this API did not write it. Refused as a MemberCollisionError (app.py's
    409) naming the leftover, never a raw 500."""
    repo_root = tmp_path / "repo"
    pack = repo_root / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    _git("init", "-q", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    (pack / "onboarding" / "ptsb").mkdir(parents=True)
    (pack / "onboarding" / "ptsb" / "notes.md").write_text("not this API's\n")
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "seed", cwd=repo_root,
    )

    with pytest.raises(writer.MemberCollisionError, match="onboarding/ptsb/"):
        writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)


def _tracked_status(repo_root: pathlib.Path) -> str:
    """The same three paths apply_real's own dirty check watches. Scoped
    deliberately: _copy_pack does not copy .gitignore, so in a throwaway repo
    hurl/'s generated files are tracked and every generate.py run shows up as
    a diff -- true for a SUCCESSFUL join too, so including them here would
    assert something the transaction never promised."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "configs", "manifest.yaml", "onboarding"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    print(status)  # only shown by pytest when the assertion below fails
    return status.strip()


def _committed_pack(tmp_path) -> tuple[pathlib.Path, pathlib.Path]:
    """A throwaway repo holding a committed copy of the pack -- the state
    apply_real demands before it will write anything."""
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
    return repo_root, pack


def test_a_failed_apply_real_leaves_the_pack_exactly_as_it_found_it(tmp_path):
    """The transaction. generate.py refusing the config used to leave
    configs/member-<key>/ and manifest.yaml modified for a join that never
    happened -- which then blocked every later approval on apply_real's own
    dirty-checkout guard (docs/production-delta.md). git status is the
    assertion, not a file-by-file comparison: it sees anything the join
    touched, including files this test never thought to name."""
    repo_root, pack = _committed_pack(tmp_path)
    bad = _payload(
        security_server={"code": "SS-BAD", "dns_name": "ss-bad", "hosted_on": "ss-does-not-exist"}
    )

    with pytest.raises(writer.GenerateFailure):
        writer.apply_real(pack, "ptsb", bad, repo_root=repo_root)

    assert _tracked_status(repo_root) == "", "a failed join left the tree dirty"
    assert not (pack / "configs" / "member-ptsb").exists()
    # hurl/'s files are gitignored, so git status above cannot see them --
    # they are regenerated from the restored inputs, and must not name the
    # member that failed.
    assert "ptsb" not in (pack / "hurl" / "topology.json").read_text()


def test_a_failed_apply_real_puts_a_retired_members_onboarding_tree_back(tmp_path):
    """The rollback has to undo the rmtree render_onboarding_tree does when a
    retired member re-joins -- otherwise a failed re-join destroys the very
    retirement record the un-join was careful to keep."""
    repo_root, pack = _committed_pack(tmp_path)
    retired = pack / "onboarding" / "ptsb"
    retired.mkdir(parents=True)
    (retired / writer.RETIREMENT_FILE).write_text("retired earlier\n")
    _git("add", "-A", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.invalid", "-c", "user.name=test",
        "commit", "-q", "-m", "retirement", cwd=repo_root,
    )

    def boom(*args, **kwargs):
        raise RuntimeError("catalogue write failed")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(writer, "write_catalogue", boom)
        with pytest.raises(RuntimeError):
            writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)

    assert (retired / writer.RETIREMENT_FILE).read_text() == "retired earlier\n"
    assert _tracked_status(repo_root) == "", "a failed re-join left the tree dirty"


def test_a_failed_restore_is_a_rollback_failure_carrying_the_original(tmp_path):
    """The one case where a half-written join survives: the restore itself
    failed. It must say so loudly and keep the original failure as __cause__,
    not present itself as an ordinary refusal."""
    repo_root, pack = _committed_pack(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("catalogue write failed")

    def undo_boom(*args, **kwargs):
        raise OSError("read-only file system")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(writer, "write_catalogue", boom)
        mp.setattr(writer, "_restore", undo_boom)
        with pytest.raises(writer.RollbackFailure) as exc_info:
            writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)

    assert "needs a human" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_apply_real_removes_its_stash_directory(tmp_path):
    tmp_root = pathlib.Path(tempfile.gettempdir())
    before = set(tmp_root.glob("kp2-join-apply-*"))
    repo_root, pack = _committed_pack(tmp_path)

    writer.apply_real(pack, "ptsb", _payload(), repo_root=repo_root)

    assert set(tmp_root.glob("kp2-join-apply-*")) == before


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


# -- onboarding/<key>/ --------------------------------------------------


def test_render_gates_table_links_sla_when_the_member_has_services():
    text = writer.render_gates_table(True)
    assert "[`03-sla/`](03-sla/)" in text
    assert "not implemented in this demo" in text


def test_render_gates_table_names_the_consumer_only_absence():
    text = writer.render_gates_table(False)
    assert "consumer-only member has none" in text
    assert "[`03-sla/`]" not in text


def test_render_gates_table_points_g5_at_the_catalogue_but_keeps_the_pattern_absence():
    """The entry closes half of G5's remaining gap; the tier-1 pattern
    register is untouched and must still be named."""
    g5 = next(line for line in writer.render_gates_table(True).splitlines()
              if line.startswith("| Service conformance"))
    assert "[`04-catalogue/`](04-catalogue/)" in g5
    assert "no service-catalogue entry" not in g5
    assert "**named absence** for the tier-1 BB pattern register" in g5


def test_every_gate_row_opens_with_a_status_from_the_path_conformance_vocabulary():
    """docs/path-conformance.yaml's four statuses, and no fifth. G3 in
    particular: the Test CA signs any CSR, which is `simulated`, not an
    absence -- this row disagreed with G3.1 until it was reconciled."""
    vocabulary = ("**implemented**", "**simulated**", "**named absence**", "**out of scope**")
    for has_services in (True, False):
        for line in writer.render_gates_table(has_services).splitlines():
            if not line.startswith("| ") or line.startswith("| Gate") or line.startswith("| ---"):
                continue
            status = line.rstrip(" |").rsplit("|", 1)[1].strip()
            assert status.startswith(vocabulary), f"{line[:40]!r} status opens with {status[:30]!r}"


def test_render_gates_table_does_not_link_a_catalogue_a_consumer_has_not_got():
    g5 = next(line for line in writer.render_gates_table(False).splitlines()
              if line.startswith("| Service conformance"))
    assert "[`04-catalogue/`]" not in g5
    assert "nothing to catalogue" in g5


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


def test_render_requirements_record_sanitises_a_pipe_and_newline_lawful_basis():
    payload = _payload(member_requirements=_requirements(lawful_basis="Act 1 | 2\n(consent)"))
    text = writer.render_requirements_record(payload.member_requirements)
    basis_line = next(line for line in text.splitlines() if line.startswith("| Lawful basis"))
    assert basis_line == "| Lawful basis for its exchanges | Act 1 \\| 2 (consent) |"


def test_render_sla_record_carries_every_term_and_the_signatory():
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml", "sla": _sla()}],
    )
    text = writer.render_sla_record(payload.services[0])
    assert "awards-api" in text
    assert "99.5% monthly uptime" in text
    assert "Signed by: Head of IT" in text


def _catalogue_entry(service, **overrides) -> str:
    kwargs = dict(
        service_id="PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api",
        provider="Progressa Tertiary Scholarship Board (PTSB)",
        semantic=None,
        semantic_anchor=None,
        request_id=None,
    )
    kwargs.update(overrides)
    return writer.render_catalogue_entry(service, **kwargs)


def test_render_catalogue_entry_carries_the_service_id_contract_and_acl():
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "access": ["PROGRESSA/GOV/PNEA/EXAMS"],
                   "lawful_basis": "Scholarship Act", "sla": _sla()}],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id"],
                  "pattern": "digital_registries_lookup"},
    )
    text = _catalogue_entry(
        payload.services[0], semantic=payload.semantic, semantic_anchor="CEDS", request_id="req-1"
    )
    assert "`PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api`" in text
    assert "http://app-ptsb:8000/spec.yaml" in text
    assert "`award`" in text and "anchor: CEDS" in text
    assert "`digital_registries_lookup`" in text
    assert "Scholarship Act" in text
    assert "`PROGRESSA/GOV/PNEA/EXAMS`" in text
    assert "`req-1`" in text
    # Publication is not permission, on the face of the entry itself.
    assert "appearing here grants nothing" in text


def test_render_catalogue_entry_links_the_sla_rather_than_copying_it():
    """One SLA, written once and reachable from two directions -- the entry
    must carry the link and none of the five terms."""
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml", "sla": _sla()}],
    )
    text = _catalogue_entry(payload.services[0])
    assert "[`../03-sla/awards-api.md`](../03-sla/awards-api.md)" in text
    for term in ("99.5% monthly uptime", "Mon-Fri 08:00-18:00 ICT", "Head of IT"):
        assert term not in text


def test_render_catalogue_entry_names_an_unclassified_services_absence():
    """An unclassified service and a service whose classification was lost
    render identically as a blank cell -- so the absence is stated in words
    the reader can act on instead."""
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml", "sla": _sla()}],
        semantic={"entity": "award", "key": "award_id", "fields": ["award_id"]},
    )
    text = _catalogue_entry(payload.services[0], semantic=payload.semantic)
    assert "cannot be found by pattern" in text
    assert "| Exchange pattern (tier 1) |  |" not in text
    # The reader has no way to look an internal conformance id up.
    assert "S6a" not in text


def test_render_catalogue_entry_names_every_other_empty_source():
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml", "sla": _sla()}],
    )
    text = _catalogue_entry(payload.services[0])
    assert "*not declared*" in text        # no semantic entity
    assert "*not stated*" in text          # no lawful basis
    assert "no consumer has been granted access" in text


def test_render_catalogue_entry_sanitises_a_pipe_and_newline_lawful_basis():
    payload = _payload(
        services=[{"code": "awards-api", "spec_url": "http://app-ptsb:8000/spec.yaml",
                   "lawful_basis": "Act 1 | 2\n(consent)", "sla": _sla()}],
    )
    text = _catalogue_entry(payload.services[0])
    basis_line = next(line for line in text.splitlines() if line.startswith("| Lawful basis"))
    assert basis_line == "| Lawful basis | Act 1 \\| 2 (consent) |"


# -- onboarding/catalogue.yaml -------------------------------------------------


def test_catalogue_lists_every_published_service_on_the_instance():
    doc = yaml.safe_load(writer.render_catalogue(REAL_PACK_DIR))
    assert doc["instance"] == "PROGRESSA"
    ids = [s["id"] for s in doc["services"]]
    assert "PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api" in ids
    assert "PROGRESSA/GOV/PNIA/IDENTITY/identity-api" in ids
    assert ids == sorted(ids)
    # A consumer-only member publishes nothing, so it contributes no rows.
    assert not [s for s in doc["services"] if s["provider"]["key"] == "pnea"]
    assert "grants nothing" in doc["publication_is_not_permission"]

    enrolment = next(s for s in doc["services"] if s["service_code"] == "enrolment-api")
    assert enrolment["semantic"] == {"entity": "enrolment", "anchor": "OneRoster"}
    assert enrolment["pattern"] == "digital_registries_lookup"
    assert enrolment["access"] == ["PROGRESSA/GOV/PNEA/EXAMS"]
    # Both paths point at files that are actually on disk.
    assert (REAL_PACK_DIR / enrolment["sla"]).exists()
    assert (REAL_PACK_DIR / enrolment["entry"]).exists()


def test_catalogue_regenerates_byte_identically_from_unchanged_inputs():
    assert writer.render_catalogue(REAL_PACK_DIR) == writer.render_catalogue(REAL_PACK_DIR)
    assert writer.render_catalogue(REAL_PACK_DIR) == (REAL_PACK_DIR / "onboarding" / "catalogue.yaml").read_text()


def test_catalogue_drops_a_removed_members_services_without_a_delete_path(tmp_path):
    """An un-join deletes configs/member-<key>/ and nothing else; the
    services leave the catalogue because the next regeneration does not find
    them, not because anything removed them."""
    pack = tmp_path / "pack"
    writer._copy_pack(REAL_PACK_DIR, pack)
    before = yaml.safe_load(writer.render_catalogue(pack))
    assert [s for s in before["services"] if s["provider"]["key"] == "plr"]

    shutil.rmtree(pack / "configs" / "member-plr")

    after = yaml.safe_load(writer.render_catalogue(pack))
    assert not [s for s in after["services"] if s["provider"]["key"] == "plr"]
    assert [s for s in after["services"] if s["provider"]["key"] == "pnia"]


def test_apply_real_regenerates_the_catalogue_including_the_joined_member(tmp_path):
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
    writer.apply_real(
        pack, "ptsb", payload, repo_root=repo_root, request_id="req-123",
        decision_reference="RIHA-2026-001", approved_at="2026-08-08T00:00:00+00:00",
    )

    doc = yaml.safe_load((pack / "onboarding" / "catalogue.yaml").read_text())
    joined = next(s for s in doc["services"] if s["provider"]["key"] == "ptsb")
    assert joined["id"] == "PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api"
    assert (pack / joined["entry"]).exists()
    assert (pack / joined["sla"]).exists()


def test_render_gates_table_names_the_admission_absence_by_default():
    text = writer.render_gates_table(True)
    admission_row = next(line for line in text.splitlines() if line.startswith("| Admission"))
    assert "not implemented in this demo" in admission_row


def test_render_gates_table_points_at_01_admission_when_admitted():
    text = writer.render_gates_table(True, admitted=True)
    admission_row = next(line for line in text.splitlines() if line.startswith("| Admission"))
    assert "01-admission.md" in admission_row
    assert "not implemented in this demo" not in admission_row


def test_render_admission_record_carries_the_reference_id_and_role():
    text = writer.render_admission_record("req-789", "RIHA-2026-003", "2026-08-08T12:00:00+00:00")
    assert "req-789" in text
    assert "RIHA-2026-003" in text
    assert "2026-08-08T12:00:00+00:00" in text
    assert "| Approving role | operator |" in text


def test_render_admission_record_sanitises_a_pipe_and_newline_reference():
    """A pasted decision_reference containing a pipe or newline must not
    break the record's table structure. A raw (unescaped) "|" in the value
    would add a cell the table never declared; this asserts the pipe
    survives only escaped, and the newline is gone."""
    text = writer.render_admission_record("req-1", "RIHA | 2026\n001", "2026-08-08T00:00:00+00:00")
    lines = text.splitlines()
    reference_line = next(line for line in lines if line.startswith("| Decision reference"))
    assert reference_line == "| Decision reference | RIHA \\| 2026 001 |"
    assert reference_line.count("\\|") == 1


def test_sanitize_cell_collapses_whitespace_and_escapes_pipes():
    assert writer._sanitize_cell("a\nb  c | d") == "a b c \\| d"


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
    assert "Signing key" not in text


def test_render_registration_record_names_the_signing_key_delegation_when_hosted():
    """A hosted member's SIGN key lives on the host's token, not its own --
    the path's own G2 warning names this as 'a delegation with no
    counterpart in the obligation set', and until this row existed the fact
    was recorded nowhere a member would read."""
    payload = _payload()  # default: hosted_on="ss-plr"
    text = writer.render_registration_record(
        subsystem=payload.subsystem,
        security_server=payload.security_server,
        acl_subjects=[],
        request_id="abc123",
    )
    assert "Signing key" in text
    assert "held on `ss-plr`'s token" in text
    assert "delegation" in text


def test_render_retirement_record_carries_the_fixed_facts():
    text = writer.render_retirement_record("ptsb", "2026-08-08T12:00:00+00:00", "req-999")
    assert "2026-08-08T12:00:00+00:00" in text
    assert "req-999" in text
    assert "REVERSAL_ORDER" in text
    assert "message-log" in text.lower() and "separate" in text.lower()


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
    writer.apply_real(
        pack, "ptsb", payload, repo_root=repo_root, request_id="req-123",
        decision_reference="RIHA-2026-001", approved_at="2026-08-08T00:00:00+00:00",
    )

    onboarding = pack / "onboarding" / "ptsb"
    assert (onboarding / "00-gates.md").exists()
    assert (onboarding / "02-requirements.md").exists()
    assert (onboarding / "03-sla" / "awards-api.md").exists()
    entry = (onboarding / "04-catalogue" / "awards-api.md").read_text()
    # The instance and member class come off manifest.yaml and the join
    # policy, not a constant in the renderer.
    assert "`PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api`" in entry
    # The SLA link resolves to a file that is actually there -- a dangling
    # link is the one failure this artefact exists to prevent.
    assert (onboarding / "04-catalogue" / "../03-sla/awards-api.md").resolve().exists()
    registration = (onboarding / "05-registration.md").read_text()
    assert "req-123" in registration
    assert "PROGRESSA/GOV/PNEA/EXAMS" in registration
    admission = (onboarding / "01-admission.md").read_text()
    assert "req-123" in admission and "RIHA-2026-001" in admission
    admission_row = next(
        line for line in (onboarding / "00-gates.md").read_text().splitlines() if line.startswith("| Admission")
    )
    assert "01-admission.md" in admission_row
    assert "not implemented in this demo" not in admission_row


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

    writer.apply_real(
        pack, "ptsb", _payload(), repo_root=repo_root, request_id="req-456",
        decision_reference="RIHA-2026-002", approved_at="2026-08-08T00:00:00+00:00",
    )

    onboarding = pack / "onboarding" / "ptsb"
    assert (onboarding / "00-gates.md").exists()
    assert not (onboarding / "03-sla").exists()
    # Nothing published, so nothing to catalogue -- no empty directory and
    # no placeholder file, exactly as for the SLA above.
    assert not (onboarding / "04-catalogue").exists()
    assert (onboarding / "01-admission.md").exists()


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


def test_every_subprocess_run_in_writer_passes_a_timeout():
    """E.4, parse-level regression guard -- the same style
    test_app_requests.py's test_no_job_scrub_call_site_passes_the_narrow_job_
    secrets_set uses to catch a call-site regression by parsing the source,
    not by exercising it. _run_generate and _git_status both learned a
    timeout= this phase; this is what stops a future subprocess.run() call
    added to this file from silently going back to none."""
    import ast

    tree = ast.parse((pathlib.Path(__file__).resolve().parent.parent / "writer.py").read_text())
    offending = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and not any(kw.arg == "timeout" for kw in node.keywords)
    ]
    assert not offending, (
        f"writer.py line(s) {offending}: subprocess.run() with no timeout= -- a hung "
        "child would block the caller (the request thread, for _run_generate) "
        "indefinitely (security-review-remediation-plan.md E.4)."
    )
