"""apps/join-api/writer.py -- turning a validated JoinPayload into files on
disk that hurl/generate.py accepts (design spec S9). Nothing
here talks to X-Road; nothing here decides whether a payload is admissible
(that is validate.py's job, already run before either function below is
called).

Two modes, sharing one write-then-regenerate core (_write_member /
_run_generate):

  dry_run_diff()  -- copies the whole pack to a throwaway temp directory,
                     writes the candidate config + manifest entry into THAT
                     COPY, runs the copy's own generate.py, and returns a
                     diff string. Used at submission (spec S7: "the config
                     diff the join would write, computed at submission").
                     Never writes to, or reads mutable state from, the real
                     checkout -- it reads pack_dir exactly once, to seed the
                     copy, then only ever touches the copy.

  apply_real()     -- the same sequence, against the real pack_dir. Refuses
                     first (DirtyCheckoutError) if `git status --porcelain
                     configs/ manifest.yaml onboarding/` is not clean (spec
                     S9's mitigation: a join must never stack on top of
                     uncommitted work of unclear provenance). Called by
                     app.py's POST /requests/{id}/approve, before the job
                     (job.py) starts. Once generate.py accepts the result,
                     also renders onboarding/<key>/ (G-07) --
                     render_onboarding_tree() is the same function
                     scripts/render-onboarding.sh calls for the three
                     canonical members.

Design spec S9 is explicit that config-writing happens "on APPROVED, before
any live mutation" -- that governs apply_real only. dry_run_diff runs at
submission, always against a copy, precisely so an unapproved (possibly
REJECTED) request never leaves a mark on the real checkout.
"""
from __future__ import annotations

import datetime
import difflib
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

from schema import JoinPayload, MemberRequirements, SecurityServer, Service

# Everything hurl/generate.py's main() reads via load()/discover_members()/
# TEMPLATES/read_env() (hurl/generate.py: PACK/HURL_DIR/ENV_PATH at the top,
# discover_members() ~line 191, TEMPLATES ~line 421, read_env() ~line 301) --
# configs/, manifest.yaml, deployment.yaml, the whole hurl/ tree (generate.py
# itself, steps.py it imports, hurl/templates/), and .env for read_env()'s
# XROAD_* values. Not apps/, prompts/, .git, or anything Docker/deployment-
# only -- generate.py never touches those.
_COPY_ITEMS = ["configs", "manifest.yaml", "deployment.yaml", "hurl", ".env"]


class GenerateFailure(Exception):
    """python3 hurl/generate.py exited non-zero. generate.py's own failure
    modes are `raise SystemExit(str)` -- loud and specific by design
    (discover_members, check_join_policy, resolve_hosted_on_map, ...) --
    so stderr is carried verbatim, not re-wrapped -- "passing them through
    verbatim is more useful than wrapping them"."""

    def __init__(self, stderr: str, returncode: int):
        super().__init__(stderr)
        self.stderr = stderr
        self.returncode = returncode


class DirtyCheckoutError(Exception):
    """`git status --porcelain configs/ manifest.yaml` was not empty (spec
    S9) -- refuses to start a real-apply job on top of uncommitted,
    unattributable work."""


class GitCheckFailure(Exception):
    """The dirty-checkout check itself could not run -- a structural problem
    (the pack copy ended up outside the monorepo, `repo_root` does not
    resolve to a real git repo, `git` itself is missing) rather than the
    checkout genuinely being dirty. Distinct from DirtyCheckoutError: this is
    "could not tell", not "and the answer is yes". Refusing here is the safe
    default either way -- apps/join-api/app.py's `_live_uncommitted` already
    treats this same class of git failure as "assume the worst", not
    "assume clean" -- a subprocess.CalledProcessError here must not escape
    uncaught and surface as a raw 500 instead of a clear refusal."""


