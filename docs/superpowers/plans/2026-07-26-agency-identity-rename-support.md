# Consolidated Agency Identity (Rename Support) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended — same reasoning as the two prior KP2 plans: nearly every task after the config edits shares the live Docker/colima stack) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the five-file hand-duplication of agency identity
(instance, member_class, owner, per-member code/name/subsystem) that makes
renaming Progressa/PDGA/MOEYS/PNEA/PLR/PNIA today an error-prone, uncoordinated
edit. After this plan, renaming is: edit `manifest.yaml`'s new `identity:`
block, regenerate, done.

**Architecture:** `manifest.yaml` gains an `identity:` block (rich: names,
subsystem descriptions) alongside its existing `identifiers:` block (flat
cross-pack join keys, left untouched for compatibility). `configs/*.yaml`
drop their duplicated `member:`/`subsystem:`/owner fields. `hurl/generate.py`
reads identity from `manifest.yaml` and injects it into the in-memory config
dicts it already builds, so every downstream function
(`build_ss_file`/`build_service_file`/`build_hosted_client`) needs zero
changes to its own logic — only where the values come from changes.

**Tech Stack:** Same as the two prior KP2 plans — Python 3 + PyYAML, bash,
the live X-Road 7.7.0 stack for verification.

## Global Constraints

- `identifiers:` (the flat cross-pack contract) is never restructured —
  only a new `identity:` block is added alongside it.
- `configs/*.yaml` never regain the fields removed here — a checker
  (Task 4) prevents `identity:`/`identifiers:` from drifting within
  `manifest.yaml` itself, the one place duplication remains by choice.
- Task 3's refactor must produce **byte-identical** generated scenarios to
  today's output (same Progressa/PDGA/etc. values, just sourced from
  `identity:` instead of `configs/*.yaml`) — verify with a diff.
- Member count/shape, seed-data grounding, sector, and container/DNS naming
  are explicitly out of scope (see the design spec) — do not generalize
  beyond consolidating identity.
- Commit after every task.

---

### Task 1: Add `identity:` to `manifest.yaml`

**Files:**
- Modify: `manifest.yaml`

**Interfaces:**
- Produces: `manifest.yaml`'s `identity:` block, consumed by `generate.py`
  (Task 3) and cross-checked against `identifiers:` (Task 4)

- [ ] **Step 1: insert the block**

Insert immediately after the existing `identifiers:` block (before
`modules:`):

```yaml
# Rich identity — names, subsystem descriptions — that configs/*.yaml used to
# duplicate by hand. identifiers: above stays the flat cross-pack contract,
# untouched; this is what hurl/generate.py actually reads. The two must agree
# -- hurl/check_scenarios.py checks that (Task 4 of
# docs/superpowers/plans/2026-07-26-agency-identity-rename-support.md).
identity:
  instance: PROGRESSA
  member_class: GOV
  owner:
    code: PDGA
    name: Progressa Digital Government Authority
    management_subsystem: MANAGEMENT
  members:
    moeys:
      code: MOEYS
      name: "Progressa Ministry of Education, Youth and Sport"
      subsystem: PEMIS
      subsystem_description: School information system (education management)
    pnea:
      code: PNEA
      name: Progressa National Examination Authority
      subsystem: EXAMS
      subsystem_description: Examination and certification services (consumer of identity + enrolment)
    plr:
      code: PLR
      name: Progressa Learner Registry
      subsystem: ENROLMENT
      subsystem_description: Authoritative learner enrolment register
    pnia:
      code: PNIA
      name: Progressa National Identity Authority
      subsystem: IDENTITY
      subsystem_description: Authoritative person identity register
```

Note the quotes around MoEYS's name — it contains a comma, and YAML is
happier parsing it quoted (the *value* is identical to the unquoted form
used elsewhere in this pack; this is just cleaner YAML, not a new escaping
requirement — `dn_escape()` in `generate.py` still does the real work at the
X-Road layer).

- [ ] **Step 2: validate YAML syntax**

```bash
python3 -c "import yaml; d = yaml.safe_load(open('manifest.yaml')); print(d['identity']['members']['moeys']['name'])"
```
Expected: `Progressa Ministry of Education, Youth and Sport`

