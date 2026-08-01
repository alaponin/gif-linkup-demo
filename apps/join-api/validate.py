"""apps/join-api/validate.py -- the twelve checks spec S8 requires before a
join request can be approved (join-b Task 2). Pure functions over a payload,
the manifest, the join policy and a fetched OpenAPI document -- no X-Road,
no containers, no job. Checks run in the exact order spec S8 lists them
(1 schema .. 12 identifier characters); the first failure raises
RejectionError(check, message) naming the check, which is what a REJECTED
request carries (spec S4).

Two things this module deliberately does NOT do, both on purpose:
  - it never sets origin. schema.JoinPayload has no such field; wherever a
    validated payload becomes a manifest.yaml identity.members entry (a
    later task, spec S9), origin: joined is forced there, not here.
  - it never touches a canonical member. check 4 (not_canonical) reads the
    canonical set from manifest.yaml itself (identity.members' origin:
    canonical entries, plus identity.owner.code) rather than hardcoding the
    five codes -- the same "nothing hardcodes the member set" discipline
    hurl/generate.py's discover_members() already holds to.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Callable

import httpx
import pydantic
import yaml

from schema import BackendAuth, JoinPayload

# -- disk loader --------------------------------------------------------------
# The one piece of "existing federation state" the checks need that manifest.
# yaml does not carry: every existing member's Security Server code/dns_name/
# hosted_on. Read the same way hurl/generate.py's discover_members() finds
# member directories (hurl/generate.py ~line 191) -- collision (check 3) and
# hosting (check 6) are the only checks that need it, so this loads only the
# security_server block, not a full member config parse.


def load_existing_security_servers(pack_dir: str | pathlib.Path) -> dict[str, dict]:
    pack_dir = pathlib.Path(pack_dir)
    result: dict[str, dict] = {}
    for member_dir in sorted((pack_dir / "configs").glob("member-*")):
        if not member_dir.is_dir():
            continue
        key = member_dir.name.removeprefix("member-")
        yaml_files = sorted(member_dir.glob("*.yaml"))
        if not yaml_files:
            continue
        cfg = yaml.safe_load(yaml_files[0].read_text()) or {}
        ss = cfg.get("security_server") or {}
        result[key] = {
            "code": ss.get("code"),
            "dns_name": ss.get("dns_name"),
            "hosted_on": ss.get("hosted_on"),
        }
    return result


# -- backend I/O ---------------------------------------------------------------
# Two separate callables, both overridable (apps/join-api/tests inject fakes):
# fetch_spec reads the OpenAPI document text from spec_url; check_reachable
# attempts to resolve-and-connect to a backend URL, raising on failure. Kept
# separate rather than one URL-dispatched callable because production fetches
# spec_url over the network too (a third-party spec, spec S2.2) -- only the
# *test* fixtures short-circuit that half; the reachability half must always
# be a real attempt (spec S8 check 9, task-2 brief point 6).


def _default_fetch_spec(url: str) -> str:
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return resp.text


def _default_check_reachable(url: str) -> None:
    # Any response -- even a 404 -- proves the TCP/TLS handshake and HTTP
    # exchange succeeded, which is what "resolve and connect to it" (spec
    # S2.4) asks for. No raise_for_status(): endpoint correctness is not
    # this check's job, only reachability.
    httpx.get(url, timeout=5.0)


@dataclasses.dataclass
class ValidationContext:
    payload: JoinPayload
    manifest: dict
    policy: dict  # configs/x-road-bus/2.7.yaml's join: block only
    existing_servers: dict[str, dict]  # key -> {code, dns_name, hosted_on}
    fetch_spec: Callable[[str], str] = _default_fetch_spec
    check_reachable: Callable[[str], None] = _default_check_reachable
    # Populated by check 9, consumed by check 10 -- avoids fetching the same
    # spec twice for services that pass check 9.
    fetched_specs: dict[str, dict] = dataclasses.field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.payload.code.lower()


class RejectionError(Exception):
    """Carries the specific check name spec S4's REJECTED state records."""

    def __init__(self, check: str, message: str):
        super().__init__(f"{check}: {message}")
        self.check = check
        self.message = message


# -- checks 2-8, 12: payload/manifest/policy only, no network ----------------


