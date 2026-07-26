# KP2 build pack — consolidated agency identity for rename support (design)

**Status:** approved by user 2026-07-26, ready for implementation planning.

**Goal:** Reduce the risk of the country/agency-naming choices baked into this
pack (Progressa, PDGA, PNEA, PLR, PNIA, MoEYS) by eliminating the hand-
duplication that makes renaming them today an error-prone, five-file edit.
After this, renaming the agency layer is: edit `manifest.yaml`'s `identity:`
block, regenerate, done.

**Explicitly out of scope** (per user decision 2026-07-26):
- **Member count/shape.** The once-only-exchange narrative (Module 5.6) is
  specifically one consumer pre-filling from exactly two providers, plus one
  unauthorized third party for the negative check. That shape is what the
  acceptance checks, the semantic map, and the prompts teach — not an
  incidental config value. Changing it means re-authoring the module map, not
  flipping a flag; this design does not attempt to make it parametric.
- **Seed-data grounding.** `gen_seed_data.py`'s Gambia-specific region/school/
  person-name pools stay hardcoded — a separate, larger concern the user
  confirmed is not part of this feature.
- **Sector scope.** Still Education-only.
- **Docker container/DNS names** (`ss-pdga`, `app-pnia`, etc.) — infrastructure
  labels, deliberately decoupled from agency branding. A rename does not
  touch `docker-compose.yml`, `hurl/compose.hurl.yml`, or `scripts/lib.sh`'s
  `SS_UI`/`SS_REST`/`HOST_SS` maps.
- **Prose in docs/prompts/acceptance criteria.** Renaming still means hand-
  editing paragraphs that say "Progressa" — templating natural-language
  teaching prose is undesirable, not merely deferred.

## 1. Where agency identity lives today (the problem)

| Field | Currently declared in |
| --- | --- |
| instance, member_class | `manifest.yaml` `identifiers:` (flat strings) **and** `configs/x-road-bus/2.1.yaml` `central_server.instance_identifier`/`owner.member_class` |
| owner code/name | `manifest.yaml` `identifiers.owner` (as `PROGRESSA/GOV/PDGA`, no display name) **and** `configs/x-road-bus/2.1.yaml` `central_server.owner.member_code`/`member_name` |
| each member's code/name | `manifest.yaml` `identifiers.members` (as `PROGRESSA/GOV/MOEYS:PEMIS`, no display name) **and** `configs/member-*/2.x.yaml` `member.member_code`/`member_name` (5 files) |
| each subsystem's code/description | `configs/member-*/2.x.yaml` `subsystem.code`/`description` only — not in manifest at all |

Renaming today means editing `manifest.yaml` plus five `configs/*.yaml` files
by hand, with nothing catching a value that gets changed in one place and
missed in another — exactly the class of drift this pack's dogfooding pass
(Task 4 of the prior plan) found and removed elsewhere.

## 2. `manifest.yaml`'s new `identity:` block

Added **alongside** the existing `identifiers:` block, not replacing it — the
flat cross-pack join-key format stays exactly as-is, since it's unclear
whether KP3/KP4 parse it programmatically or just match the literal strings
by convention, and every design doc in this pack calls it a frozen contract.
Safer to add a richer block than to risk breaking something invisible from
here.

