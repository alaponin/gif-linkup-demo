"""apps/join-api/validate.py -- eleven of the twelve checks required
before a join request can be approved, plus three more that go
beyond that (lawful_basis, sla_required and spec_url_origin) -- fourteen
per-request checks in total. Check 5 (member class) moved to
hurl/generate.py's check_join_policy() -- a generate-time structural check,
not a per-request one -- see the comment above where _check_member_class
used to be; this module runs the other eleven, plus
lawful_basis, sla_required and spec_url_origin. Pure functions over a
payload, the manifest, the join policy and a fetched OpenAPI document -- no
X-Road, no containers, no job. Checks run in the exact order listed (1 schema
.. 12 identifier characters, minus 5); the three additional checks run
alongside them (see _CHECKS below for where), and the first failure of any of
them raises RejectionError(check, message) naming the check, which is what a
REJECTED request carries.

spec_url_origin is numbered 9a rather than given a number of its own: it is
the guard on check 9's own fetch, and it is only meaningful immediately
above it.

Two things this module deliberately does NOT do, both on purpose:
  - it never sets origin. schema.JoinPayload has no such field; wherever a
    validated payload becomes a manifest.yaml identity.members entry (a
    later task), origin: joined is forced there, not here.
  - it never touches a canonical member. check 4 (not_canonical) reads the
    canonical set from manifest.yaml itself (identity.members' origin:
    canonical entries, plus identity.owner.code) rather than hardcoding the
    five codes -- the same "nothing hardcodes the member set" discipline
    hurl/generate.py's discover_members() already holds to.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import pathlib
import re
import urllib.parse
from typing import Callable

import httpx
import pydantic
import yaml

from schema import BackendAuth, JoinPayload

# -- disk loader --------------------------------------------------------------
# The one piece of "existing federation state" the checks need that manifest.
# yaml does not carry: every existing member's Security Server code/dns_name/
# hosted_on. Read the same way hurl/generate.py's discover_members() finds
# member directories -- collision (check 3) and hosting (check 6) are the only
# checks that need it, so this loads only the security_server block, not a full
# member config parse.


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


# The Module 4 semantic map: entity -> {anchor,
# fields}. Loaded the same inline way as the security-server scan above --
# one small function, no shared YAML-loading utility for a single file.
def load_semantic_map(pack_dir: str | pathlib.Path) -> dict:
    pack_dir = pathlib.Path(pack_dir)
    path = pack_dir / "configs" / "semantic" / "semantic-map.yaml"
    return yaml.safe_load(path.read_text()) or {}


# -- backend I/O ---------------------------------------------------------------
# Two separate callables, both overridable (apps/join-api/tests inject fakes):
# fetch_spec reads the OpenAPI document text from spec_url; check_reachable
# attempts to resolve-and-connect to a backend URL, raising on failure. Kept
# separate rather than one URL-dispatched callable because production fetches
# spec_url over the network too (a third-party spec) -- only the
# *test* fixtures short-circuit that half; the reachability half must always
# be a real attempt (the backend_reachability check).


# follow_redirects=False in both: httpx's own default today, pinned here
# because the spec_url_origin check below is only worth having if the host it
# approved is the host that is actually contacted. A 302 to http://cs:4000
# from an applicant-controlled URL walks straight past an allowlist that was
# checked once, before the fetch -- so the no-redirect behaviour is stated
# rather than inherited, and a future httpx default change (or a
# well-meaning edit) cannot silently reopen it.
def _default_fetch_spec(url: str) -> str:
    resp = httpx.get(url, timeout=5.0, follow_redirects=False)
    resp.raise_for_status()
    return resp.text


def _default_check_reachable(url: str) -> None:
    # Any response -- even a 404 -- proves the TCP/TLS handshake and HTTP
    # exchange succeeded, which is what "resolve and connect to it"
    # asks for. No raise_for_status(): endpoint correctness is not
    # this check's job, only reachability.
    httpx.get(url, timeout=5.0, follow_redirects=False)


@dataclasses.dataclass
class ValidationContext:
    payload: JoinPayload
    manifest: dict
    policy: dict  # configs/x-road-bus/join-policy.yaml's join: block only
    existing_servers: dict[str, dict]  # key -> {code, dns_name, hosted_on}
    semantic_map: dict  # configs/semantic/semantic-map.yaml -- entity -> {anchor, fields}
    fetch_spec: Callable[[str], str] = _default_fetch_spec
    check_reachable: Callable[[str], None] = _default_check_reachable
    # Populated by check 9, consumed by check 10 -- avoids fetching the same
    # spec twice for services that pass check 9.
    fetched_specs: dict[str, dict] = dataclasses.field(default_factory=dict)
    # Populated by check 9 alongside fetched_specs -- a service's declared
    # and required response fields, persisted on the request record at
    # validation time so job.py's r1 step never re-fetches spec_url: the
    # post-approval job path must not add a second fetch of an
    # applicant-controlled URL.
    contract_fields: dict[str, tuple[frozenset[str], frozenset[str]]] = dataclasses.field(
        default_factory=dict
    )

    @property
    def key(self) -> str:
        return self.payload.code.lower()


class RejectionError(Exception):
    """Carries the specific check name that ends up on a REJECTED record."""

    def __init__(self, check: str, message: str):
        super().__init__(f"{check}: {message}")
        self.check = check
        self.message = message


# -- checks 2-8, 12: payload/manifest/policy only, no network ----------------


def _check_key_derivation(ctx: ValidationContext) -> str | None:
    """key == code.lower() -- hurl/generate.py's discover_members() already
    enforces exactly this agreement between a config directory's key and its
    identity.members entry, and fails loudly at generate time otherwise.
    Enforced here too, at request time, against the stricter constraint the
    key must actually satisfy:
    configs/member-<key>/ becomes a directory name and
    hurl/check_scenarios.py's scenario_member_re expects [a-z0-9]+."""
    # [a-z0-9]+ here is deliberately narrower than _check_identifier_characters'
    # X-Road allowlist below (a-zA-Z0-9'()+,-.=?) -- not a contradiction, a
    # different thing being constrained. That check enforces what X-Road
    # itself will accept for code/subsystem/service codes; this one enforces
    # what a *directory name and YAML map key* can survive, which is
    # stricter (no uppercase, no punctuation) and unrelated to X-Road's own
    # rules. Both stay as they are.
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
    this payload's proposed values."""
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
    """The key is not one of the frozen four. Derived from
    manifest.yaml, not hardcoded: every identity.members entry whose origin
    is canonical (default, if absent) PLUS identity.owner.code -- the
    Central Server's owner, PDGA, which is not itself an identity.members
    entry."""
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


