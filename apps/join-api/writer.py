"""apps/join-api/writer.py -- turning a validated JoinPayload into files on
disk that hurl/generate.py accepts. Nothing
here talks to X-Road; nothing here decides whether a payload is admissible
(that is validate.py's job, already run before either function below is
called).

Two modes, sharing one write-then-regenerate core (_write_member /
_run_generate):

  dry_run_diff()  -- copies the whole pack to a throwaway temp directory,
                     writes the candidate config + manifest entry into THAT
                     COPY, runs the copy's own generate.py, and returns a
                     diff string. Used at submission ("the config
                     diff the join would write, computed at submission").
                     Never writes to, or reads mutable state from, the real
                     checkout -- it reads pack_dir exactly once, to seed the
                     copy, then only ever touches the copy.

  apply_real()     -- the same sequence, against the real pack_dir. Refuses
                     first (DirtyCheckoutError) if `git status --porcelain
                     configs/ manifest.yaml onboarding/` is not clean (a join
                     must never stack on top of
                     uncommitted work of unclear provenance). Called by
                     app.py's POST /requests/{id}/approve, before the job
                     (job.py) starts. Once generate.py accepts the result,
                     also renders onboarding/<key>/ --
                     render_onboarding_tree() is the same function
                     scripts/render-onboarding.sh calls for the three
                     canonical members.

Config-writing happens "on APPROVED, before
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

from schema import JoinPayload, MemberRequirements, SecurityServer, Semantic, Service

# Everything hurl/generate.py's main() reads via load()/discover_members()/
# TEMPLATES/read_env() (hurl/generate.py: PACK/HURL_DIR/ENV_PATH at the top,
# discover_members(), TEMPLATES, read_env()) -- configs/, manifest.yaml,
# deployment.yaml, the whole hurl/ tree (generate.py
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
    """`git status --porcelain configs/ manifest.yaml` was not empty --
    refuses to start a real-apply job on top of uncommitted,
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


_ROLLBACK_FAILED = (
    "the join for {key} failed AND restoring the pack failed -- this working tree "
    "needs a human: check configs/member-{key}/, manifest.yaml, onboarding/{key}/ and "
    "onboarding/catalogue.yaml against git, then re-run hurl/generate.py. "
    "Restore failed with:\n{detail}"
)


class RollbackFailure(Exception):
    """apply_real failed AND putting the tree back failed -- the only case
    left where a half-written join survives on disk. Carries the original
    failure as its __cause__ and names the paths a human has to look at:
    unlike every other error here, nothing downstream can clean up after
    this one."""


class MemberCollisionError(Exception):
    """A directory this join needs to create is already there. Two shapes,
    one refusal: configs/member-<key>/ created between validation and
    approval -- the race _write_member's own comment names as the only way
    its `mkdir(parents=True)` (not exist_ok) can raise FileExistsError; and
    a leftover onboarding/<key>/ that carries no RETIREMENT_FILE, so it is
    not a retired member re-joining (render_onboarding_tree replaces that
    one) but a tree this API did not write.
    validate.py's collision check already refused any request
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
    produces (see configs/member-pnia/pnia.yaml for the committed precedent),
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
                # Recorded and surfaced only, same treatment as
                # access above and semantic.pattern below -- never resolved
                # against anything; there is no lawful-basis registry in
                # this pack to check it against.
                **({"lawful_basis": svc.lawful_basis} if svc.lawful_basis else {}),
                # validate.py's sla_required check already guarantees
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
    # Module 5.2's checklist, required on every payload -- rendered
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


# -- onboarding/<key>/ --------------------------------------------------
#
# A handful of generated files per member, plus one per published service --
# not the onboarding path's ten (no
# curriculum change). Never hand-maintained: an
# absent file means the gate has not been passed, whatever the calendar
# says, so nothing here backfills a plausible-looking stub.

_GATES_TABLE = """\
# Onboarding gates

One row per gate: what this member has, and the file that proves it. An
absence named here is a gate that has not been passed, whatever the calendar
says -- naming it is the point, not an oversight.

Statuses are **implemented**, **simulated**, **named absence** and **out of
scope**, defined in `docs/path-conformance.md`. That file states the pack's
status as a whole; this one is this member's own record.
`docs/production-delta.md` describes what each absence and simulation below
would need in production.

