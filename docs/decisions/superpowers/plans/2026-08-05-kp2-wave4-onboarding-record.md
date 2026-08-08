# KP2 — Wave 4: the onboarding record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements **Wave 4** of `docs/decisions/onboarding-alignment-design.md` §4, trimmed per decision **D3**. **Prerequisites: Wave 3 complete, committed, `--full` green, and the new topology measured.**

**Goal:** the two artefacts Topic 5 teaches and the pack does not carry — the
Member Requirements checklist (5.2) and the SLA (5.3) — plus a per-member
onboarding record that makes gate exits visible.

> **Simplification pass, 2026-08-05.** Three cuts, all in the same direction —
> fewer files, no hand-maintenance:
>
> - **Four stub files per member → one `00-gates.md` table.** The first draft
>   generated seven files per member, four of which were stubs saying the same
>   thing in four places. A gates table shows every gate and its status in one
>   readable artefact, and preserves P2's named absence better than four
>   near-empty files do.
> - **Hand-authored canonical records → generated.** The draft hand-wrote records
>   for the three canonical members and then added a test asserting they matched
>   the generator's output. If they must match, generate them; the test was
>   guarding a duplication that should not exist.
> - **The asserted/derived distinction on requirements → all asserted.** Modelling
>   which of 5.2's six items the API can infer was fiddly for no teaching gain.
>   Six fields, all stated.
>
> Files per member: 7 → **4**. Hand-maintained files: 3 → **0**.

**Read this before starting.** This wave is deliberately **smaller than the
onboarding path's §7**, because of D3 (no curriculum change). The path specifies
ten files per member; this builds **three**, covering the gates KP2 actually
teaches:

| Gate | Video | In this wave |
| --- | --- | --- |
| Member Requirements | **5.2** | ✅ |
| SLA | **5.3** | ✅ |
| Registration | **5.4** | ✅ |
| Application, admission, certificates, catalogue entry, go-live, retirement | — | ❌ named absences in `production-delta.md` |

Building the other seven would be the pack teaching gates no video covers — P6's
failure mode. If a later decision adds a join subtopic to Topic 5, revisit.

**The two field lists are already specified — by the video scripts.** No design
work is needed:

- **5.2's six requirements:** a security server; a registered identity on the
  bus; the standards portfolio adopted; data cleaned and conformed to the
  schema; a lawful basis for its exchanges; a named technical contact.
- **5.3's five SLA terms:** availability; response time; support hours; incident
  response; change notice.

**Architecture:** two new blocks on `JoinPayload`, rendered by `writer.py` into a
new top-level `onboarding/<key>/` tree.

**Tech Stack:** unchanged.

## Global Constraints

- **No topology change.** `tests/golden/` must be byte-identical before and
  after. This wave adds records about members, not members.
- **`onboarding/<key>/` is a separate top-level tree** (D4), keyed by the same
  lowercase key as `configs/member-<key>/`. It must not live inside
  `configs/member-<key>/` — a `contract.openapi.yaml` there would break
  `discover_members()`'s exactly-one-YAML rule.
- **Generated, never hand-maintained.** Stubs are three lines (P2). A record a
  human has to keep in sync is a record that drifts.
- Commit after every task.

## Design decisions

1. **Requirements are booleans plus evidence, not prose.** Five of 5.2's six map
   to facts the API can already see or the payload can assert; the sixth
   (lawful basis) reuses the declared `lawful_basis` field Wave 2 Task 3 added
   to the service block — one field, no resolution against a decree the pack
   does not contain.
2. **An SLA is per service, not per member.** 5.3: "reuse the same template for
   every service on the bus." A consumer-only member therefore has none — see
   Task 3 Step 3, which surfaces that as an open question rather than inventing
   an answer.
3. **An empty gate file is the point.** A missing `05-registration.md` means the
   gate has not been passed, "whatever the calendar says." Do not backfill stubs
   with plausible content.
4. **Canonical members get records too.** Otherwise the tree only ever has one
   entry (a joined member) and the demo never shows a populated onboarding
   folder.

## Out of scope

- The other seven §7 gate files (D3).
- `catalogue-entry.md` and any service catalogue — the SLA lands as `sla.md` on
  the member record instead (design §8.4).
- Monitoring add-ons (Wave 5).
- Any change to the approve/admission flow — Wave 2 did that.

---

## Task 1: `member_requirements` and `sla` on the payload (K-01)

**Files:** `apps/join-api/schema.py`, `apps/join-api/validate.py`, `apps/join-api/tests/`, `configs/x-road-bus/join-policy.yaml`

