# KP2 — Service catalogue: the entry, the aggregate, and one read endpoint

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Design:** `docs/decisions/service-catalogue-design.md` — read it first; this
plan implements it and does not restate it.
**Driver:** amendment **A9** in
`docs/GEATDM-Interop-Member-Onboarding-Path-v0.3-amendments.md`.
**Un-defers:** `docs/decisions/onboarding-alignment-design.md` §4.4's G-05b,
whose stated unlock condition was "a curriculum or framework driver".

**Goal:** a member that has just joined can find out what is published on this
bus, under what lawful basis, against which SLA, and whether it has been granted
access — without asking someone who already knows. Today the honest answer to
that question is that it asks someone.

**Architecture:** three artefacts, one derivation. A markdown entry per published
service (`onboarding/<key>/04-catalogue/<code>.md`), a YAML aggregate over the
whole instance (`onboarding/catalogue.yaml`), and a read-only `GET /catalogue` on
`apps/join-api` serving the same derived data as JSON. Everything is generated
from `manifest.yaml` + `configs/member-*/` + the validated `JoinPayload` — the
inputs that already exist. **No `schema.py` change: not one new field is asked of
a joining member.** No collector, no portal, no scraping of X-Road metaservices.

---

## Global Constraints

- **No new obligation on the member.** If a task finds itself adding a field to
  `schema.JoinPayload`, stop — that is a different plan with a different
  justification, and A9 is written specifically so it isn't needed.
- **Derived, never accumulated.** The aggregate is regenerated wholesale from
  configs every time. Nothing appends an entry, and nothing deletes one. If a
  task needs a delete path, the derivation is wrong.
- **No status claim before the code.** `docs/path-conformance.yaml` rows move in
  Task 6, after the artefacts exist and the tests pass — not alongside the
  implementation, and not before. This is the 2026-08-08 rule and it applies to
  this plan exactly as to any other.
- **The demo must not slow down.** `scripts/acceptance.sh` green, unchanged, at
  the end of every task. Rendering is file I/O over three members; if any task
  adds measurable deploy time, it is doing something it shouldn't.
- **Publication is not permission.** Every artefact this plan produces states it.
  A reviewer who cannot find that sentence on an entry should fail the task.
- **Code comments: relevant, concise, generic.** A comment says what the code
  does, or why it is the way it is, in terms a reader of that file can check
  from that file. No pointers to this plan, to
  `service-catalogue-design.md`, to amendment A9, to rule numbers (R1–R5), or to
  conformance ids. Those are the wrong kind of durable: they get superseded,
  renumbered and frozen, and every one of them is a second thing to maintain
  whose staleness nothing detects. *"The SLA is linked rather than copied, so
  there is one signed record and nothing to drift"* is a good comment. *"design
  R1"* is not — it costs a reader a file open and tells them less. Reasoning
  belongs in the docs; the code states what holds.
  **Applies to new code only.** Much of the surrounding code comments in the
  opposite style, citing documents and decision ids. Do not rewrite it as part
  of this plan — that is a separate, larger change with its own argument to
  make, and mixing it in here would bury this work in an unrelated diff.
- **The same rule for generated output, for a different reason.** Nothing this
  plan renders into an entry or the aggregate cites an internal id either. A
  member reading a catalogue entry does not know what S6a.1 is and has no way to
  look it up; the absence has to be stated in words that mean something to them.
- Commit after every task.

### The one new coupling, named up front

Before this plan, a join wrote only `configs/member-<key>/`, `manifest.yaml`, and
that member's own `onboarding/<key>/`. The aggregate is the first **shared** file
a join touches. `writer._git_status_dirty` already guards `onboarding/`, so an
uncommitted `catalogue.yaml` from one join will refuse the next one with
`DirtyCheckoutError` — the same trap `docs/production-delta.md` already records
for `render_onboarding_tree`'s non-atomic write. Two consequences, both design
decisions rather than accidents:

- the aggregate is regenerated **last**, after the tree, so a failed join never
  leaves a catalogue naming a member that does not exist;