class MemberCollisionError(Exception):
    """A member directory with this key was created between validation and
    approval -- the race _write_member's own comment names as the only way
    its `mkdir(parents=True)` (not exist_ok) can raise FileExistsError.
    validate.py's collision check (S8 check 3) already refused any request
    whose key collided with an existing configs/member-<key>/ at submission
    time; this catches the (unlikely) case where a second request for the
    same key was approved in between -- this must not escape apply_real
    uncaught and surface as a raw 500 instead of a clear refusal."""


def _copy_pack(pack_dir: pathlib.Path, dest: pathlib.Path) -> None:
    """Read pack_dir exactly once here; every write from here on lands in
    dest. "configs" copies first: shutil.copytree creates dest's missing
    parent directories via os.makedirs, so dest itself exists by the time
    the plain-file copy2 calls (manifest.yaml, deployment.yaml) need it."""
    for name in _COPY_ITEMS:
        src = pack_dir / name
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dest / name)
        else:
            shutil.copy2(src, dest / name)


def render_member_config(key: str, payload: JoinPayload) -> str:
    """configs/member-<key>/<key>.yaml, in the shape prompts/member.md
    produces (see configs/member-pnia/2.5.yaml for the committed precedent),
    plus the backend:, member_requirements: and requested_access: blocks.
    Deliberately omits type, forwarding URL, enabled,
    tls_verify -- generate.py never reads them and a copy here would drift
    (2.5.yaml's own comment)."""
    today = datetime.date.today().isoformat()
    header = (
        f"# Member {payload.code} -- joined via the join API on {today}. Do "
        "not hand-edit -- regenerate via a new join or scripts/member.sh "
        "remove.\n"
    )

    security_server: dict = {
        "code": payload.security_server.code,
        "dns_name": payload.security_server.dns_name,
    }
    if payload.security_server.hosted_on:
        security_server["hosted_on"] = payload.security_server.hosted_on

    body: dict = {"building_block": f"member-{key}", "security_server": security_server}
    if payload.services:
        body["services"] = [
            {
                "code": svc.code,
                "spec_url": svc.spec_url,
                **({"access": list(svc.access)} if svc.access else {}),
                # Recorded and surfaced only (K-02), same treatment as
                # access above and semantic.pattern below -- never resolved
                # against anything; there is no lawful-basis registry in
                # this pack to check it against.
                **({"lawful_basis": svc.lawful_basis} if svc.lawful_basis else {}),
                # validate.py's sla_required check (K-01) already guarantees
                # this is set for a provider's service by the time
                # apply_real writes this file.
                **({"sla": svc.sla.model_dump()} if svc.sla else {}),
            }
            for svc in payload.services
        ]
    if payload.semantic:
        body["semantic"] = {
            "entity": payload.semantic.entity,
            "key": payload.semantic.key,
            "fields": list(payload.semantic.fields),
        }
        if payload.semantic.pattern:
            body["semantic"]["pattern"] = payload.semantic.pattern.value
    body["backend"] = {"auth": payload.backend.auth.value}
    # Module 5.2's checklist (K-01), required on every payload -- rendered
    # unconditionally, unlike the optional blocks above.
    body["member_requirements"] = payload.member_requirements.model_dump(exclude_none=True)
    if payload.requested_access:
        body["requested_access"] = list(payload.requested_access)

    dumped = yaml.safe_dump(body, sort_keys=False, default_flow_style=False)
    # module: "<key>" must stay a quoted string (2.5.yaml: module: "2.5" --
    # a bareword numeric-looking id would round-trip as a non-string).
    # validate.py's key_derivation check already constrains key to
    # [a-z0-9]+, so this f-string can never produce invalid YAML.
    return header + f'module: "{key}"\n' + dumped


