# KP2 — Parameterising the Pack for Arbitrary Members

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan changes only the pack's generation and verification machinery. It does **not** build the join interface — that is a later plan and depends on this one.

**Goal:** Make the number and identity of members a property of configuration rather than of the code. Today the pack is four named agencies wired into eleven places by hand; a fifth cannot join without editing `generate.py`, `lib.sh`, `docker-compose.yml`, `acceptance.sh` and `manifest.yaml`. After this plan, adding a member is a directory under `configs/` plus an entry under `manifest.yaml`'s `identity.members`, and everything else is derived.

**Architecture:** Three moves. (1) **Discover** members from `configs/member-*/` instead of a hardcoded dict. (2) **Generalise `hosted_on`** — the lite profile's "PNIA and MoEYS live as extra clients on ss-plr" mechanism is exactly what a joining member needs, so it becomes a per-member config field and lite becomes a preset of it. (3) **Generate the derived topology once**, in `generate.py`, and have bash, the console and Compose all read that one artefact instead of keeping their own copies.

**Tech Stack:** Unchanged — Python 3 + PyYAML, bash, Docker Compose v2, X-Road 7.7.0 admin REST APIs, Hurl.

## Global Constraints

- **The canonical five never renumber.** `ss-pdga`/`ss-pnea`/`ss-plr`/`ss-pnia`/`ss-moeys` keep their container names, host ports (including `ss-pnia`'s 5100/5180, which exists because port 5000 collides with macOS AirPlay Receiver) and scenario numbers (`10`, `20`–`23`, `30`–`32`). Renumbering would invalidate the runbook table, the console, `docs/`, and every screenshot taken for Module 5.6. New members are allocated from fresh ranges; existing ones are pinned.
- **Byte-identical output after every refactor task.** Each task that touches `generate.py` must produce scenarios identical to the pre-task baseline for **both** profiles. Baseline with `cp -r`, not with git — `hurl/scenarios/`, `hurl/vars.env` and `hurl/topology.json` are untracked generated artefacts.
- **`manifest.yaml`'s `identifiers:` block is never written by tooling.** It is the frozen cross-pack contract KP3 and KP4 join against. Tooling reads it and checks agreement; only a human edits it.
- **`configs/*.yaml` stay bb-config-gen artefacts.** New fields added here must be fields a generating prompt would plausibly produce from an agency brief — not machine bookkeeping. Machine bookkeeping goes in generated files.
- No new host dependencies. No change to the write/reset semantics of the demo console. Commit after every task.

## Design decisions

1. **Member key = lowercase member code**, validated. `configs/member-pnia/` ⟷ `identity.members.pnia` ⟷ `code: PNIA`. Any disagreement is a hard failure, not a coercion.
2. **Canonical vs joined.** `identity.members.<key>.origin: canonical | joined`, defaulting to `canonical`. Only `canonical` members are required to appear in `identifiers:`; `joined` members are allowed to be absent from it, which is what lets a demonstration member join without touching the KP3/KP4 contract.
3. **`hosted_on` is a first-class per-member field**, naming the Security Server that hosts the subsystem. Absent means "its own server". `LITE_HOSTED_ON` stops being a constant and becomes the lite profile's default overlay onto that field. One mechanism, two uses — and a joining member costs zero extra containers, which matters when five Security Servers already need ~16 GB.
4. **Derived topology is generated once and consumed everywhere.** `generate.py` emits `hurl/topology.json` (already exists, gains ports and origin) and a new `hurl/topology.sh` (bash `declare -A` fragment). `scripts/lib.sh` sources the fragment instead of declaring `SS_UI`/`SS_REST`/`HOST_SS`/`SS_ORDER` itself. This closes the duplication the earlier plans could only flag.
5. **Compose is extended, not generated.** `docker-compose.yml` keeps the canonical five with their hard-won comments. Joined members land in a generated `hurl/compose.members.yml` overlay that `lib.sh` adds when it exists. Nothing regenerates the file that carries the AirPlay note and the healthcheck-retry rationale.
6. **Joining is not undoable in the live federation.** Removing a member removes it from configuration; the running Central Server still holds it until `teardown.sh --purge`. Say so plainly rather than implying a reversal the pack cannot perform. (Whether `DELETE /members/{id}` can do better is an investigation, not a promise — Task 9.)

## Out of scope

The join interface (declare/admit/catalogue surfaces), Joget-specific adapters, semantic conformance checking, and any non-`docker-local` deployment target. Those all sit on top of this plan and none of them should start before its final verification passes.

---

## Task 1: Discover members instead of listing them

**Files:** `hurl/generate.py`, `hurl/check_scenarios.py`

**Interfaces:** Produces the member dict every later task consumes.

- [x] **Step 1:** baseline both profiles before touching anything:

```bash
for p in full lite; do
  sed -i.bak "s/^profile: .*/profile: $p/" deployment.yaml
  python3 hurl/generate.py
  cp -r hurl/scenarios /tmp/base-$p-scenarios
  cp hurl/vars.env /tmp/base-$p-vars.env
  cp hurl/topology.json /tmp/base-$p-topology.json
done
```

- [x] **Step 2:** replace the hardcoded `members = {...}` dict with discovery: glob `configs/member-*/`, take the key from the directory suffix, load the single `2.*.yaml` inside it. Fail loudly on: a directory with no config, a directory with more than one, a key with no `identity.members` entry, an `identity.members` entry with no directory, or a key that is not the lowercase of that entry's `code`.
- [x] **Step 3:** keep the existing injection of `member`/`subsystem` from `identity.members` unchanged.
- [x] **Step 4:** the member iteration order must be **deterministic and stable** — sort by (pinned scenario number if any, then key). Do not let filesystem order leak in.
- [x] **Step 5:** regenerate both profiles; `diff -r` against the baselines must be empty. Commit.

  **Verified live (2026-07-28):** all five fail-loudly cases triggered for
  real (removed a config, duplicated one, renamed a directory, renamed a
  manifest code, removed a directory) -- each produced the exact intended
  message, nothing else broke. Introduced `PINNED_SCENARIO_NO` now rather
  than waiting for Task 3, because the byte-identical guarantee is a
  Global Constraint and the sort needs a pinned-number concept to
  reproduce the old hardcoded dict's order; Task 3 builds `PINNED_PORTS`
  and fresh-range allocation on top of the same table.
  `check_scenarios.py` needed no changes -- its existing checks already
  covered this correctly and passed unchanged. `check_scenarios.py` green;
  `str.removeprefix()` needed swapping for a slice (the host runs system
  python3.7.9, not 3.9+).

## Task 2: `hosted_on` as a first-class field

**Files:** `hurl/generate.py`, `configs/member-*/2.*.yaml`, `prompts/2.2.md`–`2.5.md`, `hurl/check_scenarios.py`

- [x] **Step 1:** teach `generate.py` to read `security_server.hosted_on` from a member config (a Security Server DNS name, or absent). Resolution order: explicit `hosted_on` in config → profile overlay (`LITE_HOSTED_ON` when `profile: lite`) → own server.
- [x] **Step 2:** `build_hosted_client` and the `sess_p`/`cap_p` service-file path already handle the hosted case; route the generalised resolution into them so there is exactly one hosted-client code path.
- [x] **Step 3:** a member whose `hosted_on` names a server that no member owns is a hard failure with the list of valid hosts.
- [x] **Step 4:** document the field in the four member prompts as an optional line ("if this agency will not run its own Security Server, name the one that hosts it") — it is a field an agency brief genuinely determines.
- [x] **Step 5:** regenerate both profiles; diffs empty (the canonical four leave `hosted_on` unset, so lite still resolves through the overlay exactly as before). Commit.

  **Verified live (2026-07-28):** both hard-failure cases triggered for
  real (a `hosted_on` naming a nonexistent server -- lists the valid
  hosts; a genuine hosting-chain cycle between two members). Also proved
  the generalisation is real, not cosmetic: gave PNEA an explicit
  `hosted_on: ss-plr` under `profile: full` (impossible with the old
  lite-only constant) and confirmed `topology.json` and the generated
  scenario both reflected it correctly -- `security_servers` correctly
  dropped `SS-PNEA`, the subsystem entry correctly showed `hosted_on:
  ss-plr` -- then reverted the test. One rough edge found and
  deliberately left alone: the stub comment written when a member is
  hosted still reads "lite profile: ..." regardless of which resolution
  path put it there. Making it path-aware is a real improvement but was
  out of scope for this task and risked violating the byte-identical
  constraint for no requested behavior change; the comment is provably
  correct for both paths that matter today (the canonical four only ever
  hit it under `profile: lite`), so left as-is. `check_scenarios.py`
  green; 25 unit tests green.

## Task 3: Stable allocation of scenario numbers and host ports

**Files:** `hurl/generate.py`

**Interfaces:** Produces the `ports` and `scenario_number` fields consumed by Tasks 4–5.

- [x] **Step 1:** add two pinned tables to `generate.py`, with a comment explaining that they exist so the canonical five never move:

```python
PINNED_SCENARIO_NO = {"pnia": "20", "plr": "21", "moeys": "22", "pnea": "23"}
PINNED_PORTS = {  # ui, rest -- see lib.sh's AirPlay note on ss-pnia
    "pdga": (1000, 1080), "pnea": (2000, 2080), "plr": (3000, 3080),
    "pnia": (5100, 5180), "moeys": (6000, 6080),
}
```

- [x] **Step 2:** allocate anything not pinned deterministically from a fresh range — Security Server scenarios from `40`, service scenarios from `50`, host ports from `7000` (UI `7000 + 100n`, REST `7080 + 100n`), ordered by sorted member key so the same member set always yields the same allocation.
- [x] **Step 3:** refuse to allocate a port already present in `PINNED_PORTS` or already allocated, and refuse the 5000–5099 range outright with the AirPlay reason in the error message.
- [x] **Step 4:** service-scenario numbers follow the same pinned-then-allocated rule (`30`–`32` stay).
- [x] **Step 5:** extend `hurl/topology.json` with per-server `ui_port`/`rest_port` (host-side) and per-member `origin`; keep the existing in-network ports as they are, and name the two clearly enough that nobody confuses them.
- [x] **Step 6:** regenerate both profiles; scenario and `vars.env` diffs empty, `topology.json` gains fields only. Commit.

  **Verified live (2026-07-28):** added a throwaway fifth member owning
  its own server, regenerated -- got a real `40-ss-testagency.hurl` /
  `50-services-testagency.hurl` and a fresh `7000`/`7080` port allocation,
  correctly avoiding every pinned value. Removed it and confirmed
  scenarios/vars.env return byte-identical and `topology.json` returns to
  exactly this task's own field-gain diff, proving the allocation is
  stable and reversible -- the same property Task 9's real end-to-end
  proof depends on. `PINNED_PORTS` replaced `HOST_PORTS` (re-keyed from
  DNS name to SS-owner key, since that is what the allocator and
  `_ss_entry` both need to resolve pinned-vs-fresh). The canonical four's
  two historically divergent iteration orders (`security_servers`:
  pnea/plr/pnia/moeys; `subsystems`: pnia/plr/moeys/pnea) were kept
  exactly as they were, since JSON array order is part of the
  byte-identical guarantee -- a new member is appended after, in its own
  deterministic order, in both. `check_scenarios.py` green; 25 unit tests
  green.

## Task 4: One topology, consumed by bash

**Files:** `hurl/generate.py`, `scripts/lib.sh`, `scripts/acceptance.sh` (read-only impact)

- [x] **Step 1:** `generate.py` emits `hurl/topology.sh` — a sourceable fragment declaring `SS_UI`, `SS_REST`, `SS_ORDER` and `HOST_SS` with exactly the values it just put in `topology.json`, carrying a "GENERATED — do not hand-edit" header.
- [x] **Step 2:** `lib.sh` sources `hurl/topology.sh` instead of declaring those four itself. If the file is missing it runs `python3 hurl/generate.py` once to produce it (offline, no stack needed), then sources it; if it is still missing, hard fail.
- [x] **Step 3:** preserve the lite branch semantics exactly — under lite, `SS_ORDER` excludes hosted members' servers and `HOST_SS` points them at their host. This now comes from the generator rather than from a bash `if`.
- [x] **Step 4:** keep `lib.sh`'s AirPlay comment, moved to the generator's `PINNED_PORTS` table so the reasoning survives.
- [x] **Step 5:** verify with the stack up: `scripts/acceptance.sh` green under `full`; switch to `lite`, redeploy, green again. Commit.

  **Verified live (2026-07-28):** fresh cold deploy under `full` -> seed ->
  `acceptance.sh` GREEN (both attempts hit the same known transient
  right-after-deploy `JSONDecodeError` this pack already has elsewhere in
  its history -- unrelated to this task, clean on retry). Purged,
  regenerated for `lite`, redeployed, seeded -> GREEN again, with 2.6.4's
  negative check correctly reporting "denied by ... ss-plr" -- the
  generated `HOST_SS`'s lite branch, no longer a hand-written bash `if`.
  A real risk found and fixed before it could bite: `lib.sh` only
  regenerated `topology.sh` when *missing*, not when *stale* -- a
  leftover file from the other profile would be sourced silently. Added a
  profile-agreement check (`topology.json`'s own `profile` field vs
  `deployment.yaml`'s current one) that fails loudly instead.
  `PDGA:MANAGEMENT` (in `HOST_SS` but absent from `topology.json`'s
  subsystems list, since PDGA is the owner, not a discovered member) is
  added back explicitly when emitting `topology.sh`, so nothing lib.sh
  depended on was silently dropped. `check_scenarios.py` green; 25 unit
  tests green; restored `deployment.yaml` to `profile: full` afterward.

## Task 5: Compose overlay for joined members

**Files:** `hurl/generate.py`, `scripts/lib.sh`, `.gitignore`

- [x] **Step 1:** `generate.py` emits `hurl/compose.members.yml` containing service blocks **only** for members with `origin: joined` that own a Security Server — sidecar image, container name, allocated host ports, the three named volumes, `networks: [linkup]`, `depends_on: [cs, ca]` — mirroring the canonical blocks' shape.
- [x] **Step 2:** when no joined member owns a server, emit nothing (or a comment-only file) so the overlay is always safe to include.
- [x] **Step 3:** `lib.sh` adds `-f hurl/compose.members.yml` to both `COMPOSE` and `COMPOSE_ALL` when the file exists. `COMPOSE_ALL` must include it unconditionally where present, for the same reason `hurl/compose.hurl.yml` is already there — a volume defined in an overlay cannot be removed by a `down -v` that does not name that overlay.
- [x] **Step 4:** add `hurl/compose.members.yml` and `hurl/topology.sh` to `.gitignore` alongside the other generated artefacts.
- [x] **Step 5:** commit.

  **Verified live (2026-07-28):** added a throwaway joined member owning
  its own server and ran `docker compose ... config` on the merged files
  -- got back a fully resolved service (correct image, env vars from
  `.env`, ports, correctly joined to the `linkup` network
  `docker-compose.yml` declares), confirming YAML anchors don't cross
  Compose's `-f` file boundaries but its `services:`/`volumes:`/
  `networks:` keys do -- `compose.members.yml` redeclares its own
  `x-sidecar` anchor but not the network. Removed the test member
  afterward. Found and fixed a real ordering bug via an actual
  fresh-clone simulation (deleted both generated files, sourced `lib.sh`):
  the original placement checked for `compose.members.yml`'s existence
  *before* the generate-if-missing fallback had a chance to create it, so
  a fresh clone's first script run would have silently omitted the file
  it had just generated moments later in the same source. Reordered so
  the fallback runs first. Also found and fixed: `generate.py` never
  cleared stale scenario files from a removed member -- directly relevant
  to Task 9's byte-identical-after-remove proof. `.gitignore` gained all
  five generated `hurl/` artefacts (confirmed via `git check-ignore` that
  none were actually ignored before, only conventionally never staged).
  Live-verified end to end: fresh full-profile deploy -> seed ->
  `acceptance.sh` GREEN on the first attempt, with the generated (empty)
  overlay merged into every compose call with zero effect on the real
  deploy. `check_scenarios.py` green; 25 unit tests green.

## Task 6: Canonical vs joined in the manifest and the gate

**Files:** `manifest.yaml`, `hurl/check_scenarios.py`

- [x] **Step 1:** add `origin: canonical` to each of the four `identity.members` entries, with a comment that `identifiers:` is the frozen KP3/KP4 contract and only canonical members belong in it.
- [x] **Step 2:** relax the agreement check: every `identifiers.members` entry must still have a matching canonical `identity.members` entry; a `joined` member absent from `identifiers:` is fine; a `joined` member *present* in `identifiers:` is an error (it would silently enter the cross-pack contract).
- [x] **Step 3:** module `scenarios:` claims currently require every scenario file to be claimed. Joined members produce unclaimed scenarios by construction — allow files whose member key resolves to a `joined` member, and keep the strict rule for everything else.
- [x] **Step 4:** add checks that the allocation is sane: no duplicate host port, no duplicate scenario number, no joined member in the 5000–5099 range, every `hosted_on` resolvable.
- [x] **Step 5:** `python3 hurl/check_scenarios.py` green; the ship gate (`check_pack.py --ready`) green. Commit.

  **Verified live (2026-07-28):** every new/relaxed check triggered for
  real, not just read for plausibility. Added a throwaway joined member
  (14 scenarios) -- static gate green with its files correctly
  unclaimed-but-tolerated; put it in `identifiers.members` -- caught
  ("only canonical members belong in the frozen identifiers: cross-pack
  contract"); then individually broke `topology.json` four ways (a
  duplicate port, a port in 5000-5099, an unresolvable `hosted_on`, a
  duplicate scenario-number file) -- each produced the exact intended
  failure message, one at a time, reverted between tests. Lost the
  Step 1 manifest edit once to an uncommitted `git checkout --
  manifest.yaml` during test cleanup and had to redo it -- a reminder
  that "revert the test" needs to mean "revert only the test," not the
  whole file, when there's uncommitted real work sitting in it too.
  `check_scenarios.py` green; `check_pack.py --ready` green;
  `acceptance.sh` green on the live stack (still up from Task 5); 25 unit
  tests green.

## Task 7: Generic per-member acceptance

**Files:** `scripts/acceptance.sh`, `acceptance/member.md` (new)

- [x] **Step 1:** replace the hardcoded `for pair in MOEYS:PEMIS PNEA:EXAMS PLR:ENROLMENT PNIA:IDENTITY` with a loop over `topology.sh`'s `HOST_SS` keys, so the registration check covers whatever set is deployed.
- [x] **Step 2:** replace the two bespoke ACL checks (`check_241`, `check_251`) with one loop over every service declared in every member config: the subject list must equal the config's `access:` list exactly, and that subject's granted service codes must equal exactly the service published. Services with an empty `access:` must have **no** subjects — that is `pemis-api`'s current state, and it is currently unchecked.
- [x] **Step 3:** make the 2.6 exchange read its two r1 paths, its consumer and its negative caller from `configs/x-road-bus/2.6.yaml` rather than hardcoding `ID_URL`/`EN_URL`. The field-set assertions stay as they are — 2.6 is the education story's headline check and is deliberately specific.
- [x] **Step 4:** write `acceptance/member.md` in the pack's given/when/then idiom, describing the generic per-member check so joined members have a documented acceptance rather than an implicit one.
- [x] **Step 5:** run the full suite on both profiles; green. Commit.

**Verified live (2026-07-28):** rewrote `scripts/acceptance.sh`'s registration
loop to iterate `"${!HOST_SS[@]}"` (sorted, excluding `PDGA:MANAGEMENT`), and
replaced the two bespoke ACL checks with one loop driven by
`hurl/topology.json`'s `subsystems[].services[].access` — read from the
generated artefact directly, not re-parsed from configs. Confirmed live on
`full`: `2.x.acl(pemis-api) — pemis-api grants exactly (nobody)` now actually
asserts the empty-access case, previously entirely unchecked. The 2.6
exchange's `X-Road-Client`/negative-caller headers and both r1 path templates
now come from `configs/x-road-bus/2.6.yaml` via a `mapfile` read; entrypoint
(host:port) resolution deliberately still goes through `HOST_SS`/`SS_REST`
rather than 2.6.yaml's static `entrypoint:` fields — the same profile-unaware
trap `apps/console/truth.py` already documents. Ran the full suite GREEN
end-to-end on a fresh cold deploy under `lite` (correctly showing
`2.x(PNIA:IDENTITY) — client REGISTERED on ss-plr` and the negative check
denied via its hosting server) and then again on a fresh cold deploy under
`full` (all five members, `2.6.4` denied via each member's own server,
`check_scenarios.py` also clean: 82 captures, 18 variables, identifiers match
manifest.yaml). `deployment.yaml` left at its committed default,
`profile: full`. Wrote `acceptance/member.md` in the established
given/when/then house style, explicitly scoping it as the check 2.2–2.5
already make, expressed once and generically — not a new module number — so
a joined member added via Task 8's `prompts/member.md` has documented
acceptance from the moment it exists.

## Task 8: The generating prompt and the member script

**Files:** `prompts/member.md` (new), `scripts/member.sh` (new), `README.md`, `runbook.md`

- [x] **Step 1:** `prompts/member.md` — the bb-config-gen play that turns an agency brief into `configs/member-<key>/<module>.yaml` plus the `identity.members.<key>` entry: agency name and code, subsystem code and description, whether it runs its own Security Server or is `hosted_on` another, the services it publishes (service code, OpenAPI spec URL, semantic entity/key/fields) and who may call them. Same house style as the existing prompts: opens "Below is …", decomposes into named fields, ends with the exact output format, every identifier `[confirm: verify against the live registry]`.
- [x] **Step 2:** `scripts/member.sh list` prints the deployed member set with origin, host server and ports, from `topology.json`.
- [x] **Step 3:** `scripts/member.sh remove <key>` deletes the config directory and the `identity.members` entry, regenerates, and prints plainly that the live federation still holds the member until `scripts/teardown.sh --purge`. Refuses on a `canonical` member.
- [x] **Step 4:** no `member.sh add` that writes config by hand — adding a member is running the prompt. Say that in the script's help text; it is the pack's teaching claim.
- [x] **Step 5:** document both in `README.md` and `runbook.md`. Commit.

**Verified live (2026-07-28):** wrote `prompts/member.md` in the established
house style, generalised to produce TWO YAML documents (unlike 2.2–2.5's
config-only output) since a joining member has neither a config nor a
manifest identity entry yet — separated by a `---` line, the first being the
`identity.members.<key>` entry (always `origin: joined`, never touching the
frozen `identifiers:` block), the second `configs/member-<key>/<key>.yaml`.
Wrote `scripts/member.sh` with `list` (reads `hurl/topology.json`, confirmed
live: correctly showed origin/server/ports for all four canonical members)
and `remove <key>` (refuses on `canonical`, confirmed live against `moeys`
and against an unknown key). Live-tested the full add/remove mechanics with a
throwaway `test` member (`hosted_on: ss-plr`, no services): `generate.py`
discovered it, `member.sh list` showed `test joined ss-plr ...` resolved
through the hosted mapping correctly, `member.sh remove test` then deleted
`configs/member-test/`, removed the manifest block via the script's
indentation-scoped text surgery, and regenerated — `manifest.yaml` came back
**byte-identical** to the pre-test baseline (`diff` clean) and
`hurl/check_scenarios.py` passed clean afterward. Re-ran
`scripts/acceptance.sh` on the live full-profile stack after this round-trip
to confirm no residual damage: GREEN, all five checks including 2.6. No
`member.sh add`, stated in the script's own `usage()` text and in
`prompts/member.md`'s Safeguard section. Documented `member.sh`/
`prompts/member.md` in `README.md` (a new short paragraph plus updated
"What's here") and `runbook.md` (new "Joining a member" section between
Teardown and Known traps).

## Task 9: End-to-end proof, and the two investigations

**Files:** `docs/superpowers/plans/…` (this file's checkboxes), `docs/production-delta.md`, `docs/xroad-770-notes.md`

- [ ] **Step 1:** **the acceptance criterion for this whole plan.** With the stack up, add a throwaway sixth member — a health ministry publishing one service, `hosted_on: ss-plr`, granting access to `PNEA:EXAMS` — by running `prompts/member.md`. Regenerate, redeploy, and confirm: its subsystem registers, its service publishes, its ACL is exact, and a live call from PNEA resolves.
- [ ] **Step 2:** `scripts/member.sh remove` it, regenerate, and confirm the generated artefacts return **byte-identical** to the Task 1 baselines for both profiles. If they do not, the allocation is not stable and this task is not done.
- [ ] **Step 3:** repeat Step 1 with a member that owns its own Security Server, to exercise the compose overlay and port allocation. Record the RAM cost, and record in `README.md` that `hosted_on` is the recommended default for joined members on a single host.
- [ ] **Step 4:** **investigation** — whether `DELETE /clients/{id}` and member deletion on the Central Server can retire a member from a running federation, or whether `teardown.sh --purge` really is the only path. Record the finding in `docs/xroad-770-notes.md` either way; it decides whether a demonstration join can be undone on camera.
- [ ] **Step 5:** **investigation** — what a joined member costs when it owns a server (RAM, boot time, and whether the `retries: 120` healthcheck budget still covers a six-server start from persisted volumes). Record in `docs/production-delta.md`.
- [ ] **Step 6:** commit.

---

## Sequencing and risk

Tasks 1–3 are pure refactors behind a byte-identical guarantee and can go in one sitting. Task 4 is the highest-risk change in the plan — `lib.sh` is sourced by every script, and a bad `topology.sh` breaks deploy, seed, acceptance and teardown at once; do it with the stack up so a regression is visible immediately. Tasks 5–6 are additive. Task 7 changes the acceptance suite, so it must be the last thing that can mask a fault, not the first. Task 9 Step 2 — remove a member and get byte-identical artefacts back — is the single check that proves the parameterisation is real rather than a set of special cases.
