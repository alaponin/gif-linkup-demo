#!/usr/bin/env python3
"""Generate the Linkup federation Hurl scenario set from configs/ + manifest.yaml.

Reference implementation: nordic-institute/X-Road @ tag 7.7.0,
development/hurl/scenarios/setup.hurl (the sanctioned config-as-code path for the
Central Server and Security Server admin APIs on :4000).

The scenarios are GENERATED. Do not hand-edit anything in hurl/scenarios/ or
hurl/vars.env -- change configs/ or this file and re-run:

    python3 hurl/generate.py

Every identifier comes from configs/*.yaml and manifest.yaml, so the pack has a
single source of truth for the Progressa identifiers and the Hurl set cannot
drift from the YAML the bb-config-gen plays produce.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("generate.py needs PyYAML: pip install pyyaml")

import steps as steps_module

PACK = pathlib.Path(__file__).resolve().parent.parent
# HURL_DIR/OUT/ENV_PATH are reassigned in main() when --out/--env are passed
# (tests/test_golden.py only). Every
# other read (manifest.yaml, deployment.yaml, configs/) always goes through
# PACK itself and is never redirected: a golden-corpus run must read the
# real, committed member configuration, only write its output elsewhere.
HURL_DIR = PACK / "hurl"
OUT = HURL_DIR / "scenarios"
ENV_PATH = PACK / ".env"

# --- credentials ------------------------------------------------------------
# The Security Server credentials and the software-token PIN are NOT declared
# here. Compose injects XROAD_ADMIN_USER / XROAD_ADMIN_PASSWORD / XROAD_TOKEN_PIN
# into the containers from .env, so anything written here would be a second,
# competing declaration of the same secret -- and upstream's PIN (Secret1234) is
# not this pack's PIN. They are read from .env below instead.
#
# The Central Server's admin credentials genuinely are fixed in the test/release
# image and are not settable from .env, so they stay constants. DEMO ONLY.
CS_USER, CS_PASS = "xrd", "secret"

# The Test CA's FiVRK certificate profile validates the country code; setup.hurl
# uses FI and so must we. This is an artefact of the demo trust anchor, not a
# statement about Progressa -- see docs/decisions/xroad-770-notes.md.
CSR_COUNTRY = "FI"

# Host-mapped ports from docker-compose.yml's `ports:` lines -- mirrors
# hurl/topology.sh's generated SS_UI/SS_REST bash maps; the two must move
# together if a port ever changes. Carried into topology.json (not
# re-derived) so the demo console can emit a "copy as curl" command a
# presenter can run on the host, outside the linkup network, where the
# in-network :4000/:8080 ports don't resolve. Keyed by SS-owner key
# (member key, or "pdga" for the management server -- PDGA is the
# federation owner, not a discovered member, so it never appears in
# configs/member-*/ and needs its own entry here).
#
# PNIA is 5100/5180, not 5000/5080: port 5000 collides with macOS's
# AirPlay Receiver (ControlCenter), which hangs the connection instead of
# refusing it. See docker-compose.yml's ss-pnia comment. Confirmed at P0 --
# this is the one reason FORBIDDEN_PORT_RANGE below exists.
PINNED_PORTS = {
    "pdga": (1000, 1080), "pnea": (2000, 2080), "plr": (3000, 3080),
    "pnia": (5100, 5180),
    # "moeys" stays RESERVED here even though MoEYS is retired (see
    # docs/production-delta.md) and discover_members() will never produce a
    # "moeys" key again: this table only matters when a key IS present, and
    # keeping the entry costs nothing while documenting that 6000/6080 must
    # never be handed to a different member -- allocate_ports() determinism
    # and the un-join byte-identity clause both depend on nothing below
    # FRESH_PORT_START moving for any other pinned or fresh member.
    "moeys": (6000, 6080),
}

# Service-publication scenario numbers -- the canonical three providers'
# 30/31/32, pinned the same way PINNED_SCENARIO_NO pins their SS numbers.
# PNEA has no entry: it publishes no service and has never had a numbered
# services file (see the "3x service publication" loop) -- only members
# with services get one, pinned or fresh.
PINNED_SERVICE_SCENARIO_NO = {"pnia": "30", "plr": "31", "moeys": "32"}

# Where a NEW member's numbers/ports come from once nothing pins them --
# safely above every pinned value today, so the canonical four never
# collide with a fresh allocation.
FRESH_SS_SCENARIO_START = 40
FRESH_SERVICE_SCENARIO_START = 50
FRESH_PORT_START = 7000
# macOS's AirPlay Receiver (ControlCenter) listens on 5000 (screen mirroring)
# AND 7000 (RAOP audio) by default, and on both it hangs the TCP connection
# mid-TLS-handshake instead of refusing it outright -- see docker-compose.yml's
# ss-pnia comment for 5000. 7000 is exactly FRESH_PORT_START's own default and
# was found live: a joined member's own
# Security Server got this port, registered fine during initial bring-up, then
# its admin API became unreachable from the host with no error -- `docker
# restart` did not fix it, `lsof -i :7000` on the host found ControlCenter, not
# the container, holding it. Refused outright, not just avoided by
# construction: a future change to FRESH_PORT_START must not silently
# reintroduce this.
FORBIDDEN_PORT_RANGE = frozenset(range(5000, 5100)) | {7000}


def _allocate_numbers(keys: list, pinned: dict, start: int) -> dict:
    """Pinned-then-allocated: every key in `pinned` keeps its number;
    everything else gets the next unused number from `start` upward, in
    `keys`' own (already-deterministic) order. Same member set -> same
    allocation, always -- the property the byte-identical-after-
    remove check depends on."""
    result: dict = {}
    used: set = set()
    for key in keys:
        if key in pinned:
            result[key] = pinned[key]
            used.add(int(pinned[key]))
    next_n = start
    for key in keys:
        if key in result:
            continue
        while next_n in used:
            next_n += 1
        result[key] = str(next_n)
        used.add(next_n)
        next_n += 1
    return result


def allocate_ports(owner_keys: list) -> dict:
    """owner_keys: every Security-Server-OWNING entity's key ("pdga" plus
    every unhosted member key), already in deterministic order. Pinned
    ports are kept; anything unpinned is allocated fresh from
    FRESH_PORT_START (UI +0, REST +80, both +100 per allocation), refusing
    the AirPlay range outright and refusing to collide with any pinned or
    already-allocated port."""
    result: dict = {}
    used_ui: set = set()
    used_rest: set = set()
    for key in owner_keys:
        if key in PINNED_PORTS:
            ui, rest = PINNED_PORTS[key]
            if ui in FORBIDDEN_PORT_RANGE or rest in FORBIDDEN_PORT_RANGE:
                raise SystemExit(
                    f"generate.py: PINNED_PORTS[{key!r}] = ({ui}, {rest}) falls in "
                    "a port macOS's AirPlay Receiver silently hangs on (5000-5099, or 7000)"
                )
            result[key] = (ui, rest)
            used_ui.add(ui)
            used_rest.add(rest)
    n = 0
    for key in owner_keys:
        if key in result:
            continue
        while True:
            ui, rest = FRESH_PORT_START + 100 * n, FRESH_PORT_START + 80 + 100 * n
            n += 1
            if ui in FORBIDDEN_PORT_RANGE or rest in FORBIDDEN_PORT_RANGE:
                continue
            if ui in used_ui or rest in used_rest:
                continue
            break
        result[key] = (ui, rest)
        used_ui.add(ui)
        used_rest.add(rest)
    return result

# The canonical four's Security Server scenario numbers -- never renumbered,
# so an existing member's scenario files keep their names as others join or
# leave. A member absent from this table sorts after every pinned one,
# alphabetically by key.
PINNED_SCENARIO_NO = {"pnia": "20", "plr": "21", "moeys": "22", "pnea": "23"}


def _member_sort_key(key: str) -> tuple:
    """Deterministic, stable member order: pinned scenario number first (in
    pinned order), then anything unpinned, alphabetically by key. Filesystem
    glob order must never leak into generated output."""
    pinned = PINNED_SCENARIO_NO.get(key)
    return (0, pinned) if pinned is not None else (1, key)


def discover_members(pack: pathlib.Path, identity: dict) -> dict[str, dict]:
    """Discover member configs from configs/member-*/ instead of a hardcoded
    dict -- the number and identity of members becomes a property of what's
    on disk. Each directory must hold exactly one config file, whose key
    (the directory's "member-" suffix) must have a matching, code-consistent
    identity.members entry; every identity.members entry must have a
    directory. Fails loudly on any of the four disagreements -- a
    silently-skipped or silently-invented member is worse than a crash.
    """
    configs_dir = pack / "configs"
    found: dict[str, pathlib.Path] = {}
    for member_dir in sorted(configs_dir.glob("member-*")):
        if not member_dir.is_dir():
            continue
        key = member_dir.name.removeprefix("member-")
        yaml_files = sorted(member_dir.glob("*.yaml"))
        if not yaml_files:
            raise SystemExit(f"generate.py: configs/{member_dir.name}/ has no config file")
        if len(yaml_files) > 1:
            names = ", ".join(p.name for p in yaml_files)
            raise SystemExit(
                f"generate.py: configs/{member_dir.name}/ has more than one "
                f"config file: {names}"
            )
        found[key] = yaml_files[0]

    identity_members = identity["members"]
    for key in found:
        if key not in identity_members:
            raise SystemExit(
                f"generate.py: configs/member-{key}/ has no matching "
                f"manifest.yaml identity.members.{key} entry"
            )
        expected_code = identity_members[key]["code"]
        if expected_code.lower() != key:
            raise SystemExit(
                f"generate.py: configs/member-{key}/ 's key does not match "
                f"identity.members.{key}.code {expected_code!r} "
                f"(lowercase would be {expected_code.lower()!r})"
            )
    for key in identity_members:
        if key not in found:
            raise SystemExit(
                f"generate.py: manifest.yaml identity.members.{key} has no "
                f"configs/member-{key}/ directory"
            )

    ordered_keys = sorted(found, key=_member_sort_key)
    return {key: load(str(found[key].relative_to(pack))) for key in ordered_keys}


def resolve_hosted_on_map(members: dict) -> dict[str, str]:
    """Member key -> host member key, for every member that does not run
    its own Security Server: an explicit `security_server.hosted_on` in its
    own config, naming a Security Server DNS name -- the mechanism a joining
    member uses to be hosted rather than own a server. Absent from the
    returned map means "own server" -- true of all three canonical members
    today (one topology, nothing preset-hosted).

    A hosted_on naming a server no member owns, or one that is itself
    hosted (a hosting chain), is a hard failure -- "hosted_on... absent
    means its own server" is the design's own definition of a valid host.
    """
    dns_to_key = {m["security_server"]["dns_name"]: k for k, m in members.items()}
    explicit_hosted: dict[str, str] = {}
    for key, member in members.items():
        explicit_dns = member["security_server"].get("hosted_on")
        if not explicit_dns:
            continue
        host_key = dns_to_key.get(explicit_dns)
        if host_key is None:
            valid = sorted(dns_to_key)
            raise SystemExit(
                f"generate.py: configs/member-{key}/*.yaml sets "
                f"security_server.hosted_on: {explicit_dns!r}, which no "
                f"member owns. Valid hosts: {', '.join(valid)}"
            )
        explicit_hosted[key] = host_key
    for key, host_key in explicit_hosted.items():
        if host_key in explicit_hosted:
            raise SystemExit(
                f"generate.py: configs/member-{key}/*.yaml sets "
                f"security_server.hosted_on to {members[host_key]['security_server']['dns_name']!r}, "
                f"but that server's own owner ({host_key}) is itself hosted "
                "-- hosting chains are not supported"
            )

    return explicit_hosted


HEADER = """# GENERATED by hurl/generate.py -- do not hand-edit.
# Source of truth: {src}
# Reference: X-Road 7.7.0 development/hurl/scenarios/setup.hurl
# DEMO ONLY -- Test CA, fixed credentials, single host.

"""


def load(path: str):
    with open(PACK / path) as fh:
        return yaml.safe_load(fh)


def read_env() -> dict[str, str]:
    """The container environment is the source of truth for secrets.

    Compose injects XROAD_TOKEN_PIN, XROAD_ADMIN_USER and XROAD_ADMIN_PASSWORD
    into every server from .env. The scenarios must log in with exactly those
    values, so they are read from the same file rather than restated here --
    otherwise the run initialises a token with one PIN and the container's
    autologin expects another, which fails at the first key generation and looks
    like a certificate problem.
    """
    path = ENV_PATH
    if not path.exists():
        raise SystemExit(
            "generate.py: .env does not exist -- run scripts/gen-secrets.sh first. "
            ".env.example ships placeholders that cannot work "
            "; falling back to "
            "it here would generate a vars.env full of CHANGEME and fail deep "
            "inside a Hurl run, the worst place to discover it."
        )
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Values in .env carry trailing comments: strip from the first ' #'.
        env[key.strip()] = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    missing = [k for k in ("XROAD_TOKEN_PIN", "XROAD_ADMIN_USER", "XROAD_ADMIN_PASSWORD")
               if not env.get(k)]
    if missing:
        raise SystemExit(f"generate.py: {path.name} does not set {', '.join(missing)}")
    return env


def check_policy(core: dict) -> None:
    """Refuse to generate from a config that declares a policy we do not apply.

    configs/ is the deployment. A block the generator silently ignores is worse
    than no block at all: it reads as configuration and is decoration.
    """
    policy = core.get("policy") or {}
    if "auto_approve" in policy:
        raise SystemExit(
            "generate.py: configs/x-road-bus/federation-core.yaml declares policy.auto_approve, "
            "but the scenarios approve management requests explicitly over the admin "
            "API and never write /etc/xroad/conf.d/local.ini. Either implement the "
            "flags here or set policy.management_request_approval: explicit. "
            "See docs/decisions/xroad-770-notes.md §1."
        )
    approval = policy.get("management_request_approval")
    if approval != "explicit":
        raise SystemExit(
            "generate.py: configs/x-road-bus/federation-core.yaml must set "
            f"policy.management_request_approval: explicit (found: {approval!r})"
        )


# The four keys apps/join-api/validate.py enforces -- kept here as
# well so a fifth key sitting undetected in configs/x-road-bus/join-policy.yaml is a
# generate-time failure, not something only discovered when a join is
# submitted. Value-correctness for each of the four is validate.py's job (it
# is the code that actually applies join: policy at request time); this is
# purely the same "no undeclared decoration" guard check_policy() already
# applies to the bus policy above.
JOIN_POLICY_KEYS = frozenset({"member_class", "default_hosting", "allowed_methods"})


def check_join_policy(join_config: dict, manifest: dict) -> None:
    """Sibling to check_policy() above, same reasoning, for
    configs/x-road-bus/join-policy.yaml's join: block -- three
    keys (max_services, require_semantic_for_provenance, backend_auth) were
    invented and deleted for exactly this: a key nothing applies reads
    as configuration and is decoration.

    Also carries the one value-correctness assertion that belongs here
    rather than in apps/join-api/validate.py: join.member_class must agree
    with manifest.yaml's identity.member_class. This is a static
    operator-misconfiguration check -- Progressa is a single-member_class
    federation by design, so nothing about a submitted join payload could
    ever make this comparison come out differently. It moved here from
    validate.py's per-request check 5: a check two static config files
    disagree on is a generate-time consistency failure, not a fact about
    any particular join request.
    """
    join = join_config.get("join") or {}
    extra = set(join) - JOIN_POLICY_KEYS
    if extra:
        raise SystemExit(
            "generate.py: configs/x-road-bus/join-policy.yaml join: declares "
            f"unrecognised key(s) {sorted(extra)} -- apps/join-api/validate.py "
            f"only enforces {sorted(JOIN_POLICY_KEYS)}. Either implement "
            "enforcement for the new key or remove it."
        )
    policy_class = join.get("member_class")
    federation_class = manifest["identity"]["member_class"]
    if policy_class is not None and policy_class != federation_class:
        raise SystemExit(
            "generate.py: configs/x-road-bus/join-policy.yaml join.member_class "
            f"({policy_class!r}) does not match manifest.yaml "
            f"identity.member_class ({federation_class!r})"
        )


def write(name: str, src: str, body: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(HEADER.format(src=src) + body.lstrip("\n"))
    print(f"  wrote hurl/scenarios/{name}")


def sub(tpl: str, **kw) -> str:
    """Token substitution that does not fight Hurl's {{var}} syntax."""
    for key, value in kw.items():
        tpl = tpl.replace(f"@{key}@", str(value))
    return tpl


# Templates are source, not output: always read from the real pack, never
# from HURL_DIR, which main() reassigns under --out (tests/test_golden.py
# only). Same distinction generate.py already draws for manifest.yaml and
# configs/ at line 32.
TEMPLATES = PACK / "hurl" / "templates"


def render(name: str, **kw) -> str:
    return sub((TEMPLATES / name).read_text(), **kw)


def dn_escape(value: str) -> str:
    """Escape a comma for RFC 2253 DN embedding in a subject_field_values value.

    Confirmed necessary at P0: the Security Server's admin API
    joins subject_field_values into a single DN string server-side without
    escaping, then parses it with BouncyCastle. MoEYS's member_name ("...
    Education, Youth and Sport") contains a literal comma, which BouncyCastle
    then reads as an RDN separator -- keys-with-csrs failed every retry with
    core.Signer.InternalError / "badly formatted directory string" in the
    signer log. PDGA/PNIA/PLR's names have no comma and were unaffected.

    The escape must survive two layers: this template is raw JSON text, not
    re-encoded, so the source needs TWO backslashes for the JSON-decoded Java
    string to carry a single `\,` -- the RFC 2253 escape BouncyCastle expects.
    Only the comma is handled: it's the only DN metacharacter confirmed to
    occur in a Progressa member_name. If a future name introduces another one
    (`+ " ; < >`), extend this the same way -- double backslash, then char.
    """
    return value.replace(",", "\\\\,")


def ss_prefix(dns_name: str) -> str:
    return dns_name.replace("-", "_")


# The once-only exchange itself is NOT generated here. scripts/acceptance.sh
# owns the once-only-exchange module's four assertions (happy path, right learner, asked once,
# negative) -- including the two that a Hurl scenario cannot make: exact-set
# equality of the assembled application, and the seeded-record comparison in
# scripts/assert_record.py. A second, weaker copy of the pack's headline check
# is worse than none: the two drift, and the weaker one passes.


def build_ss_file(member: dict, host_var: str, capture_ca_name: bool = False) -> str:
    """Full bring-up for one member Security Server: certs, registration, client."""
    m, sub_cfg, ss = member["member"], member["subsystem"], member["security_server"]
    prefix = ss_prefix(ss["dns_name"])
    conn = member.get("client", {}).get("connection_type", "HTTP")
    # SS_BRINGUP_INIT was split at the AUTH-key/CSR boundary so ca_name
    # capture (management-server-only) could become its
    # own registry step -- see hurl/steps.py "ss.bringup_init" /
    # "ss.ca_name_capture" / "ss.auth_key_csr". capture_ca_name is always
    # False for a member's own bring-up today (only 10-ss-pdga passes it).
    body = render(
        steps_module.BY_ID["ss.bringup_init"].template,
        SS=ss["dns_name"],
        SS_CODE=ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        P=prefix,
    )
    if capture_ca_name:
        body += render(steps_module.BY_ID["ss.ca_name_capture"].template, HOSTVAR=host_var, P=prefix)
    body += "\n"
    body += render(
        steps_module.BY_ID["ss.auth_key_csr"].template,
        SS_CODE=ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        P=prefix,
    )
    body += render(
        steps_module.BY_ID["ss.sign_key_csr"].template,
        SS_CODE=ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        SESS_P=prefix,
        CAP_P=prefix,
    )
    body += render(steps_module.BY_ID["ss.bringup_register"].template, HOSTVAR=host_var, P=prefix)
    body += render(steps_module.BY_ID["ss.activate"].template, HOSTVAR=host_var, P=prefix)
    body += render(steps_module.BY_ID["ss.tsa_post"].template, HOSTVAR=host_var, P=prefix)
    client_kwargs = dict(
        SS=ss["dns_name"],
        MEMBER_CODE=m["member_code"],
        SUBSYSTEM=sub_cfg["code"],
        CONNECTION_TYPE=conn,
        HOSTVAR=host_var,
        SESS_P=prefix,
        CAP_P=prefix,
    )
    # ss.client_add -> ss.client_register: see hurl/steps.py's comment on
    # these ids for why this order is load-bearing.
    body += render(steps_module.BY_ID["ss.client_add"].template, **client_kwargs)
    body += render(steps_module.BY_ID["ss.client_register"].template, **client_kwargs)
    return body


def build_service_file(member: dict, host_var: str, sess_p: str | None = None) -> str:
    m, sub_cfg, ss = member["member"], member["subsystem"], member["security_server"]
    cap_p = ss_prefix(ss["dns_name"])
    sess_p = sess_p or cap_p
    out = ""
    for svc in member.get("services") or []:
        service_code = svc["code"]
        sc = service_code.replace("-", "_")
        out += render(
            steps_module.BY_ID["service.publish"].template,
            MEMBER_CODE=m["member_code"],
            SUBSYSTEM=sub_cfg["code"],
            SERVICE_CODE=service_code,
            SC=sc,
            HOSTVAR=host_var,
            SESS_P=sess_p,
            CAP_P=cap_p,
            SPECVAR=f"{m['member_code'].lower()}_spec_url",
        )
        for subject in svc.get("access") or []:
            out += render(
                steps_module.BY_ID["service.acl"].template,
                SERVICE_CODE=service_code,
                HOSTVAR=host_var,
                SESS_P=sess_p,
                CAP_P=cap_p,
                ACL_SUBJECT=subject.replace("/", ":"),
                # The 2.6 negative check's unauthorised caller (moved off the
                # now-retired MoEYS/PEMIS, onto PLR:ENROLMENT --
                # configs/x-road-bus/once-only-exchange.yaml's negative_check.unauthorised_client
                # is the source of truth; this is the same value restated for
                # the generated comment below).
                NEGATIVE="PROGRESSA:GOV:PLR:ENROLMENT",
            )
    return out


def build_hosted_client(member: dict, host_member: dict, host_var: str) -> str:
    """Register a member's subsystem as an extra client on an already-
    bootstrapped Security Server -- the mechanism a member with an explicit
    security_server.hosted_on (e.g. a joining member)
    uses instead of owning its own server: a fresh SIGN key/cert for this
    member specifically, then the client-registration flow -- both
    authenticated with the HOST's session (sess_p), captured under this
    member's OWN namespace (cap_p).

    serialNumber in the SIGN-key subject names the *hosting* SS's own code
    (host_member's), not this member's nominal one from its own config --
    the cert genuinely lives on the host's token, and naming a server that
    was never brought up would be a lie in the cert.

    Client registration MUST come before the SIGN key: the owning member's
    SIGN key is valid because /initialization set owner_member_code, but a
    hosted member has no such relationship -- the signer rejects a member_id
    it doesn't yet recognize as a client with 400 client_not_found (confirmed
    live at P0 for the lite profile, when this was its only use).
    """
    m, sub_cfg = member["member"], member["subsystem"]
    conn = member.get("client", {}).get("connection_type", "HTTP")
    host_ss = host_member["security_server"]
    sess_p = ss_prefix(host_ss["dns_name"])
    cap_p = ss_prefix(member["security_server"]["dns_name"])
    # ss.client_add -> ss.sign_key_csr -> ss.client_register, in that order:
    # see hurl/steps.py's comment on those ids -- this is the exact ordering
    # bug this docstring's own paragraph above describes.
    body = render(
        steps_module.BY_ID["ss.client_add"].template,
        SS=host_ss["dns_name"],
        MEMBER_CODE=m["member_code"],
        SUBSYSTEM=sub_cfg["code"],
        CONNECTION_TYPE=conn,
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    body += render(
        steps_module.BY_ID["ss.sign_key_csr"].template,
        SS_CODE=host_ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    body += render(
        steps_module.BY_ID["ss.client_register"].template,
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    return body


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Linkup Hurl scenario set from configs/ + manifest.yaml."
    )
    parser.add_argument(
        "--out", metavar="DIR",
        help="write scenarios/, vars.env, topology.json, topology.sh and "
             "compose.members.yml under DIR instead of hurl/ -- tests only "
             "(tests/test_golden.py). The real deploy path never passes this.",
    )
    parser.add_argument(
        "--env", metavar="FILE",
        help="read secrets from FILE instead of .env -- tests only "
             "(tests/golden/env.fixture), so a golden vars.env is never "
             "generated from a real credential.",
    )
    return parser.parse_args()


def member_service_block(key: str, dns: str, ui: int, rest: int) -> str:
    """One joined member's own Security Server, as a compose.members.yml
    service block. A module-level function purely so tests/test_allocation.py
    can assert on it without a manifest carrying a joined member: nothing but
    main() calls it.

    `${XROAD_BIND:-127.0.0.1}` on both mappings is NOT decoration -- every
    `ports:` line in the hand-written docker-compose.yml carries it, and
    without it here a joined member's own Security Server published its admin
    UI AND its unauthenticated X-Road proxy port on 0.0.0.0, ignoring
    deployment.yaml's network.bind entirely. Found live (the first own-server join this pack ever ran): the very next
    scripts/acceptance.sh hard-failed in check-exposure.sh with
    "ss-pvtb: 0.0.0.0:7100 -> 4000/tcp". A Compose file generated for a
    joined member has to obey the same bind policy as the hand-written one,
    or the policy has a hole exactly where a demonstration puts a new agency.

    hurl/compose.hurl.yml (hand-written) adds the same healthcheck to each
    canonical Security Server BY NAME, so run-linkup.sh's runner waits for it
    to answer on :4000 before driving admin APIs at it. That file stays
    hand-written and scoped to the canonical four; a
    joined member's own server gets it here instead, so it is never a
    container nothing waits for. Found live:
    without it, Compose waits only for the process to start, not for its
    Tomcat/TLS listener, and a caller can hang indefinitely on a handshake
    that never completes.
    """
    return (
        f"  {dns}:\n"
        f"    <<: *sidecar\n"
        f"    container_name: {dns}\n"
        f'    ports: ["${{XROAD_BIND:-127.0.0.1}}:{ui}:4000", "${{XROAD_BIND:-127.0.0.1}}:{rest}:8080"]\n'
        f"    volumes:\n"
        f"      - {key}-db:/var/lib/postgresql/16/main\n"
        f"      - {key}-conf:/etc/xroad\n"
        f"      - {key}-archive:/var/lib/xroad\n"
        f"      - ./xroad-demo-local.ini:/etc/xroad/conf.d/local.ini\n"
        f"    healthcheck:\n"
        f'      test: ["CMD", "curl", "-f", "-k", "https://localhost:4000"]\n'
        f"      interval: 5s\n"
        f"      retries: 120\n"
    )


def main() -> None:
    global HURL_DIR, OUT, ENV_PATH
    args = parse_args()
    if args.out:
        HURL_DIR = pathlib.Path(args.out)
        OUT = HURL_DIR / "scenarios"
        # vars.env writes before any scenario file (see main() below) and,
        # unlike hurl/ itself, a fresh --out directory does not already
        # exist -- found live via tests/test_golden.py's first run.
        HURL_DIR.mkdir(parents=True, exist_ok=True)
    if args.env:
        ENV_PATH = pathlib.Path(args.env)

    manifest = load("manifest.yaml")
    identity = manifest["identity"]
    deployment = load("deployment.yaml")
    if deployment.get("target") != "docker-local":
        raise SystemExit(
            f"generate.py: deployment.yaml target {deployment.get('target')!r} is not "
            "supported -- only 'docker-local' is implemented today."
        )
    core = load("configs/x-road-bus/federation-core.yaml")
    check_policy(core)
    check_join_policy(load("configs/x-road-bus/join-policy.yaml"), manifest)
    env = read_env()
    members = discover_members(PACK, identity)
    # member:/subsystem: no longer live in configs/*.yaml (removed --
    # manifest.yaml's identity.members is the source now) -- inject them into
    # the same dict shape so build_ss_file/build_service_file/
    # build_hosted_client and the "02 members" loop below need zero changes.
    for key, cfg in members.items():
        ident = identity["members"][key]
        cfg["member"] = {"member_code": ident["code"], "member_name": ident["name"]}
        cfg["subsystem"] = {"code": ident["subsystem"], "description": ident["subsystem_description"]}

    instance = identity["instance"]
    owner = identity["owner"]
    mgmt_ss = core["management_security_server"]
    member_class = identity["member_class"]
    pdga_prefix = ss_prefix(mgmt_ss["dns_name"])
    hosted_on_map = resolve_hosted_on_map(members)

    print("generating Linkup Hurl scenarios (X-Road 7.7.0 admin APIs)")

    # Clear before writing: a removed member's scenario file must not
    # linger as a stray -- check_scenarios.py would (correctly) flag it as
    # unclaimed, but "regenerated fresh every run" (hurl/README.md) should
    # mean fresh, not merely overwritten. Found live: removing a throwaway
    # member left its scenario file behind.
    if OUT.exists():
        for stale in OUT.glob("*.hurl"):
            stale.unlink()

    # -- vars.env ----------------------------------------------------------
    # No comments: Hurl's --variables-file is a plain name=value list and a '#'
    # would end up inside the value. Commentary lives in hurl/README.md.
    lines = [
        f"xroad_instance={instance}",
        f"member_class={member_class}",
        f"cs_host={core['central_server']['address']}",
        "ca_host=ca",
        f"{mgmt_ss['dns_name'].replace('-', '_')}_host={mgmt_ss['dns_name']}",
    ]
    for member in members.values():
        dns = member["security_server"]["dns_name"]
        lines.append(f"{dns.replace('-', '_')}_host={dns}")
    lines += [
        "",
        f"cs_admin_user={CS_USER}",
        f"cs_admin_password={CS_PASS}",
        f"ss_admin_user={env['XROAD_ADMIN_USER']}",
        f"ss_admin_password={env['XROAD_ADMIN_PASSWORD']}",
        f"token_pin={env['XROAD_TOKEN_PIN']}",
        f"csr_country={CSR_COUNTRY}",
        "",
    ]
    for key, member in members.items():
        for svc in member.get("services") or []:
            lines.append(f"{key}_spec_url={svc['spec_url']}")
    lines.append("")
    vars_path = HURL_DIR / "vars.env"
    vars_path.write_text("\n".join(lines))
    # Contains the token PIN and admin password in cleartext (this is the
    # Hurl runner's own credentials file, mounted read-only into the
    # container) -- 600 at generation time, not left at the process umask's
    # default, which on most systems is group/world-readable.
    vars_path.chmod(0o600)
    print("  wrote hurl/vars.env")

    # -- 00 Central Server initialisation ----------------------------------
    # instance init, member class, software token login, INTERNAL/EXTERNAL
    # signing keys -- four steps in the registry, one file here (join-a
    # plan). The init response is 200, not 201 -- the
    # assertion lives in fragments/CS_INIT.hurl.tmpl, unchanged.
    body = render(steps_module.BY_ID["cs.init"].template)
    body += render(
        steps_module.BY_ID["cs.member_class"].template,
        DESCRIPTION=core["central_server"]["member_classes"][0]["description"],
    )
    body += render(steps_module.BY_ID["cs.token_login"].template)
    body += render(steps_module.BY_ID["cs.signing_keys"].template)
    write("00-cs-init.hurl", "configs/x-road-bus/federation-core.yaml", body)

    # -- 01 trust services --------------------------------------------------
    ts = core["trust_services"]
    trust_services_step = steps_module.BY_ID["cs.trust_services"]
    body = render(
        trust_services_step.template,
        CERT_PROFILE=ts["certification_service"]["certificate_profile"],
        OCSP_URL=ts["certification_service"]["ocsp_responder"]["url"].replace("ca:", "{{ca_host}}:"),
        TSA_URL=ts["timestamping_service"]["url"].replace("ca:", "{{ca_host}}:"),
    )
    write("01-cs-trust-services.hurl", "configs/x-road-bus/federation-core.yaml", body)

    # -- 02 members ---------------------------------------------------------
    body = render(
        steps_module.BY_ID["cs.members_owner"].template,
        OWNER_NAME=owner["name"],
        OWNER_CODE=owner["code"],
        MGMT_SUBSYSTEM=owner["management_subsystem"],
    )
    members_member_step = steps_module.BY_ID["cs.members_member"]
    # "moeys" dropped from this historical-order tuple: it is
    # retired and discover_members() never returns it, so `members["moeys"]`
    # would KeyError. Order for the rest is unchanged -- this literal tuple
    # is not `members.keys()`'s own order.
    for key in ("pnia", "plr", "pnea"):
        m = members[key]["member"]
        s = members[key]["subsystem"]
        body += render(
            members_member_step.template,
            MEMBER_NAME=m["member_name"],
            MEMBER_CODE=m["member_code"],
            SUBSYSTEM_CODE=s["code"],
            SUBSYSTEM_DESCRIPTION=s["description"],
        )
    write("02-cs-members.hurl", "configs/member-*/*.yaml", body)

    # -- 03 anchor ----------------------------------------------------------
    body = render(steps_module.BY_ID["cs.anchor"].template)
    write("03-cs-anchor.hurl", "configs/x-road-bus/federation-core.yaml", body)

    # -- 10 management security server -------------------------------------
    host_var = f"{pdga_prefix}_host"
    body = render(
        steps_module.BY_ID["ss.bringup_init"].template,
        SS=mgmt_ss["dns_name"],
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["code"],
        MEMBER_NAME=dn_escape(owner["name"]),
        HOSTVAR=host_var,
        P=pdga_prefix,
    )
    body += render(steps_module.BY_ID["ss.ca_name_capture"].template, HOSTVAR=host_var, P=pdga_prefix)
    body += "\n"
    body += render(
        steps_module.BY_ID["ss.auth_key_csr"].template,
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["code"],
        MEMBER_NAME=dn_escape(owner["name"]),
        HOSTVAR=host_var,
        P=pdga_prefix,
    )
    body += render(
        steps_module.BY_ID["ss.sign_key_csr"].template,
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["code"],
        MEMBER_NAME=dn_escape(owner["name"]),
        HOSTVAR=host_var,
        SESS_P=pdga_prefix,
        CAP_P=pdga_prefix,
    )
    body += render(steps_module.BY_ID["ss.bringup_register"].template, HOSTVAR=host_var, P=pdga_prefix)
    body += render(
        steps_module.BY_ID["ss.mgmt_register"].template,
        SS=mgmt_ss["dns_name"],
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["code"],
        SUBSYSTEM=owner["management_subsystem"],
        HOSTVAR=host_var,
        P=pdga_prefix,
    )
    body += render(steps_module.BY_ID["ss.activate"].template, HOSTVAR=host_var, P=pdga_prefix)
    body += render(steps_module.BY_ID["ss.tsa_capture"].template, HOSTVAR=host_var, P=pdga_prefix)
    body += render(steps_module.BY_ID["ss.tsa_post"].template, HOSTVAR=host_var, P=pdga_prefix)
    write("10-ss-pdga.hurl", "configs/x-road-bus/federation-core.yaml", body)

    # -- 2x member security servers ----------------------------------------
    # tsa_name / tsa_url and ca_name are captured on the management server
    # above; the member servers reuse them, which is why 10- must run first.
    # Numbers: pinned for the canonical four (PINNED_SCENARIO_NO), allocated
    # fresh from FRESH_SS_SCENARIO_START for anyone else -- every discovered
    # member gets one, hosted or not, since even a hosted member's stub file
    # needs a number to be claimable by a module in manifest.yaml.
    ss_scenario_no = _allocate_numbers(list(members.keys()), PINNED_SCENARIO_NO, FRESH_SS_SCENARIO_START)
    for key in members:
        num = ss_scenario_no[key]
        member = members[key]
        dns = member["security_server"]["dns_name"]
        host_var = f"{ss_prefix(dns)}_host"
        hosted_on = hosted_on_map.get(key)
        if hosted_on:
            # Not brought up as its own server -- its content is appended
            # into its host's own file below instead. Still write a stub
            # here so this module's manifest.yaml scenario claim keeps
            # resolving to a real, existing file.
            #
            # A member ends up in hosted_on_map for exactly one reason now:
            # its own config sets security_server.
            # hosted_on -- the mechanism a joining member uses. No preset
            # overlay exists anymore to produce a second kind of stub.
            stub = (
                f"# {key.upper()} is hosted as a client on ss-{hosted_on} "
                f"(configs/member-{key}/{member['module']}.yaml sets "
                "security_server.hosted_on) -- it never brings up its own "
                f"server. See {ss_scenario_no[hosted_on]}-ss-{hosted_on}.hurl.\n"
            )
            write(
                f"{num}-ss-{key}.hurl",
                f"configs/member-{key}/{member['module']}.yaml",
                stub,
            )
            continue
        body = build_ss_file(member, host_var)
        for hosted_key, host_key in hosted_on_map.items():
            if host_key == key:
                body += build_hosted_client(members[hosted_key], member, host_var)
        write(f"{num}-ss-{key}.hurl", f"configs/member-{key}/{member['module']}.yaml", body)

    # -- 3x service publication + ACLs -------------------------------------
    # Only members with services get a numbered file at all (PNEA, the
    # consumer, never has -- same as before this task).
    service_keys = [k for k in members if members[k].get("services")]
    service_scenario_no = _allocate_numbers(service_keys, PINNED_SERVICE_SCENARIO_NO, FRESH_SERVICE_SCENARIO_START)
    for key in service_keys:
        num = service_scenario_no[key]
        member = members[key]
        dns = member["security_server"]["dns_name"]
        hosted_on = hosted_on_map.get(key)
        if hosted_on:
            host_dns = members[hosted_on]["security_server"]["dns_name"]
            content = build_service_file(member, f"{ss_prefix(host_dns)}_host", sess_p=ss_prefix(host_dns))
        else:
            content = build_service_file(member, f"{ss_prefix(dns)}_host")
        if not content.strip():
            content = (
                f"# {member['member']['member_code']} publishes no service in v0.1 "
                f"({member.get('role_notes', '')}).\n"
                "# It is registered on the bus so the negative check in "
                "acceptance/once-only-exchange.md has a real,\n# registered-but-unauthorised caller "
                "to make the denied request from.\n"
            )
        write(
            f"{num}-services-{key}.hurl",
            f"configs/member-{key}/{member['module']}.yaml",
            content,
        )

    # The once-only-exchange module has no scenario by design; see the
    # note above probe data. scripts/acceptance.sh owns it.

    # -- hurl/topology.json --------------------------------------------------
    # Consumed by apps/console/truth.py so the demo console cannot describe a
    # federation different from the one actually deployed. Not
    # git-committed -- same convention as hurl/scenarios/ and hurl/vars.env
    # (regenerated fresh every run, never staged; see hurl/README.md).
    # Port allocation order: "pdga" (the owner, not a discovered member)
    # plus every SS-owning (unhosted) member, sorted alphabetically by key.
    # Pinned ports win regardless of position, so this ordering only ever affects a
    # member nothing pins.
    owner_keys = ["pdga"] + sorted(k for k in members if k not in hosted_on_map)
    ports_by_key = allocate_ports(owner_keys)

    def _ss_entry(code: str, host: str, key: str) -> dict:
        ui, rest = ports_by_key[key]
        return {
            "code": code, "host": host, "ui_port": 4000, "proxy_port": 8080,
            "host_ui_port": ui, "host_proxy_port": rest,
        }

    security_servers = [_ss_entry(mgmt_ss["code"], mgmt_ss["dns_name"], "pdga")]
    # Historical iteration order for the canonical four (byte-identical
    # constraint) -- pnea/plr/pnia/moeys, not the pinned-scenario-number
    # order subsystems below uses. Anything discovered beyond these four
    # (a joined member) is appended after, in its own deterministic order.
    _legacy_ss_order = ("pnea", "plr", "pnia", "moeys")
    ss_iter_order = [k for k in _legacy_ss_order if k in members] + \
        [k for k in members if k not in _legacy_ss_order]
    for key in ss_iter_order:
        if key in hosted_on_map:
            continue  # not brought up as its own server -- hosted elsewhere
        ss = members[key]["security_server"]
        security_servers.append(_ss_entry(ss["code"], ss["dns_name"], key))

    subsystems = []
    for key in members:  # already in deterministic (pinned, then key) order
        member = members[key]
        m, sub_cfg, ss = member["member"], member["subsystem"], member["security_server"]
        hosted_on = hosted_on_map.get(key)
        host_dns = members[hosted_on]["security_server"]["dns_name"] if hosted_on else ss["dns_name"]
        subsystems.append({
            "id": f"{instance}:{member_class}:{m['member_code']}:{sub_cfg['code']}",
            "member_code": m["member_code"],
            "member_name": m["member_name"],
            "subsystem_code": sub_cfg["code"],
            "hosted_on": host_dns,
            "origin": identity["members"][key].get("origin", "canonical"),
            "services": [
                {"code": svc["code"], "access": svc.get("access") or []}
                for svc in (member.get("services") or [])
            ],
        })

    topology = {
        "instance": instance,
        "member_class": member_class,
        "central_server": {"host": core["central_server"]["address"], "ui_port": 4000},
        "security_servers": security_servers,
        "subsystems": subsystems,
    }
    (HURL_DIR / "topology.json").write_text(json.dumps(topology, indent=2) + "\n")
    print("  wrote hurl/topology.json")

    # -- hurl/topology.sh -----------------------------------------------
    # scripts/lib-stack.sh sources this instead of declaring SS_UI/SS_REST/
    # SS_ORDER/HOST_SS itself -- one topology, generated once, consumed by
    # both the console (topology.json) and bash (topology.sh), exactly the
    # same values either way. PDGA:MANAGEMENT is not in topology.json's
    # subsystems list (that list is discovered members only; PDGA is the
    # federation owner, not a member) but lib-stack.sh has always declared it,
    # so it is added here explicitly rather than silently dropped.
    ss_lines = "\n".join(f"  [{s['host']}]={s['host_ui_port']}" for s in security_servers)
    rest_lines = "\n".join(f"  [{s['host']}]={s['host_proxy_port']}" for s in security_servers)
    order_line = " ".join(s["host"] for s in security_servers)
    host_ss_lines = [f"  [{owner['code']}:{owner['management_subsystem']}]={mgmt_ss['dns_name']}"]
    for s in subsystems:
        host_ss_lines.append(f"  [{s['member_code']}:{s['subsystem_code']}]={s['hosted_on']}")
    topology_sh = f"""# GENERATED by hurl/generate.py -- do not hand-edit.
# One topology, generated once: scripts/lib-stack.sh sources this instead of
# declaring these four itself; apps/console reads the same generation
# run's hurl/topology.json. Regenerate with: python3 hurl/generate.py

declare -A SS_UI=(
{ss_lines}
)
declare -A SS_REST=(
{rest_lines}
)
SS_ORDER=({order_line})
declare -A HOST_SS=(
{chr(10).join(host_ss_lines)}
)
"""
    (HURL_DIR / "topology.sh").write_text(topology_sh)
    print("  wrote hurl/topology.sh")

    # -- hurl/compose.members.yml --------------------------------------
    # docker-compose.yml keeps the canonical four hand-written, comments
    # and all -- a joined member that owns its own
    # Security Server gets a service block here instead, mirroring the
    # canonical blocks' shape exactly. A joined member that is hosted
    # (hosted_on_map) owns no container at all and never appears here.
    # scripts/lib-stack.sh adds this file to COMPOSE/COMPOSE_ALL when it exists.
    joined_owner_keys = [
        key for key in members
        if key not in hosted_on_map
        and identity["members"][key].get("origin", "canonical") == "joined"
    ]
    if not joined_owner_keys:
        compose_members_yml = (
            "# GENERATED by hurl/generate.py -- do not hand-edit.\n"
            "# No joined member owns a Security Server -- nothing to add.\n"
            "# Safe to include unconditionally: an empty services map.\n"
            "services: {}\n"
        )
    else:
        service_blocks = []
        volume_blocks = []
        for key in joined_owner_keys:
            ui, rest = ports_by_key[key]
            dns = members[key]["security_server"]["dns_name"]
            service_blocks.append(member_service_block(key, dns, ui, rest))
            volume_blocks.append(
                f"  {key}-db: {{name: kp2-{key}-db}}\n"
                f"  {key}-conf: {{name: kp2-{key}-conf}}\n"
                f"  {key}-archive: {{name: kp2-{key}-archive}}\n"
            )
        compose_members_yml = (
            "# GENERATED by hurl/generate.py -- do not hand-edit.\n"
            "# Joined members that own their own Security Server. Redeclares\n"
            "# x-sidecar locally -- YAML anchors do not cross Compose's -f\n"
            "# file boundaries, only its services:/volumes:/networks: keys do;\n"
            "# services here reference the linkup network docker-compose.yml\n"
            "# already declares, not a second copy of it.\n"
            "x-sidecar: &sidecar\n"
            "  image: niis/xroad-security-server-sidecar:${XROAD_VERSION:-7.7.0}\n"
            "  environment:\n"
            "    XROAD_TOKEN_PIN: ${XROAD_TOKEN_PIN:?set in .env}\n"
            "    XROAD_ADMIN_USER: ${XROAD_ADMIN_USER:-xrd}\n"
            "    XROAD_ADMIN_PASSWORD: ${XROAD_ADMIN_PASSWORD:?set in .env}\n"
            "    XROAD_LOG_LEVEL: INFO\n"
            "  networks: [linkup]\n"
            "  depends_on: [cs, ca]\n"
            "\n"
            "services:\n" + "\n".join(service_blocks) +
            "\n"
            "volumes:\n" + "\n".join(volume_blocks)
        )
    (HURL_DIR / "compose.members.yml").write_text(compose_members_yml)
    print("  wrote hurl/compose.members.yml")

    print(f"\ndone -- {instance} federation, "
          f"{len(manifest['identifiers']['members'])} members, "
          f"{len(manifest['identifiers']['services'])} services "
          f"(once-only-exchange is proved by scripts/acceptance.sh, not by a scenario)")


if __name__ == "__main__":
    main()