- `scripts/member.sh remove` (config removal only, no API) leaves the aggregate
  stale until someone regenerates. Task 2 Step 6 documents that rather than
  fixing it — `member.sh` is the deliberately dumb path, and teaching it to
  regenerate would make it the third thing that writes `onboarding/`.

---

## Task 1: The catalogue entry

**Files:** `apps/join-api/writer.py`, `scripts/render_onboarding.py` (if it
duplicates any rendering), `apps/join-api/tests/test_writer.py`,
`tests/test_render_onboarding.py`, `onboarding/{plr,pnia,pnea}/`

- [ ] **Step 1:** add `render_catalogue_entry(...) -> str` to `writer.py`,
      beside `render_sla_record`, rendering the design's §4 field table for one
      service. Fields and their sources are fixed by that table; do not invent
      one. Every field renders its own absence in italics when the source is
      empty (design R3), and the tier-1 pattern's absence renders as
      *"unclassified — this service declares no exchange pattern, so it cannot
      be found by pattern"*, not as a blank cell and not as a conformance id.
      State the consequence to the reader, not the row it corresponds to.
- [ ] **Step 2:** the SLA row is a **relative link** to `../03-sla/<code>.md`
      (design R1). Do not copy any of the five SLA terms into the entry — the
      whole point is one SLA, reachable from two directions.
- [ ] **Step 3:** the X-Road service id is
      `<instance>/<member_class>/<CODE>/<SUBSYSTEM>/<service-code>`. Read the
      instance from `manifest.yaml`'s `identity.instance` and the class from
      `configs/x-road-bus/join-policy.yaml`'s `member_class` — **do not hardcode
      `PROGRESSA` or `GOV`.** Both already have exactly one home; a second copy
      here is the drift `configs/member-plr/plr.yaml`'s own comment warns about.
- [ ] **Step 4:** the semantic anchor (`enrolment` → `OneRoster`) comes from
      `configs/semantic/semantic-map.yaml`, read from `target_dir` — the same
      file `validate.load_semantic_map` reads. `writer.py` does not import
      `validate.py` today and should not start; read the file, or pass the map
      in from the caller. Note the ordering constraint: `apply_real` writes
      configs before rendering the tree, so the map is present by then.
- [ ] **Step 5:** fixed footer text on every entry, verbatim in spirit: *this
      entry records what was published, not what you may call. Access is the
      provider's ACL; appearing here grants nothing.* (design R5)
- [ ] **Step 6:** wire into `render_onboarding_tree` next to the `03-sla/` block
      and under the same condition — `if payload.services:`. **A consumer-only
      member gets no `04-catalogue/` directory**, exactly as it gets no
      `03-sla/`. No empty directory, no placeholder file.
- [ ] **Step 7:** tests. `test_writer.py`: an entry per published service; the
      SLA link resolves to a file that exists; an unclassified service renders
      the named absence rather than an empty cell; a consumer-only payload
      produces no `04-catalogue/` at all. `tests/test_render_onboarding.py`:
      the canonical members' trees, through the same code path.
- [ ] **Step 8:** regenerate the three canonical members
      (`scripts/render-onboarding.sh`) and commit the new files. Run
      `scripts/acceptance.sh`.

**Explicitly NOT in this task:** a `validate.py` check requiring
`semantic.pattern` on a published service. It is tempting — A9 lists the
classification among the entry's required fields — and it would be the same
mistake A1 diagnoses. There is no BB pattern register to validate a value
against (P0.6 is a named absence), so a required-field check would enforce
*non-emptiness* and nothing else, and would be satisfied by any string. The
honest mechanism until a register exists is R3: render the absence where the
reader sees it. Revisit when P0.6 closes, not before.

## Task 2: The aggregate

**Files:** `apps/join-api/writer.py` (or a new `apps/join-api/catalogue.py`),
`scripts/render-onboarding.sh`, `apps/join-api/app.py`, `tests/`,
`onboarding/catalogue.yaml`

