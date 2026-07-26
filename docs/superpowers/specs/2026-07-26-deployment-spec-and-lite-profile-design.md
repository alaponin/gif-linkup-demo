# KP2 build pack — deployment spec file + working lite profile (design)

**Status:** approved by user 2026-07-26, ready for implementation planning.

**Goal:** Give an analyst a single, git-committed spec file to configure *how*
this pack deploys (topology profile, X-Road version pins), and make the
`lite` profile it declares actually deployable — today `hurl/run-linkup.sh`
refuses `LITE=1` outright (`hurl/README.md` "Known limits").

**Explicitly out of scope** (parked as separate future work, not touched by
this design):
- Non-Docker deployment targets (real VMs, the ITU cloud) — `deployment.yaml`
  reserves a `target:` field for this, but only `docker-local` is implemented.
- Arbitrary number of member agencies — this design only makes the *existing*
  four members (MOEYS, PNEA, PLR, PNIA) deployable under a 3-server lite
  profile; it does not generalize to N members.
- Full rename/reuse support for a different country or sector — a separate
  spec, sequenced after this one (per user decision 2026-07-26: independent
  pieces of work, don't design together).

## 1. `deployment.yaml` — the new spec file

New file at the pack root, git-committed (no secrets):

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

`.env` shrinks to the two real secrets plus the admin username (not secret,
but paired with the password):

```
XROAD_TOKEN_PIN=Progressa123!
XROAD_ADMIN_USER=xrd
XROAD_ADMIN_PASSWORD=secret
```

**Wiring:**
- `scripts/lib.sh` reads `deployment.yaml` via the existing `yq_get()` helper
  (already present, Python+PyYAML — no new dependency). It sets the internal
  `LITE` variable from `profile` (`lite` → `LITE=1`, anything else → `LITE=0`)
  instead of reading `LITE` from `.env` — `.env` no longer declares `LITE` at
  all. It also exports `XROAD_VERSION`, `XROAD_CS_TAG`, `TESTCA_TAG` read from
  `deployment.yaml`'s `xroad:` block, so `docker-compose.yml`'s existing
  `${XROAD_VERSION:-7.7.0}`-style substitution keeps working unchanged —
  Compose itself never parses `deployment.yaml`.
- `hurl/generate.py` reads `deployment.yaml`'s `profile` directly (it already
  `import yaml`) to decide which code path to take for PNIA/MoEYS (§2 below).
- `hurl/run-linkup.sh`'s current `fail "LITE=1 is not supported..."` guard is
  deleted — lite becomes a real, tested path instead of a refusal.

## 2. Making `lite` actually work in `hurl/generate.py`

**Why this is safe:** X-Road's SIGN-key CSR API already takes an explicit
`member_id` distinct from the Security Server's own owner — the mechanism for
"one physical SS, many members' signing identities" is already in the admin
API this pack automates, confirmed by reading the current SIGN-key-generation
request in `SS_BRINGUP`. PLAN.md §2 already asserted this is legitimate
("an SS legitimately hosts multiple members' clients"); this design is the
first time it's actually implemented and tested live.

**What stays per-physical-server, untouched, full mode only:** the owning
member's own `build_ss_file()` call — UI check, login, anchor upload,
`POST /initialization` (owner + SS code + token PIN), the AUTH key/CSR/cert
(network identity of the server itself), its registration + approval,
activation, and pointing at the timestamping service. Zero behavior change
here — a diff of the full-profile generated scenarios before/after this
change must be empty.

**What's new — two blocks extracted from the current monolithic `SS_BRINGUP`
template into standalone, reusable functions, each taking two prefixes
instead of one:**

- `sess_p` — whose already-logged-in session to authenticate the request with
  (references `{{sess_p}_xsrf_token}}`, captured by an earlier login step).
- `cap_p` — the Hurl-capture namespace for this block's own output (e.g.
  `{cap_p}_client_id`), so a hosted member's captures never collide with the
  hosting SS's own.

1. **`MEMBER_SIGN_KEY(hostvar, sess_p, cap_p, member_code, member_name,
   ss_code)`** — generate the SIGN key + CSR (`member_id` = this member, not
   the SS owner), download PEM, sign at the Test CA, import the cert. Same
   request bodies as today's SIGN-key block in `SS_BRINGUP`, just
   parameterized on two prefixes instead of one.
2. **`MEMBER_CLIENT(hostvar, sess_p, cap_p, member_code, subsystem,
   connection_type)`** — today's `SS_CLIENT` template, same parameterization.