# Check 5 ("member class -- matches the policy") has no per-request
# implementation here, on purpose. The payload schema (schema.py) carries
# no member_class field of its own --
# Progressa is a single-member_class federation by design (multi-federation
# joins are explicitly out of scope) -- so there is
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
    """Reproduces hurl/generate.py's resolve_hosted_on_map() two hard failures
    at request time -- unknown host, hosting chain -- plus the hosting decision
    itself.

    join.default_hosting: hosted_on no longer means "own-server requests are
    rejected". Plan C gave this pack a real own-server code path
    (job.py's build_sequence own-server branch, scripts/join-agent.sh), so
    the key now means what configs/x-road-bus/join-policy.yaml's own comment always
    said it meant -- "a join defaults to hosting; own_server must be asked
    for": a request that asks for NEITHER is rejected, and one that sets
    security_server.own_server: true is an own-server join. The fail-safe is
    unchanged in substance: an absent hosted_on is still never silently
    treated as an own-server request, because own_server has to be there
    too.
    """
    hosted_on = ctx.payload.security_server.hosted_on
    own_server = ctx.payload.security_server.own_server
    if hosted_on and own_server:
        return (
            f"security_server sets both hosted_on {hosted_on!r} and "
            "own_server: true -- a joining member's subsystem is either an "
            "extra client on an existing member's Security Server or has one "
            "of its own, never both"
        )
    if not hosted_on and not own_server:
        if ctx.policy.get("default_hosting") == "hosted_on":
            return (
                "security_server.hosted_on is required -- configs/x-road-bus/"
                "2.7.yaml sets join.default_hosting: hosted_on, so a join "
                "defaults to hosting and an own Security Server must be asked "
                "for explicitly (security_server.own_server: true)"
            )
        return None
    if own_server:
        # Nothing further to check here: there is no host to resolve, and
        # the joining member's own dns_name/code were already checked for
        # collision (check 3). Whether the server can actually be stood up is
        # a fact about the operator's machine, not about this payload --
        # scripts/join-agent.sh checks it, and job.py's BLOCKED state is what
        # a request waits in until it has been.
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
    <instance>/<member_class>/<CODE>/<SUBSYSTEM> form.
    The subsystem this join is itself creating is not yet in that set and is
    not a valid target either -- a member cannot grant or request access to
    itself."""
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
    """Conformance check against configs/semantic/semantic-map.yaml (closes
    Publishing a service AND
    granting another subsystem access to it is an exchange -- this pack's
    own convention for one (configs/member-pnia/pnia.yaml's semantic: block)
    is to document it, and now to declare a real entity from the Module 4
    semantic map: semantic.entity must be a key in the map, and every
    semantic.fields entry must be declared for that entity there. This
    check still cannot judge whether the field list is the legally correct
    one for the stated purpose -- that is the human operator's job at
    approval."""
    publishes_with_access = any(svc.access for svc in ctx.payload.services)
    if not publishes_with_access:
        return None
    semantic = ctx.payload.semantic
    if not (semantic and semantic.fields):
        return (
            "this join publishes a service and grants another subsystem "
            "access to it, which makes it a provenance-tracked exchange -- a "
            "semantic: block (entity, key, fields) is required, matching "
            "this pack's existing convention (configs/member-pnia/pnia.yaml)"
        )
    entity_fields = (ctx.semantic_map.get(semantic.entity) or {}).get("fields")
    if entity_fields is None:
        return (
            f"semantic.entity {semantic.entity!r} is not declared in "
            f"configs/semantic/semantic-map.yaml (known entities: "
            f"{sorted(ctx.semantic_map)})"
        )
    undeclared = [f for f in semantic.fields if f not in entity_fields]
    if undeclared:
        return (
            f"semantic.fields {undeclared} is not declared for entity "
            f"{semantic.entity!r} in configs/semantic/semantic-map.yaml "
            f"(declared fields: {entity_fields})"
        )
    return None


def _check_lawful_basis(ctx: ValidationContext) -> str | None:
    """5.2's sixth checklist item (a lawful basis for its exchanges):
    does the applicant hold a legal mandate for the data it proposes to
    expose as authoritative? One check, two shapes: a published service must
    carry its own basis (Service.lawful_basis) -- never resolved against
    anything, recorded and surfaced only, exactly like the purpose_limitation
    check's "recorded and surfaced, never resolved" treatment of
    semantic.pattern; a member
    with no services has none to carry it, so it must be on
    member_requirements.lawful_basis instead. One field, one place -- a
    payload with services that also sets member_requirements.lawful_basis is
    not double-checked; the service field is authoritative once there is
    one."""
    if ctx.payload.services:
        missing = [svc.code for svc in ctx.payload.services if not svc.lawful_basis]
        if missing:
            return (
                f"service(s) {missing} publish with no lawful_basis -- every "
                "published service must state the legal mandate for the data "
                "it proposes to expose as authoritative"
            )
        return None
    if not ctx.payload.member_requirements.lawful_basis:
        return (
            "member_requirements.lawful_basis is required for a consumer-only "
            "member (no services to state one on instead) -- Module 5.2's "
            "sixth checklist item has nowhere else to be recorded"
        )
    return None


def _check_sla_required(ctx: ValidationContext) -> str | None:
    """Every published service needs an SLA --
    Module 5.3's own "reuse the same template for every service on the bus"
    (schema.SLA lives on Service, not on JoinPayload). A
    consumer-only member (no services) has nothing to check here and is
    unaffected. Not one of the numbered twelve -- an additional check,
    in the same style: the first service missing one fails, naming it."""
    for svc in ctx.payload.services:
        if svc.sla is None:
            return (
                f"service {svc.code!r} publishes with no sla: block -- Module 5.3's "
                "SLA template is required for every service a provider publishes "
                "(a consumer-only member, with no services, needs none)"
            )
    return None


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _check_allowed_methods(ctx: ValidationContext) -> str | None:
    """No operation outside join.allowed_methods.
    Operates on the spec the backend_reachability check already fetched --
    fetching twice would be a second, driftable read of the same document."""
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
                        f"{sorted(allowed)} (configs/x-road-bus/join-policy.yaml) -- a "
                        "joined member may publish read-only services"
                    )
    return None


def _check_backend_auth_declared(ctx: ValidationContext) -> str | None:
    """backend.auth is present and one of the values the payload schema
    permits. schema.JoinPayload already requires
    Backend and validates the enum at parse time (the schema check); this
    check exists as the explicit, named enforcement, not a second, looser
    copy of that validation."""
    backend = ctx.payload.backend
    if backend is None or not isinstance(backend.auth, BackendAuth):
        return "backend.auth must be one of: none, network_allowlist, proxy_injected"
    return None


# X-Road >=7.3.0 enforces a strict identifier allowlist, strict by default
# on fresh installations (XRDDEV-1960): code, subsystem and service codes
# may contain only a-zA-Z0-9'()+,-.=? -- the Security Server itself rejects
# anything else, regardless of what this check lets through. This used to
# be a denylist instead (reject '/', ':', ';', '%', '.', whitespace and
# control characters), built from a guess at which separator characters
# might collide with X-Road's own REST message protocol -- '.' was added
# to that denylist after "awards.list" (a service code copied from a
# third-party tool's human-facing API name, hurl/generate.py's
# dn_escape() docstring tells the same "a comma in member_name broke DN
# construction" story) slipped through every other check. That guess
# disagreed with X-Road's actual allowlist in both directions: it accepted
# characters ('_', '&', '#', '@', '$', '~', '*', '!', '"', '\', '<', '[',
# '{') X-Road >=7.3.0 rejects outright, and it rejected '.', which
# X-Road's allowlist permits -- so "awards.list" was a valid X-Road
# identifier all along, and banning dots was solving the wrong problem. A
# positive match against X-Road's own published set replaces the denylist
# rather than patching it further. Empty and whitespace-only values need
# no separate check: they match nothing in this pattern and fall out of
# fullmatch() on their own. Published as the pack's stated convention in
# docs/conventions.md -- this constant is that page's cited source, not a
# copy of a value that lives somewhere else. One rule, one place, no
# indirection.
def _bad_identifier(value: str) -> bool:
    return not re.fullmatch(r"[a-zA-Z0-9'()+,\-.=?]+", value)


def _check_identifier_characters(ctx: ValidationContext) -> str | None:
    """code, subsystem, and every service code satisfy X-Road's identifier
    restrictions. X-Road >=7.3.0 enforces a strict
    allowlist by default on fresh installations (XRDDEV-1960) -- reject
    anything outside a-zA-Z0-9'()+,-.=?, which also rejects empty and
    whitespace-only values (they match nothing in that set). The permitted
    set is named in the rejection message, per spec, rather than left for
    the operator to discover later inside certificate signing."""
    candidates = [("code", ctx.payload.code), ("subsystem", ctx.payload.subsystem)]
    for svc in ctx.payload.services:
        candidates.append(("services[].code", svc.code))
    for label, value in candidates:
        if _bad_identifier(value):
            return (
                f"{label} {value!r} is not a valid X-Road identifier -- "
                "identifiers must be non-empty and contain only letters, "
                "digits, and the characters in \"'()+,-.=?\" "
                "(X-Road >=7.3.0 identifier restriction, XRDDEV-1960)"
            )
    return None


# -- checks 9-11: touch the joining member's backend --------------------------


def contract_fields(spec: dict) -> tuple[frozenset[str], frozenset[str]]:
    """Which field names a service's own OpenAPI contract declares for its
    200 response, and which of those are required. One path per service
    spec (this pack's own convention, modules 2.4/2.5), so the first path's
    GET 200 application/json schema is the contract; a spec with no
    `required` block declares nothing required, not everything.

    Mirrors apps/mock-registry/app.py's DECLARED_FIELDS expression exactly,
    on purpose -- that module is a separate container that cannot import
    this one, and the two computing the same set independently is why a live
    response silently diverging from its own contract went unnoticed for as
    long as it did: the provider and the contract could not disagree. Do not
    factor this out into a shared library -- a shared library here would
    hide the very coupling this check exists to break."""
    schema = (
        next(iter((spec.get("paths") or {}).values()), {})
        .get("get", {})
        .get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    declared = frozenset((schema.get("properties") or {}).keys())
    required = frozenset(schema.get("required") or [])
    return declared, required


# -- check 9a: the origin of every URL this API fetches ------------------------
#
# spec_url is an applicant-controlled string, fetched from inside the join-api
# container -- which holds JOB_SECRETS (admin user, admin password, token PIN)
# and can reach every Security Server's admin API on :4000. So is the
# servers[].url inside the document that fetch returns. Both are judged here,
# by the same function: this check runs immediately BEFORE
# backend_reachability so spec_url is judged before the first byte is fetched,
# and backend_reachability calls the same function again for servers[].url,
# which does not exist until after that fetch.
#
# The primary control is join.spec_url_hosts (configs/x-road-bus/
# join-policy.yaml). The scheme and IP-literal refusals below are defence in
# depth: they hold even if that list is later widened carelessly, which is
# exactly how an allowlist stops being one. Rejecting every IP literal covers
# loopback, link-local and the cloud metadata address 169.254.169.254 in one
# rule rather than three that can each be forgotten -- an allowlist entry is a
# HOSTNAME, and a member that can only be named by its address has not been
# through the naming this federation runs on.

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _origin_error(label: str, url: str, policy: dict) -> str | None:
    """None if `url` may be fetched from this container; a rejection message
    otherwise. Pure string work -- no DNS, no connection, so it is safe to
    call before any I/O and cheap to call twice."""
    allowed = policy.get("spec_url_hosts")
    if not isinstance(allowed, list) or not allowed:
        return (
            "configs/x-road-bus/join-policy.yaml declares no join.spec_url_hosts "
            "-- this API refuses to fetch any applicant-supplied URL without an "
            "allowlist to judge it against (it runs in a container holding the "
            "federation's admin credentials). Add the key and redeploy"
        )
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return (
            f"{label} {url!r} uses scheme {parsed.scheme or '(none)'!r} -- only "
            f"{sorted(_ALLOWED_SCHEMES)} are fetched (a file:// or schemeless URL "
            "reads this container's own filesystem, not the member's backend)"
        )
    host = parsed.hostname
    if not host:
        return f"{label} {url!r} names no host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return (
            f"{label} {url!r} names an IP address rather than a host name. "
            "Addresses are refused outright -- loopback, link-local and the "
            "cloud metadata address 169.254.169.254 among them -- regardless of "
            "join.spec_url_hosts; name a host on that list instead"
        )
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return (
            f"{label} {url!r} names {host!r}, which resolves inside this "
            "container -- the join API's own process and its credentials, never "
            "the member's backend"
        )
    if host not in allowed:
        return (
            f"{label} {url!r} names host {host!r}, which is not in "
            f"join.spec_url_hosts {sorted(allowed)} (configs/x-road-bus/"
            "join-policy.yaml) -- this URL is fetched from a container that "
            "holds the federation's admin credentials and can reach every "
            "Security Server's admin API, so only declared hosts are contacted"
        )
    return None


def _check_spec_url_origin(ctx: ValidationContext) -> str | None:
    """Every service's spec_url, judged before backend_reachability fetches
    any of them."""
    for svc in ctx.payload.services:
        error = _origin_error(f"service {svc.code!r}'s spec_url", svc.spec_url, ctx.policy)
        if error:
            return error
    return None


def _check_backend_reachability(ctx: ValidationContext) -> str | None:
    """Fetch spec_url, parse servers.url, resolve-and-connect to it from
    inside the linkup network. Catches the
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
        # The same origin rule as check 9a, applied to the OTHER
        # applicant-controlled URL -- one that only exists after the fetch
        # above, so it cannot be judged in that check. The rejection is
        # reported as backend_reachability because that is the check that
        # would have made the connection.
        error = _origin_error(
            f"service {svc.code!r}'s servers[].url (in {svc.spec_url})", backend_url, ctx.policy
        )
        if error:
            return error
        try:
            ctx.check_reachable(backend_url)
        except Exception as exc:  # noqa: BLE001 -- any connect failure is a rejection
            return (
                f"service {svc.code!r}'s backend at {backend_url} "
                f"(servers[].url in {svc.spec_url}) is not reachable from "
                f"inside the linkup network: {exc}"
            )
        ctx.fetched_specs[svc.code] = spec_doc
        ctx.contract_fields[svc.code] = contract_fields(spec_doc)
    return None


# Checks run in this exact order -- the original numbered list, verbatim,
# minus check 5 (member_class), which has no per-request implementation --
# see the comment above where _check_member_class used to be -- plus
# lawful_basis and sla_required, neither one of the original twelve,
# inserted after purpose_limitation since all three inspect payload.services,
# plus spec_url_origin immediately before backend_reachability, where it has
# to be: it is the guard on that check's own fetch, and a guard that runs
# after the fetch guards nothing.
# Check 1 (schema) happens in validate() itself, before a ValidationContext
# can even be built.
_CHECKS: list[tuple[str, Callable[[ValidationContext], str | None]]] = [
    ("key_derivation", _check_key_derivation),
    ("collision", _check_collision),
    ("not_canonical", _check_not_canonical),
    ("hosting", _check_hosting),
    ("acl_sanity", _check_acl_sanity),
    ("purpose_limitation", _check_purpose_limitation),
    ("lawful_basis", _check_lawful_basis),
    ("sla_required", _check_sla_required),
    ("spec_url_origin", _check_spec_url_origin),
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
    semantic_map: dict,
    fetch_spec: Callable[[str], str] = _default_fetch_spec,
    check_reachable: Callable[[str], None] = _default_check_reachable,
) -> tuple[JoinPayload, ValidationContext]:
    """Runs all fourteen per-request checks (eleven, minus check 5,
    plus lawful_basis, sla_required and spec_url_origin -- see _CHECKS' own
    comment) in order. Returns
    (validated JoinPayload, the ValidationContext checks ran against) on
    success -- the context is returned too because the backend_reachability
    check populates ctx.fetched_specs with every service's parsed OpenAPI
    document, and module 2.7's join-time drift baseline (scripts/member.sh
    drift) needs that document's endpoint set. Discarding the context here
    (as this function used to) meant the only place that data existed was
    gone by the time app.py persisted the SUBMITTED record. Raises
    RejectionError(check, message) naming the first failing check on
    failure -- the caller (app.py's endpoint) catches it to set the
    request REJECTED with that check name."""
    try:
        payload = JoinPayload(**raw)
    except pydantic.ValidationError as exc:
        raise RejectionError("schema", str(exc)) from exc

    ctx = ValidationContext(
        payload=payload,
        manifest=manifest,
        policy=policy,
        existing_servers=existing_servers,
        semantic_map=semantic_map,
        fetch_spec=fetch_spec,
        check_reachable=check_reachable,
    )
    for name, check_fn in _CHECKS:
        error = check_fn(ctx)
        if error:
            raise RejectionError(name, error)
    return payload, ctx