def _check_key_derivation(ctx: ValidationContext) -> str | None:
    """key == code.lower() (spec S8 check 2) -- discover_members()
    (hurl/generate.py ~line 191) already enforces exactly this agreement
    between a config directory's key and its identity.members entry, and
    fails loudly at generate time otherwise. Enforced here too, at request
    time, against the stricter constraint the key must actually satisfy:
    configs/member-<key>/ becomes a directory name and
    hurl/check_scenarios.py's scenario_member_re expects [a-z0-9]+."""
    key = ctx.key
    if not re.fullmatch(r"[a-z0-9]+", key):
        return (
            f"code {ctx.payload.code!r} lowers to key {key!r}, which is not a "
            "valid member key ([a-z0-9]+ only) -- this becomes the "
            "configs/member-<key>/ directory name and manifest.yaml's "
            "identity.members.<key> map key"
        )
    return None


def _check_collision(ctx: ValidationContext) -> str | None:
    """No existing configs/member-<key>/, no existing identity.members.<key>,
    no existing member's Security Server dns_name or code already equal to
    this payload's proposed values (spec S8 check 3)."""
    key = ctx.key
    if key in ctx.existing_servers:
        return f"configs/member-{key}/ already exists"
    if key in ctx.manifest["identity"]["members"]:
        return f"manifest.yaml identity.members.{key} already exists"
    proposed_dns = ctx.payload.security_server.dns_name
    proposed_code = ctx.payload.security_server.code
    for existing_key, ss in ctx.existing_servers.items():
        if ss["dns_name"] == proposed_dns:
            return (
                f"security_server.dns_name {proposed_dns!r} is already used "
                f"by member {existing_key!r}"
            )
        if ss["code"] == proposed_code:
            return (
                f"security_server.code {proposed_code!r} is already used by "
                f"member {existing_key!r}"
            )
    return None


def _check_not_canonical(ctx: ValidationContext) -> str | None:
    """The key is not one of the frozen five (spec S8 check 4). Derived from
    manifest.yaml, not hardcoded: every identity.members entry whose origin
    is canonical (default, if absent) PLUS identity.owner.code -- the
    Central Server's owner, PDGA, which is not itself an identity.members
    entry (task-2 brief point 4)."""
    identity = ctx.manifest["identity"]
    canonical = {identity["owner"]["code"]}
    for member in identity["members"].values():
        if member.get("origin", "canonical") == "canonical":
            canonical.add(member["code"])
    if ctx.payload.code.upper() in canonical:
        return (
            f"{ctx.payload.code!r} is one of the frozen canonical members "
            f"({sorted(canonical)}) -- canonical members are deployed by "
            "hurl/run-linkup.sh, never by this API"
        )
    return None


# Spec S8 check 5 ("member class -- matches the policy") has no per-request
# implementation here, on purpose (join-b Task 2 review finding 2). The
# payload schema (schema.py) carries no member_class field of its own --
# Progressa is a single-member_class federation by design (spec S9's
# "Explicitly out of scope" excludes multi-federation joins) -- so there is
# currently nothing about a *submitted request* for a check 5 to compare.
# The one real assertion that name suggested -- join.member_class must
# agree with manifest.yaml's identity.member_class -- is a static
# operator-misconfiguration check, not a fact about any given join, and now
# lives at generate time instead: hurl/generate.py's check_join_policy().
# If the payload schema ever grows a field this check could legitimately
# evaluate per-request (e.g. multi-class support), reintroduce check 5 here
# against that field -- don't resurrect the old version, which compared two
# static config files and could never fail differently for two different
# payloads.


def _check_hosting(ctx: ValidationContext) -> str | None:
    """Reproduces resolve_hosted_on_map()'s (hurl/generate.py ~line 243) two
    hard failures at request time -- unknown host, hosting chain -- plus
    Plan B's own hosted-only constraint (spec S8 check 6, task-2 brief
    point 5): join.default_hosting: hosted_on means an absent hosted_on is
    rejected outright, not silently treated as an own-server request Plan B
    has no code path for (that's Plan C)."""
    hosted_on = ctx.payload.security_server.hosted_on
    if ctx.policy.get("default_hosting") == "hosted_on" and not hosted_on:
        return (
            "security_server.hosted_on is required -- configs/x-road-bus/"
            "2.7.yaml sets join.default_hosting: hosted_on and this API "
            "supports hosted joins only; an own Security Server is Plan C, "
            "out of scope here"
        )
    if not hosted_on:
        return None
    dns_to_key = {ss["dns_name"]: key for key, ss in ctx.existing_servers.items()}
    host_key = dns_to_key.get(hosted_on)
    if host_key is None:
        valid = ", ".join(sorted(dns_to_key)) or "(none)"
        return (
            f"security_server.hosted_on {hosted_on!r} names no existing "
            f"member's Security Server. Valid hosts: {valid}"
        )
    if ctx.existing_servers[host_key].get("hosted_on"):
        return (
            f"security_server.hosted_on {hosted_on!r} is itself hosted on "
            f"another server ({host_key!r}) -- hosting chains are not "
            "supported"
        )
    return None


