# Deployment Spec + Working Lite Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended — see the note at the end on why subagent-driven-development's worktree isolation doesn't fit; same reasoning as the prior KP2 plan) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `deployment.yaml` as the analyst-facing spec (topology profile, X-Road version pins), shrink `.env` to secrets only, and make `profile: lite` a genuinely working 3-Security-Server deployment instead of the explicit refusal `hurl/run-linkup.sh` gives today.

**Architecture:** X-Road's SIGN-key CSR API already accepts a `member_id` distinct from the Security Server's own owner — that's the real mechanism for "one physical SS, many members' signing identities." Extract the SIGN-key-generation and client-registration blocks out of `hurl/generate.py`'s monolithic per-server template into two-prefix-parameterized functions (`sess_p` = whose session to authenticate with, `cap_p` = this member's own Hurl-capture namespace), reused unchanged for full-mode's own-server case (`sess_p == cap_p`) and newly for lite-mode's hosted-client case (`sess_p` = the host SS's, `cap_p` = the hosted member's own).

**Tech Stack:** Same as the prior KP2 plan — Docker Compose v2/colima, X-Road 7.7.0 admin REST APIs, Hurl (containerized), Python 3 + PyYAML, bash.

## Global Constraints

- `deployment.yaml` is git-committed, never contains secrets. `.env` holds only `XROAD_TOKEN_PIN`, `XROAD_ADMIN_USER`, `XROAD_ADMIN_PASSWORD`.
- `hurl/scenarios/*.hurl` and `hurl/vars.env` stay generated artifacts — every change goes through `hurl/generate.py`, never hand-edited.
- The full-profile refactor (Task 3) must produce **byte-identical** generated scenarios to today's output before any new lite logic is added — verify with a diff, not by inspection.
- `LITE_HOSTED_ON` (generate.py) mirrors `scripts/lib.sh`'s `HOST_SS` lite branch — both encode the same fixed fact (ss-plr hosts PNIA + MoEYS under lite) and must move together if this pack's lite topology ever changes. This is a deliberate, commented, minimal duplication — not a new general N-way hosting scheme (out of scope, see the design spec).
- Rename/reuse support and non-Docker deployment targets are explicitly out of scope — do not add speculative flexibility for them here.
- Commit after every task.

---

### Task 1: Create `deployment.yaml`, shrink `.env.example`

**Files:**
- Create: `deployment.yaml`
- Modify: `.env.example`

**Interfaces:**
- Produces: `deployment.yaml`'s `profile`/`xroad.*` fields, which Task 2 wires into `lib.sh` and Task 4 wires into `generate.py`

- [ ] **Step 1: create `deployment.yaml`**

```yaml
# deployment.yaml — the KP2 analyst-facing deployment spec.
# Secrets (token PIN, admin password) stay in .env — never here, this file is
# git-committed. Do not hand-edit configs/*.yaml or hurl/scenarios/ to change
# deployment shape; change this file and regenerate (hurl/generate.py).

target: docker-local   # only supported value today. Future: itu-cloud, vm (PLAN.md §9)

profile: full           # full = 5 Security Servers. lite = 3 (PNIA + MoEYS
                        # hosted as extra clients on ss-plr — see PLAN.md §2)

xroad:
  version: 7.7.0
  cs_tag: noble-7.7.0
  testca_tag: "latest@sha256:018e9f6ea04634ec61a3d44abee9a86d9dc4f6a2508296fd8db95d272c16c0c5"
```

- [ ] **Step 2: shrink `.env.example`**

Read the current file first (`Read .env.example`), then replace its full content with:

```
# KP2 build pack — copy to .env and adjust. Demo values only.
# Deployment shape (topology profile, X-Road version pins) lives in
# deployment.yaml, not here — this file is secrets only and is gitignored.
XROAD_TOKEN_PIN=Progressa123!  # soft-token PIN (all servers, demo only)
XROAD_ADMIN_USER=xrd
XROAD_ADMIN_PASSWORD=secret    # SS admin UI/API. CS test image is fixed xrd/secret.
```

- [ ] **Step 3: commit**

```bash
git add deployment.yaml .env.example
git commit -m "feat: add deployment.yaml spec file; shrink .env to secrets only

Topology profile and X-Road version pins move to a new, git-committed
deployment.yaml -- the analyst-facing deployment spec. .env keeps only the
two real secrets. Not wired into lib.sh/generate.py yet (Tasks 2-4)."
```

