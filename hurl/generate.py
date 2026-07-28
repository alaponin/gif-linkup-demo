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

import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("generate.py needs PyYAML: pip install pyyaml")

PACK = pathlib.Path(__file__).resolve().parent.parent
OUT = PACK / "hurl" / "scenarios"

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
# statement about Progressa -- see docs/xroad-770-notes.md.
CSR_COUNTRY = "FI"

# Under the lite profile, these members are NOT brought up as their own
# Security Server -- they're extra clients on the shared provider SS. Mirrors
# scripts/lib.sh's HOST_SS lite branch; the two must move together if this
# pack's lite topology ever changes (only one lite arrangement exists today,
# so this is a fixed fact of docker-compose.yml, not a general N-way mapping
# -- see docs/superpowers/specs/2026-07-26-deployment-spec-and-lite-profile-design.md).
LITE_HOSTED_ON = {"pnia": "plr", "moeys": "plr"}

# Host-mapped ports from docker-compose.yml's `ports:` lines -- mirrors
# scripts/lib.sh's SS_UI/SS_REST bash maps; the two must move together if a
# port ever changes. Carried into topology.json (not re-derived) so the demo
# console can emit a "copy as curl" command a presenter can run on the host,
# outside the linkup network, where the in-network :4000/:8080 ports don't
# resolve. Keyed by SS-owner key (member key, or "pdga" for the management
# server -- PDGA is the federation owner, not a discovered member, so it
# never appears in configs/member-*/ and needs its own entry here).
PINNED_PORTS = {
    "pdga": (1000, 1080), "pnea": (2000, 2080), "plr": (3000, 3080),
    "pnia": (5100, 5180), "moeys": (6000, 6080),
}

# Service-publication scenario numbers -- the canonical three providers'
# 30/31/32, pinned the same way PINNED_SCENARIO_NO pins their SS numbers.
# PNEA has no entry: it publishes no service and has never had a numbered
# services file (see the "3x service publication" loop) -- only members
# with services get one, pinned or fresh.
PINNED_SERVICE_SCENARIO_NO = {"pnia": "30", "plr": "31", "moeys": "32"}

# Where a NEW member's numbers/ports come from once nothing pins them --
# safely above every pinned value today, so the canonical five never
# collide with a fresh allocation.
FRESH_SS_SCENARIO_START = 40
FRESH_SERVICE_SCENARIO_START = 50
FRESH_PORT_START = 7000
# macOS's AirPlay Receiver (ControlCenter) listens on 5000 by default and
# hangs the connection instead of refusing it -- see docker-compose.yml's
# ss-pnia comment. Refused outright, not just avoided by construction: a
# future change to FRESH_PORT_START must not silently reintroduce this.
FORBIDDEN_PORT_RANGE = range(5000, 5100)


def _allocate_numbers(keys: list, pinned: dict, start: int) -> dict:
    """Pinned-then-allocated: every key in `pinned` keeps its number;
    everything else gets the next unused number from `start` upward, in
    `keys`' own (already-deterministic) order. Same member set -> same
    allocation, always -- the property Task 9's byte-identical-after-
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
                    "the 5000-5099 range macOS's AirPlay Receiver silently hangs on"
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

# The canonical five's Security Server scenario numbers -- never renumbered
# (docs/superpowers/plans/2026-07-27-kp2-member-parameterisation.md, Global
# Constraints). A member absent from this table sorts after every pinned one,
# alphabetically by key; Task 3 of that plan adds fresh-range allocation for
# such a member instead of leaving it unordered.
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
    directory. Fails loudly on any of the four disagreements, per this
    plan's Task 1 -- a silently-skipped or silently-invented member is worse
    than a crash.
    """
    configs_dir = pack / "configs"
    found: dict[str, pathlib.Path] = {}
    for member_dir in sorted(configs_dir.glob("member-*")):
        if not member_dir.is_dir():
            continue
        key = member_dir.name[len("member-"):]  # host runs system python3.7 -- no str.removeprefix
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