- [ ] **Step 1:** add a `MemberRequirements` block to `schema.py` — 5.2's six
      items, **all as stated fields**, `extra="forbid"` like every other block.
      Do not model which the API could infer (simplification pass): the teaching
      value is that the applicant answers the checklist, and a field that is
      sometimes asked and sometimes derived is harder to read than six that are
      always asked.
- [ ] **Step 2:** add an `SLA` block — 5.3's five terms plus a signatory. On
      `Service`, not on `JoinPayload` (design decision 2).
- [ ] **Step 3:** the requirements block's lawful-basis item **reuses the
      `lawful_basis` field Wave 2 Task 3 added to the service**, rather than
      declaring it a second time. If a provider's services already state a basis,
      the requirement is satisfied by them; a consumer-only member states one on
      the requirements block. One field, one place.
- [ ] **Step 4:** required for a **provider**, optional for a consumer-only
      member. A join publishing a service with no SLA is a rejection naming the
      check, in the style of the existing eleven.
- [ ] **Step 5:** update `prompts/join-member.md` and `prompts/member.md` so the
      by-hand path produces the same blocks. These prompts currently cite Module
      5.2/5.3 as preconditions held in the Interop Toolkit; they can now cite the
      fields the payload actually carries.
- [ ] **Step 6:** tests — a provider without an SLA, a consumer without one
      (accepted), an unresolvable lawful basis, and the existing PTSB fixture
      updated. `--fast` green. Commit.

---

## Task 2: Render `onboarding/<key>/` (G-07)

**Files:** `apps/join-api/writer.py`, `apps/join-api/tests/test_writer.py`, `scripts/member.sh`, `.gitignore`

- [ ] **Step 1:** `writer.apply_real()` additionally renders `onboarding/<key>/`
      — **four files, not seven**: `00-gates.md` (the gates table, Step 2),
      `02-requirements.md` (5.2's six items), `03-sla/<service-code>.md` (5.3's
      five terms per published service), `05-registration.md` (subsystem,
      Security Server, ACL subjects, join request id).
- [ ] **Step 2 — one gates table, not four stub files.** `00-gates.md` lists
      every gate with its status: the three this pack implements (with a link to
      the file that proves each) and the four it does not, each naming what the
      gate asks, who is accountable, and "not implemented in this demo — see
      `docs/production-delta.md`". This is still P2's named absence — it is more
      legible in one table than in four near-identical files, and it is the
      artefact a learner actually reads.
- [ ] **Step 3:** `scripts/member.sh remove` deletes the member's onboarding
      record alongside its config — with the **inverse** of Wave 1 Task 3's
      retention note in mind: the record is demo evidence, not a message log, so
      deleting it is correct. Say so in the code comment so the two do not get
      confused.
- [ ] **Step 4:** the live-but-uncommitted window that `production-delta.md`
      already documents now covers a third tree. Confirm `apply_real()`'s
      refusal-when-dirty check includes `onboarding/`.
- [ ] **Step 5:** tests over the rendered tree; `--fast` green. Commit.

---

## Task 3: Records for the canonical members

**Files:** `onboarding/`, `configs/member-*/`, `docs/production-delta.md`

- [ ] **Step 1 — generate them, do not hand-author them.** Add the
      `member_requirements` and per-service `sla` blocks to the three canonical
      member configs, then have `scripts/member.sh` (or a small
      `scripts/render-onboarding.sh`) render `onboarding/{pnia,plr,pnea}/`
      through the **same** `writer.py` code path a join uses. No hand-written
      records, and therefore no consistency test guarding a duplication that
      should not exist.
- [ ] **Step 2:** SLA numbers should be defensible for a demo and marked as
      illustrative — 5.3's own warning is that "a target the provider cannot meet
      is a target the provider will quietly ignore."
- [ ] **Step 3 — leave the open question visible.** PNEA is consumer-only, so it
      has no `sla/` directory. Put a one-line note in its record: TK-IO-09 is
      written for providers, and a consumer's obligations (rate, purpose
      limitation, log cooperation) have no template — the onboarding path's own
      §8 open question 5. **Do not invent a consumer SLA.** A pack that makes a
      framework's open question concrete is doing its job.
- [ ] **Step 4:** one `production-delta.md` row listing the four unbuilt gates
      as named absences, with the reason (D3) and what would change the decision.
- [ ] **Step 5:** `--fast` green; `--live` green; `tests/golden/` unchanged.
      Commit.

---

## Sequencing

Task 1 → Task 2 → Task 3, strictly. Task 2 renders what Task 1 defines; Task 3
hand-authors what Task 2 generates.

**Exit:** three members each carrying a requirements record and a registration
record, two of them carrying per-service SLAs, four visible unpassed gates, and
`tests/golden/` byte-identical to its post-Wave-3 state.