def _check_acl_sanity(ctx: ValidationContext) -> str | None:
    """Every access: subject and every requested_access: target resolves to
    a subsystem that exists in manifest.yaml's identity.members, in
    <instance>/<member_class>/<CODE>/<SUBSYSTEM> form (spec S8 check 7).
    The subsystem this join is itself creating is not yet in that set and is
    not a valid target either -- a member cannot grant or request access to
    itself (task-2 brief point 4)."""
    identity = ctx.manifest["identity"]
    prefix = f"{identity['instance']}/{identity['member_class']}"
    valid_subsystems = {
        f"{prefix}/{m['code']}/{m['subsystem']}" for m in identity["members"].values()
    }
    own_subsystem = f"{prefix}/{ctx.payload.code.upper()}/{ctx.payload.subsystem.upper()}"

    targets: list[tuple[str, str]] = []
    for svc in ctx.payload.services:
        for subject in svc.access:
            targets.append((f"services[{svc.code}].access", subject))
    for subject in ctx.payload.requested_access:
        targets.append(("requested_access", subject))

    for field, target in targets:
        if target == own_subsystem:
            return f"{field} names {target!r} -- a member cannot grant or request access to itself"
        if target not in valid_subsystems:
            return (
                f"{field} names {target!r}, which is not a subsystem in "
                "manifest.yaml identity.members"
            )
    return None


def _check_purpose_limitation(ctx: ValidationContext) -> str | None:
    """Presence/non-empty check only, not a semantic judgement (spec S8
    check 8, task-2 brief point 8). Publishing a service AND granting
    another subsystem access to it is an exchange -- this pack's own
    convention for one (configs/member-pnia/2.5.yaml's semantic: block) is
    to document it. This check enforces that the joining payload does the
    same when it creates an exchange; it cannot judge whether the field list
    is the legally correct one for the stated purpose -- that is the human
    operator's job at approval."""
    publishes_with_access = any(svc.access for svc in ctx.payload.services)
    if publishes_with_access and not (ctx.payload.semantic and ctx.payload.semantic.fields):
        return (
            "this join publishes a service and grants another subsystem "
            "access to it, which makes it a provenance-tracked exchange -- a "
            "semantic: block (entity, key, fields) is required, matching "
            "this pack's existing convention (configs/member-pnia/2.5.yaml)"
        )
    return None


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _check_allowed_methods(ctx: ValidationContext) -> str | None:
    """No operation outside join.allowed_methods (spec S8 check 10, S2.3).
    Operates on the spec check 9 already fetched -- fetching twice would be
    a second, driftable read of the same document."""
    allowed = {m.upper() for m in (ctx.policy.get("allowed_methods") or [])}
    for svc in ctx.payload.services:
        spec_doc = ctx.fetched_specs.get(svc.code)
        if spec_doc is None:
            continue
        for path, operations in (spec_doc.get("paths") or {}).items():
            for method in operations or {}:
                if method.lower() not in _HTTP_METHODS:
                    continue
                if method.upper() not in allowed:
                    return (
                        f"service {svc.code!r}'s OpenAPI spec declares "
                        f"{method.upper()} {path}, outside join.allowed_methods "
                        f"{sorted(allowed)} (configs/x-road-bus/2.7.yaml) -- a "
                        "joined member may publish read-only services (spec S2.3)"
                    )
    return None


def _check_backend_auth_declared(ctx: ValidationContext) -> str | None:
    """backend.auth is present and one of the values the payload schema
    permits (spec S8 check 11, S2.5). schema.JoinPayload already requires
    Backend and validates the enum at parse time (check 1); this check
    exists as the explicit, named enforcement spec S8 lists as its own
    numbered item, not a second, looser copy of that validation."""
    backend = ctx.payload.backend
    if backend is None or not isinstance(backend.auth, BackendAuth):
        return "backend.auth must be one of: none, network_allowlist, proxy_injected"
    return None


# X-Road identifier separators this pack has already been bitten by adjacent
# characters in (hurl/generate.py's dn_escape() docstring: a comma in a
# member_name broke DN construction) -- code/subsystem/service-code are more
# restrictive fields than member_name, so reject the characters X-Road's own
# REST message protocol uses to separate identifier components, plus
# whitespace and control characters, rather than wait for one of these to
# break something downstream that looks like an unrelated failure. Design
# spec S8 check 12 names spaces, dots and slashes as the plausible source
# ("a service code copied from a third-party tool's human-facing API name")
# -- '.' is included here for exactly that reason (join-b Task 2 review
# finding 1: it was missing, and a dotted service code like "awards.list"
# passed every check without it).
_BAD_CHARS = frozenset("/:;%.")


