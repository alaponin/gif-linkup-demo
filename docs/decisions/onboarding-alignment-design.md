# KP2 build pack — alignment design

**Status:** decision record — frozen. Kept for the reasoning in §6, not as a
statement of what the pack contains today.

> **Correction (2026-08-08).** Parts of §3, §4.2, §5, §6a and §8 below describe a
> data layer that the later reduction wave did not build. `configs/governance/governance.yaml`,
> `configs/legal/lawful-basis.yaml` and `configs/x-road-bus/conventions.yaml` do
> not exist, and `join-policy.yaml` carries no BB pattern register. Those passages
> are annotated in place rather than rewritten — this is a decision record, the
> decisions were genuinely taken here, and what changed afterwards is what was
> built. **Current status lives in `docs/path-conformance.md`, generated from
> `docs/path-conformance.yaml`, whose every cited evidence path is
> existence-checked by `tests/test_path_conformance.py`. Where this document and
> that one disagree, that one is right.**

> **Correction (2026-08-09) — the catalogue entry.** This document contradicts
> itself about `catalogue-entry.md`, and the contradiction has been live since
> D3. §5's "not building" table says the pack builds *"a generated
> `catalogue-entry.md` per service"* **instead of** a catalogue; §4.4, written
> after D3, puts the same file **out of scope** *"until a curriculum or framework
> driver"*; §8.4 records it as a conscious deferral. **§4.4 and §8.4 are the
> post-D3 position and they win — §5's row is stale and should be read as
> withdrawn.** The framework driver §4.4 names now exists: amendment **A9** in
> `docs/GEATDM-Interop-Member-Onboarding-Path-v0.3-amendments.md` makes the
> catalogue entry a G5 exit condition. The design that replaces §5's row lives in
> `docs/decisions/service-catalogue-design.md`, and the deferral's consequence is
> now named where a reader meets it (`docs/production-delta.md`) rather than only
> here, in a frozen document. Annotated in place, per the rule above.

**Closes:** the findings in `docs/decisions/onboarding-path-gap-analysis.md` — with the
exceptions the correction above names.
**Decision record:** `docs/decisions/topology-profile-decision.md` (analysis and sources).
**Scope:** how the pack gets from what is implemented today to a pack that
demonstrates the member-onboarding workflow end to end, without growing into
something unteachable.

---

## 1. Three pushbacks, including one on my own recommendation

### 1.1 Sequencing by re-baselining cost, not by "reductions first"

An earlier framing of the gap analysis argued "the structural reductions go
first — every later item is cheaper against a smaller pack." That is half
right.

The cost that actually dominates is **re-baselining**, not authoring.
`tests/golden/{lite,full}/topology.json` were byte-identical assertions, the
un-join clause asserted byte-identity against them, and a full reproducibility
proof ran to several minutes. Anything that changes topology forces a
regenerate and a full proof.

So the right rule is not "reductions first." It is:

> **One re-baselining event. Everything that does not change topology goes
> before it; everything member-heavy goes after it.**

That splits the work differently and better:

- Governance, semantic map and pattern register are **member-light** — they add
  config files and change no topology. They go *before* the reduction and cost
  nothing extra for being done at five members.
- The onboarding record, the SLA fields and the monitoring add-ons are
  **member-heavy or server-heavy**. They go *after*, and get the benefit of
  running against a smaller member set that argument was actually reaching
  for.

The design below follows this rule, not a reductions-first order.

### 1.2 Retiring MoEYS breaks a contract the pack calls frozen — but the contract is stale

Retiring MoEYS is not a local change. `manifest.yaml`'s `identifiers:` block is
labelled *"Frozen identifiers — cross-pack join keys for KP3/KP4"* and lists
`PROGRESSA/GOV/MOEYS:PEMIS`. `hurl/check_scenarios.py` enforces that every
entry there resolves to an `identity.members` entry, so dropping MoEYS from
the demo **requires amending the frozen contract**.

That would normally be a reason to keep MoEYS. Checking the actual downstream
consumer says otherwise:

- `grep -rn "MOEYS\|PEMIS" 10-Knowledge-Products/KP3-DPI/` returns **nothing**.
- KP3's own config skeleton is `configs/{registration, identity-pnia,
  registry-plr, payment-paypro, _toolkit, _compose}/` — it builds on **PNIA and
  PLR**, exactly the two providers the reduction keeps.

So the frozen contract is over-specified relative to its only consumer. The
recommendation stands, but the change is **governed, not local**: amend
`identifiers:` with KP3/KP4 sign-off, while KP3 is still scaffolding.

### 1.3 The naming decision reaches beyond KP2 — but KP2 goes first anyway

KP3-build-pack uses the identical scheme — `prompts/3.4.md`, `acceptance/3.4.md`.
Renaming KP2 while KP3 keeps numbers trades one inconsistency for another, and
KP3 is still almost entirely `.gitkeep`, so changing it would be nearly free
today.

> **Resolved by D2: KP2 only; KP3 is not touched.** KP3 is not being worked on,
> and a scaffolding pack is not worth opening for a convention change on its own.
> The consequence is accepted and named: **KP2 and KP3 use different naming
> until KP3 is built.** The cost is that KP3's eventual conversion is no longer
> free — it will have content by then. See §6 for the note that has to reach
> whoever picks KP3 up.

---

## 2. Design principles

Five rules. Most of the design falls out of them, and they are the guardrails
against the obvious failure mode — a pack that answers "demonstrate every
necessary step" by growing without limit.

**P1 · Data over code.** A YAML file the learner reads is cheap to build, cheap
to maintain and teaches directly. An endpoint, a job step or a validator is
expensive and teaches indirectly. Every gap below is closed with config plus at
most one validator, never with a new subsystem.

**P2 · A named absence teaches as well as an implementation.** An
`00-application.md` holding three lines — what the gate asks, who signs, and
"not implemented in the demo; see production-delta" — teaches the gate as well as
a filled-in one, at a twentieth of the cost. This is what makes the §7 onboarding
record affordable.

**P3 · One re-baselining event.** See §1.1.

**P4 · The manifest is the index; filenames name capabilities.** `video_ref`
already carries curriculum traceability. Encoding it a second time in filenames
is the "second, driftable copy" the pack's own config comments reject four times.

**P5 · Member count is data, not curriculum.** `discover_members()` already makes
this true in code. The four near-identical registration modules are the residue.

**P6 · The pack may exceed the curriculum; it must label the surplus.**
(Added following D3 — see §6.) The build pack is allowed to demonstrate
capabilities no video teaches, because a runnable artefact outlives a video
cut and the video bundles are contracted deliverables with fixed subtopic
counts and runtimes. What it must not do is let that surplus drive its own
centre of gravity, or leave it unmarked. `manifest.yaml`'s `video_ref: "?"` on
the join module is the correct behaviour, not a defect — it is the pack
saying, accurately, "this exceeds the curriculum." **K-04 is withdrawn as a
finding on that basis.**

---

## 3. Target shape

```
manifest.yaml                       index: capability id → BB → config → prompt
                                    → acceptance, video_ref retained
configs/
  governance/governance.yaml        NOT BUILT -- see the correction at the top
  legal/lawful-basis.yaml           NOT BUILT -- lawful_basis is a payload
                                    field instead (schema.py:90, :144)
  semantic/semantic-map.yaml        entities + OneRoster/CEDS/11179 anchors
  x-road-bus/
    federation.yaml                 shipped as federation-core.yaml
    once-only-exchange.yaml         was 2.6
    join-policy.yaml                was 2.7; BB pattern register NOT BUILT --
                                    the file admits exactly four keys
  member-pnia/pnia.yaml             provider — identity      (own server)
  member-plr/plr.yaml               provider — enrolment     (own server)
  member-pnea/pnea.yaml             consumer; denied caller  (own server)
onboarding/<key>/                   the gate record, one file per gate exit
                                    same lowercase key as configs/member-<key>/
prompts/
  federation-core.md
  register-member.md                was 2.2–2.5, now one, parameterised
  once-only-exchange.md
  join-member.md                    was 2.7
acceptance/
  <same names>.md + member.md
tests/golden/
  deployment/                       the real topology, byte-identical
  hosted-fixture/                   generator-only; never deployed (§8.6)