def resolve_hosted_on_map(members: dict, profile: str) -> dict[str, str]:
    """Member key -> host member key, for every member that does not run
    its own Security Server. Resolution order per member: (1) an explicit
    `security_server.hosted_on` in its own config, naming a Security Server
    DNS name -- profile-independent, and the mechanism a joining member
    uses; (2) the lite-profile overlay (LITE_HOSTED_ON), only when
    profile == "lite" -- the canonical four's existing behavior, now
    expressed as this same mechanism's default preset rather than a
    separate code path. Absent from the returned map means "own server".

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

    resolved = dict(explicit_hosted)
    if profile == "lite":
        for key, host_key in LITE_HOSTED_ON.items():
            resolved.setdefault(key, host_key)
    return resolved


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
    path = PACK / ".env"
    if not path.exists():
        path = PACK / ".env.example"
        print(f"  note: no .env yet, reading {path.name} "
              "(re-run generate.py after cp .env.example .env)")
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
            "generate.py: configs/x-road-bus/2.1.yaml declares policy.auto_approve, "
            "but the scenarios approve management requests explicitly over the admin "
            "API and never write /etc/xroad/conf.d/local.ini. Either implement the "
            "flags here or set policy.management_request_approval: explicit. "
            "See docs/xroad-770-notes.md §1."
        )
    approval = policy.get("management_request_approval")
    if approval != "explicit":
        raise SystemExit(
            "generate.py: configs/x-road-bus/2.1.yaml must set "
            f"policy.management_request_approval: explicit (found: {approval!r})"
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


def dn_escape(value: str) -> str:
    """Escape a comma for RFC 2253 DN embedding in a subject_field_values value.

    Confirmed necessary at P0 (2026-07-25): the Security Server's admin API
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


# ---------------------------------------------------------------------------
# Reusable fragment: bring one Security Server up to a registered auth cert.
# Mirrors setup.hurl's SS0/SS1 sequences request for request.
# ---------------------------------------------------------------------------

SS_BRINGUP_INIT = """
############################################################
# @SS@ -- @MEMBER_NAME@ (@SS_CODE@)
############################################################

# Check that the Security Server UI is up
GET https://{{@HOSTVAR@}}:4000

HTTP 200

# Log in to the Security Server
POST https://{{@HOSTVAR@}}:4000/login
[FormParams]
username: {{ss_admin_user}}
password: {{ss_admin_password}}

HTTP 200
[Captures]
@P@_xsrf_token: cookie "XSRF-TOKEN"

# Upload the global configuration anchor downloaded from the Central Server
POST https://{{@HOSTVAR@}}:4000/api/v1/system/anchor
X-XSRF-TOKEN: {{@P@_xsrf_token}}
Content-Type: application/octet-stream
```
{{gconf_anchor}}
```

HTTP 201

# Initialise the Security Server (owner + server code + token PIN)
POST https://{{@HOSTVAR@}}:4000/api/v1/initialization
X-XSRF-TOKEN: {{@P@_xsrf_token}}
Content-Type: application/json
{
  "owner_member_class": "{{member_class}}",
  "owner_member_code": "@MEMBER_CODE@",
  "security_server_code": "@SS_CODE@",
  "software_token_pin": "{{token_pin}}",
  "ignore_warnings": true
}

HTTP 201

# Log in to the software token
PUT https://{{@HOSTVAR@}}:4000/api/v1/tokens/0/login
X-XSRF-TOKEN: {{@P@_xsrf_token}}
Content-Type: application/json
{
  "password": "{{token_pin}}"
}

HTTP *
@CANAME@
# Generate the AUTH key and its CSR in one call
POST https://{{@HOSTVAR@}}:4000/api/v1/tokens/0/keys-with-csrs
X-XSRF-TOKEN: {{@P@_xsrf_token}}
Content-Type: application/json
{
  "key_label": "Auth key",
  "csr_generate_request": {
    "key_usage_type": "AUTHENTICATION",
    "ca_name": "{{ca_name}}",
    "csr_format": "DER",
    "subject_field_values": {
      "CN": "{{@HOSTVAR@}}",
      "C": "{{csr_country}}",
      "O": "@MEMBER_NAME@",
      "subjectAltName": "{{@HOSTVAR@}}",
      "serialNumber": "{{xroad_instance}}/@SS_CODE@/{{member_class}}"
    }
  }
}

# setup.hurl@7.7.0 notes the API returns 200 here although the OpenAPI model says 201.
HTTP 200

[Captures]
@P@_auth_key_id: jsonpath "$.key.id"
@P@_auth_key_csr_id: jsonpath "$.csr_id"

# Download the AUTH CSR in PEM (the Test CA signs PEM, the SS generates DER)
GET https://{{@HOSTVAR@}}:4000/api/v1/keys/{{@P@_auth_key_id}}/csrs/{{@P@_auth_key_csr_id}}?csr_format=PEM
X-XSRF-TOKEN: {{@P@_xsrf_token}}

HTTP 200

[Captures]
@P@_auth_key_csr: body

# Sign the AUTH CSR against the Test CA (needs a filename, hence the raw multipart body)
POST http://{{ca_host}}:8888/testca/sign
Content-Type: multipart/form-data; boundary=certboundary
```
--certboundary
Content-Disposition: form-data; name="type"

auth
--certboundary
Content-Disposition: form-data; name="certreq"; filename="auth.csr.pem"

{{@P@_auth_key_csr}}
--certboundary--
```

HTTP 200
[Captures]
@P@_auth_key_cert: body

# Import the AUTH certificate
POST https://{{@HOSTVAR@}}:4000/api/v1/token-certificates
X-XSRF-TOKEN: {{@P@_xsrf_token}}
Content-Type: application/octet-stream
```
{{@P@_auth_key_cert}}
```

HTTP 201

[Captures]
@P@_auth_key_cert_hash: jsonpath "$.certificate_details.hash"
"""