def render_manifest_entry(key: str, payload: JoinPayload) -> str:
    """The identity.members.<key> block, 4-space-indented to match the
    existing entries' style (manifest.yaml's identity.members.pnea etc.):
    code, name, subsystem, subsystem_description, origin. origin: joined is
    forced here, always -- schema.JoinPayload has no such field, so there is
    nothing a hand-crafted payload could set to make a join look canonical."""
    entry = {
        key: {
            "code": payload.code,
            "name": payload.name,
            "subsystem": payload.subsystem,
            "subsystem_description": payload.subsystem_description,
            "origin": "joined",
        }
    }
    dumped = yaml.safe_dump(entry, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return "".join(("    " + line if line.strip() else line) for line in dumped.splitlines(keepends=True))


def _insert_manifest_entry(text: str, entry_block: str) -> str:
    """The mirror of scripts/member.sh's cmd_remove Python heredoc: that one
    finds "    <key>:\\n" under identity.members and text-surgically deletes
    it plus every deeper-or-blank line that follows; this finds identity's
    "  members:\\n" (2-space indent) and inserts entry_block right before
    the first line that ends that mapping (a sibling key at <4-space indent,
    e.g. "modules:\\n"). Line-level surgery, not a YAML round-trip -- a
    round-trip would reformat/reorder/re-comment the whole file.

    manifest.yaml has TWO "  members:\\n" lines -- one under identifiers:
    (the frozen list, untouchable) and one under identity: (this function's
    target) -- so the search starts after the top-level "identity:\\n" line,
    not from the top of the file.
    """
    lines = text.splitlines(keepends=True)
    try:
        identity_idx = lines.index("identity:\n")
    except ValueError as exc:
        raise RuntimeError("manifest.yaml has no top-level 'identity:' key") from exc

    members_idx = None
    for i in range(identity_idx + 1, len(lines)):
        line = lines[i]
        if line == "  members:\n":
            members_idx = i
            break
        if line.strip() and not line.startswith(" "):
            break  # identity: block ended (next top-level key) without a members: key
    if members_idx is None:
        raise RuntimeError("manifest.yaml identity: has no 'members:' key")

    end = len(lines)
    for i in range(members_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < 4:
            end = i
            break

    return "".join(lines[:end] + entry_block.splitlines(keepends=True) + lines[end:])


# -- onboarding/<key>/ (G-07) --------------------------------------------------
#
# Four generated files per member -- not the onboarding path's ten (D3: no
# curriculum change). Never hand-maintained (P2, design decision 3): an
# absent file means the gate has not been passed, whatever the calendar
# says, so nothing here backfills a plausible-looking stub.

_GATES_TABLE = """\
# Onboarding gates

Every row is a gate exit (`docs/onboarding-alignment-design.md`'s P2: a named
absence teaches as well as an implementation). A missing file means the gate
has not been passed, whatever the calendar says.

| Gate | Exit test | Accountable | Status |
| --- | --- | --- | --- |
| Member Requirements (5.2) | Checklist stated by the applicant | Operating Authority | [`02-requirements.md`](02-requirements.md) |
| SLA (5.3) | Signed SLA per published service | Operating Authority | {sla_status} |
| Registration (5.4) | Subsystem registered, ACL granted | Operating Authority | [`05-registration.md`](05-registration.md) |
| Application (G0) | Application + signed membership agreement | Operating Authority | not implemented in this demo -- see `docs/production-delta.md` |
| Admission (G1) | Minuted admission decision | Steering Committee | not implemented in this demo -- see `docs/production-delta.md` |
| Certificates (G3) | CA/TSA issuance record, member-verified | Operating Authority | not implemented in this demo -- see `docs/production-delta.md` |
| Go-live (G6) | Monitored first production transactions | Operating Authority | not implemented in this demo -- see `docs/production-delta.md` |
"""


def render_gates_table(has_services: bool) -> str:
    """00-gates.md -- one table, not four near-identical stub files: every
    gate KP2 teaches or exceeds, with the file that proves it or a named absence pointing at
    production-delta.md. Identical for every member except the SLA row,
    which depends on whether this member published anything to sign one
    for."""
    sla_status = (
        "[`03-sla/`](03-sla/)"
        if has_services
        else (
            "no services published -- a consumer-only member has none "
            "(TK-IO-09 is written for providers; the onboarding path's own "
            "§8 open question 5)"
        )
    )
    return _GATES_TABLE.format(sla_status=sla_status)


def render_requirements_record(requirements: MemberRequirements) -> str:
    """02-requirements.md -- Module 5.2's six-item checklist, stated by the
    applicant, not derived (design decision 1)."""
    lawful_basis = requirements.lawful_basis or (
        "satisfied by this member's published services (each service's own "
        "`lawful_basis`) -- see `03-sla/`"
    )
    return (
        "# Member Requirements -- Module 5.2\n\n"
        "The checklist Module 5.2 teaches: what an agency must have in place "
        "before it can join.\n\n"
        "| Item | Stated |\n"
        "| --- | --- |\n"
        f"| Security Server in place | {'yes' if requirements.has_security_server else 'no'} |\n"
        f"| Registered identity on the bus | {'yes' if requirements.has_registered_identity else 'no'} |\n"
        f"| Standards portfolio adopted | {'yes' if requirements.standards_portfolio_adopted else 'no'} |\n"
        f"| Data cleaned and conformed to the schema | {'yes' if requirements.data_conformant else 'no'} |\n"
        f"| Lawful basis for its exchanges | {lawful_basis} |\n"
        f"| Named technical contact | {requirements.technical_contact} |\n"
    )


def render_sla_record(service: Service) -> str:
    """03-sla/<service-code>.md -- Module 5.3's five-term template, "reuse
    the same template for every service on the bus" (design decision 2).
    Caller guarantees service.sla is set (validate.py's sla_required check,
    or the canonical configs, which already have it set)."""
    sla = service.sla
    assert sla is not None, f"render_sla_record called for {service.code!r} with no sla block"
    return (
        f"# SLA -- {service.code}\n\n"
        "Module 5.3's template, signed and reused for every service on the bus.\n\n"
        "| Term | Target |\n"
        "| --- | --- |\n"
        f"| Availability | {sla.availability} |\n"
        f"| Response time | {sla.response_time} |\n"
        f"| Support hours | {sla.support_hours} |\n"
        f"| Incident response | {sla.incident_response} |\n"
        f"| Change notice | {sla.change_notice} |\n\n"
        f"Signed by: {sla.signatory}\n"
    )


def render_registration_record(
    *,
    subsystem: str,
    security_server: SecurityServer,
    acl_subjects: list[str],
    request_id: str | None,
) -> str:
    """05-registration.md -- Module 5.4's registration gate: subsystem,
    Security Server, the ACL subjects this member's services granted, and
    the join request that did it (None for a canonical member, registered
    by hand via prompts/register-member.md, never through the join API)."""
    if security_server.own_server:
        hosting = "runs its own Security Server"
    else:
        hosting = f"hosted on `{security_server.hosted_on}`"
    acl = ", ".join(f"`{s}`" for s in acl_subjects) if acl_subjects else "none"
    request_line = (
        f"`{request_id}`"
        if request_id
        else "registered by hand (`prompts/register-member.md`) -- no join request"
    )
    return (
        "# Registration -- Module 5.4\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| Subsystem | {subsystem} |\n"
        f"| Security Server | {security_server.code} (`{security_server.dns_name}`) |\n"
        f"| Hosting | {hosting} |\n"
        f"| ACL subjects granted | {acl} |\n"
        f"| Join request id | {request_line} |\n"
    )


def render_onboarding_tree(
    target_dir: pathlib.Path,
    key: str,
    payload: JoinPayload,
    *,
    request_id: str | None = None,
) -> None:
    """Writes onboarding/<key>/'s four files under target_dir. Shared by
    apply_real() (a real join) and scripts/render-onboarding.sh (the three
    canonical members) -- "the same writer.py code path a
    join uses" for both, so there is exactly one place that decides what an
    onboarding record looks like."""
    onboarding_dir = target_dir / "onboarding" / key
    onboarding_dir.mkdir(parents=True)
    (onboarding_dir / "00-gates.md").write_text(render_gates_table(bool(payload.services)))
    (onboarding_dir / "02-requirements.md").write_text(render_requirements_record(payload.member_requirements))
    if payload.services:
        sla_dir = onboarding_dir / "03-sla"
        sla_dir.mkdir()
        for svc in payload.services:
            (sla_dir / f"{svc.code}.md").write_text(render_sla_record(svc))
    acl_subjects = sorted(
        {subject for svc in payload.services for subject in svc.access} | set(payload.requested_access)
    )
    (onboarding_dir / "05-registration.md").write_text(
        render_registration_record(
            subsystem=payload.subsystem,
            security_server=payload.security_server,
            acl_subjects=acl_subjects,
            request_id=request_id,
        )
    )


def _write_member(target_dir: pathlib.Path, key: str, payload: JoinPayload) -> None:
    """The one write-the-config-and-manifest-entry routine, shared by both
    dry_run_diff (target_dir is a temp copy) and apply_real (target_dir is
    the real pack_dir) -- only the directory, and whether a git-dirty check
    ran first, differs between the two callers."""
    member_dir = target_dir / "configs" / f"member-{key}"
    member_dir.mkdir(parents=True)  # not exist_ok: validate.py's collision
    # check (S8 check 3) already refuses a request whose key collides with
    # an existing configs/member-<key>/ -- a FileExistsError here means that
    # guarantee was violated somewhere upstream, and should be loud. apply_real
    # (the only caller where this race is reachable -- dry_run_diff's target
    # is always a fresh temp copy) turns it into MemberCollisionError.
    (member_dir / f"{key}.yaml").write_text(render_member_config(key, payload))

    manifest_path = target_dir / "manifest.yaml"
    updated = _insert_manifest_entry(manifest_path.read_text(), render_manifest_entry(key, payload))
    manifest_path.write_text(updated)


def _run_generate(generate_py: pathlib.Path) -> subprocess.CompletedProcess:
    """python3 <generate_py>. cwd is the pack root the invoked file itself
    sits under -- matches `cd "$PACK_DIR" && python3 hurl/generate.py`, the
    pack's own invocation convention (scripts/member.sh). generate.py's own
    PACK resolution is `Path(__file__).resolve().parent.parent` (--out only
    redirects where scenarios/ etc. get WRITTEN),
    so cwd does not actually change what it reads -- this just avoids
    depending on ambient process state for something that doesn't need it.
    No env= is passed: generate.py reads its own .env from disk (read_env()),
    never from the environment, so there is no credential to route through
    here (global constraint: credentials never leave the process this way)."""
    return subprocess.run(
        [sys.executable, str(generate_py)],
        cwd=str(generate_py.parent.parent),
        capture_output=True,
        text=True,
    )


def _render_diff(key: str, new_config: str, manifest_before: str, manifest_after: str) -> str:
    manifest_diff = "".join(
        difflib.unified_diff(
            manifest_before.splitlines(keepends=True),
            manifest_after.splitlines(keepends=True),
            fromfile="manifest.yaml",
            tofile="manifest.yaml",
        )
    )
    return (
        f"--- new file: configs/member-{key}/{key}.yaml ---\n{new_config}\n"
        f"--- manifest.yaml ---\n{manifest_diff}"
    )


def dry_run_diff(pack_dir: pathlib.Path, key: str, payload: JoinPayload) -> str:
    """Copy pack_dir to a throwaway temp directory, write the candidate
    config + manifest entry into the copy, run the copy's own generate.py,
    and return a diff string on success. Raises GenerateFailure (stderr
    attached, verbatim) if generate.py rejects the result. Always removes
    the temp directory, success or failure -- never leaks it.

    This function must never write to, or read mutable state from, the real
    checkout: pack_dir is read exactly once, by _copy_pack, to seed tmp.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kp2-join-dryrun-"))
    try:
        _copy_pack(pack_dir, tmp)
        manifest_before = (tmp / "manifest.yaml").read_text()
        _write_member(tmp, key, payload)
        proc = _run_generate(tmp / "hurl" / "generate.py")
        if proc.returncode != 0:
            raise GenerateFailure(proc.stderr, proc.returncode)
        manifest_after = (tmp / "manifest.yaml").read_text()
        new_config = (tmp / "configs" / f"member-{key}" / f"{key}.yaml").read_text()
        return _render_diff(key, new_config, manifest_before, manifest_after)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _git_status_dirty(repo_root: pathlib.Path, pack_dir: pathlib.Path) -> str:
    try:
        rel = pack_dir.relative_to(repo_root)
        proc = subprocess.run(
            # The live-but-uncommitted window production-delta.md documents
            # covers a third tree (G-07): apply_real() writes onboarding/<key>/
            # too, so the refusal-when-dirty check must watch it exactly
            # like configs/ and manifest.yaml, not just the other two trees.
            ["git", "-C", str(repo_root), "status", "--porcelain",
             str(rel / "configs"), str(rel / "manifest.yaml"), str(rel / "onboarding")],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        # ValueError: pack_dir is not under repo_root. CalledProcessError:
        # repo_root is not a git repo (or some other structural git failure).
        # OSError: git itself is missing. None of these mean "clean" --
        # apply_real must refuse exactly as it would for a genuinely dirty
        # checkout, not silently proceed.
        raise GitCheckFailure(f"could not check whether {pack_dir} is a clean checkout: {exc}") from exc
    return proc.stdout


def apply_real(
    pack_dir: pathlib.Path,
    key: str,
    payload: JoinPayload,
    *,
    repo_root: pathlib.Path | None = None,
    request_id: str | None = None,
) -> None:
    """The real write-then-regenerate sequence, against the real pack_dir --
    what actually makes the join real. repo_root defaults to three levels
    above pack_dir (docker-compose.yml: PACK_DIR is /repo/10-Knowledge-
    Products/KP2-GIF/KP2-build-pack, repo_root is /repo, the enclosing
    .git); overridable so tests can point it at a throwaway repo instead of
    relying on that exact nesting.

    Refuses (DirtyCheckoutError) before writing anything if `git status
    --porcelain configs/ manifest.yaml onboarding/` is not clean (spec S9) --
    a join must never stack on top of uncommitted work of unclear provenance.

    Called by app.py's POST /requests/{id}/approve, before the job starts --
    the point a request moves SUBMITTED -> APPROVED -> RUNNING. request_id is
    the approved request's own id, threaded through to
    onboarding/<key>/05-registration.md's "join request id" field.
    """
    repo_root = repo_root or pack_dir.resolve().parents[2]
    dirty = _git_status_dirty(repo_root, pack_dir)
    if dirty.strip():
        raise DirtyCheckoutError(
            "refusing to start a join job: configs/, manifest.yaml or "
            f"onboarding/ already has uncommitted changes (spec S9) -- commit "
            f"or discard them first:\n{dirty}"
        )
    try:
        _write_member(pack_dir, key, payload)
    except FileExistsError as exc:
        raise MemberCollisionError(
            f"configs/member-{key}/ already exists -- another request for the same key was "
            "approved between this request's validation and its approval"
        ) from exc
    proc = _run_generate(pack_dir / "hurl" / "generate.py")
    if proc.returncode != 0:
        raise GenerateFailure(proc.stderr, proc.returncode)
    # Only after generate.py accepts the result -- a rejected/failed config
    # write must not leave onboarding evidence for a member that was never
    # actually created.
    render_onboarding_tree(pack_dir, key, payload, request_id=request_id)