deployment.yaml                     no `profile:` key — one topology
```

**Topology: one Central Server, four Security Servers, three members.**
`ss-pdga` (management — load-bearing, it provides the CS's own management
services), `ss-pnia`, `ss-plr`, `ss-pnea`. **All own-server; no canonical member
is hosted, and there is no `hosted_on` in any canonical config.** Four capability
modules instead of seven numbered ones.

Hosting is still demonstrated — by the join API, whose `default_hosting:
hosted_on` makes every joined member a client on an existing member's server.
That is member-hosts-member, which is X-Road's own defined *security server
host*: "a member who provides security server hosting services to third parties
and other members." It is the sourced pattern, demonstrated in the right place.

**Note on `onboarding/` vs the path's `members/`.** The path §7 specifies
`members/<member-code>/`. That collides conceptually with `configs/member-<key>/`
and mechanically with `discover_members()`, which requires **exactly one** YAML
per member directory — a `contract.openapi.yaml` dropped in there fails the
build. Separate top-level tree, different word. Recommend flagging the deviation
back to the path document rather than working around it.

---

## 4. What changed, by area

Each area below landed leaving the pack green. Only the reduction (§4.3)
regenerated the golden corpus.

### 4.1 Corrections — no topology change

The bug and the paperwork. Isolated, golden files untouched.

- `_BAD_CHARS` → the X-Road 7.3+ allowlist (**G-01** — the only finding where the
  pack produces a wrong answer rather than an absence)
- Reconcile `acceptance/2.7.md` and `production-delta.md` with
  `R1_RETRY_BUDGET = 54` (**G-10**)
- Doc rows: ports 5500/5577, GX retention note, development-track personal-data
  prohibition (**G-08, G-03a, G-09**)
- Comment in `steps.py` naming the inbound-ACL reversal gap for KP3 (withdrawn
  G-03b)

**Exit:** `verify.sh --fast` green, no regenerate.

### 4.2 The data layers — no topology change

The three missing layers KP2 teaches and the pack did not carry. All are config
plus one validator hook each (P1). Member-light, so doing them before the
reduction cost nothing.

- ~~**`configs/governance/governance.yaml`** (**K-02, G-02**) — the RACI as data:
  per gate, the accountable and responsible role. `POST /approve` then requires
  the role the RACI names accountable for admission, which is a second bearer
  token, not a workflow engine.~~
  **NOT BUILT.** `POST /approve` (`apps/join-api/app.py:493`) requires the
  single operator token plus a `decision_reference` checked only for
  non-emptiness. There is no second role. K-02 and G-02 are **open**, and
  §8.1's "G1 admission authority ✓" below is wrong.
- ~~**`configs/legal/lawful-basis.yaml`** (**K-02**) — the decree's basis per
  exchange. Feeds 5.2's sixth requirement and G5's data-protection envelope.~~
  **NOT BUILT.** `lawful_basis` is a free-text field on `Service` and
  `MemberRequirements` (`apps/join-api/schema.py:90, :144`), required only for
  consumer-only members (`validate.py:349-366` returns early when
  `payload.services` is non-empty) — so no provider states one.
- **`configs/semantic/semantic-map.yaml`** (**K-03**) — the Module 4 map the two
  member configs already cite and that did not exist. `validate.py` check 8 goes
  from presence to conformance.
- **BB pattern register** in `join-policy.yaml` + optional `pattern:` on
  `schema.Semantic` (**G-04**) — **half built.** The `ExchangePattern` enum
  exists (`apps/join-api/schema.py:99-118`) and both providers set it, but the
  **register was not added**: `join-policy.yaml` admits exactly four keys and
  bans a fifth by design, and nothing validates `pattern:` against anything
  (`apps/join-api/writer.py:146` — "never resolved against anything").
- ~~**`configs/x-road-bus/conventions.yaml`** — identifier charset, member code
  scheme, subsystem code scheme, Security Server host naming, as data.
  `validate.py`'s identifier charset fix reads its pattern from here rather
  than hardcoding it.~~
  **NOT BUILT, and deliberately reversed.** The charset is a literal in
  `apps/join-api/validate.py:448`, whose own comment argues for it: "this
  constant is that page's cited source, not a copy of a value that lives
  somewhere else. One rule, one place, no indirection (design decision 1)."
  The four conventions are published as prose in `docs/conventions.md`; only
  the charset is enforced.

**Exit:** `--live` green. Topology unchanged, so goldens still match.

### 4.3 The reduction — the single re-baseline

**The rename and the profile removal landed together, in one pass** — doing
them separately would have meant regenerating the golden corpus twice. This
was the largest and only topology-touching piece of work in the programme —
see §7.

**(a) Reduce and rename**

- Collapse modules 2.2–2.5 into one `register-member` module, prompt and
  acceptance document, parameterised over `configs/member-*/` — which
  `acceptance/member.md` already is generically
- Retire MoEYS; amend `identifiers:` with KP3/KP4 sign-off (§1.2)
- Reassign 2.6's negative check to `PLR:ENROLMENT` calling PNIA's `identity-api`
- Rename everything to capability names; `manifest.yaml` keeps `video_ref`
- **KP3 is not touched** (D2). The convention is recorded where KP3's eventual
  build plan will find it — one line in KP3's `README.md` or `manifest.yaml`
  pointing at this design, not a rename of its scaffolding

**(b) Remove the profiles (D5)** — topology becomes four servers, all own-server

- `generate.py`: delete `LITE_HOSTED_ON`; `resolve_hosted_on_map(members,
  profile)` loses its parameter and its lite branch (~25 `profile` mentions)
- `deployment.yaml`: delete the `profile:` key
- `docker-compose.yml`: remove compose profiles — **this removes a workaround**,
  the `depends_on` hole where `ss-pnia`/`ss-moeys` sit outside the dependency
  graph because "a non-profiled dependency cannot reference" a profiled service
- Stub scenario files (`20-ss-pnia.hurl` written as a stub so `manifest.yaml`'s
  claims resolve) disappear — **second workaround removed**
- `tests/golden/{full,lite}/` → `tests/golden/{deployment,hosted-fixture}/`;
  rename `generate.py --profile` to `--topology-fixture` so nothing is called
  "profile" once the deployment key is gone (§8.6)
- `tests/test_golden.py`, `test_tiers.py`, `test_steps.py`: drop profile
  parametrisation, keep two fixtures
- `acceptance/2.2.md`, `2.5.md`, `2.7.md`, `member.md`: remove lite caveats
- `acceptance/join-member.md` clause 5: "byte-identical to the golden file for
  this deployment's profile" → the single deployment golden
- `README.md`: delete the tier×profile guidance; `runbook.md` and
  `production-delta.md` likewise
- `apps/console/tests/fixtures/{full,lite}/` → one (keep `inconsistent/`)

**(c) Re-baseline — once, at the end**

- Regenerate both goldens; **one** `--full` proof

**Exit:** `--full` green on the new baseline, one regeneration, one topology, one
story, and a large cut in total verification time per plan (§8.6).

### 4.4 The onboarding record — on the new baseline, trimmed per D3

Member-heavy, so it landed after the reduction and was written three times, not
five. **Trimmed following D3 (no curriculum change):** the record covers the
gates KP2 actually teaches, not the path's full seven.

**In scope — curriculum-backed:**

- `member_requirements` (5.2's six items) and `sla` (5.3's five numbers) on
  `JoinPayload`, rendered into the member's record (**K-01**). Unaffected by
  D3 — 5.2 and 5.3 are existing subtopics teaching exactly these as templates,
  and this is the whole justification for this piece of work.
- `onboarding/<key>/` covering **three** gate exits, not ten: requirements
  (5.2), SLA (5.3), registration (5.4). Generated, three-line stubs under P2.
- One `sla.md` per service — 5.3's own "reuse the same template for every service
  on the bus."

**Out of scope, deferred:**

- The remaining seven §7 files (application, admission, certificates, catalogue
  entry, go-live, retirement). These are path-backed only, and with the
  curriculum unchanged they would be the pack teaching gates no video covers —
  P6's failure mode. Recorded in `production-delta.md` as named absences instead.
- `catalogue-entry.md` (**G-05b**) — out of scope. The SLA half is
  curriculum-backed and stays as `sla.md`; the catalogue metadata half waits for
  a curriculum or framework driver.
  > **Un-deferred (2026-08-09).** The framework driver arrived: amendment **A9**
  > makes the catalogue entry a G5 exit condition, which is the trigger this
  > bullet names. The curriculum door stays closed and D3 stays correct — Topic 5
  > is a fixed-runtime contracted deliverable, and the entry is built because the
  > *framework* requires it, not because the pack found a gap and grew. Design:
  > `docs/decisions/service-catalogue-design.md`. Plan:
  > `docs/decisions/superpowers/plans/2026-08-09-kp2-service-catalogue.md`. This
  > bullet also supersedes §5's row 1, which said the opposite.

**Exit:** three members each carrying a requirements record, an SLA per service,
and a registration record — the three things Topic 5 teaches before the bus call.

### 4.5 Monitoring add-ons

- Operational and environmental monitoring add-ons installed during Security
  Server bring-up (**G-06**)

This landed last because it changes bring-up for every server and adds time to
every subsequent cycle — but it was **scheduled, not deferred**. It is the
path's one asymmetric-cost item ("trivial at G4, a campaign afterwards"), it
completes G4's three-part exit test, and Module 6.2 teaches bus monitoring.
Three servers rather than five made this a cheap moment to do it.

---

## 5. What we are deliberately not building

Stated so scope creep has something to bounce off.

| Not building | Instead | Why |
|---|---|---|
| A service catalogue (collector, portal) | ~~A generated `catalogue-entry.md` per service~~ — **withdrawn, see below** | Path §6 makes the catalogue an operator building block; the pack demonstrates the *entry* and the SLA attachment, which is the G5 gap |
| A membership-agreement workflow | A reference and a stub in `onboarding/<key>/` | P2 — a signed instrument is not a demo artefact; the gate it creates is |
| A Steering Committee as a running system | ~~A role in `governance.yaml` + a second token on `/approve`~~ → **as built:** a `decision_reference` on `/approve`, checked for non-emptiness only | Revised: in both reference instantiations admission is decided outside any system (Estonia via RIHA, Finland by form to DVV), and path §4 says G0–G3 are "not automatable, and should not be". The enforceable artefact is the *reference to* the decision, not the committee |
| Retention/archival machinery | Two sentences beside the teardown instruction | Demo teardown deleting a volume is fine; the silence about retention is not |
| BB implementations | The `pattern:` classification only | KP3 |
| Real backends | The Joget seam stays as-is | KP4 |

> **Annotation (2026-08-09).** Row 1's "instead" column was written before D3 and
> is withdrawn by §4.4 below, which puts `catalogue-entry.md` out of scope. The
> *left* column is still correct and still holds: no collector, no portal. What
> replaces the middle column is `docs/decisions/service-catalogue-design.md`,
> whose §2 draws the boundary this row was reaching for more precisely — a
> **collector** pulls from the bus and can never see an SLA, a lawful basis or a
> pattern classification, because none of the three are on the wire; the
> **register's own derived output** has all three because the join payload
> carried them. This pack builds the second and still not the first.

---

## 6. Decisions

| # | Decision | Outcome | Affects |
|---|---|---|---|
| **D1** | Retire MoEYS and amend the frozen `identifiers:` contract? | **DECIDED — retire, amend now.** Three members; deny-check moves to `PLR:ENROLMENT` → PNIA `identity-api`. Amendment needs explicit KP3/KP4 sign-off, while KP3 is scaffolding. | The reduction |
| **D3** | Does the curriculum gain a subtopic covering the join workflow? | **DECIDED — no curriculum change.** The onboarding-record work trimmed accordingly; P6 added. | The onboarding record |
| **D5** | Topology size, and keep or drop the `full`/`lite` profiles? | **DECIDED — T1 + drop profiles.** Four servers, all own-server; single topology. Reverses an earlier T2 recommendation on sourced evidence — see below. | The reduction |
| **D2** | Is capability-based naming applied to KP3 too? | **DECIDED — no. KP2 only; KP3 untouched.** Accepted consequence: the two packs diverge until KP3 is built, and KP3's conversion stops being free. Mitigation: leave a pointer, not a rename. | The reduction |
| **D4** | `onboarding/<key>/` vs the path's `members/<code>/` | **DECIDED — `onboarding/<key>/`.** Reasoning below. | The onboarding record |

### What D4 settled

`onboarding/`, with the **same lowercase key** as `configs/member-<key>/`.

- **The path's own concept name is "onboarding," not "members."** §7's heading is
  *"The onboarding file — one folder per member"*; `members/` is only the example
  path. Naming the tree after the concept is what P4 asks for — the folder holds
  gate evidence, not member configuration.
- **It keeps two member-shaped trees visibly distinct.** `configs/member-plr/` is
  what gets deployed; `onboarding/plr/` is what proves the gates were passed. Two
  trees both called member-something would invite exactly the confusion the
  rename is meant to remove.
- **One key convention, not two.** The path writes `<member-code>` (uppercase,
  `PLR`); the pack's key is lowercase and already enforced — `[a-z0-9]+` in
  `validate.py`'s `_check_key_derivation`, matching `configs/member-<key>/`.
  Introducing an uppercase directory convention for one tree would mean every
  tool case-converts. Not worth it for literal fidelity.
- The mechanical collision that originally motivated the question — a
  `contract.openapi.yaml` breaking `discover_members()`'s one-YAML-per-directory
  rule — disappears with any separate top-level tree, so it does not decide
  between the two names.

**Feed back to the path document:** a note that KP2 implements §7 as
`onboarding/<key>/`, and that the section's own heading is the better name for
the concept.

### What D5 decided, and why the earlier answer was wrong

Full analysis and sources in `docs/decisions/topology-profile-decision.md`. In brief:

**The floor is three members and four servers.** `ss-pdga` is load-bearing —
`steps.py` records it as "PDGA-only: nominates the management Security Server as
the provider of the CS's own management services." Two providers are irreducible
if once-only is to mean composition across authoritative sources, and a consumer
is irreducible. Two servers is not reachable without either killing once-only or
hosting an authoritative publisher on a peer.

**An intermediate option (T2 — three servers, PNEA hosted) was recommended and
then withdrawn.** Its case was that it teaches the G2 hosting decision by
construction. Checking that against X-Road documentation and the two reference
instantiations:

- Hosting itself is well founded — multi-tenancy is architectural and *security
  server host* is a defined X-Road term.
- But **"consumer-only bodies are hosted" is the onboarding path's own inference,
  not sourced practice** — X-Road's definition of a security server client is
  role-neutral, and commercial hosts serve providers too.
- And **the host in practice is a commercial third party, not the operator** —
  in Estonia, Telia via Riigipilv, plus turvaserver.ee and Almic. RIA does not
  host. T2 would have put a member on the operator's server.

T2 would therefore have taught a worked example of something unsourced, which is
worse than teaching it in prose. **Finland's documented answer for small
organisations is the containerised Sidecar, not hosting** — and
`docker-compose.yml` already runs `niis/xroad-security-server-sidecar` at ~2.1 GiB
per server. The pack is already implementing that answer; T1 is consistent with
it, at roughly 2 GB and a few minutes more than T2.

**Feed back to the path document:** G2's hosting table frames hosting as a
delegation to avoid, where practice treats it as a service to buy with the
delegation handled by contract and HSM. Its "suits small consumer-only bodies"
row should be marked as the path's own reasoning rather than reference practice.

### What D3 changes

The video bundles are contracted deliverables (RFQ-S-GIGA-2026-022) with fixed
subtopic counts and runtimes — Topic 5 is "seven subtopics (5.1–5.7),
approximately 33 minutes." Adding one is a scope change to an ITU contract, so
"no curriculum change" is not only a preference but the cheap answer. Three
consequences, all now folded in:

1. **The onboarding record shrinks** to the three gates Topic 5 teaches —
   requirements, SLA, registration. The other seven §7 files become named
   absences in `production-delta.md` rather than stubs in the tree.
2. **The join API stays a labelled surplus** (P6). It remains fully built,
   live-verified and demonstrable; it does not become the pack's organising
   principle, and the earlier "join module as centre of gravity" framing is
   withdrawn.
3. **K-04 is withdrawn.** `video_ref: "?"` is the pack correctly recording that
   it exceeds the curriculum, not a defect to close.

Worth revisiting only if Topic 5 is ever re-cut for another reason — at which
point 5.2 → 5.3 → join → 5.4 is the sequence that was on the table.

---

## 7. Risks

- **The reduction was the only genuinely risky piece of work, and D5 made it
  bigger.** It carried the member reduction, the rename, the frozen-contract
  amendment, the profile removal and one golden regeneration. Splitting it
  would have been worse: profile removal changes topology, so doing it
  separately buys a *second* re-baselining event and breaks P3. It stayed as
  one plan, structured as sequenced steps: (a) reduce and rename → (b) remove
  profiles → (c) regenerate once, with its own `--full` proof at the end.
- **After the reduction, `--full` is the only deploy path** — a regression in
  it has no cheaper sibling to bisect against. Mitigated by `--fast` and
  `--live` being untouched (§8.6) and by the hosted fixture keeping the
  generator honest.
- **The monitoring add-ons slow every cycle after them.** Worth measuring the
  add-on cost before committing, and worth accepting — invisible members are
  the more expensive outcome.
- **P1 is under pressure from the data layers.** "The RACI should really be
  enforced per gate" is how a config file becomes a workflow engine. The test:
  can it be set to another value, and does something observably change? — the
  pack's own rule from `configs/x-road-bus/2.7.yaml`.
- **The frozen-contract amendment needs a real sign-off**, not a commit. It is
  the one change here that another pack could be building against.

---

## 8. Component completeness review

Does the pack hold every component the onboarding path needs? Checked against
the path's own component lists rather than its gates. **Mostly yes — one
defect in this design, one real gap it does not close, and three conscious
deferrals.**

### 8.1 Component-by-component

**Superseded by `docs/path-conformance.md`.** This section's tables and ✓
marks conflated "built", "simulated" and "labelled", which is how the G1
error below survived review. The generated matrix uses four statuses and no
✓, and its evidence is existence-checked — see it for current status on the
ecosystem prerequisites, the semantic layer's three tiers, the operator's own
building blocks, and every gate.

### 8.2 Defect in this design: the default profile violates G2

**An earlier recommendation in this design was withdrawn.** It had proposed
three members and `profile: lite` as the default. Checking what lite actually
was:

> "Lite profile hosts PNIA and MoEYS on `ss-plr`. Their SIGN key/cert and client
> registration are generated as fragments appended into `21-ss-plr.hurl`"
> — `hurl/README.md`, Known limits

So lite hosted **PNIA — the authoritative person identity register** — as a
client on PLR's Security Server. The path's G2 exit test:

> "A body publishing authoritative personal data should not be hosted on a peer's
> server, because the host's token then holds its signing key — a delegation with
> no counterpart in the obligation set."

PNIA publishes `nin`, `given_name`, `family_name`, `date_of_birth`, `sex`,
`region`. Making lite the default would have put the pack's **headline
configuration in direct violation of G2 using its most sensitive member**, and
would have modelled PLR's operator being able to sign as the national identity
authority.

**Corrected target: four Security Servers, all own-server** — PDGA, PNIA, PLR,
PNEA. Costs roughly 2 GB more than lite and some cycle time. Worth it: the
alternative teaches a delegation the framework forbids.

**Hosting is still demonstrated, and in the right place** — the join API's
`default_hosting: hosted_on` makes every joined member hosted by default. That is
member-hosts-member, X-Road's own defined *security server host*, which is the
sourced pattern.

> **Confirmed and strengthened by D5.** This section originally rested only on
> the G2 argument, and its conclusion was briefly overturned by an intermediate
> three-server proposal. The evidence check behind D5 (§6) restores it on firmer
> ground: not only does hosting PNIA violate G2, but the criterion that would
> have justified hosting anyone in the canonical set is unsourced, and Finland's
> documented answer for small organisations is the containerised Sidecar the pack
> already runs. **Lite does not survive as a development convenience either** —
> it was removed with the profile split, and §8.6 shows why nothing cheap was lost.

### 8.3 The real gap this design does not close: the conventions register

**§0 prerequisite 5 and the whole of §1a were unaddressed for a while.** The path
treats these as ecosystem decisions made once, before member #1:

| Convention | Status |
|---|---|
| Identifier character set | **Enforced** in `validate.py`; **published** in `docs/conventions.md` |
| Member code scheme | Published in `docs/conventions.md` |
| Subsystem code scheme | Published in `docs/conventions.md` |
| Security Server host naming | `ss-<key>` by convention, published in `docs/conventions.md` |

The path is blunt about why this matters: *"a convention retrofitted after fifty
members is not retrofitted at all"* and *"certificates, DNS, firewall rules and
monitoring all key off the host name."*

**This gap is still open.** `configs/x-road-bus/conventions.yaml` was never
created; see §4.2. The four rules are published as prose in
`docs/conventions.md`, and only the identifier charset is enforced
(`validate.py:448`, a literal). §0.5 and §1a are therefore *published* but
not *testable*, which is the weaker half of what this section claimed.

### 8.4 Conscious deferrals — complete list

| Deferred | By | Consequence to accept |
|---|---|---|
| Service catalogue entry | D3 | The SLA lands as `sla.md` on the member record rather than as catalogue metadata. Better than having nothing, but a smaller version of the path's orphan-SLA problem survives — the SLA is attached to the member, not discoverable with the service. **(2026-08-09: deferral lifted by A9 — this row was right about the consequence, and the consequence is what the amendment cites.)** |
| Monitoring collection layer | Scope of the monitoring-add-ons work | Add-ons installed at G4 emitting to no collector, so **G4's third exit test — "is its monitoring data arriving centrally?" — remains unmet.** Defensible under P2: the lesson G-06 carries is *install the add-on at G4 or run a retrofit campaign*, and an installed add-on with a documented absent collector teaches exactly that. Adding `xroad-metrics` (NIIS, OSS) later closes it properly. |
| Membership agreement, admission record, certificates record, go-live record | D3 | Four of the seven §7 gate files become named absences in `production-delta.md`. |
| Inbound ACL revocation at GX | KP3 | Unreachable today; comment left in `steps.py`. |
| BB implementations | KP3 | Pattern classification only. |

### 8.5 One question the pack now poses rather than answers

PNEA is a consumer-only member with no `services:` block — so it gets no
`sla.md`. That is the path's §8 open question 5 appearing concretely:

> "Does a consumer-only member need an SLA? TK-IO-09 is written for providers; a
> consumer's obligations (rate, purpose limitation, log cooperation) have no
> template."

Worth leaving visible rather than papering over. A pack that makes a framework's
open question *concrete and observable* is doing its job — and this one is cheap
to surface as a one-line note in PNEA's record.

### 8.6 Testing after D5 — nothing cheap was lost

Full working in `docs/decisions/topology-profile-decision.md` §5.

**The cheap tiers were untouched, because profiles never made them cheap.**
`--fast` has "no running containers, no network, no federation" — there was no
topology for it to have a profile of. `--live` needs a running stack but
explicitly refuses to deploy one. The profile split only ever discounted
`--full`, which the README itself calls "not a per-task ritual."

**The golden corpus did not shrink**, because `profile` was two things sharing a
word: `deployment.yaml`'s `profile:` (what gets stood up) and `generate.py
--profile` (what gets rendered). `test_golden.py` already used the second and
never deployed anything. So:

| | Before | After |
|---|---|---|
| Deployable topologies | 2 | **1** |
| Golden fixtures | 2, tied to profiles | **2, decoupled** — `deployment/` and `hosted-fixture/` |

The second fixture is what keeps `build_hosted_client()` and
`resolve_hosted_on_map()` under byte-identical test once no canonical member is
hosted. The join API's tests do not cover that path — `job.py`'s docstring is
explicit that the job engine differs from what `run-linkup.sh` does with the same
templates. Cost: a directory of YAML and a generated tree. No containers, no
deploy time, no contributor-facing choice.

**Total verification time per plan went down.** A plan used to pay N lite
cycles *plus* a mandatory full-profile proof; after the reduction it pays N
single-topology cycles, and for the 1–3 cycle range most plans actually run,
that is faster end to end. See `docs/production-delta.md` and
`docs/decisions/topology-profile-decision.md` §5.3 for the current arithmetic and
measured figures.

**A reliability gain worth more than the seconds.** `production-delta.md`
records a reproducible contention failure at six concurrent Security Server
JVMs (Hikari "thread starvation or clock leap detected", admin API hanging
mid-handshake). A `--full` run with an own-server join used to run 5 canonical
+ 1 = 6 servers, exactly that count; after the reduction it runs 4 + 1 = 5,
one clear of it. A flaky verification tier costs more than a slow one.