# Extracted so it can also run for a member HOSTED on someone else's Security
# Server (the lite profile's PNIA/MoEYS-on-ss-plr pattern): SESS_P is whose
# already-open session authenticates the request; CAP_P is this member's own
# capture namespace, so a hosted member's client_id/sign_key never collides
# with the hosting SS's own. The owning member's own bring-up (build_ss_file)
# calls this with SESS_P == CAP_P -- identical behavior to before this split.
MEMBER_SIGN_KEY = """
# Generate the SIGN key and its CSR for @MEMBER_CODE@
POST https://{{@HOSTVAR@}}:4000/api/v1/tokens/0/keys-with-csrs
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}
Content-Type: application/json
{
  "key_label": "Sign key",
  "csr_generate_request": {
    "key_usage_type": "SIGNING",
    "ca_name": "{{ca_name}}",
    "csr_format": "DER",
    "member_id": "{{xroad_instance}}:{{member_class}}:@MEMBER_CODE@",
    "subject_field_values": {
      "CN": "@MEMBER_CODE@",
      "C": "{{csr_country}}",
      "O": "@MEMBER_NAME@",
      "subjectAltName": "{{@HOSTVAR@}}",
      "serialNumber": "{{xroad_instance}}/@SS_CODE@/{{member_class}}"
    }
  }
}

HTTP 200

[Captures]
@CAP_P@_sign_key_id: jsonpath "$.key.id"
@CAP_P@_sign_key_csr_id: jsonpath "$.csr_id"

# Download the SIGN CSR in PEM
GET https://{{@HOSTVAR@}}:4000/api/v1/keys/{{@CAP_P@_sign_key_id}}/csrs/{{@CAP_P@_sign_key_csr_id}}?csr_format=PEM
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}

HTTP 200

[Captures]
@CAP_P@_sign_key_csr: body

# Sign the SIGN CSR against the Test CA
POST http://{{ca_host}}:8888/testca/sign
Content-Type: multipart/form-data; boundary=certboundary
```
--certboundary
Content-Disposition: form-data; name="type"

sign
--certboundary
Content-Disposition: form-data; name="certreq"; filename="sign.csr.pem"

{{@CAP_P@_sign_key_csr}}
--certboundary--
```

HTTP 200
[Captures]
@CAP_P@_sign_key_cert: body

# Import the SIGN certificate
POST https://{{@HOSTVAR@}}:4000/api/v1/token-certificates
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}
Content-Type: application/octet-stream
```
{{@CAP_P@_sign_key_cert}}
```

HTTP 201

[Captures]
@CAP_P@_sign_key_cert_hash: jsonpath "$.certificate_details.hash"
"""

SS_BRINGUP_REGISTER = """
# Register the AUTH certificate (the SS's address is its DNS name on the linkup network)
PUT https://{{@HOSTVAR@}}:4000/api/v1/token-certificates/{{@P@_auth_key_cert_hash}}/register
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "address": "{{@HOSTVAR@}}"
}

# setup.hurl@7.7.0: 204, although the OpenAPI model says 200.
HTTP 204

# Approve the registration request on the Central Server.
# This is the explicit alternative to the auto-approve-* flags in local.ini:
# nothing has to be written into /etc/xroad/conf.d on the CS.
GET https://{{cs_host}}:4000/api/v1/management-requests?sort=id&desc=true&status=WAITING
X-XSRF-TOKEN: {{cs_xsrf_token}}

HTTP 200

[Captures]
@P@_auth_cert_req_id: jsonpath "$.items[0].id"

POST https://{{cs_host}}:4000/api/v1/management-requests/{{@P@_auth_cert_req_id}}/approval
X-XSRF-TOKEN: {{cs_xsrf_token}}

HTTP 200
"""

SS_ACTIVATE = """
# Activate the AUTH certificate
PUT https://{{@HOSTVAR@}}:4000/api/v1/token-certificates/{{@P@_auth_key_cert_hash}}/activate
X-XSRF-TOKEN: {{@P@_xsrf_token}}

HTTP 204
"""