def _bad_identifier(value: str) -> bool:
    if not value or not value.strip():
        return True
    return any(c in _BAD_CHARS or c.isspace() or ord(c) < 0x20 for c in value)


def _check_identifier_characters(ctx: ValidationContext) -> str | None:
    """code, subsystem, and every service code satisfy X-Road's identifier
    restrictions (spec S8 check 12). Reject empty, whitespace, and the
    characters X-Road uses as identifier separators (/, :, ;, %, .) and
    control characters -- named in the rejection message, per spec, rather
    than discovered later inside certificate signing."""
    candidates = [("code", ctx.payload.code), ("subsystem", ctx.payload.subsystem)]
    for svc in ctx.payload.services:
        candidates.append(("services[].code", svc.code))
    for label, value in candidates:
        if _bad_identifier(value):
            return (
                f"{label} {value!r} is not a valid X-Road identifier -- "
                "identifiers must be non-empty, contain no whitespace or "
                "control characters, and must not contain '/', ':', ';', "
                "'%' or '.' (X-Road: Message Protocol for REST)"
            )
    return None


# -- checks 9-11: touch the joining member's backend --------------------------


def _check_backend_reachability(ctx: ValidationContext) -> str | None:
    """Fetch spec_url, parse servers.url, resolve-and-connect to it from
    inside the linkup network (spec S8 check 9, S2.4). Catches the
    registry-perfect-but-dead member: a spec that fetches fine but whose
    servers.url the Security Server can never reach. Rejection names the URL
    it could not reach."""
    for svc in ctx.payload.services:
        try:
            text = ctx.fetch_spec(svc.spec_url)
            spec_doc = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 -- any fetch/parse failure is a rejection
            return (
                f"could not fetch OpenAPI spec for service {svc.code!r} at "
                f"{svc.spec_url}: {exc}"
            )
        servers = (spec_doc or {}).get("servers") or []
        backend_url = servers[0].get("url") if servers else None
        if not backend_url:
            return (
                f"OpenAPI spec for service {svc.code!r} at {svc.spec_url} "
                "declares no servers[].url"
            )
        try:
            ctx.check_reachable(backend_url)
        except Exception as exc:  # noqa: BLE001 -- any connect failure is a rejection
            return (
                f"service {svc.code!r}'s backend at {backend_url} "
                f"(servers[].url in {svc.spec_url}) is not reachable from "
                f"inside the linkup network: {exc}"
            )
        ctx.fetched_specs[svc.code] = spec_doc
    return None


# Checks run in this exact order -- spec S8's own numbered list, verbatim,
# minus check 5 (member_class), which has no per-request implementation --
# see the comment above where _check_member_class used to be. Check 1
# (schema) happens in validate() itself, before a ValidationContext can even
# be built.
_CHECKS: list[tuple[str, Callable[[ValidationContext], str | None]]] = [
    ("key_derivation", _check_key_derivation),
    ("collision", _check_collision),
    ("not_canonical", _check_not_canonical),
    ("hosting", _check_hosting),
    ("acl_sanity", _check_acl_sanity),
    ("purpose_limitation", _check_purpose_limitation),
    ("backend_reachability", _check_backend_reachability),
    ("allowed_methods", _check_allowed_methods),
    ("backend_auth_declared", _check_backend_auth_declared),
    ("identifier_characters", _check_identifier_characters),
]


def validate(
    raw: dict,
    *,
    manifest: dict,
    policy: dict,
    existing_servers: dict,
    fetch_spec: Callable[[str], str] = _default_fetch_spec,
    check_reachable: Callable[[str], None] = _default_check_reachable,
) -> JoinPayload:
    """Runs all twelve checks (spec S8) in order. Returns the validated
    JoinPayload on success. Raises RejectionError(check, message) naming the
    first failing check on failure -- the caller (a later task's endpoint)
    catches it to set the request REJECTED with that check name (spec S4)."""
    try:
        payload = JoinPayload(**raw)
    except pydantic.ValidationError as exc:
        raise RejectionError("schema", str(exc)) from exc

    ctx = ValidationContext(
        payload=payload,
        manifest=manifest,
        policy=policy,
        existing_servers=existing_servers,
        fetch_spec=fetch_spec,
        check_reachable=check_reachable,
    )
    for name, check_fn in _CHECKS:
        error = check_fn(ctx)
        if error:
            raise RejectionError(name, error)
    return payload