- [ ] **Step 3: commit**

```bash
git add manifest.yaml
git commit -m "feat: add manifest.yaml identity: block (names, subsystem descriptions)

Not yet consumed by anything -- generate.py still reads configs/*.yaml's
duplicated member:/subsystem: fields (Task 3 switches that over). This task
only adds the new source of truth alongside the untouched identifiers:
block."
```

---

### Task 2: Slim `configs/*.yaml` — remove duplicated identity fields

**Files:**
- Modify: `configs/x-road-bus/2.1.yaml`, `configs/member-moeys/2.2.yaml`,
  `configs/member-pnea/2.3.yaml`, `configs/member-plr/2.4.yaml`,
  `configs/member-pnia/2.5.yaml`

**Interfaces:**
- Produces: configs with no identity fields left to drift — everything
  `generate.py` will read after Task 3 comes only from `manifest.yaml`

This task alone will **break `generate.py`** until Task 3 lands (it still
reads these fields from configs) — that's expected; Tasks 2 and 3 are one
atomic change split for reviewability, not independently deployable.

- [ ] **Step 1: `configs/x-road-bus/2.1.yaml`**

Replace:

```yaml
central_server:
  instance_identifier: PROGRESSA
  address: cs                      # container DNS name on the linkup network
  member_classes:
    - code: GOV
      description: Government institutions of Progressa
  owner:
    member_class: GOV
    member_code: PDGA
    member_name: Progressa Digital Government Authority
    management_subsystem: MANAGEMENT
```

with:

```yaml
central_server:
  # instance_identifier, owner (code/name/management_subsystem) and
  # member_classes[0].code all come from manifest.yaml's identity: block now
  # -- restating them here was never read by generate.py for .code (dead
  # field) and was a second, driftable copy for the rest. Dogfooded 2026-07-26.
  address: cs                      # container DNS name on the linkup network
  member_classes:
    - description: Government institutions of Progressa
```

- [ ] **Step 2: `configs/member-moeys/2.2.yaml`**

Replace:

```yaml
member:
  instance: PROGRESSA
  member_class: GOV
  member_code: MOEYS
  member_name: Progressa Ministry of Education, Youth and Sport
subsystem:
  code: PEMIS
  description: School information system (education management)
security_server:
```

with:

```yaml
# member/subsystem identity comes from manifest.yaml's identity.members.moeys
# now -- restating it here was a second, driftable copy of the same values.
# Dogfooded 2026-07-26.
security_server:
```

- [ ] **Step 3: `configs/member-pnea/2.3.yaml`**

Replace:

```yaml
member:
  instance: PROGRESSA
  member_class: GOV
  member_code: PNEA
  member_name: Progressa National Examination Authority
subsystem:
  code: EXAMS
  description: Examination and certification services (consumer of identity + enrolment)
security_server:
```

with:

```yaml
# member/subsystem identity comes from manifest.yaml's identity.members.pnea
# now -- restating it here was a second, driftable copy of the same values.
# Dogfooded 2026-07-26.
security_server:
```

- [ ] **Step 4: `configs/member-plr/2.4.yaml`**

Replace:

```yaml
member:
  instance: PROGRESSA
  member_class: GOV
  member_code: PLR
  member_name: Progressa Learner Registry
subsystem:
  code: ENROLMENT
  description: Authoritative learner enrolment register
security_server:
```

with:

```yaml
# member/subsystem identity comes from manifest.yaml's identity.members.plr
# now -- restating it here was a second, driftable copy of the same values.
# Dogfooded 2026-07-26.
security_server:
```

- [ ] **Step 5: `configs/member-pnia/2.5.yaml`**

Replace:

```yaml
member:
  instance: PROGRESSA
  member_class: GOV
  member_code: PNIA
  member_name: Progressa National Identity Authority
subsystem:
  code: IDENTITY
  description: Authoritative person identity register
security_server:
```

with:

```yaml
# member/subsystem identity comes from manifest.yaml's identity.members.pnia
# now -- restating it here was a second, driftable copy of the same values.
# Dogfooded 2026-07-26.
security_server:
```