| Gate | Exit test | Accountable | Status |
| --- | --- | --- | --- |
| Member Requirements (5.2) | Checklist stated by the applicant | Operating Authority | **implemented** -- [`02-requirements.md`](02-requirements.md) |
| SLA (5.3) | Signed SLA per published service | Operating Authority | {sla_status} |
| Registration (5.4) | Subsystem registered, ACL granted | Operating Authority | **implemented** -- [`05-registration.md`](05-registration.md) |
| Application (G0) | Application + signed membership agreement; Technical Focal Point and, where personal data flows, a DPO | Operating Authority | **named absence** -- not implemented in this demo |
| Admission (G1) | Minuted admission decision | Steering Committee | {admission_status} |
| Hosting (G2) | Own Security Server vs hosted as a client; hosting compatible with the member's role | Operating Authority | **implemented** -- [`05-registration.md`](05-registration.md)'s hosting row |
| Certificates (G3) | CA/TSA issuance record, member-verified | Operating Authority | **simulated** -- the Test CA signs any CSR presented, with no identity vetting; the own-server/hosted key asymmetry is real |
| Platform conformance (G4) | Add-ons installed; monitoring data arriving centrally | Operating Authority | **implemented** -- add-ons confirmed per server (`acceptance/member.md`); **named absence** for central monitoring collection |
| Service conformance (G5) | Contract, SLA and ACL registered; a live response carries exactly the fields the contract declares | Operating Authority | **implemented** -- SLA (above) and ACL ([`05-registration.md`](05-registration.md)) recorded here; contract and field conformance checked at join time, not copied into this record; catalogue entry per published service: {catalogue_status}; **named absence** for the tier-1 BB pattern register |
| Go-live (G6) | Monitored first production transactions | Operating Authority | **named absence** -- not implemented in this demo |
| Retirement (GX) | Absent everywhere; message-log records retained for the statutory period | Operating Authority | **simulated** -- `99-retirement.md` written at exit, and the absence asserted (`acceptance/join-member.md`); **named absence** for statutory message-log retention, which demo teardown does not meet |

`01-admission.md` is written by the join API at approval; canonical members
predate it, so their records begin at `02`.
"""

_ADMISSION_NOT_IMPLEMENTED = "**named absence** -- not implemented in this demo"
_ADMISSION_RECORDED = "**implemented** -- decided outside this system; reference recorded in [`01-admission.md`](01-admission.md)"


def render_gates_table(has_services: bool, *, admitted: bool = False) -> str:
    """00-gates.md -- one table, not four near-identical stub files: every
    gate KP2 teaches or exceeds, with the file that proves it or a named absence pointing at
    production-delta.md. Identical for every member except the SLA and
    catalogue rows (both of which turn on whether this member published
    anything at all) and the Admission row (`admitted`: True for a member that joined through this
    API and has a 01-admission.md; False -- including every canonical
    member, which never passed an admission -- keeps the named absence)."""
    sla_status = (
        "**implemented** -- [`03-sla/`](03-sla/)"
        if has_services
        else (
            "**out of scope** -- nothing published; the SLA template is "
            "written for providers, and a consumer-only member has none"
        )
    )
    catalogue_status = (
        "[`04-catalogue/`](04-catalogue/)"
        if has_services
        else "nothing published, so nothing to catalogue"
    )
    admission_status = _ADMISSION_RECORDED if admitted else _ADMISSION_NOT_IMPLEMENTED
    return _GATES_TABLE.format(
        sla_status=sla_status, catalogue_status=catalogue_status, admission_status=admission_status
    )


def _sanitize_cell(text: str) -> str:
    """Operator-supplied free text going into a markdown table cell -- strip
    newlines and escape pipes, exactly as scripts/render_path_conformance.py's
    _cell() already does. Without this, one pasted decision_reference or
    lawful_basis value breaks the record's structure."""
    return " ".join(text.split()).replace("|", "\\|")