# Captured once, on the management Security Server, after its auth certificate is
# active -- the global list is only readable from an initialised server. Every
# later server reuses the captured name/url.
TSA_CAPTURE = """
# Read the timestamping service out of the global configuration
GET https://{{@HOSTVAR@}}:4000/api/v1/timestamping-services
X-XSRF-TOKEN: {{@P@_xsrf_token}}

HTTP 200

[Captures]
tsa_name: jsonpath "$[0].name"
tsa_url: jsonpath "$[0].url"
"""

SS_TSA_POST = """
# Point the Security Server at that timestamping service
POST https://{{@HOSTVAR@}}:4000/api/v1/system/timestamping-services
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "name": "{{tsa_name}}",
  "url": "{{tsa_url}}"
}

HTTP 201
"""

# Extracted alongside MEMBER_SIGN_KEY for the same reason -- SESS_P/CAP_P let a
# hosted member register as a client using the hosting SS's session while
# keeping its own capture namespace. Split into ADD/REGISTER because a hosted
# member's SIGN key must be generated *between* the two: the signer rejects a
# member_id it doesn't yet know as a client (client_not_found) if generated
# before ADD, but /register rejects a member with no certificate yet
# (core.Signer.UnknownMember) if called before the SIGN key is imported.
# Confirmed live at P0 for lite, 2026-07-26. build_ss_file calls ADD then
# REGISTER back-to-back (SESS_P == CAP_P, unchanged full-mode behavior);
# build_hosted_client interleaves ADD -> MEMBER_SIGN_KEY -> REGISTER.
MEMBER_CLIENT_ADD = """
# Add @MEMBER_CODE@:@SUBSYSTEM@ as a client of @SS@
POST https://{{@HOSTVAR@}}:4000/api/v1/clients
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}
{
  "ignore_warnings": true,
  "client": {
    "member_class": "{{member_class}}",
    "member_code": "@MEMBER_CODE@",
    "subsystem_code": "@SUBSYSTEM@",
    "connection_type": "@CONNECTION_TYPE@"
  }
}

HTTP 201

[Captures]
@CAP_P@_client_id: jsonpath "$.id"
"""

MEMBER_CLIENT_REGISTER = """
# Register the subsystem
PUT https://{{@HOSTVAR@}}:4000/api/v1/clients/{{@CAP_P@_client_id}}/register
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}

HTTP 204

# Approve the client registration request on the Central Server
GET https://{{cs_host}}:4000/api/v1/management-requests?sort=id&desc=true&status=WAITING
X-XSRF-TOKEN: {{cs_xsrf_token}}

HTTP 200

[Captures]
@CAP_P@_client_req_id: jsonpath "$.items[0].id"

POST https://{{cs_host}}:4000/api/v1/management-requests/{{@CAP_P@_client_req_id}}/approval
X-XSRF-TOKEN: {{cs_xsrf_token}}

HTTP 200
"""

CA_NAME_CAPTURE = """
# Capture the Test CA's name from the global configuration. Captured once on the
# management Security Server and reused by every later CSR in the run.
GET https://{{@HOSTVAR@}}:4000/api/v1/certificate-authorities
X-XSRF-TOKEN: {{@P@_xsrf_token}}

HTTP 200

[Captures]
ca_name: jsonpath "$[0].name"
"""

# SESS_P/CAP_P split for the same reason as MEMBER_SIGN_KEY/MEMBER_CLIENT: a
# hosted member's service publish authenticates with the host SS's session
# but must operate on its OWN client_id, not the host's.
SERVICE_PUBLISH = """
############################################################
# @MEMBER_CODE@:@SUBSYSTEM@ -- publish @SERVICE_CODE@ (OPENAPI3)
############################################################

# Add the OpenAPI 3 service description. The Security Server parses servers.url
# from the spec as the forwarding target.
POST https://{{@HOSTVAR@}}:4000/api/v1/clients/{{@CAP_P@_client_id}}/service-descriptions
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}
{
  "url": "{{@SPECVAR@}}",
  "type": "OPENAPI3",
  "rest_service_code": "@SERVICE_CODE@"
}

HTTP 201

[Captures]
@CAP_P@_@SC@_description_id: jsonpath "$.id"

# Services are disabled when added -- enable it explicitly
PUT https://{{@HOSTVAR@}}:4000/api/v1/service-descriptions/{{@CAP_P@_@SC@_description_id}}/enable
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}

# setup.hurl@7.7.0: 200, although the OpenAPI model says 204.
HTTP 200
"""

SERVICE_ACL = """
# Grant @ACL_SUBJECT@ access to @SERVICE_CODE@ -- and nobody else.
# The omission is deliberate: @NEGATIVE@ is left out so the negative check in
# acceptance/2.6.md proves the ACL, not an accident of configuration.
POST https://{{@HOSTVAR@}}:4000/api/v1/clients/{{@CAP_P@_client_id}}/service-clients/@ACL_SUBJECT@/access-rights
X-XSRF-TOKEN: {{@SESS_P@_xsrf_token}}
{
  "items": [
    {
      "service_code": "@SERVICE_CODE@"
    }
  ]
}

HTTP 201
"""