`build_ss_file()` (full mode) calls both with `sess_p == cap_p == P` — no
change in emitted output. A new `build_hosted_client(member, host_ss_prefix,
host_var)` calls both with `sess_p="plr"`, `cap_p=<the hosted member's own
prefix, e.g. "pnia">`, `hostvar=<ss-plr's host var>`. Its output is appended
into `21-ss-plr.hurl` immediately after ss-plr's own bring-up — not a separate
file — because it depends on ss-plr's session token already being open, and
Hurl captures don't cross file boundaries (the existing constraint every other
scenario file already respects).

**Service publishing:** `build_service_file()` already takes `(member,
host_var)`; for a hosted member in lite mode, it's called with `host_var`
pointed at ss-plr and keeps using the member's own prefix for captures (no
change needed to the function itself, just what's passed in).

**`main()`'s branch:** for each of PNIA and MoEYS, if `profile == "lite"`,
skip `build_ss_file()`/write a `20-ss-pnia.hurl`/`22-ss-moeys.hurl` file at
all — instead call `build_hosted_client()` and append its output to
`21-ss-plr.hurl`'s content before writing it, then still call
`build_service_file()` for their services with `host_var` = ss-plr's.

**Unaffected by this change (confirmed, no edits needed):**
- `docker-compose.yml` already tags `ss-pnia`/`ss-moeys` with `profiles:
  ["full"]`, so lite already omits those containers correctly.
- `hurl/compose.hurl.yml`'s `hurl` runner already `depends_on` only
  `cs`/`ca`/`ss-pdga`/`ss-pnea`/`ss-plr` — already correct for both profiles.
- `manifest.yaml`'s `scenarios:` claims per module reference file paths
  (`hurl/scenarios/20-ss-pnia.hurl`, etc.) that must still exist and be
  non-empty for `check_scenarios.py`'s crosswalk — lite mode's hosted-client
  content still needs to land in files matching those claims. **Open question
  for the plan:** either lite mode still writes near-empty `20-ss-pnia.hurl`/
  `22-ss-moeys.hurl` files (service-publish scenarios only, since the
  SS-bring-up content moved into `21-ss-plr.hurl`) so the manifest claims keep
  resolving, or `check_scenarios.py`/`manifest.yaml` need a profile-aware
  exception. Resolve this by inspecting `check_scenarios.py`'s exact claim
  logic during planning, not by guessing here.
- `scripts/acceptance.sh` needs no changes — `check_client_registered` and
  `check_acl_exact` already key off `lib.sh`'s `HOST_SS` map, which already
  has a lite branch (`HOST_SS[PNIA:IDENTITY]=ss-plr` etc. when `LITE=1`), and
  the 2.6.4 negative check already routes through `${HOST_SS[MOEYS:PEMIS]}`
  generically.

## 3. Testing this for real

Given this session's repeated experience that paper design and live X-Road
behavior disagree in specific, unpredictable ways, the implementation plan
must include actually standing up `profile: lite` end to end on the live
colima stack — `teardown.sh --purge` → `hurl/run-linkup.sh` with lite
selected → `scripts/seed.sh` → `scripts/acceptance.sh` green — not just a
`--dry-run` syntax check. Budget for at least one live-debugging iteration
cycle, matching how P0's from-zero stand-up needed two real fixes before it
worked.

## 4. Docs to update

- `hurl/README.md` "Known limits" — remove the "`LITE=1` is not supported"
  bullet once it's genuinely supported; add a short note on how hosted-member
  content lands in `21-ss-plr.hurl` instead of its own file.
- `runbook.md` — replace `.env`'s `LITE=1` instruction with editing
  `deployment.yaml`'s `profile:` field; update the RAM/prerequisites section
  (lite's footprint is smaller than the measured ~13 GB full-profile figure —
  measure it live rather than estimate).
- `.env.example` — shrink to the two secrets + admin user, per §1.
- `PLAN.md` §9 (parked items) — record that rename/reuse support and
  non-Docker targets are tracked as separate, not-yet-started specs.
- `README.md` — the `docker-compose.yml`/lite description currently says the
  lite profile isn't supported by the Hurl scenario set (added 2026-07-25);
  update once this ships.

## Self-review notes

- **Placeholder scan:** the one genuinely open question (manifest scenario
  claims vs. where lite content physically lands) is flagged explicitly above
  as something the plan must resolve by reading `check_scenarios.py`, not a
  vague TODO.
- **Consistency:** confirmed against the actual current template code
  (read `hurl/generate.py` lines 130–460 during design, not from memory) —
  the `member_id`-bearing SIGN-key request is real, not assumed.
- **Scope:** deliberately excludes rename/reuse support and non-Docker
  targets, per explicit user sequencing decision — those get their own spec.