---

### Task 2: Wire `scripts/lib.sh` to read `deployment.yaml`

**Files:**
- Modify: `scripts/lib.sh`

**Interfaces:**
- Consumes: `deployment.yaml` (Task 1)
- Produces: `LITE` (0/1), `XROAD_VERSION`, `XROAD_CS_TAG`, `TESTCA_TAG` exported into the process environment before any `docker compose` invocation — consumed by `docker-compose.yml`'s existing `${VAR:-default}` substitution and by every script that sources `lib.sh`

- [ ] **Step 1: move `yq_get()`'s definition earlier and read the spec file**

Read `scripts/lib.sh` first to get its exact current content. Replace:

```bash
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$PACK_DIR/.env" ] && set -a && . "$PACK_DIR/.env" && set +a
export PACK_DIR

# Full topology by default; LITE=1 drops ss-pnia/ss-moeys (compose profile "full")
# and hosts their subsystems on ss-plr instead.
COMPOSE=(docker compose -f "$PACK_DIR/docker-compose.yml")
[ "${LITE:-0}" != "1" ] && COMPOSE+=(--profile full)
```

with:

```bash
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$PACK_DIR/.env" ] && set -a && . "$PACK_DIR/.env" && set +a
export PACK_DIR

# yq wrapper (python fallback: hard deps stay curl+jq+python3). Defined here,
# ahead of its first use below, because deployment.yaml is now read before
# COMPOSE is built. Clean error on a missing key instead of a traceback.
yq_get() { python3 -c "
import sys, yaml
try:
    doc = yaml.safe_load(open('$1'))
    node = doc
    for part in '$2'.split('.'):
        node = node[int(part)] if part.isdigit() else node[part]
    print(node)
except (KeyError, IndexError, TypeError):
    sys.exit('yq_get: no key \\'$2\\' in $1')
"; }

# deployment.yaml is the analyst-facing spec (topology profile, X-Road version
# pins); .env carries only secrets. See
# docs/superpowers/specs/2026-07-26-deployment-spec-and-lite-profile-design.md.
DEPLOY_SPEC="$PACK_DIR/deployment.yaml"
case "$(yq_get "$DEPLOY_SPEC" profile)" in
  lite) LITE=1 ;;
  full) LITE=0 ;;
  *) echo "lib.sh: deployment.yaml profile must be 'full' or 'lite'" >&2; exit 1 ;;
esac
export XROAD_VERSION=$(yq_get "$DEPLOY_SPEC" xroad.version)
export XROAD_CS_TAG=$(yq_get "$DEPLOY_SPEC" xroad.cs_tag)
export TESTCA_TAG=$(yq_get "$DEPLOY_SPEC" xroad.testca_tag)

# Full topology by default; profile: lite (deployment.yaml) drops ss-pnia/
# ss-moeys (compose profile "full") and hosts their subsystems on ss-plr instead.
COMPOSE=(docker compose -f "$PACK_DIR/docker-compose.yml")
[ "${LITE:-0}" != "1" ] && COMPOSE+=(--profile full)
```

- [ ] **Step 2: delete the now-duplicate `yq_get()` definition at the bottom of the file**