def ss_prefix(dns_name: str) -> str:
    return dns_name.replace("-", "_")


# The once-only exchange itself is NOT generated here. scripts/acceptance.sh
# owns module 2.6's four assertions (happy path, right learner, asked once,
# negative) -- including the two that a Hurl scenario cannot make: exact-set
# equality of the assembled application, and the seeded-record comparison in
# scripts/assert_record.py. A second, weaker copy of the pack's headline check
# is worse than none: the two drift, and the weaker one passes.


def build_ss_file(member: dict, host_var: str, capture_ca_name: bool = False) -> str:
    """Full bring-up for one member Security Server: certs, registration, client."""
    m, sub_cfg, ss = member["member"], member["subsystem"], member["security_server"]
    prefix = ss_prefix(ss["dns_name"])
    conn = member.get("client", {}).get("connection_type", "HTTP")
    body = sub(
        SS_BRINGUP_INIT,
        SS=ss["dns_name"],
        SS_CODE=ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        P=prefix,
        CANAME=sub(CA_NAME_CAPTURE, HOSTVAR=host_var, P=prefix) if capture_ca_name else "",
    )
    body += sub(
        MEMBER_SIGN_KEY,
        SS_CODE=ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        SESS_P=prefix,
        CAP_P=prefix,
    )
    body += sub(SS_BRINGUP_REGISTER, HOSTVAR=host_var, P=prefix)
    body += sub(SS_ACTIVATE, HOSTVAR=host_var, P=prefix)
    body += sub(SS_TSA_POST, HOSTVAR=host_var, P=prefix)
    client_kwargs = dict(
        SS=ss["dns_name"],
        MEMBER_CODE=m["member_code"],
        SUBSYSTEM=sub_cfg["code"],
        CONNECTION_TYPE=conn,
        HOSTVAR=host_var,
        SESS_P=prefix,
        CAP_P=prefix,
    )
    body += sub(MEMBER_CLIENT_ADD, **client_kwargs)
    body += sub(MEMBER_CLIENT_REGISTER, **client_kwargs)
    return body


