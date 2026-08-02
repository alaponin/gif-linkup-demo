"""apps/join-api/writer.py -- turning a validated JoinPayload into files on
disk that hurl/generate.py accepts (join-b Task 3, design spec S9). Nothing
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
                     configs/ manifest.yaml` is not clean (spec S9's
                     mitigation: a join must never stack on top of
                     uncommitted work of unclear provenance). Called by
                     app.py's POST /requests/{id}/approve, before the job
                     (job.py) starts.

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

from schema import JoinPayload

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
    so stderr is carried verbatim, not re-wrapped (task-3 brief step 3:
    "passing them through verbatim is more useful than wrapping them")."""

    def __init__(self, stderr: str, returncode: int):
        super().__init__(stderr)
        self.stderr = stderr
        self.returncode = returncode


class DirtyCheckoutError(Exception):
    """`git status --porcelain configs/ manifest.yaml` was not empty (spec
    S9) -- refuses to start a real-apply job on top of uncommitted,
    unattributable work."""


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
    plus the backend: and requested_access: blocks S2 adds. Deliberately
    omits type, forwarding URL, enabled, tls_verify -- generate.py never
    reads them and a copy here would drift (2.5.yaml's own comment)."""
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
            }
            for svc in payload.services
        ]
    if payload.semantic:
        body["semantic"] = {
            "entity": payload.semantic.entity,
            "key": payload.semantic.key,
            "fields": list(payload.semantic.fields),
        }
    body["backend"] = {"auth": payload.backend.auth.value}
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
    existing entries' style (manifest.yaml's identity.members.moeys etc.):
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


def _write_member(target_dir: pathlib.Path, key: str, payload: JoinPayload) -> None:
    """The one write-the-config-and-manifest-entry routine, shared by both
    dry_run_diff (target_dir is a temp copy) and apply_real (target_dir is
    the real pack_dir) -- only the directory, and whether a git-dirty check
    ran first, differs between the two callers."""
    member_dir = target_dir / "configs" / f"member-{key}"
    member_dir.mkdir(parents=True)  # not exist_ok: validate.py's collision
    # check (S8 check 3) already refuses a request whose key collides with
    # an existing configs/member-<key>/ -- a FileExistsError here means that
    # guarantee was violated somewhere upstream, and should be loud.
    (member_dir / f"{key}.yaml").write_text(render_member_config(key, payload))

    manifest_path = target_dir / "manifest.yaml"
    updated = _insert_manifest_entry(manifest_path.read_text(), render_manifest_entry(key, payload))
    manifest_path.write_text(updated)


def _run_generate(generate_py: pathlib.Path) -> subprocess.CompletedProcess:
    """python3 <generate_py>. cwd is the pack root the invoked file itself
    sits under -- matches `cd "$PACK_DIR" && python3 hurl/generate.py`, the
    pack's own invocation convention (scripts/member.sh). generate.py's own
    PACK resolution is `Path(__file__).resolve().parent.parent` (task-3
    brief point 4: --out only redirects where scenarios/ etc. get WRITTEN),
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
    rel = pack_dir.relative_to(repo_root)
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", str(rel / "configs"), str(rel / "manifest.yaml")],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def apply_real(
    pack_dir: pathlib.Path,
    key: str,
    payload: JoinPayload,
    *,
    repo_root: pathlib.Path | None = None,
) -> None:
    """The real write-then-regenerate sequence, against the real pack_dir --
    what actually makes the join real. repo_root defaults to three levels
    above pack_dir (docker-compose.yml: PACK_DIR is /repo/10-Knowledge-
    Products/KP2-GIF/KP2-build-pack, repo_root is /repo, the enclosing
    .git); overridable so tests can point it at a throwaway repo instead of
    relying on that exact nesting.

    Refuses (DirtyCheckoutError) before writing anything if `git status
    --porcelain configs/ manifest.yaml` is not clean (spec S9) -- a join
    must never stack on top of uncommitted work of unclear provenance.

    Called by app.py's POST /requests/{id}/approve, before the job starts --
    the point a request moves SUBMITTED -> APPROVED -> RUNNING.
    """
    repo_root = repo_root or pack_dir.resolve().parents[2]
    dirty = _git_status_dirty(repo_root, pack_dir)
    if dirty.strip():
        raise DirtyCheckoutError(
            "refusing to start a join job: configs/ or manifest.yaml already "
            f"has uncommitted changes (spec S9) -- commit or discard them "
            f"first:\n{dirty}"
        )
    _write_member(pack_dir, key, payload)
    proc = _run_generate(pack_dir / "hurl" / "generate.py")
    if proc.returncode != 0:
        raise GenerateFailure(proc.stderr, proc.returncode)