- [ ] **Step 1:** `render_catalogue(pack_dir) -> str`, deriving the whole file
      from `manifest.yaml` + every `configs/member-*/<key>.yaml`. Not from the
      `onboarding/` tree — deriving a catalogue from the records the catalogue
      generator itself wrote is a check of the code against itself
      (`docs/conventions.md`'s own rule on derived values).
- [ ] **Step 2:** shape per the design §5 sample. Deterministic ordering — sort
      by service id — so the output is byte-identical on regeneration from
      unchanged inputs.
- [ ] **Step 3:** write `onboarding/catalogue.yaml` with a generated-file header
      naming the regeneration command, matching `render_member_config`'s header
      convention.
- [ ] **Step 4:** call it from `scripts/render-onboarding.sh` (canonical
      members), from `writer.apply_real` **after** `render_onboarding_tree`, and
      from `app.py`'s `DELETE /members/{key}` handler after the un-join reaches
      `RETIRED`. Three callers, one function.
- [ ] **Step 5:** a golden or an equivalent regeneration assertion: regenerating
      twice from unchanged inputs produces identical bytes.
- [ ] **Step 6:** one paragraph in `runbook.md` and one in `README.md`: what the
      file is, how to regenerate it, and that `scripts/member.sh remove` does
      **not** regenerate it (the staleness window named in Global Constraints).
- [ ] **Step 7:** `scripts/acceptance.sh`. Commit.

## Task 3: `GET /catalogue`

**Files:** `apps/join-api/app.py`, `apps/join-api/tests/`, `README.md`,
`runbook.md`

- [ ] **Step 1:** `GET /catalogue`, read-only, returning the same derived data
      as JSON. It reads configs, never X-Road, and has no write path.
- [ ] **Step 2:** **applicant token, not operator token** (design §6). One line
      of comment on the dependency, in the plain form the constraints ask for —
      *the operator credential would gate discovery behind the people who
      already know what is published* — and no citation. Not anonymous.
- [ ] **Step 3:** the response carries the publication-is-not-permission
      statement as a top-level field, not only in the docs. A client that
      renders the payload should be unable to omit it by accident.
- [ ] **Step 4:** tests — 200 with the applicant token, 401/403 without, the
      payload matches `onboarding/catalogue.yaml` for the same inputs.
- [ ] **Step 5:** document in `README.md` and `runbook.md`, including the one
      thing a reader will want and not get: **this is `listMethods`, not
      `allowedMethods`.** Commit.

**Deferred sub-task, deliberately:** `GET /catalogue?subject=<subsystem>`, the
`allowedMethods` analogue, filtering to services whose ACL already names that
subject. Cheap to build — the subjects are in the data. Deferred because it
answers *what the register recorded*, which is not *what the bus will let you
call*, and an operator who conflates the two gets a wrong answer at the worst
possible moment. If it is ever built, that caveat ships **on the response**, not
in the documentation.

## Task 4: Acceptance — the end-to-end property, not a self-check

**Files:** `acceptance/join-member.md`, `acceptance/member.md`,
`scripts/acceptance.sh`

- [ ] **Step 1:** for each canonical provider, assert an entry exists per
      published service, and that the SLA link in each entry resolves to a file
      on disk. A dangling link is the specific failure this artefact exists to
      prevent, so it is the specific thing to assert.
- [ ] **Step 2:** the live property, asserted through the API rather than the
      files: after a real join reaches `ACTIVE`, the joined member's service
      appears in `GET /catalogue`; after `DELETE /members/{key}` reaches
      `RETIRED`, it does not. **This is the assertion that means something** —
      comparing a rendered file to the config it was rendered from would test
      the renderer against itself, which `docs/conventions.md` already rules out
      as evidence.
- [ ] **Step 3:** Step 2's second half is also GX.3's catalogue third, proven
      live. Say so in the clause, so the next reader of `path-conformance.yaml`
      finds the evidence rather than re-deriving the argument.
- [ ] **Step 4:** full `scripts/acceptance.sh`. Commit.

## Task 5: The gate register

**Files:** `apps/join-api/writer.py` (`_GATES_TABLE`), regenerated
`onboarding/*/00-gates.md`

- [ ] **Step 1:** G5's row currently ends *"no service-catalogue entry, no
      tier-1 BB pattern register — see `docs/production-delta.md`"*. Rewrite the
      first half to point at `04-catalogue/`; **keep the second half** — P0.6 is
      still a named absence and Task 1 deliberately did not close it.
- [ ] **Step 2:** regenerate all three members. Commit.

## Task 6: Conformance and the production delta

**Files:** `docs/path-conformance.yaml`, `docs/path-conformance.md` (generated),
`docs/production-delta.md`

- [ ] **Step 1:** move the rows the design §8 table lists, and only those:
      G5.6 → `implemented`; S6a.4 → `implemented`; S6.2 → `implemented` with a
      note that this is the register-derived half and the collector remains
      absent. **S7.6 stays `named-absence`** (four of five, relocated — the
      per-service folder is a deliberate non-goal). **S6a.1 stays
      `named-absence`**, note updated: the absence is now rendered per service.
      **GX.3 stays `named-absence`** — revocation and consumer notification are
      untouched.
- [ ] **Step 2:** repoint each moved row's evidence at the code and the test
      that would fail, not at a document. A row that cites only prose is the
      failure mode this file exists to prevent.
- [ ] **Step 3:** regenerate `docs/path-conformance.md` via
      `scripts/render_path_conformance.py`; run `tests/test_path_conformance.py`.
- [ ] **Step 4:** rewrite the head of `docs/production-delta.md`'s catalogue
      section from *"the absence is this section"* to what production must still
      add — the collector, the portal, the federation-wide view, the freshness
      policy, the RIHA-analogue system registry. **Keep the on-the-wire table
      unchanged**: it is the argument for having done this at registration, and
      it stays true after the work lands. Commit.

## Task 7 (conditional): if v0.3 is adopted

Do **not** run this task on the strength of this plan. A9 is a proposal; whether
it is adopted is a decision this pack does not take.

- [ ] **Step 1:** when v0.3 supersedes v0.2, update
      `path-conformance.yaml`'s `meta.path_document`, `path_document_sha256` and
      `path_version`.
- [ ] **Step 2:** add a clause row for G5's **fourth** exit test (the catalogue
      entry), evidenced by Task 1's renderer and Task 4's live assertion.