def build_service_file(member: dict, host_var: str, sess_p: str | None = None) -> str:
    m, sub_cfg, ss = member["member"], member["subsystem"], member["security_server"]
    cap_p = ss_prefix(ss["dns_name"])
    sess_p = sess_p or cap_p
    out = ""
    for svc in member.get("services") or []:
        service_code = svc["code"]
        sc = service_code.replace("-", "_")
        out += sub(
            SERVICE_PUBLISH,
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
            out += sub(
                SERVICE_ACL,
                SERVICE_CODE=service_code,
                HOSTVAR=host_var,
                SESS_P=sess_p,
                CAP_P=cap_p,
                ACL_SUBJECT=subject.replace("/", ":"),
                NEGATIVE="PROGRESSA:GOV:MOEYS:PEMIS",
            )
    return out


def build_hosted_client(member: dict, host_member: dict, host_var: str) -> str:
    """Register a member's subsystem as an extra client on an already-
    bootstrapped Security Server (the lite profile's PNIA/MoEYS-on-ss-plr
    pattern): a fresh SIGN key/cert for this member specifically, then the
    client-registration flow -- both authenticated with the HOST's session
    (sess_p), captured under this member's OWN namespace (cap_p).

    serialNumber in the SIGN-key subject names the *hosting* SS's own code
    (host_member's), not this member's nominal one from its own config --
    the cert genuinely lives on the host's token, and naming a server that
    was never brought up under this profile would be a lie in the cert.

    Client registration MUST come before the SIGN key: the owning member's
    SIGN key is valid because /initialization set owner_member_code, but a
    hosted member has no such relationship -- the signer rejects a member_id
    it doesn't yet recognize as a client with 400 client_not_found (confirmed
    live at P0 for lite, 2026-07-26).
    """
    m, sub_cfg = member["member"], member["subsystem"]
    conn = member.get("client", {}).get("connection_type", "HTTP")
    host_ss = host_member["security_server"]
    sess_p = ss_prefix(host_ss["dns_name"])
    cap_p = ss_prefix(member["security_server"]["dns_name"])
    body = sub(
        MEMBER_CLIENT_ADD,
        SS=host_ss["dns_name"],
        MEMBER_CODE=m["member_code"],
        SUBSYSTEM=sub_cfg["code"],
        CONNECTION_TYPE=conn,
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    body += sub(
        MEMBER_SIGN_KEY,
        SS_CODE=host_ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    body += sub(
        MEMBER_CLIENT_REGISTER,
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    return body


def main() -> None:
    manifest = load("manifest.yaml")
    identity = manifest["identity"]
    deployment = load("deployment.yaml")
    if deployment.get("target") != "docker-local":
        raise SystemExit(
            f"generate.py: deployment.yaml target {deployment.get('target')!r} is not "
            "supported -- only 'docker-local' is implemented today."
        )
    profile = deployment.get("profile", "full")
    if profile not in ("full", "lite"):
        raise SystemExit(f"generate.py: deployment.yaml profile must be 'full' or 'lite' (got {profile!r})")
    core = load("configs/x-road-bus/2.1.yaml")
    check_policy(core)
    env = read_env()
    members = discover_members(PACK, identity)
    # member:/subsystem: no longer live in configs/*.yaml (removed 2026-07-26,
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
    hosted_on_map = resolve_hosted_on_map(members, profile)

    print("generating Linkup Hurl scenarios (X-Road 7.7.0 admin APIs)")

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
    (PACK / "hurl" / "vars.env").write_text("\n".join(lines))
    print("  wrote hurl/vars.env")

    # -- 00 Central Server initialisation ----------------------------------
    body = f"""
############################################################
# Central Server -- initialisation (module 2.1)
############################################################

# Check that the Central Server UI is up
GET https://{{{{cs_host}}}}:4000

HTTP 200

# Log in to the Central Server
POST https://{{{{cs_host}}}}:4000/login
[FormParams]
username: {{{{cs_admin_user}}}}
password: {{{{cs_admin_password}}}}

HTTP 200
[Captures]
cs_xsrf_token: cookie "XSRF-TOKEN"

# Initialise the Central Server: instance identifier, address, token PIN
POST https://{{{{cs_host}}}}:4000/api/v1/initialization
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "software_token_pin": "{{{{token_pin}}}}",
  "instance_identifier": "{{{{xroad_instance}}}}",
  "central_server_address": "{{{{cs_host}}}}"
}}

# setup.hurl@7.7.0: 200, although the OpenAPI model says 201.
HTTP 200

# Add the member class. Progressa's federation admits government bodies only.
POST https://{{{{cs_host}}}}:4000/api/v1/member-classes
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "code": "{{{{member_class}}}}",
  "description": "{core['central_server']['member_classes'][0]['description']}"
}}

HTTP 201

# Log in to the Central Server's software token
PUT https://{{{{cs_host}}}}:4000/api/v1/tokens/0/login
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "password": "{{{{token_pin}}}}"
}}

HTTP *

# Internal configuration signing key -- signs the global conf the Security
# Servers download. Without it there is no anchor to hand out.
POST https://{{{{cs_host}}}}:4000/api/v1/configuration-sources/INTERNAL/signing-keys
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "key_label": "Internal signing key",
  "token_id": 0
}}

HTTP 200

# External configuration signing key -- signs the conf a federated instance
# would consume. Not federated in the demonstration, generated for completeness.
POST https://{{{{cs_host}}}}:4000/api/v1/configuration-sources/EXTERNAL/signing-keys
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "key_label": "External signing key",
  "token_id": 0
}}

HTTP 200
"""
    write("00-cs-init.hurl", "configs/x-road-bus/2.1.yaml", body)

    # -- 01 trust services --------------------------------------------------
    ts = core["trust_services"]
    body = f"""
############################################################
# Central Server -- trust services (module 2.1)
# The Test CA is the demonstration trust anchor. Certificates are read from the
# shared ca volume at {{file-root}}/ca; no renaming is needed with the
# xrddev-testca image, which writes ca.pem / ocsp.pem / tsa.pem itself.
############################################################

# Register the Test CA as a certification service
POST https://{{{{cs_host}}}}:4000/api/v1/certification-services
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
[MultipartFormData]
certificate_profile_info: {ts['certification_service']['certificate_profile']}
tls_auth: false
acme_server_directory_url: http://{{{{ca_host}}}}:8887
certificate: file,ca/ca.pem;

HTTP 201

[Captures]
ca_id: jsonpath "$.id"

# Register the OCSP responder against that CA
POST https://{{{{cs_host}}}}:4000/api/v1/certification-services/{{{{ca_id}}}}/ocsp-responders
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
[MultipartFormData]
url: {ts['certification_service']['ocsp_responder']['url'].replace('ca:', '{{ca_host}}:')}
certificate: file,ca/ocsp.pem;

HTTP 201

# Register the timestamping authority
POST https://{{{{cs_host}}}}:4000/api/v1/timestamping-services
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
[MultipartFormData]
url: {ts['timestamping_service']['url'].replace('ca:', '{{ca_host}}:')}
certificate: file,ca/tsa.pem;

HTTP 201
"""
    write("01-cs-trust-services.hurl", "configs/x-road-bus/2.1.yaml", body)

    # -- 02 members ---------------------------------------------------------
    body = f"""
############################################################
# Central Server -- members and subsystems (modules 2.1-2.5)
# PDGA owns the federation and provides the management services; the four
# education-sector members are registered here and attach their Security
# Servers later in the run.
############################################################

# {owner['name']} -- federation owner
POST https://{{{{cs_host}}}}:4000/api/v1/members
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "member_id": {{
    "member_class": "{{{{member_class}}}}",
    "member_code": "{owner['code']}"
  }},
  "member_name": "{owner['name']}"
}}

HTTP 201

# The MANAGEMENT subsystem, through which the CS's own management services run
POST https://{{{{cs_host}}}}:4000/api/v1/subsystems
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "subsystem_id": {{
    "member_class": "{{{{member_class}}}}",
    "member_code": "{owner['code']}",
    "subsystem_code": "{owner['management_subsystem']}"
  }}
}}

HTTP 201

# Nominate it as the Central Server's management service provider
PATCH https://{{{{cs_host}}}}:4000/api/v1/management-services-configuration
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "service_provider_id": "{{{{xroad_instance}}}}:{{{{member_class}}}}:{owner['code']}:{owner['management_subsystem']}"
}}

HTTP 200
"""
    for key in ("pnia", "plr", "moeys", "pnea"):
        m = members[key]["member"]
        s = members[key]["subsystem"]
        body += f"""
# {m['member_name']}
POST https://{{{{cs_host}}}}:4000/api/v1/members
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "member_id": {{
    "member_class": "{{{{member_class}}}}",
    "member_code": "{m['member_code']}"
  }},
  "member_name": "{m['member_name']}"
}}

HTTP 201

# {s['code']} -- {s['description']}
POST https://{{{{cs_host}}}}:4000/api/v1/subsystems
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "subsystem_id": {{
    "member_class": "{{{{member_class}}}}",
    "member_code": "{m['member_code']}",
    "subsystem_code": "{s['code']}"
  }}
}}

HTTP 201
"""
    write("02-cs-members.hurl", "configs/member-*/2.*.yaml", body)

    # -- 03 anchor ----------------------------------------------------------
    body = """
############################################################
# Central Server -- global configuration anchor
# Captured once and uploaded to every Security Server later in the run. This is
# why the scenarios are concatenated into a single Hurl file: captures do not
# cross file boundaries.
############################################################

GET https://{{cs_host}}:4000/api/v1/configuration-sources/INTERNAL/anchor/download
X-XSRF-TOKEN: {{cs_xsrf_token}}

HTTP 200

[Captures]
gconf_anchor: body
"""
    write("03-cs-anchor.hurl", "configs/x-road-bus/2.1.yaml", body)

    # -- 10 management security server -------------------------------------
    host_var = f"{pdga_prefix}_host"
    body = sub(
        SS_BRINGUP_INIT,
        SS=mgmt_ss["dns_name"],
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["code"],
        MEMBER_NAME=dn_escape(owner["name"]),
        HOSTVAR=host_var,
        P=pdga_prefix,
        CANAME=sub(CA_NAME_CAPTURE, HOSTVAR=host_var, P=pdga_prefix),
    )
    body += sub(
        MEMBER_SIGN_KEY,
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["code"],
        MEMBER_NAME=dn_escape(owner["name"]),
        HOSTVAR=host_var,
        SESS_P=pdga_prefix,
        CAP_P=pdga_prefix,
    )
    body += sub(SS_BRINGUP_REGISTER, HOSTVAR=host_var, P=pdga_prefix)
    body += sub(
        """
# Nominate this Security Server as the one hosting the management services
POST https://{{cs_host}}:4000/api/v1/management-services-configuration/register-provider
X-XSRF-TOKEN: {{cs_xsrf_token}}
{
  "security_server_id": "{{xroad_instance}}:{{member_class}}:@MEMBER_CODE@:@SS_CODE@"
}

HTTP 200

# Add the MANAGEMENT subsystem as a client of @SS@
POST https://{{@HOSTVAR@}}:4000/api/v1/clients
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "ignore_warnings": true,
  "client": {
    "member_class": "{{member_class}}",
    "member_code": "@MEMBER_CODE@",
    "subsystem_code": "@SUBSYSTEM@",
    "connection_type": "HTTP"
  }
}

HTTP 201

[Captures]
@P@_client_id: jsonpath "$.id"

# Read the management service addresses the Central Server publishes
GET https://{{cs_host}}:4000/api/v1/management-services-configuration
X-XSRF-TOKEN: {{cs_xsrf_token}}

HTTP 200

[Captures]
cs_management_service_address: jsonpath "$.services_address"
cs_management_service_wsdl: jsonpath "$.wsdl_address"

# Publish the management services (WSDL) on the management Security Server
POST https://{{@HOSTVAR@}}:4000/api/v1/clients/{{@P@_client_id}}/service-descriptions
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "url": "{{cs_management_service_wsdl}}",
  "type": "WSDL",
  "ignore_warnings": true
}

HTTP 201

[Captures]
@P@_management_description_id: jsonpath "$.id"
@P@_auth_cert_deletion_service_id: jsonpath "$.services[0].id"

# Point every management service at the CS's services address
PATCH https://{{@HOSTVAR@}}:4000/api/v1/services/{{@P@_auth_cert_deletion_service_id}}
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "ignore_warnings": true,
  "ssl_auth": false,
  "ssl_auth_all": true,
  "timeout": 60,
  "timeout_all": true,
  "url": "{{cs_management_service_address}}",
  "url_all": true
}

# Grant the security-server-owners global group access to the management services
POST https://{{@HOSTVAR@}}:4000/api/v1/clients/{{@P@_client_id}}/service-clients/{{xroad_instance}}:security-server-owners/access-rights
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "items": [
    { "service_code": "authCertDeletion" },
    { "service_code": "clientDeletion" },
    { "service_code": "clientReg" },
    { "service_code": "ownerChange" },
    { "service_code": "clientEnable" },
    { "service_code": "clientDisable" },
    { "service_code": "addressChange" },
    { "service_code": "clientRename" },
    { "service_code": "maintenanceModeEnable" },
    { "service_code": "maintenanceModeDisable" }
  ]
}

# Enable the management service description
PUT https://{{@HOSTVAR@}}:4000/api/v1/service-descriptions/{{@P@_management_description_id}}/enable
X-XSRF-TOKEN: {{@P@_xsrf_token}}

HTTP 200
""",
        SS=mgmt_ss["dns_name"],
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["code"],
        SUBSYSTEM=owner["management_subsystem"],
        HOSTVAR=host_var,
        P=pdga_prefix,
    )
    body += sub(SS_ACTIVATE, HOSTVAR=host_var, P=pdga_prefix)
    body += sub(TSA_CAPTURE, HOSTVAR=host_var, P=pdga_prefix)
    body += sub(SS_TSA_POST, HOSTVAR=host_var, P=pdga_prefix)
    write("10-ss-pdga.hurl", "configs/x-road-bus/2.1.yaml", body)

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
            write(
                f"{num}-ss-{key}.hurl",
                f"configs/member-{key}/{member['module']}.yaml",
                f"# lite profile: {key.upper()} is hosted as an extra client on "
                f"ss-{hosted_on} -- see {ss_scenario_no[hosted_on]}-ss-{hosted_on}.hurl. "
                "The full-profile bring-up below is not run under lite.\n",
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
                "acceptance/2.6.md has a real,\n# registered-but-unauthorised caller "
                "to make the denied request from.\n"
            )
        write(
            f"{num}-services-{key}.hurl",
            f"configs/member-{key}/{member['module']}.yaml",
            content,
        )

    # Module 2.6 -- the once-only exchange -- has no scenario by design; see the
    # note above probe data. scripts/acceptance.sh owns it.

    # -- hurl/topology.json --------------------------------------------------
    # Consumed by apps/console/truth.py so the demo console cannot describe a
    # federation different from the one this profile actually deploys. Not
    # git-committed -- same convention as hurl/scenarios/ and hurl/vars.env
    # (regenerated fresh every run, never staged; see hurl/README.md).
    # Port allocation order: "pdga" (the owner, not a discovered member)
    # plus every SS-owning (unhosted) member, sorted alphabetically by key --
    # the fresh-allocation order this plan's Task 3 specifies. Pinned ports
    # win regardless of position, so this ordering only ever affects a
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
        "profile": profile,
        "instance": instance,
        "member_class": member_class,
        "central_server": {"host": core["central_server"]["address"], "ui_port": 4000},
        "security_servers": security_servers,
        "subsystems": subsystems,
    }
    (PACK / "hurl" / "topology.json").write_text(json.dumps(topology, indent=2) + "\n")
    print("  wrote hurl/topology.json")

    print(f"\ndone -- {instance} federation, "
          f"{len(manifest['identifiers']['members'])} members, "
          f"{len(manifest['identifiers']['services'])} services "
          f"(module 2.6 is proved by scripts/acceptance.sh, not by a scenario)")


if __name__ == "__main__":
    main()