- [ ] **Step 6: confirm `generate.py` now fails (expected — proves these fields were actually removed)**

```bash
python3 hurl/generate.py 2>&1 | tail -5
```
Expected: a `KeyError` on `'member'` or `'instance_identifier'` — this is the
correct, expected state between Task 2 and Task 3. Do not "fix" it here.

- [ ] **Step 7: commit**

```bash
git add configs/x-road-bus/2.1.yaml configs/member-moeys/2.2.yaml \
        configs/member-pnea/2.3.yaml configs/member-plr/2.4.yaml \
        configs/member-pnia/2.5.yaml
git commit -m "refactor: remove identity fields from configs/*.yaml (now in manifest.yaml)

member:/subsystem: blocks and central_server.instance_identifier/owner
removed -- identity.members[key].code/name and identity.owner take over.
member_classes[0].code also removed: it was never read by generate.py at all
(only .description is), a dead duplicate found while doing this pass.

generate.py now fails until Task 3 updates it to read manifest.yaml's
identity: block instead -- expected, these two tasks are one atomic change."
```

---

### Task 3: `hurl/generate.py` reads identity from `manifest.yaml`

**Files:**
- Modify: `hurl/generate.py`

**Interfaces:**
- Consumes: `manifest.yaml`'s `identity:` block (Task 1)
- Produces: byte-identical Hurl scenarios to before this task (verified by
  diff) — same Progressa values, sourced differently

- [ ] **Step 1: the on-disk `hurl/scenarios/`/`vars.env` already ARE the correct baseline**

`generate.py` calls `write()` incrementally as it goes (00-cs-init first,
then 01, 02, ...), and Task 2's `KeyError` fires at the very first line of
`main()`'s body (`instance = core["central_server"]["instance_identifier"]`),
before any `write()` call. So Task 2 Step 6's confirmed failure never
touched `hurl/scenarios/*.hurl` or `hurl/vars.env` — they still hold the
last successful generation (this pack's full profile, from the end of the
prior plan). Just copy them as the diff baseline:

```bash
cp -r hurl/scenarios /tmp/scenarios-before-identity
cp hurl/vars.env /tmp/vars.env-before-identity
```

- [ ] **Step 2: inject identity into the `members` dict; update `owner`/`member_class`/`instance`**

Read `hurl/generate.py` first. Replace:

```python
def main() -> None:
    manifest = load("manifest.yaml")
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
    members = {
        "pnia": load("configs/member-pnia/2.5.yaml"),
        "plr": load("configs/member-plr/2.4.yaml"),
        "moeys": load("configs/member-moeys/2.2.yaml"),
        "pnea": load("configs/member-pnea/2.3.yaml"),
    }

    instance = core["central_server"]["instance_identifier"]
    owner = core["central_server"]["owner"]
    mgmt_ss = core["management_security_server"]
    member_class = owner["member_class"]
    pdga_prefix = ss_prefix(mgmt_ss["dns_name"])
```

with:

```python
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
    members = {
        "pnia": load("configs/member-pnia/2.5.yaml"),
        "plr": load("configs/member-plr/2.4.yaml"),
        "moeys": load("configs/member-moeys/2.2.yaml"),
        "pnea": load("configs/member-pnea/2.3.yaml"),
    }
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
```

- [ ] **Step 3: update every `owner["member_code"]`/`owner["member_name"]` reference to `owner["code"]`/`owner["name"]`**

`identity["owner"]` has keys `code`/`name`/`management_subsystem` (no
`member_class`, no `member_code`/`member_name`) — six call sites need the
rename. Read the file's "02 members" section and "10 management security
server" section first (they've shifted slightly from Task 2's config
changes affecting nothing here, but confirm line numbers before editing).

Replace (in the "02 members" f-string body):