```yaml
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
      name: Progressa Ministry of Education, Youth and Sport
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

The `members:` keys (`moeys`, `pnea`, `plr`, `pnia`) match the dict keys
`hurl/generate.py`'s `main()` already uses internally (`members = {"pnia":
..., "plr": ..., "moeys": ..., "pnea": ...}`) — no new naming scheme, reusing
what's already there.

## 3. `configs/*.yaml` shrink — no more duplicated identity

- `configs/member-moeys/2.2.yaml`, `member-pnea/2.3.yaml`,
  `member-plr/2.4.yaml`, `member-pnia/2.5.yaml`: remove the `member:` and
  `subsystem:` blocks entirely. Keep `security_server` (dns_name/code —
  infrastructure, untouched), `client.connection_type` (2.3 only), `services`
  (spec_url/access ACL — 2.2/2.4/2.5), `semantic` (2.4/2.5), `consumes` (2.3),
  `role_notes` (2.2).
- `configs/x-road-bus/2.1.yaml`: remove `central_server.instance_identifier`
  and `owner` (all four fields) — `member_classes[0].description` stays
  (hand-authored prose, not identity data). `management_security_server`
  stays (infrastructure).

## 4. `hurl/generate.py` reads identity from `manifest.yaml`, not per-member configs

`main()` currently does, for each of the four members: `m =
member["member"]` / `sub_cfg = member["subsystem"]` — these become lookups
into `manifest["identity"]["members"][key]` instead. Concretely:

- `load("configs/member-pnia/2.5.yaml")` etc. still loads each config (for
  `security_server`/`services`/etc.), but the member/subsystem identity for
  that key comes from `manifest["identity"]["members"]["pnia"]` — a single
  `identity = manifest["identity"]` lookup near the top of `main()`, then
  every `m["member_code"]`/`m["member_name"]`/`sub_cfg["code"]`/
  `sub_cfg["description"]` reference across `build_ss_file`,
  `build_hosted_client`, `build_service_file`, `build_hosted_client`, and the
  "02 members" CS-registration section switches to
  `identity["members"][key]["code"]` / `["name"]` / `["subsystem"]` /
  `["subsystem_description"]`.
- The owner (PDGA) and `core["central_server"]["owner"]` references
  throughout `main()` switch to `identity["owner"]`.
- `instance`/`member_class` (currently read from `core["central_server"]`)
  switch to `identity["instance"]`/`identity["member_class"]`.

This is a mechanical substitution — every call site already exists and
already receives these exact values as function arguments; only the *source*
of the value changes, not the functions' signatures or the generated Hurl
output's shape.

## 5. Consistency check: `identity:` vs `identifiers:`

Extend `hurl/check_scenarios.py` (already the pack's static-check home for
exactly this kind of cross-reference) with one more check: for each member,
`identifiers.members` entry `PROGRESSA/GOV/{CODE}:{SUBSYSTEM}` must match
`identity.members[key].code`/`.subsystem` (and instance/member_class/owner
similarly). This is the one place duplication still exists (by deliberate
choice, §2) — now it's checkable instead of scattered across five files with
nothing watching them.

The existing check `ids["instance"] != configs/x-road-bus/2.1.yaml
central_server.instance_identifier` is removed — that field no longer exists
in `2.1.yaml` (§3), so the check has nothing to compare.

## 6. Prompts change scope: identity becomes an input, not an output

Each `prompts/2.x.md`'s field list currently asks for `member — instance,
member_class, member_code, member_name` as generated output. Reword to:
identity (instance/member_class/member_code/member_name/subsystem
code+description) comes from `manifest.yaml`'s `identity:` block — the
prompt generates only the module-specific fields (security_server, services,
ACL, semantic map, connection_type, consumes). This is not just a wording
change: it's the prompts catching up to what the pack's own anti-drift rule
already says elsewhere ("a block nothing reads is not documentation, it is
drift") — the prompts were asking the model to restate a value that's now
frozen upstream.

## 7. What a rename actually looks like after this ships

1. Edit `manifest.yaml`'s `identity:` block (and `identifiers:`, kept in
   sync — the new checker catches a miss).
2. `python3 hurl/generate.py && python3 hurl/check_scenarios.py`.
3. Redeploy: `scripts/teardown.sh --purge && hurl/run-linkup.sh &&
   scripts/seed.sh && scripts/acceptance.sh`.
4. Hand-edit prose (docs, prompt Problem sections, acceptance criteria
   narrative) — unavoidable, bounded, and explicitly not automated (§ scope).

Steps 2-3 need no config edits beyond step 1 — that's the actual deliverable.

## Self-review notes

- **Placeholder scan:** every field listed in §2/§3 is checked against the
  actual current file structure read during this session (Task 4 of the
  prior plan dogfooded all five `configs/*.yaml` files and the `2.1.yaml`
  config already), not assumed.
- **Consistency:** the `identity.members` dict keys match `generate.py`'s
  existing internal dict keys exactly — verified against the current file
  read during the deployment-spec/lite-profile plan's Task 3-4 work.
- **Scope:** member count/shape, seed-data grounding, sector, container/DNS
  naming, and prose are all explicitly excluded per the user's own scoping
  decisions in this conversation — not silently dropped.