def render_admission_record(request_id: str, decision_reference: str, approved_at: str) -> str:
    """01-admission.md -- G1's admission decision and allocated identity.
    The API already holds everything this needs at approve time -- request
    id, decision reference, approving role, timestamp -- this is the first
    place it gets written down. The admission decision itself (whether to
    admit) is taken outside this system (Ref Model §5.3's Steering
    Committee); this file records only the coupling already enforced at the
    API boundary: the technical join could not proceed without this
    reference."""
    return (
        "# Admission -- G1\n\n"
        "The admission decision itself is taken outside this system (Ref "
        "Model §5.3's Steering Committee) -- this file records only the "
        "coupling: the technical join could not proceed without this "
        "reference.\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| Join request id | `{request_id}` |\n"
        f"| Decision reference | {_sanitize_cell(decision_reference)} |\n"
        f"| Approved at | {approved_at} |\n"
        "| Approving role | operator |\n"
    )


def render_requirements_record(requirements: MemberRequirements) -> str:
    """02-requirements.md -- Module 5.2's six-item checklist, stated by the
    applicant, not derived."""
    lawful_basis = (
        _sanitize_cell(requirements.lawful_basis) if requirements.lawful_basis else
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
    the same template for every service on the bus".
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


# Fixed text on every catalogue artefact. The single most likely misreading
# of a service catalogue is that finding a service means being allowed to
# call it, so each one says otherwise on its own face rather than in a
# document the reader may never open.
PUBLICATION_IS_NOT_PERMISSION = (
    "This records what was published, not what you may call. Access is the "
    "provider's own access-control list; appearing here grants nothing."
)


def _request_line(request_id: str | None) -> str:
    """How a record names the join request behind it. None for a canonical
    member, registered by hand and never through the join API."""
    if request_id:
        return f"`{request_id}`"
    return "registered by hand (`prompts/register-member.md`) -- no join request"


def render_catalogue_entry(
    service: Service,
    *,
    service_id: str,
    provider: str,
    semantic: Semantic | None,
    semantic_anchor: str | None,
    request_id: str | None,
) -> str:
    """04-catalogue/<service-code>.md -- what this service is, published so
    that a body deciding whether to join can find it out without asking
    someone who already knows.

    Every value is derived from what the registration already produced; no
    field here is one a human typed into this file. Where a source is empty
    the row says so in words the reader can act on, because a blank cell and
    an unclassified service look identical and mean very different things.

    The SLA is linked rather than copied: 03-sla/<code>.md stays the one
    place an SLA is written, and this is the missing direction -- reachable
    from the service, not only from the member.
    """
    if semantic and semantic.entity:
        anchor = f" (anchor: {semantic_anchor})" if semantic_anchor else ""
        entity = f"`{semantic.entity}`{anchor}"
    else:
        entity = "*not declared*"
    pattern = (
        f"`{semantic.pattern.value}`"
        if semantic and semantic.pattern
        else (
            "*unclassified -- this service declares no exchange pattern, so "
            "it cannot be found by pattern*"
        )
    )
    return (
        f"# Catalogue entry -- {service.code}\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| Service code | `{service.code}` |\n"
        f"| X-Road service id | `{service_id}` |\n"
        f"| Provider | {provider} |\n"
        f"| Contract | `{service.spec_url}` |\n"
        # Deliberately not a copy of the contract's field list: the contract
        # above is the one source, and a snapshot taken when this file was
        # written would drift from it silently.
        "| Declared fields | *not copied -- read them from the contract "
        "above, which is not re-fetched when this entry is written* |\n"
        f"| Semantic entity (tier 2) | {entity} |\n"
        f"| Exchange pattern (tier 1) | {pattern} |\n"
        f"| Lawful basis | {_sanitize_cell(service.lawful_basis) if service.lawful_basis else '*not stated*'} |\n"
        f"| SLA | {f'[`../03-sla/{service.code}.md`](../03-sla/{service.code}.md)' if service.sla else '*not signed -- the SLA gate was not passed*'} |\n"
        f"| Access granted to | {', '.join(f'`{s}`' for s in service.access) if service.access else '*nobody -- no consumer has been granted access*'} |\n"
        f"| Registered by | {_request_line(request_id)} |\n\n"
        f"{PUBLICATION_IS_NOT_PERMISSION}\n"
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
        delegation_row = ""
    else:
        hosting = f"hosted on `{security_server.hosted_on}`"
        # G2's own warning is a delegation with no counterpart in the
        # obligation set: this member's SIGN key lives on the host's token,
        # not its own, and until this row existed that fact was recorded
        # nowhere a member would read.
        delegation_row = (
            f"| Signing key | held on `{security_server.hosted_on}`'s token, "
            "not this member's own (a hosting delegation) |\n"
        )
    acl = ", ".join(f"`{s}`" for s in acl_subjects) if acl_subjects else "none"
    request_line = _request_line(request_id)
    return (
        "# Registration -- Module 5.4\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| Subsystem | {subsystem} |\n"
        f"| Security Server | {security_server.code} (`{security_server.dns_name}`) |\n"
        f"| Hosting | {hosting} |\n"
        f"{delegation_row}"
        f"| ACL subjects granted | {acl} |\n"
        f"| Join request id | {request_line} |\n"
    )


# steps.py's own REVERSAL_ORDER states the sequence once; this sentence is
# fixed text, not a dynamic enumeration of it -- the two must be read
# together, not kept in sync field by field.
_RETIREMENT_REVERSAL_SENTENCE = (
    "the standard reversal was applied: service ACLs revoked, service "
    "descriptions deleted, client unregistered and deleted, signing key "
    "deleted, member removed from the Central Server (hurl/steps.py's "
    "REVERSAL_ORDER)"
)


# The filename is load-bearing in two places now: app.py writes it at exit,
# and render_onboarding_tree reads it back to tell a retired member's tree
# (replace it) from a leftover this API did not write (refuse).
RETIREMENT_FILE = "99-retirement.md"


def render_retirement_record(key: str, retired_at: str, request_id: str) -> str:
    """99-retirement.md -- written only at exit. Written by
    apps/join-api/app.py's DELETE /members/{key} handler once job.unjoin()
    has reached RETIRED -- that module already imports this one and already
    performs the federation-side retirement, so it writes the record;
    scripts/member.sh remove is config removal only and neither writes nor
    destroys it. Four lines of content, no more: retired-at, the request id,
    the fixed reversal sentence, and a pointer naming message-log retention
    as a SEPARATE question this file does not answer (the archive volume's
    own retention policy, not asserted here)."""
    return (
        "# Retirement -- GX\n\n"
        f"Retired at: {retired_at}\n\n"
        f"Join request id: `{request_id}`\n\n"
        f"Reversal: {_RETIREMENT_REVERSAL_SENTENCE}.\n\n"
        "Message-log retention is a separate question, governed by the "
        "archive volume's own retention policy -- not answered by this file.\n"
    )


def _read_identifiers(target_dir: pathlib.Path) -> tuple[str, str]:
    """The X-Road instance and member class, each read from the file that
    owns it -- the generator reads the same two values from the same two
    files, and a constant here would be a second copy free to drift from
    them."""
    manifest = yaml.safe_load((target_dir / "manifest.yaml").read_text())
    policy = yaml.safe_load((target_dir / "configs" / "x-road-bus" / "join-policy.yaml").read_text())
    return manifest["identity"]["instance"], policy["join"]["member_class"]


def _semantic_anchor(target_dir: pathlib.Path, entity: str | None) -> str | None:
    """The standard a semantic entity is anchored in, per the semantic map.
    None when the member declared no entity, or declared one the map does
    not carry -- the caller renders that absence rather than a blank."""
    if not entity:
        return None
    doc = yaml.safe_load((target_dir / "configs" / "semantic" / "semantic-map.yaml").read_text()) or {}
    return (doc.get(entity) or {}).get("anchor")


def render_onboarding_tree(
    target_dir: pathlib.Path,
    key: str,
    payload: JoinPayload,
    *,
    request_id: str | None = None,
    decision_reference: str | None = None,
    approved_at: str | None = None,
) -> None:
    """Writes onboarding/<key>/'s files under target_dir. Shared by
    apply_real() (a real join) and scripts/render-onboarding.sh (the three
    canonical members) -- "the same writer.py code path a
    join uses" for both, so there is exactly one place that decides what an
    onboarding record looks like.

    01-admission.md is written only when request_id is not None: the
    three canonical members never passed an admission, and
    writing them one would be fiction. apply_real() is the only real caller
    that ever passes request_id, and it always passes decision_reference/
    approved_at alongside it."""
    onboarding_dir = target_dir / "onboarding" / key
    if (onboarding_dir / RETIREMENT_FILE).exists():
        # A retired member re-joining. An un-join deliberately leaves
        # onboarding/<key>/ behind -- app.py writes 99-retirement.md INTO it,
        # it *is* the retirement record -- so the pack's own exercise loop
        # (join, un-join, join the same member again) lands here, and a bare
        # mkdir made it FileExistsError after apply_real had already written
        # configs/ and manifest.yaml, wedging every later join behind the
        # dirty-checkout guard (docs/production-delta.md recorded it).
        # Semantics: replace, do not merge. The old tree describes a
        # membership that has ended; keeping its 99-retirement.md beside the
        # new record would render a member that is both active and retired.
        # Same call scripts/render_onboarding.py already makes to re-render a
        # canonical member's tree, for the same reason.
        shutil.rmtree(onboarding_dir)
    onboarding_dir.mkdir(parents=True)
    (onboarding_dir / "00-gates.md").write_text(
        render_gates_table(bool(payload.services), admitted=request_id is not None)
    )
    (onboarding_dir / "02-requirements.md").write_text(render_requirements_record(payload.member_requirements))
    if request_id is not None:
        assert decision_reference is not None and approved_at is not None, (
            "render_onboarding_tree: request_id given but decision_reference/"
            "approved_at missing -- every real caller must pass both"
        )
        (onboarding_dir / "01-admission.md").write_text(
            render_admission_record(request_id, decision_reference, approved_at)
        )
    if payload.services:
        sla_dir = onboarding_dir / "03-sla"
        sla_dir.mkdir()
        catalogue_dir = onboarding_dir / "04-catalogue"
        catalogue_dir.mkdir()
        instance, member_class = _read_identifiers(target_dir)
        anchor = _semantic_anchor(target_dir, payload.semantic.entity if payload.semantic else None)
        for svc in payload.services:
            (sla_dir / f"{svc.code}.md").write_text(render_sla_record(svc))
            (catalogue_dir / f"{svc.code}.md").write_text(
                render_catalogue_entry(
                    svc,
                    service_id=f"{instance}/{member_class}/{payload.code}/{payload.subsystem}/{svc.code}",
                    provider=f"{payload.name} ({payload.code})",
                    semantic=payload.semantic,
                    semantic_anchor=anchor,
                    request_id=request_id,
                )
            )
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


# -- onboarding/catalogue.yaml -------------------------------------------------
#
# One file for the whole instance, regenerated wholesale from the member
# configs every time and never appended to. Two things follow from that, and
# both are the reason it is built this way: an un-join needs no delete path
# (the next regeneration simply does not find the member's config), and the
# file cannot drift from the register, because regenerating it IS the
# register.

CATALOGUE_PATH = pathlib.PurePosixPath("onboarding/catalogue.yaml")

_CATALOGUE_HEADER = (
    "# Generated -- do not hand-edit. Regenerate: scripts/render-onboarding.sh\n"
)


def catalogue_data(pack_dir: pathlib.Path) -> dict:
    """Every service published on this instance, derived from manifest.yaml
    and configs/member-*/ -- the register's own inputs, not the onboarding
    tree this same module writes. Deriving it from those records instead
    would check the generator against itself and prove nothing.

    Sorted by service id, so regenerating from unchanged inputs produces the
    same bytes. Absent values are present as null rather than as a missing
    key: a service with no pattern and a service whose pattern was dropped
    on the way in must not look the same.
    """
    pack_dir = pathlib.Path(pack_dir)
    instance, member_class = _read_identifiers(pack_dir)
    members = (yaml.safe_load((pack_dir / "manifest.yaml").read_text())["identity"]["members"]) or {}

    services = []
    for member_dir in sorted((pack_dir / "configs").glob("member-*")):
        key = member_dir.name.removeprefix("member-")
        identity = members.get(key)
        if identity is None:
            continue  # no manifest entry: hurl/generate.py refuses this state already
        config_files = sorted(member_dir.glob("*.yaml"))
        cfg = yaml.safe_load(config_files[0].read_text()) if config_files else None
        semantic = (cfg or {}).get("semantic") or {}
        for svc in (cfg or {}).get("services") or []:
            code = svc["code"]
            services.append({
                "id": f"{instance}/{member_class}/{identity['code']}/{identity['subsystem']}/{code}",
                "provider": {"key": key, "code": identity["code"], "name": identity["name"]},
                "service_code": code,
                "contract": svc.get("spec_url"),
                "semantic": {
                    "entity": semantic.get("entity"),
                    "anchor": _semantic_anchor(pack_dir, semantic.get("entity")),
                },
                "pattern": semantic.get("pattern"),
                "lawful_basis": svc.get("lawful_basis"),
                "sla": f"onboarding/{key}/03-sla/{code}.md" if svc.get("sla") else None,
                "access": list(svc.get("access") or []),
                "entry": f"onboarding/{key}/04-catalogue/{code}.md",
            })
    services.sort(key=lambda s: s["id"])

    return {
        "instance": instance,
        "generated_from": "manifest.yaml + configs/member-*/",
        "publication_is_not_permission": PUBLICATION_IS_NOT_PERMISSION,
        "services": services,
    }


def render_catalogue(pack_dir: pathlib.Path) -> str:
    return _CATALOGUE_HEADER + yaml.safe_dump(
        catalogue_data(pack_dir), sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def write_catalogue(pack_dir: pathlib.Path) -> pathlib.Path:
    """The one writer of onboarding/catalogue.yaml, called by everything that
    changes the member set: a canonical re-render, a real join, and an
    un-join once the member is gone. Always last, after the member's own
    tree -- a failed join must never leave a catalogue naming a member that
    does not exist."""
    path = pathlib.Path(pack_dir) / CATALOGUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_catalogue(pack_dir))
    return path


def _write_member(target_dir: pathlib.Path, key: str, payload: JoinPayload) -> None:
    """The one write-the-config-and-manifest-entry routine, shared by both
    dry_run_diff (target_dir is a temp copy) and apply_real (target_dir is
    the real pack_dir) -- only the directory, and whether a git-dirty check
    ran first, differs between the two callers."""
    member_dir = target_dir / "configs" / f"member-{key}"
    member_dir.mkdir(parents=True)  # not exist_ok: validate.py's collision
    # check already refuses a request whose key collides with
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


def _written_paths(key: str) -> tuple[pathlib.PurePosixPath, ...]:
    """Every path in the pack a single join creates or rewrites -- and so
    every path a rolled-back join has to put back. hurl/'s generated files
    (scenarios/, vars.env, topology.json, topology.sh, compose.members.yml)
    are deliberately NOT here: they are derived from the two below it, never
    edited, so a rollback re-derives them by running generate.py again
    rather than carrying a copy of them around."""
    return (
        pathlib.PurePosixPath("manifest.yaml"),
        CATALOGUE_PATH,
        pathlib.PurePosixPath(f"configs/member-{key}"),
        pathlib.PurePosixPath(f"onboarding/{key}"),
    )


def _snapshot(pack_dir: pathlib.Path, key: str, stash: pathlib.Path) -> dict:
    """Pre-image of the four paths, taken before the first write. A path
    that does not exist yet records None -- restoring it means deleting
    whatever the failed join left there, not writing an empty file.

    Deliberately NOT git: `git checkout`/`git clean` would put a delete
    inside the join-api container, whose whole justification for
    `safe.directory = *` (apps/join-api/Dockerfile) is that every git call
    it makes is a read. A copy under /tmp needs no such capability, and
    works the same in a checkout, on the droplet and in a bare copy."""
    saved: dict = {}
    for rel in _written_paths(key):
        src = pack_dir / rel
        if not src.exists():
            saved[rel] = None
            continue
        dest = stash / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        saved[rel] = dest
    return saved


def _restore(pack_dir: pathlib.Path, saved: dict) -> None:
    """Put every snapshotted path back exactly as it was. Delete-then-copy,
    not overwrite: a directory that gained files (onboarding/<key>/'s
    03-sla/, 04-catalogue/) is only back to its pre-image once the extra
    files are gone."""
    for rel, dest in saved.items():
        target = pack_dir / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if dest is None:
            continue
        if dest.is_dir():
            shutil.copytree(dest, target)
        else:
            shutil.copy2(dest, target)


def _git_status(repo_root: pathlib.Path, pack_dir: pathlib.Path, rel_paths: tuple[str, ...]) -> str:
    """The read `git status --porcelain <paths>` shared by _git_status_dirty
    (apply_real's whole-tree pre-write refusal) and member_git_status_dirty
    (job.py's config.commit gate, scoped to one member's own paths) -- same
    subprocess call and the same GitCheckFailure contract, only `rel_paths`
    differs."""
    try:
        rel = pack_dir.relative_to(repo_root)
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain",
             *(str(rel / p) for p in rel_paths)],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        # ValueError: pack_dir is not under repo_root. CalledProcessError:
        # repo_root is not a git repo (or some other structural git failure).
        # OSError: git itself is missing. None of these mean "clean" --
        # the caller must refuse exactly as it would for a genuinely dirty
        # checkout, not silently proceed.
        raise GitCheckFailure(f"could not check whether {pack_dir} is a clean checkout: {exc}") from exc
    return proc.stdout


def _git_status_dirty(repo_root: pathlib.Path, pack_dir: pathlib.Path) -> str:
    # The live-but-uncommitted window production-delta.md documents covers a
    # third tree: apply_real() writes onboarding/<key>/ too, so the
    # refusal-when-dirty check must watch it exactly like configs/ and
    # manifest.yaml, not just the other two trees.
    return _git_status(repo_root, pack_dir, ("configs", "manifest.yaml", "onboarding"))


def member_git_status_dirty(repo_root: pathlib.Path, pack_dir: pathlib.Path, key: str) -> str:
    """Same read as _git_status_dirty, scoped to one member's own paths
    (configs/member-<key>/, manifest.yaml, onboarding/<key>/) rather than the
    whole configs/ and onboarding/ trees. apps/join-api/job.py's
    config.commit gate (join_workflow.commit_gate: required,
    docs/production-delta.md row 33) reuses this to ask "did THIS join's own
    writes get committed" -- a narrower question than apply_real's pre-write
    refusal above, which guards the window's OTHER edge (starting a new job
    on top of someone else's uncommitted work, whole-tree). Public (no
    leading underscore): job.py, not writer.py's own apply_real, is the
    caller."""
    return _git_status(repo_root, pack_dir, (f"configs/member-{key}", "manifest.yaml", f"onboarding/{key}"))


def apply_real(
    pack_dir: pathlib.Path,
    key: str,
    payload: JoinPayload,
    *,
    repo_root: pathlib.Path | None = None,
    request_id: str | None = None,
    decision_reference: str | None = None,
    approved_at: str | None = None,
) -> None:
    """The real write-then-regenerate sequence, against the real pack_dir --
    what actually makes the join real. repo_root defaults to three levels
    above pack_dir (docker-compose.yml: PACK_DIR is /repo/10-Knowledge-
    Products/KP2-GIF/KP2-build-pack, repo_root is /repo, the enclosing
    .git); overridable so tests can point it at a throwaway repo instead of
    relying on that exact nesting.

    Refuses (DirtyCheckoutError) before writing anything if `git status
    --porcelain configs/ manifest.yaml onboarding/` is not clean --
    a join must never stack on top of uncommitted work of unclear provenance.

    All-or-nothing: every path a join writes is snapshotted first
    (_snapshot) and restored on any failure, with hurl/'s derived files
    regenerated over the restored inputs. A caller that catches any error
    from here can assume the pack is exactly as it was -- except
    RollbackFailure, which is the one case where it is not.

    Called by app.py's POST /requests/{id}/approve, before the job starts --
    the point a request moves SUBMITTED -> APPROVED -> RUNNING. request_id is
    the approved request's own id, threaded through to
    onboarding/<key>/05-registration.md's "join request id" field.
    decision_reference/approved_at, when request_id is given, must be passed
    in by the caller rather than read off the request record afterwards --
    app.py's approve endpoint computes both BEFORE calling this function and
    only assigns them onto the record after it returns, so reading them off
    the record here would write 01-admission.md with them still empty.
    """
    repo_root = repo_root or pack_dir.resolve().parents[2]
    dirty = _git_status_dirty(repo_root, pack_dir)
    if dirty.strip():
        raise DirtyCheckoutError(
            "refusing to start a join job: configs/, manifest.yaml or "
            f"onboarding/ already has uncommitted changes -- commit "
            f"or discard them first:\n{dirty}"
        )
    # Transactional from here: everything below either lands whole or is put
    # back. apply_real is a sequence of writes with three failure points after
    # the first one (generate.py rejecting the config, the onboarding tree,
    # the catalogue), and a failure at any of them used to leave configs/ and
    # manifest.yaml modified -- which then blocked every later join on the
    # dirty-checkout guard above, for a join that never happened
    # (docs/production-delta.md). The pre-image is a copy, not a git
    # operation: see _snapshot.
    #
    # ponytail: file-level compensation, not a real transaction -- a crash
    # BETWEEN the restore and the regenerate below still leaves a stale hurl/.
    # The upgrade path is one transactional write of the whole pack tree,
    # which the live bind mounts (docker-compose.yml) rule out today.
    stash = pathlib.Path(tempfile.mkdtemp(prefix="kp2-join-apply-"))
    try:
        saved = _snapshot(pack_dir, key, stash)
        try:
            try:
                _write_member(pack_dir, key, payload)
            except FileExistsError as exc:
                raise MemberCollisionError(
                    f"configs/member-{key}/ already exists -- another request for the same key "
                    "was approved between this request's validation and its approval"
                ) from exc
            proc = _run_generate(pack_dir / "hurl" / "generate.py")
            if proc.returncode != 0:
                raise GenerateFailure(proc.stderr, proc.returncode)
            # Only after generate.py accepts the result -- a rejected/failed
            # config write must not leave onboarding evidence for a member
            # that was never actually created.
            try:
                render_onboarding_tree(
                    pack_dir, key, payload,
                    request_id=request_id,
                    decision_reference=decision_reference,
                    approved_at=approved_at,
                )
            except FileExistsError as exc:
                # A leftover onboarding/<key>/ that is NOT a retired member's
                # (that case is replaced inside render_onboarding_tree) -- so
                # something created the directory outside this API. A clear
                # 409 naming it, not a raw 500. Everything this call had
                # already written is rolled back below, so the message no
                # longer has to hand out recovery commands.
                raise MemberCollisionError(
                    f"onboarding/{key}/ already exists and carries no {RETIREMENT_FILE} -- "
                    "refusing to overwrite a record this API did not write. Nothing was left "
                    "behind: remove or rename that directory, then approve again."
                ) from exc
            # Last: the instance-wide catalogue is the first SHARED file a
            # join touches, and it must never name a member whose own record
            # failed to write.
            write_catalogue(pack_dir)
        except BaseException as exc:
            try:
                _restore(pack_dir, saved)
                # hurl/'s generated files are derived, never snapshotted, so
                # after a restore they still describe the member that just
                # failed. Re-run generate.py over the restored inputs:
                # acceptance.sh, member.sh and join-agent.sh all read
                # hurl/topology.json, and a stale one is worse than the
                # half-write this undoes.
                regenerated = _run_generate(pack_dir / "hurl" / "generate.py")
            except Exception as undo_exc:
                raise RollbackFailure(
                    _ROLLBACK_FAILED.format(
                        key=key, detail=f"{type(undo_exc).__name__}: {undo_exc}"
                    )
                ) from exc
            if regenerated.returncode != 0:
                # The four tracked paths ARE back -- what failed is rebuilding
                # the derived files FROM them, so generate.py rejects a state
                # that pre-dates this join (an empty configs/member-<key>/,
                # say). Not a rollback failure, and not this join's doing:
                # note it on the original error rather than replacing it with
                # a scarier one. stderr is left out on purpose -- generate.py
                # reads .env, so its output can carry the token PIN, and a
                # note travels into logs no caller scrubs (job.scrub).
                exc.add_note(
                    f"rolled back cleanly, but `python3 hurl/generate.py` then exited "
                    f"{regenerated.returncode} over the RESTORED inputs -- the pack was "
                    "already in a state generate.py rejects. Run it by hand to see why; "
                    "hurl/ is stale until it passes."
                )
            raise
    finally:
        shutil.rmtree(stash, ignore_errors=True)