```python
# {owner['member_name']} -- federation owner
POST https://{{{{cs_host}}}}:4000/api/v1/members
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "member_id": {{
    "member_class": "{{{{member_class}}}}",
    "member_code": "{owner['member_code']}"
  }},
  "member_name": "{owner['member_name']}"
}}

HTTP 201

# The MANAGEMENT subsystem, through which the CS's own management services run
POST https://{{{{cs_host}}}}:4000/api/v1/subsystems
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "subsystem_id": {{
    "member_class": "{{{{member_class}}}}",
    "member_code": "{owner['member_code']}",
    "subsystem_code": "{owner['management_subsystem']}"
  }}
}}

HTTP 201

# Nominate it as the Central Server's management service provider
PATCH https://{{{{cs_host}}}}:4000/api/v1/management-services-configuration
X-XSRF-TOKEN: {{{{cs_xsrf_token}}}}
Content-Type: application/json
{{
  "service_provider_id": "{{{{xroad_instance}}}}:{{{{member_class}}}}:{owner['member_code']}:{owner['management_subsystem']}"
}}
```

with:

```python
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
```

- [ ] **Step 4: remove the dead `pdga_member` variable; fix the remaining `owner[...]` references in the "10 management security server" section**

Replace:

```python
    # -- 10 management security server -------------------------------------
    pdga_member = {
        "member": {
            "member_code": owner["member_code"],
            "member_name": owner["member_name"],
        },
        "subsystem": {"code": owner["management_subsystem"]},
        "security_server": {"code": mgmt_ss["code"], "dns_name": mgmt_ss["dns_name"]},
    }
    host_var = f"{pdga_prefix}_host"
    body = sub(
        SS_BRINGUP_INIT,
        SS=mgmt_ss["dns_name"],
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["member_code"],
        MEMBER_NAME=dn_escape(owner["member_name"]),
        HOSTVAR=host_var,
        P=pdga_prefix,
        CANAME=sub(CA_NAME_CAPTURE, HOSTVAR=host_var, P=pdga_prefix),
    )
    body += sub(
        MEMBER_SIGN_KEY,
        SS_CODE=mgmt_ss["code"],
        MEMBER_CODE=owner["member_code"],
        MEMBER_NAME=dn_escape(owner["member_name"]),
        HOSTVAR=host_var,
        SESS_P=pdga_prefix,
        CAP_P=pdga_prefix,
    )
```

with:

```python
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
```

(`pdga_member` was assigned once and never referenced anywhere else in the
file — confirmed with `grep -n "pdga_member" hurl/generate.py` returning
only its own definition line before this edit. Dead code found while already
touching this section; deleted rather than carried forward.)