Find and remove the original `yq_get()` function block that used to live at the end of `lib.sh` (it's now defined earlier, in Step 1) — leave everything else in that region (the trailing comment about `retry` being exported, `export -f log fail retry api_key api`, etc.) untouched.

- [ ] **Step 3: verify**

```bash
. scripts/lib.sh
echo "LITE=$LITE XROAD_VERSION=$XROAD_VERSION XROAD_CS_TAG=$XROAD_CS_TAG TESTCA_TAG=$TESTCA_TAG"
```
Expected (deployment.yaml still says `profile: full` from Task 1): `LITE=0 XROAD_VERSION=7.7.0 XROAD_CS_TAG=noble-7.7.0 TESTCA_TAG=latest@sha256:018e9f...c16c0c5`

```bash
sed -i.bak 's/profile: full/profile: lite/' deployment.yaml && . scripts/lib.sh && echo "LITE=$LITE" && mv deployment.yaml.bak deployment.yaml
```
Expected: `LITE=1`, and `deployment.yaml` restored to `profile: full` by the final `mv` (don't leave it changed — Task 4/5 will flip it deliberately when it's time to test lite).

- [ ] **Step 4: commit**

```bash
git add scripts/lib.sh
git commit -m "feat: lib.sh reads deployment.yaml for profile + X-Road version pins

LITE is now derived from deployment.yaml's profile field, not .env — .env no
longer declares it at all. XROAD_VERSION/XROAD_CS_TAG/TESTCA_TAG are exported
from deployment.yaml's xroad: block so docker-compose.yml's existing
\${VAR:-default} substitution keeps working unchanged; Compose itself never
parses deployment.yaml."
```

---

### Task 3: Refactor `hurl/generate.py`'s templates (no behavior change yet)

**Files:**
- Modify: `hurl/generate.py`

**Interfaces:**
- Produces: `MEMBER_SIGN_KEY(HOSTVAR, SESS_P, CAP_P, MEMBER_CODE, MEMBER_NAME, SS_CODE)` and `MEMBER_CLIENT(HOSTVAR, SESS_P, CAP_P, SS, MEMBER_CODE, SUBSYSTEM, CONNECTION_TYPE)` template functions, reused by both `build_ss_file` (this task, `sess_p == cap_p`) and `build_hosted_client` (Task 4, `sess_p != cap_p`)
- Consumes: nothing new — pure refactor of existing templates

This task must be a **no-op for full-mode output** — verified by diffing generated scenarios before and after.

- [ ] **Step 1: save the current full-mode output for the diff check**

```bash
python3 hurl/generate.py > /tmp/gen-before.log 2>&1
cp -r hurl/scenarios /tmp/scenarios-before
cp hurl/vars.env /tmp/vars.env-before
```

- [ ] **Step 2: split `SS_BRINGUP` into three pieces and rename/parameterize `SS_CLIENT`**

Read `hurl/generate.py` first. Replace the entire block from `SS_BRINGUP = """` through the end of `SS_CLIENT`'s closing `"""` (currently everything between the `SS_BRINGUP` assignment and the `CA_NAME_CAPTURE` assignment) with:

```python
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
# keeping its own capture namespace. build_ss_file calls this with
# SESS_P == CAP_P; build_hosted_client (lite profile) does not.
MEMBER_CLIENT = """
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
```

Note this new block **omits** `SS_CLIENT` (renamed to `MEMBER_CLIENT`, now above) and keeps everything else (`SS_ACTIVATE`, `TSA_CAPTURE`, `SS_TSA_POST`) textually identical to before.

- [ ] **Step 3: update `SERVICE_PUBLISH` and `SERVICE_ACL`**

Replace:

```python
SERVICE_PUBLISH = """
############################################################
# @MEMBER_CODE@:@SUBSYSTEM@ -- publish @SERVICE_CODE@ (OPENAPI3)
############################################################

# Add the OpenAPI 3 service description. The Security Server parses servers.url
# from the spec as the forwarding target.
POST https://{{@HOSTVAR@}}:4000/api/v1/clients/{{@P@_client_id}}/service-descriptions
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "url": "{{@SPECVAR@}}",
  "type": "OPENAPI3",
  "rest_service_code": "@SERVICE_CODE@"
}

HTTP 201

[Captures]
@P@_@SC@_description_id: jsonpath "$.id"

# Services are disabled when added -- enable it explicitly
PUT https://{{@HOSTVAR@}}:4000/api/v1/service-descriptions/{{@P@_@SC@_description_id}}/enable
X-XSRF-TOKEN: {{@P@_xsrf_token}}

# setup.hurl@7.7.0: 200, although the OpenAPI model says 204.
HTTP 200
"""

SERVICE_ACL = """
# Grant @ACL_SUBJECT@ access to @SERVICE_CODE@ -- and nobody else.
# The omission is deliberate: @NEGATIVE@ is left out so the negative check in
# acceptance/2.6.md proves the ACL, not an accident of configuration.
POST https://{{@HOSTVAR@}}:4000/api/v1/clients/{{@P@_client_id}}/service-clients/@ACL_SUBJECT@/access-rights
X-XSRF-TOKEN: {{@P@_xsrf_token}}
{
  "items": [
    {
      "service_code": "@SERVICE_CODE@"
    }
  ]
}

HTTP 201
"""
```

with:

```python
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
```

- [ ] **Step 4: update `build_ss_file()` to compose the three pieces**

Replace:

```python
def build_ss_file(member: dict, host_var: str, capture_ca_name: bool = False) -> str:
    """Full bring-up for one member Security Server: certs, registration, client."""
    m, sub_cfg, ss = member["member"], member["subsystem"], member["security_server"]
    prefix = ss_prefix(ss["dns_name"])
    conn = member.get("client", {}).get("connection_type", "HTTP")
    body = sub(
        SS_BRINGUP,
        SS=ss["dns_name"],
        SS_CODE=ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        P=prefix,
        CANAME=sub(CA_NAME_CAPTURE, HOSTVAR=host_var, P=prefix) if capture_ca_name else "",
    )
    body += sub(SS_ACTIVATE, HOSTVAR=host_var, P=prefix)
    body += sub(SS_TSA_POST, HOSTVAR=host_var, P=prefix)
    body += sub(
        SS_CLIENT,
        SS=ss["dns_name"],
        MEMBER_CODE=m["member_code"],
        SUBSYSTEM=sub_cfg["code"],
        CONNECTION_TYPE=conn,
        HOSTVAR=host_var,
        P=prefix,
    )
    return body
```

with:

```python
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
    body += sub(
        MEMBER_CLIENT,
        SS=ss["dns_name"],
        MEMBER_CODE=m["member_code"],
        SUBSYSTEM=sub_cfg["code"],
        CONNECTION_TYPE=conn,
        HOSTVAR=host_var,
        SESS_P=prefix,
        CAP_P=prefix,
    )
    return body
```

- [ ] **Step 5: update `build_service_file()` to accept an optional `sess_p` override**

Replace:

```python
def build_service_file(member: dict, host_var: str) -> str:
    m, sub_cfg, ss = member["member"], member["subsystem"], member["security_server"]
    prefix = ss_prefix(ss["dns_name"])
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
            P=prefix,
            SPECVAR=f"{m['member_code'].lower()}_spec_url",
        )
        for subject in svc.get("access") or []:
            out += sub(
                SERVICE_ACL,
                SERVICE_CODE=service_code,
                HOSTVAR=host_var,
                P=prefix,
                ACL_SUBJECT=subject.replace("/", ":"),
                NEGATIVE="PROGRESSA:GOV:MOEYS:PEMIS",
            )
    return out
```

with:

```python
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
```

- [ ] **Step 6: regenerate and diff against the pre-refactor baseline**

```bash
python3 hurl/generate.py > /tmp/gen-after.log 2>&1
diff -r /tmp/scenarios-before hurl/scenarios
echo "SCENARIOS DIFF EXIT: $?"
diff /tmp/vars.env-before hurl/vars.env
echo "VARS DIFF EXIT: $?"
diff /tmp/gen-before.log /tmp/gen-after.log
```
Expected: all three diffs empty, all exit codes 0. **If any diff is non-empty, this task is not done** — find and fix the discrepancy before proceeding; do not paper over it by updating the baseline.

- [ ] **Step 7: full static + syntax check**

```bash
python3 hurl/check_scenarios.py
hurl/run-linkup.sh --dry-run
docker run --rm --entrypoint hurlfmt -v "$PWD/hurl:/hurl-src:ro" ghcr.io/orange-opensource/hurl:latest \
  --check /hurl-src/.build/setup.hurl
echo "hurlfmt exit: $?"
```
Expected: `check_scenarios.py` prints `OK`, `run-linkup.sh --dry-run` reports the same request count as before the refactor (145, per the prior KP2 plan's P0 measurement), `hurlfmt` exits 0.

- [ ] **Step 8: commit**

```bash
git add hurl/generate.py
git commit -m "refactor: extract SESS_P/CAP_P-parameterized SIGN-key and client-registration templates

Pure refactor, no behavior change -- verified by diffing full-mode generated
scenarios before and after (identical). Splits SS_BRINGUP into
SS_BRINGUP_INIT + MEMBER_SIGN_KEY + SS_BRINGUP_REGISTER, renames SS_CLIENT to
MEMBER_CLIENT, and adds the same SESS_P/CAP_P split to SERVICE_PUBLISH/
SERVICE_ACL. build_ss_file/build_service_file call these with sess_p==cap_p
(full mode); Task 4 adds the lite-mode caller that doesn't."
```

---

### Task 4: Make `profile: lite` actually generate a working topology

**Files:**
- Modify: `hurl/generate.py`, `hurl/run-linkup.sh`

**Interfaces:**
- Consumes: `deployment.yaml`'s `profile` (Task 1/2), the templates from Task 3
- Produces: a lite-mode scenario set where PNIA/MoEYS's SIGN-key + client-registration + service-publish content is appended into `21-ss-plr.hurl` instead of their own files

- [ ] **Step 1: add the hosting map and read `deployment.yaml`'s profile**

Add near `CS_USER, CS_PASS = "xrd", "secret"`:

```python
# Under the lite profile, these members are NOT brought up as their own
# Security Server -- they're extra clients on the shared provider SS. Mirrors
# scripts/lib.sh's HOST_SS lite branch; the two must move together if this
# pack's lite topology ever changes (only one lite arrangement exists today,
# so this is a fixed fact of docker-compose.yml, not a general N-way mapping
# -- see docs/superpowers/specs/2026-07-26-deployment-spec-and-lite-profile-design.md).
LITE_HOSTED_ON = {"pnia": "plr", "moeys": "plr"}
```

In `main()`, right after `manifest = load("manifest.yaml")`, add:

```python
    deployment = load("deployment.yaml")
    if deployment.get("target") != "docker-local":
        raise SystemExit(
            f"generate.py: deployment.yaml target {deployment.get('target')!r} is not "
            "supported -- only 'docker-local' is implemented today."
        )
    profile = deployment.get("profile", "full")
    if profile not in ("full", "lite"):
        raise SystemExit(f"generate.py: deployment.yaml profile must be 'full' or 'lite' (got {profile!r})")
```

- [ ] **Step 2: add `build_hosted_client()`**

Add right after `build_service_file()`:

```python
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
    """
    m, sub_cfg = member["member"], member["subsystem"]
    conn = member.get("client", {}).get("connection_type", "HTTP")
    host_ss = host_member["security_server"]
    sess_p = ss_prefix(host_ss["dns_name"])
    cap_p = ss_prefix(member["security_server"]["dns_name"])
    body = sub(
        MEMBER_SIGN_KEY,
        SS_CODE=host_ss["code"],
        MEMBER_CODE=m["member_code"],
        MEMBER_NAME=dn_escape(m["member_name"]),
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    body += sub(
        MEMBER_CLIENT,
        SS=host_ss["dns_name"],
        MEMBER_CODE=m["member_code"],
        SUBSYSTEM=sub_cfg["code"],
        CONNECTION_TYPE=conn,
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    return body
```

- [ ] **Step 3: branch the "2x member security servers" loop on `profile`**

Replace:

```python
    # -- 2x member security servers ----------------------------------------
    # tsa_name / tsa_url and ca_name are captured on the management server
    # above; the member servers reuse them, which is why 10- must run first.
    order = [("20", "pnia"), ("21", "plr"), ("22", "moeys"), ("23", "pnea")]
    for num, key in order:
        member = members[key]
        dns = member["security_server"]["dns_name"]
        write(
            f"{num}-ss-{key}.hurl",
            f"configs/member-{key}/{member['module']}.yaml",
            build_ss_file(member, f"{ss_prefix(dns)}_host"),
        )
```

with:

```python
    # -- 2x member security servers ----------------------------------------
    # tsa_name / tsa_url and ca_name are captured on the management server
    # above; the member servers reuse them, which is why 10- must run first.
    order = [("20", "pnia"), ("21", "plr"), ("22", "moeys"), ("23", "pnea")]
    num_for_key = dict((k, n) for n, k in order)
    for num, key in order:
        member = members[key]
        dns = member["security_server"]["dns_name"]
        host_var = f"{ss_prefix(dns)}_host"
        hosted_on = LITE_HOSTED_ON.get(key) if profile == "lite" else None
        if hosted_on:
            # Not brought up as its own server under lite -- its content is
            # appended into its host's own file below instead. Still write a
            # stub here so this module's manifest.yaml scenario claim keeps
            # resolving to a real, existing file.
            write(
                f"{num}-ss-{key}.hurl",
                f"configs/member-{key}/{member['module']}.yaml",
                f"# lite profile: {key.upper()} is hosted as an extra client on "
                f"ss-{hosted_on} -- see {num_for_key[hosted_on]}-ss-{hosted_on}.hurl. "
                "The full-profile bring-up below is not run under lite.\n",
            )
            continue
        body = build_ss_file(member, host_var)
        if profile == "lite":
            for hosted_key, host_key in LITE_HOSTED_ON.items():
                if host_key == key:
                    body += build_hosted_client(members[hosted_key], member, host_var)
        write(f"{num}-ss-{key}.hurl", f"configs/member-{key}/{member['module']}.yaml", body)
```

- [ ] **Step 4: branch the "3x service publication" loop on `profile`**

Replace:

```python
    # -- 3x service publication + ACLs -------------------------------------
    for num, key in [("30", "pnia"), ("31", "plr"), ("32", "moeys")]:
        member = members[key]
        dns = member["security_server"]["dns_name"]
        content = build_service_file(member, f"{ss_prefix(dns)}_host")
```

with:

```python
    # -- 3x service publication + ACLs -------------------------------------
    for num, key in [("30", "pnia"), ("31", "plr"), ("32", "moeys")]:
        member = members[key]
        dns = member["security_server"]["dns_name"]
        hosted_on = LITE_HOSTED_ON.get(key) if profile == "lite" else None
        if hosted_on:
            host_dns = members[hosted_on]["security_server"]["dns_name"]
            content = build_service_file(member, f"{ss_prefix(host_dns)}_host", sess_p=ss_prefix(host_dns))
        else:
            content = build_service_file(member, f"{ss_prefix(dns)}_host")
```

(the lines below this, writing `content` to `{num}-services-{key}.hurl`, are unchanged)

- [ ] **Step 5: remove `run-linkup.sh`'s lite refusal**

Read `hurl/run-linkup.sh` first. Delete this block entirely:

```bash
# The scenarios describe the full five-server topology: they initialise ss-pnia
# and ss-moeys as servers in their own right, which the lite profile does not
# start. Fail loudly rather than 20 minutes in, on a host that was never going
# to have those containers. Lite support is a generator change (host the two
# subsystems as extra clients of ss-plr) — see PLAN.md §2.
if [ "${LITE:-0}" = "1" ]; then
  fail "LITE=1 is not supported by the Hurl scenario set — it stands up the
     full five-server federation. Unset LITE, or configure the lite profile by hand
     per runbook.md 'Admin UIs (manual fallback)'."
fi
```

- [ ] **Step 6: static verification with `profile: lite`**

```bash
sed -i.bak 's/profile: full/profile: lite/' deployment.yaml
python3 hurl/generate.py
python3 hurl/check_scenarios.py
```
Expected: `check_scenarios.py` reports `OK` — this is the first real test that the lite-mode captures/variables line up correctly (order, no undefined-before-use, member/subsystem/service presence in the concatenated body).

```bash
cat hurl/scenarios/20-ss-pnia.hurl
```
Expected: the stub comment from Step 3, no requests.

```bash
grep -c "PROGRESSA:GOV:PNIA" hurl/scenarios/21-ss-plr.hurl
```
Expected: non-zero — PNIA's hosted-client content is physically inside `21-ss-plr.hurl`.

```bash
hurl/run-linkup.sh --dry-run
docker run --rm --entrypoint hurlfmt -v "$PWD/hurl:/hurl-src:ro" ghcr.io/orange-opensource/hurl:latest \
  --check /hurl-src/.build/setup.hurl
echo "hurlfmt exit: $?"
```
Expected: dry-run succeeds with a **smaller** request count than full mode's 145 (fewer per-server init/AUTH-cert steps for the two hosted members); `hurlfmt` exits 0.

- [ ] **Step 7: commit**

```bash
git add hurl/generate.py hurl/run-linkup.sh
git commit -m "feat: profile: lite now generates a real 3-Security-Server topology

PNIA and MoEYS's SIGN key/cert + client registration + service publish are
generated as hosted-client fragments appended into 21-ss-plr.hurl (their own
20-ss-pnia.hurl/22-ss-moeys.hurl become stub files documenting where their
content actually runs, so manifest.yaml's scenario claims keep resolving).
Removed run-linkup.sh's explicit refusal. Static checks (check_scenarios.py,
hurlfmt --check) pass; live verification is Task 5."
```

---

### Task 5: Live-test the lite profile end to end

**Files:** none — pure verification, following the same rigor the prior KP2 plan needed for P0 (expect at least one real bug; do not assume the design is correct just because it's internally consistent)

**Interfaces:**
- Consumes: everything from Tasks 1–4, plus `deployment.yaml` set to `profile: lite`

- [ ] **Step 1: confirm `deployment.yaml` says `profile: lite`** (left set from Task 4 Step 6 — confirm, don't re-set)

```bash
grep '^profile:' deployment.yaml
```
Expected: `profile: lite`

- [ ] **Step 2: purge and stand up the lite topology from zero**

```bash
scripts/teardown.sh --purge
hurl/run-linkup.sh > /tmp/lite-run-1.log 2>&1
rc=$?
echo "ACTUAL_EXIT_CODE=$rc" >> /tmp/lite-run-1.log
echo "ACTUAL_EXIT_CODE=$rc"
```
Expected: `0`. If not, the same debugging approach as the original P0 spike applies: check `docker compose --profile full logs <container>` (note: lite mode doesn't need `--profile full`; use plain `docker compose -f docker-compose.yml -f hurl/compose.hurl.yml logs`), find the failing request in the log, and trace it back to which template/function produced it. A `BadRequest`/`404`/`AccessDenied`-shaped failure on a PNIA/MoEYS call most likely means a SESS_P/CAP_P mix-up (wrong session used, or a capture referencing the wrong prefix) — grep the generated `21-ss-plr.hurl` for the exact request and compare its `{{...}}` references against Task 3/4's design.

- [ ] **Step 3: confirm the topology is genuinely 3 servers**

```bash
docker compose -f docker-compose.yml -f hurl/compose.hurl.yml ps --format "table {{.Name}}\t{{.Status}}"
```
Expected: `cs`, `ca`, `ss-pdga`, `ss-pnea`, `ss-plr`, `app-pnia`, `app-plr`, `app-pemis` — **no** `ss-pnia`, **no** `ss-moeys` containers at all.

- [ ] **Step 4: confirm PNIA and MoEYS are registered — on ss-plr**

```bash
jar=$(mktemp)
curl -ksf -c "$jar" -X POST "https://localhost:3000/login" --data-urlencode "username=xrd" --data-urlencode "password=secret" >/dev/null
token=$(awk '$6 == "XSRF-TOKEN" { print $7 }' "$jar")
curl -ksf -b "$jar" -X GET "https://localhost:3000/api/v1/clients" -H "X-XSRF-TOKEN: ${token}" | python3 -m json.tool
rm -f "$jar"
```
Expected: four clients on ss-plr now — `PROGRESSA:GOV:PLR` (owner), `PROGRESSA:GOV:PLR:ENROLMENT`, `PROGRESSA:GOV:PNIA:IDENTITY`, `PROGRESSA:GOV:MOEYS:PEMIS` — all `"status": "REGISTERED"`.

- [ ] **Step 5: seed and run the acceptance suite**

```bash
scripts/seed.sh
scripts/acceptance.sh
```
Expected: `ACCEPTANCE GREEN` — this exercises `lib.sh`'s `HOST_SS` lite branch (already correct from before this plan) against the newly-real lite topology, including the 2.6.4 negative check (MOEYS:PEMIS denied via *its own* SS, which under lite is ss-plr).

- [ ] **Step 6: if any step fails, root-cause and fix in the relevant task's file, then repeat from Step 2 after a purge — do not weaken a check to make it pass**

- [ ] **Step 7: record the result**

Note the actual RAM footprint (`docker stats --no-stream`) and stand-up time for the lite profile — this replaces the runbook's placeholder guess.

---

### Task 6: Regression-check the full profile still works

**Files:** none — pure verification

**Interfaces:**
- Consumes: everything from Tasks 1–4 (the refactor's diff check in Task 3 already proved static output is unchanged; this proves the *live* behavior still is too)

- [ ] **Step 1: switch back to full and redeploy from zero**

```bash
sed -i.bak 's/profile: lite/profile: full/' deployment.yaml && rm -f deployment.yaml.bak
scripts/teardown.sh --purge
hurl/run-linkup.sh > /tmp/full-regression.log 2>&1
rc=$?
echo "ACTUAL_EXIT_CODE=$rc" >> /tmp/full-regression.log
echo "ACTUAL_EXIT_CODE=$rc"
```
Expected: `0`, full 5-Security-Server topology.

- [ ] **Step 2: seed and accept**

```bash
scripts/seed.sh
scripts/acceptance.sh
```
Expected: `ACCEPTANCE GREEN`, same as the prior plan's Task 9/12.

- [ ] **Step 3: commit only if this step forced a fix; otherwise nothing to commit here**

---

### Task 7: Documentation

**Files:**
- Modify: `hurl/README.md`, `runbook.md`, `README.md`, `PLAN.md`

**Interfaces:**
- Consumes: the measured lite-profile RAM/timing from Task 5 Step 7

- [ ] **Step 1: `hurl/README.md` — remove the lite refusal note, explain the new shape**

Remove the "**`LITE=1` is not supported.**" bullet from "Known limits" (it named the exact gap this plan closes). Add in its place:

```markdown
- **Lite profile hosts PNIA and MoEYS on ss-plr.** Their SIGN key/cert and
  client registration are generated as fragments appended into
  `21-ss-plr.hurl`, not their own files — `20-ss-pnia.hurl`/`22-ss-moeys.hurl`
  become stubs (still written, so `manifest.yaml`'s scenario claims keep
  resolving) explaining where the real content actually runs. See
  `generate.py`'s `LITE_HOSTED_ON` and `build_hosted_client()`.
```

- [ ] **Step 2: `runbook.md` — replace the `.env` `LITE=1` instruction with `deployment.yaml`**

Find the prerequisites section's `LITE=1` instruction and the RAM sizing note (added in the prior plan's Task 3); update both to reference `deployment.yaml`'s `profile:` field instead of `.env`'s `LITE`, and add the measured lite-profile RAM/timing from Task 5 Step 7 alongside the existing full-profile figures.

- [ ] **Step 3: `README.md` — update the lite-support claim**

Find the line added in the prior plan ("the compose topology has a three-server lite profile, but the generated Hurl scenario set does not yet support it") and update it to say both profiles are supported, referencing `deployment.yaml`.

- [ ] **Step 4: `PLAN.md` §9 — record what's tracked separately**

Add a line noting that full rename/reuse support for a different country or sector, and non-Docker deployment targets, are tracked as separate specs (not yet started), distinct from this plan's `deployment.yaml`/lite-profile work.

- [ ] **Step 5: commit**

```bash
git add hurl/README.md runbook.md README.md PLAN.md
git commit -m "docs: deployment.yaml + working lite profile — update runbook, READMEs, PLAN.md §9"
```

---

## Self-Review Notes

**Spec coverage:** the design doc's four sections (deployment.yaml shape, generator refactor, live testing, docs) map to Tasks 1–2, 3–4, 5–6, 7 respectively.

**Placeholder scan:** every code block above is the actual current file content (re-read fresh immediately before writing this plan, after the earlier `dn_escape` edit shifted line numbers) or the actual new content — no "similar to above" shorthand, no invented API shapes. The one thing genuinely unknowable before live testing — whether the SESS_P/CAP_P split behaves correctly against real X-Road server-side validation — is explicitly Task 5, not glossed over.

**Type/name consistency:** `MEMBER_SIGN_KEY`/`MEMBER_CLIENT`/`SESS_P`/`CAP_P` are used identically across Tasks 3 and 4; `LITE_HOSTED_ON` is defined once (Task 4 Step 1) and read in two places (Task 4 Steps 3 and 4) with the same key names (`pnia`, `moeys`, `plr`).

## Execution Handoff

Same reasoning as the prior KP2 plan: nearly every task after Task 1 shares the live Docker/colima stack and its named volumes, so subagent-driven-development's worktree isolation doesn't fit. Recommend **inline execution via superpowers:executing-plans**, in order. Natural checkpoints: after Task 3 (refactor proven safe via diff, before any new behavior exists), after Task 5 (lite genuinely works), after Task 6 (full mode confirmed not regressed).