- [ ] **Step 3:** regenerate, test, commit.

---

## Sequencing

Tasks 1 → 2 → 3 are a genuine chain: the aggregate derives the same fields the
entry does, and the endpoint serves the aggregate. Task 4 needs 1–3. Task 5 is
independent of 2 and 3 and can land any time after 1. Task 6 must be last of the
unconditional tasks — it is the only one that makes a claim, and it must have
everything else to point at. Task 7 waits on a decision.

The re-baselining cost is small and worth stating so nobody defers on a guess:
no topology change, no `hurl/` regeneration, no golden `topology.json` churn. The
new goldens are the onboarding tree and one YAML file. This is a member-heavy
change on a settled topology, which is exactly the position
`onboarding-alignment-design.md` §1.1's rule says to do such work from.

## Exit

- Three canonical members each carry a `04-catalogue/<code>.md` per published
  service, generated, with a resolving SLA link and an explicit tier-1
  classification or an explicit named absence in its place.
- `onboarding/catalogue.yaml` regenerates byte-identically and drops a retired
  member's services without a delete path.
- `GET /catalogue` answers, with the applicant token, and says on its face that
  it is not an authorisation.
- `scripts/acceptance.sh` green, including the join→appears / un-join→gone
  property.
- G5.6, S6.2 and S6a.4 moved, each citing a check that would fail; S6a.1, S7.6
  and GX.3 unmoved, with better notes than they had.
- A member joining this instance tomorrow can answer "what is on this bus, and
  may I call it" from artefacts, in that order, without asking anyone.