- [ ] **Step 5: fix the last two `owner[...]` references (the inline management-services `sub()` call's kwargs)**

Read the file again to find the `sub(""" ... """, SS=..., SS_CODE=...,
MEMBER_CODE=owner["member_code"], SUBSYSTEM=owner["management_subsystem"],
...)` call right after Step 4's edit (the big inline template registering
the MANAGEMENT subsystem as a client and publishing the management WSDL).
Change its `MEMBER_CODE=owner["member_code"],` kwarg to
`MEMBER_CODE=owner["code"],` — `SUBSYSTEM=owner["management_subsystem"]`
stays unchanged (that key name didn't move).

- [ ] **Step 6: regenerate and diff against the pre-Task-2 baseline**

```bash
python3 hurl/generate.py > /tmp/gen-after-identity.log 2>&1
echo "GENERATE EXIT: $?"
diff -r /tmp/scenarios-before-identity hurl/scenarios
echo "SCENARIOS DIFF EXIT: $?"
diff /tmp/vars.env-before-identity hurl/vars.env
echo "VARS DIFF EXIT: $?"
```
Expected: `GENERATE EXIT: 0`, both diffs empty (exit 0) — every value is
identical because the *values* haven't changed, only their source. **If
either diff is non-empty, find and fix the discrepancy — this task is not
done until the diff is clean.**

- [ ] **Step 7: full static check**

```bash
python3 hurl/check_scenarios.py
```
Expected: fails on the stale `configs/x-road-bus/2.1.yaml` instance-identifier
check (Task 4 removes it) — confirm the *only* failure is that one line, not
something new:
```
FAIL manifest instance identifier disagrees with configs/x-road-bus/2.1.yaml
```

- [ ] **Step 8: commit**

```bash
git add hurl/generate.py
git commit -m "feat: generate.py reads agency identity from manifest.yaml, not configs/*.yaml

Injects identity.members[key] into the same members[key][\"member\"/\"subsystem\"]
dict shape build_ss_file/build_service_file/build_hosted_client already
expect -- their own logic needs zero changes, only the source of the values
does. owner now uses identity.owner's code/name keys (six call sites
updated). Deleted pdga_member, dead code found while touching this section
(assigned once, never referenced).

Verified byte-identical to the pre-refactor baseline via diff -- same
Progressa values, sourced differently. check_scenarios.py now fails on its
stale configs/x-road-bus/2.1.yaml cross-check (that field no longer exists
there) -- expected, Task 4 replaces it."
```

---

### Task 4: `check_scenarios.py`'s identity/identifiers consistency check

**Files:**
- Modify: `hurl/check_scenarios.py`

**Interfaces:**
- Consumes: `manifest.yaml`'s `identity:` and `identifiers:` blocks
- Produces: the one drift-detection point this design deliberately keeps
  (§5 of the design spec) — replaces the now-stale 2.1.yaml cross-check

- [ ] **Step 1: replace the stale check**

Read `hurl/check_scenarios.py` first. Replace:

```python
    if ids["instance"] != yaml.safe_load(
        (PACK / "configs/x-road-bus/2.1.yaml").read_text()
    )["central_server"]["instance_identifier"]:
        note("manifest instance identifier disagrees with configs/x-road-bus/2.1.yaml")
```

with:

```python
    # identity: and identifiers: are the one place duplication remains inside
    # manifest.yaml itself (by design -- identifiers: is the untouched
    # cross-pack contract, identity: is what generate.py actually reads).
    # Nothing else watches them from here on; check they agree.
    identity = manifest["identity"]
    if identity["instance"] != ids["instance"]:
        note(f"identity.instance ({identity['instance']!r}) disagrees with identifiers.instance ({ids['instance']!r})")
    if identity["member_class"] != ids["member_class"]:
        note(f"identity.member_class ({identity['member_class']!r}) disagrees with identifiers.member_class ({ids['member_class']!r})")
    owner_expected = f"{ids['instance']}/{ids['member_class']}/{identity['owner']['code']}"
    if owner_expected != ids["owner"]:
        note(f"identity.owner.code ({identity['owner']['code']!r}) disagrees with identifiers.owner ({ids['owner']!r})")
    for member_str in ids["members"]:
        _, _, code, subsystem = re.split(r"[:/]", member_str.replace(":", "/"))
        if not any(v["code"] == code and v["subsystem"] == subsystem for v in identity["members"].values()):
            note(f"identifiers.members entry {member_str} has no matching identity.members entry (code+subsystem)")
```

- [ ] **Step 2: verify**

```bash
python3 hurl/check_scenarios.py
echo "EXIT: $?"
```
Expected: `OK -- ... identifiers match manifest.yaml`, exit 0 — Task 3's diff
already proved the generated scenarios are correct; this confirms the new
consistency check itself passes against the (currently unchanged) Progressa
identity.

- [ ] **Step 3: prove the check actually catches drift (then revert)**

```bash
sed -i.bak 's/code: PDGA/code: PDGA-TYPO/' manifest.yaml
python3 hurl/check_scenarios.py; echo "EXIT (expect 1): $?"
mv manifest.yaml.bak manifest.yaml
python3 hurl/check_scenarios.py; echo "EXIT (expect 0, reverted): $?"
```
Expected: first run fails with `identity.owner.code ('PDGA-TYPO') disagrees
with identifiers.owner ('PROGRESSA/GOV/PDGA')`, second run passes clean after
the revert.

- [ ] **Step 4: commit**

```bash
git add hurl/check_scenarios.py
git commit -m "feat: check_scenarios.py verifies identity: agrees with identifiers:

Replaces the stale configs/x-road-bus/2.1.yaml cross-check (that field no
longer exists there after Task 3) with the real remaining drift risk:
identity: and identifiers: are the one place duplication is deliberately
kept inside manifest.yaml. Verified it actually catches a planted typo
before reverting it."
```

---

### Task 5: Prompts — identity is an input, not generated output

**Files:**
- Modify: `prompts/2.1.md`, `prompts/2.2.md`, `prompts/2.3.md`,
  `prompts/2.4.md`, `prompts/2.5.md`

**Interfaces:** none — pure documentation/instruction correction, no code
consumes these files

- [ ] **Step 1: `prompts/2.1.md`**

Replace:

```
Generate the federation-core configuration for Progressa's demonstration
federation as a single YAML document with these fields:
(1) central_server — instance_identifier, address, member_classes (one class for
    government institutions), owner (the digital-government authority PDGA, with a
    MANAGEMENT subsystem);
```

with:

```
Generate the federation-core configuration for Progressa's demonstration
federation as a single YAML document with these fields:
(1) central_server — address, member_classes (one class for government
    institutions, description only — the code and the owner's identity
    (code/name/management_subsystem) are frozen in manifest.yaml's identity:
    block and are inputs here, not something this prompt generates);
```

- [ ] **Step 2: `prompts/2.2.md`, `2.3.md`, `2.4.md`, `2.5.md`**

Each currently opens field (1) with `member — instance, member_class,
member_code, member_name;` and field (2) with `subsystem — code and
description` (wording varies slightly per file). Replace field (1) in each
with:

```
(1) member/subsystem identity is frozen in manifest.yaml's identity.members
    block (code, name, subsystem code, subsystem description) — an input to
    this prompt, not something it generates. Do not restate it;
```

and remove the separate `(2) subsystem — code and description` line each
file had (folded into the sentence above), renumbering the remaining fields
`(2)`→`(3)` etc. in each file's list accordingly.

- [ ] **Step 3: commit**

```bash
git add prompts/2.1.md prompts/2.2.md prompts/2.3.md prompts/2.4.md prompts/2.5.md
git commit -m "docs: prompts treat agency identity as an input, not generated output

manifest.yaml's identity: block is now the frozen source (Task 1-3); the
prompts were still asking the model to restate member_code/member_name/
subsystem fields that would immediately become a second, driftable copy --
exactly the anti-drift rule these same prompts already state elsewhere
(\"a block nothing reads is not documentation, it is drift\")."
```

---

### Task 6: Live regression proof

**Files:** none — pure verification

**Interfaces:**
- Consumes: everything from Tasks 1–5

- [ ] **Step 1: ship-gate check**

```bash
python3 ../../ITU-Giga-KP-Plugin/skills/kp-solution-verify/scripts/check_pack.py . --ready
echo "STATIC_EXIT=$?"
```
Expected: `Static check PASS`, exit 0.

- [ ] **Step 2: full profile, cold**

```bash
grep '^profile:' deployment.yaml   # confirm: full
scripts/teardown.sh --purge
hurl/run-linkup.sh > /tmp/full-identity-regression.log 2>&1
rc=$?; echo "ACTUAL_EXIT_CODE=$rc" >> /tmp/full-identity-regression.log
echo "ACTUAL_EXIT_CODE=$rc"
scripts/seed.sh
scripts/acceptance.sh
```
Expected: deploy exit 0, `ACCEPTANCE GREEN`.

- [ ] **Step 3: lite profile, cold**

```bash
sed -i.bak 's/profile: full/profile: lite/' deployment.yaml && rm -f deployment.yaml.bak
scripts/teardown.sh --purge
hurl/run-linkup.sh > /tmp/lite-identity-regression.log 2>&1
rc=$?; echo "ACTUAL_EXIT_CODE=$rc" >> /tmp/lite-identity-regression.log
echo "ACTUAL_EXIT_CODE=$rc"
scripts/seed.sh
scripts/acceptance.sh
sed -i.bak 's/profile: lite/profile: full/' deployment.yaml && rm -f deployment.yaml.bak
```
Expected: deploy exit 0, `ACCEPTANCE GREEN`, `deployment.yaml` restored to
`full` at the end.

- [ ] **Step 4: if either profile fails, root-cause and fix — do not weaken a check to make it pass. Commit any fix found here as its own commit before continuing.**

---

### Task 7: Prove the actual rename capability (dry-run, then revert)

**Files:** none — temporary edit, reverted at the end of this task; this is
the task that proves the whole point of the feature

**Interfaces:**
- Consumes: everything from Tasks 1–6

- [ ] **Step 1: rename the owner and one member in `manifest.yaml`'s `identity:` block only (leave `identifiers:` alone on purpose — this step proves the drift check, not a full rename)**

```bash
cp manifest.yaml /tmp/manifest-before-rename.yaml
sed -i.bak "s/code: PDGA/code: PDA2/; s/name: Progressa Digital Government Authority/name: Renamed Test Authority/" manifest.yaml
rm -f manifest.yaml.bak
python3 hurl/check_scenarios.py 2>&1 | tail -5
```
Expected: **fails**, correctly — `identifiers:` still says `PDGA` and
`identity:` now says `PDA2`, exactly the drift-check Task 4 built.

- [ ] **Step 2: do the rename properly — both blocks together — and confirm the generator propagates it**

```bash
cp /tmp/manifest-before-rename.yaml manifest.yaml   # restore first
sed -i.bak \
  -e "s/code: PDGA/code: PDA2/" \
  -e "s/name: Progressa Digital Government Authority/name: Renamed Test Authority/" \
  -e "s#owner: PROGRESSA/GOV/PDGA#owner: PROGRESSA/GOV/PDA2#" \
  manifest.yaml
rm -f manifest.yaml.bak
python3 hurl/generate.py
python3 hurl/check_scenarios.py
echo "CHECK EXIT: $?"
grep -c "PDA2\|Renamed Test Authority" hurl/scenarios/00-cs-init.hurl hurl/scenarios/02-cs-members.hurl hurl/scenarios/10-ss-pdga.hurl
```
Expected: `check_scenarios.py` passes clean, and the new code/name appear in
the generated scenarios that reference the owner (CS init, CS members,
ss-pdga bring-up) — proof the rename actually propagates through
`generate.py` without touching any other file.

- [ ] **Step 3: revert — this was a proof, not a real rename**

```bash
cp /tmp/manifest-before-rename.yaml manifest.yaml
python3 hurl/generate.py
python3 hurl/check_scenarios.py
echo "CHECK EXIT (expect 0, reverted): $?"
git diff --stat manifest.yaml hurl/scenarios hurl/vars.env
```
Expected: `check_scenarios.py` passes, and `git diff --stat` shows **no
changes** — the revert is exact (regenerating from the reverted manifest
reproduces byte-identical scenarios to what's already committed).

- [ ] **Step 4: no commit needed** — this task proves the capability and reverts; nothing should remain changed. If `git diff --stat` in Step 3 shows anything, that's a bug (the revert or the generator isn't idempotent) — root-cause it before ending this task.

---

## Self-Review Notes

**Spec coverage:** the design doc's seven sections (§1 problem, §2
`identity:` block, §3 configs shrink, §4 generate.py, §5 checker, §6 prompts,
§7 what a rename looks like) map to Tasks 1, 1, 2, 3, 4, 5, 7 respectively.

**Placeholder scan:** every code block is the actual current file content
(re-read fresh immediately before writing this plan) or the actual new
content. The one thing genuinely new in this pass — the dead `pdga_member`
variable — was confirmed dead with a `grep` before the plan called for
deleting it, not assumed.

**Type/name consistency:** `identity["owner"]`'s `code`/`name` keys are used
identically in Task 1 (manifest), Task 3 (generate.py), and Task 4
(check_scenarios.py); `identity["members"][key]`'s `code`/`name`/`subsystem`/
`subsystem_description` keys match the injection in Task 3 Step 2 exactly.

## Execution Handoff

Same reasoning as the two prior KP2 plans for Tasks 6–7 (live Docker/colima
stack, shared state — no worktree isolation). Tasks 1–5 are pure file edits
with no live dependency and could in principle run in parallel subagents, but
they're small and sequential-by-construction (Task 3 depends on Task 2's
config shape, Task 4 depends on Task 3's generator output, Task 5 is
independent but trivial) — not worth splitting. Recommend **inline execution
via superpowers:executing-plans**, in order.
