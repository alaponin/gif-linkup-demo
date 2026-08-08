# KP2 — Wave 2: the missing layers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements **Wave 2** of `docs/onboarding-alignment-design.md` §4. **Prerequisite: Wave 1 complete and committed.**

**Goal:** make good on three references the pack already makes and cannot honour —
the Module 4 semantic map, the admission decision, and the conventions — using
the smallest mechanism that works in each case.

> **Simplification pass, 2026-08-05.** The first draft of this plan had five
> tasks, four new config files, a third bearer token and three validator hooks.
> It was cut to three tasks, **one** new config file, no new token and two hooks,
> because three of the five violated rules the pack states about itself. The cuts
> and their reasons are in "What was cut and why" below — read it, because two of
> the deleted items look obviously useful and are not.

**Read this before starting.** These are not new layers. Each is a reference the
pack already makes to something that does not exist:

- `configs/member-plr/2.4.yaml` and `member-pnia/2.5.yaml` both say
  `semantic: # from the Module 4 semantic map`. There is no such map.
- Module 3's scope statement calls the Governance Pack "the organisational-layer
  artefact of the runnable build pack." `configs/` has no governance anything.
- The onboarding path §0.5/§1a require conventions to be *published*. Wave 1
  corrected the identifier check; nothing states the rule.

**Architecture:** one new config file (`semantic-map.yaml`), one new schema enum,
one new required payload field, one new doc page. No new services, no new tokens,
no new endpoints.

**Tech Stack:** unchanged.

## Global Constraints

- **No topology change.** None of this is read by `generate.py`'s topology
  allocation. `hurl/topology.json` and `tests/golden/` must be byte-identical
  before and after — that is what lets Wave 2 precede Wave 3.
- **The pack's own test for a new config key**, from `configs/x-road-bus/2.7.yaml`:
  *can it be set to another value, and does something observably change?* If not,
  it is "configuration and is decoration" and must not be added.
- **The pack's own rule on permissible values**, from `schema.py`'s `BackendAuth`:
  *"the permissible values of a field are a schema concern"* — an enum in
  `schema.py`, **not** a policy key in `2.7.yaml`.
- `--fast` and `--live` stay green.
- Commit after every task.

## What was cut and why

| Cut | Reason |
| --- | --- |
| **`conventions.yaml` (4 keys)** | Only `identifier_charset` had a reader. `member_code_scheme` and `subsystem_code_scheme` are prose no code consults — precisely the `max_services` / `require_semantic_for_provenance` / `backend_auth` mistake `2.7.yaml`'s header records and forbids. **The gap was that nothing *publishes* the conventions, and publishing is a documentation act.** → Task 3, a doc page. |
| **`security_server_host_naming` validation** | `generate.py` derives `ss-<key>` itself. A check that a derived value matches the pattern it was derived from tests the code against itself. |
| **A third bearer token for admission** | Models a Steering Committee with a login. Real committees minute a decision and someone else actuates it — which is what the plan's own `production-delta` row said. The token would have modelled the wrong thing at the cost of secrets, console and test changes. → Task 2, a required `decision_reference` on approve. |
| **`configs/legal/lawful-basis.yaml` + resolution check** | A file of bases with no decree to resolve against is half an artefact; Module 2's decree is not in the pack. → one declared field on the service, in the pack's existing `[confirm: ...]` house style. |
| **`bb_patterns:` register in `2.7.yaml`** | A fifth key in the file whose header says "do not add a fifth", listing five patterns for a pack that uses one. The pack's own precedent (`BackendAuth`) puts permissible values in `schema.py`. → Task 1, an enum. |

Net: four new config files → **one**; three validator hooks → **two**; one new
token → **none**.

## Design decisions

1. **Publish conventions as documentation, enforce only what code reads.** The
   charset stays the constant Wave 1 made it, with the doc page as its stated
   source. One rule, one place, no indirection.
2. **The admission gate is evidence, not authorisation.** `decision_reference`
   on approve is simpler than a token *and* a more honest model: the API
   actuates a decision taken elsewhere and records which one.
3. **The semantic map is checked, not merely published** — otherwise
   `validate.py` check 8 stays a presence check and K-03 is half closed. This is
   the one place in the wave where a new file genuinely earns its place.
4. **The map is entities and fields, not a data dictionary.** Entity → source
   anchor → field list. Resist per-field ISO 11179 element descriptions; the
   anchor names where the real definitions live.

## Out of scope

- Any member, module, filename or topology change (Wave 3).
- Member Requirements and SLA as payload fields (Wave 4).
- Enforcing the RACI at any gate other than admission.
- A service catalogue (design §5).

---

## Task 1: The semantic map, and `pattern` as an enum (K-03, G-04)

**Files:** `configs/semantic/semantic-map.yaml` (new), `apps/join-api/schema.py`, `apps/join-api/validate.py`, `apps/join-api/tests/`, `configs/member-plr/2.4.yaml`, `configs/member-pnia/2.5.yaml`, `README.md`

- [ ] **Step 1:** write `semantic-map.yaml` for the two entities already in use —
      `person` and `enrolment`. Per entity: the sector-standard anchor
      (**OneRoster** / **CEDS**, both named in Module 4's tag line) and the field
      list. The ten fields are already in the two member configs; copy them.
      **Do not write per-field element descriptions** — the anchor is where those
      live (design decision 4).
- [ ] **Step 2:** `validate.py` check 8 goes from presence to **conformance**:
      `semantic.entity` must exist in the map, and every `semantic.fields` entry
      must be declared for that entity. This is what closes K-03.
- [ ] **Step 3:** add `pattern` to `schema.Semantic` as an **enum in
      `schema.py`**, exactly as `BackendAuth` is, with a docstring citing the same
      rule ("the permissible values of a field are a schema concern"). Values:
      `registration`, `digital_registries_lookup`, `consent`, `messaging`,
      `payments`. Optional — making it required would reject existing configs
      until all are classified.
- [ ] **Step 4:** classify the two live exchanges as `digital_registries_lookup`
      — the onboarding path calls that contract shape "the one worth
      standardising first". Update the two member configs' comments to point at
      the real map instead of an absent one.
- [ ] **Step 5:** one paragraph in `README.md` naming the pack as an instance of
      the GovStack **Information Mediation** building block, cross-referencing
      subtopic 4.7. `grep -i govstack` currently returns 0 across the pack.
- [ ] **Step 6:** tests — unknown entity, undeclared field, bad pattern value,
      and the two existing configs passing unchanged. `--fast` green;
      `topology.json` unchanged. Commit.

---

## Task 2: The admission decision (K-02, G-02)

**Files:** `apps/join-api/schema.py`, `apps/join-api/app.py`, `apps/join-api/tests/test_app_approve.py`, `apps/console/`, `docs/production-delta.md`

`configs/x-road-bus/2.7.yaml`'s `approval: explicit` — "a human operator
approves; never automatic" — is the arrangement the onboarding path §5 gap 2
calls a contradiction of the RACI: the Operating Authority admits members under
its own authority where Ref Model §5.3 makes the Steering Committee accountable.

**The fix is a recorded decision, not a second login.** A Steering Committee does
not hold an API token; it minutes a decision that someone then actuates.

- [ ] **Step 1:** `POST /requests/{id}/approve` takes a required
      `decision_reference` — the minute identifier and date of the admission
      decision. Free text, non-empty, in the pack's `[confirm: ...]` register
      where a demo cannot supply a real one.
- [ ] **Step 2:** the reference is persisted on the request record and surfaced
      in `GET /requests/{id}`. An approval with no recorded decision is now
      impossible, which is the whole of the gate.
- [ ] **Step 3:** the rejection message when it is missing names *why* — the
      Steering Committee is accountable for admission (Ref Model §5.3) and the
      operator actuates that decision. The demo teaches the gate through the
      error, at zero infrastructure cost.
- [ ] **Step 4:** console join tab gains the field. It is `--full`'s smoke pass;
      a required field with no input is a demo that stalls in front of an
      audience.
- [ ] **Step 5:** one `production-delta.md` row: in production this is a minuted
      committee decision and the endpoint actuates it; the demo records the
      reference and verifies nothing about it.
- [ ] **Step 6:** tests — approve without a reference is rejected; with one,
      succeeds and persists it. `--fast` green. Commit.

---

## Task 3: Publish the conventions and the lawful basis (§0.5, §1a, K-02)

**Files:** `docs/conventions.md` (new), `apps/join-api/schema.py`, `runbook.md`, `README.md`

The onboarding path §0.5 makes conventions an ecosystem prerequisite decided once
before member #1 — *"a convention retrofitted after fifty members is not
retrofitted at all."* The gap is that nothing states them. Stating them is a
document.

- [ ] **Step 1:** write `docs/conventions.md` with the four §1a conventions:
      identifier character set (the pattern Wave 1 put in `validate.py`, cited as
      the authority, with XRDDEV-1960), member code scheme, subsystem code scheme
      (one per system, not per service — UC-MEMBER), Security Server host naming
      (`ss-<key>`, and what a production convention encodes — owner, role,
      environment, sequence).
- [ ] **Step 2:** `validate.py`'s charset constant gets a comment pointing at
      this page as its stated source. **No config indirection** — one rule, one
      place.
- [ ] **Step 3:** link it from `README.md` and `runbook.md` where a joining
      member's identifiers are first discussed.
- [ ] **Step 4 — lawful basis.** Add an optional `lawful_basis` string to the
      service block in `schema.py`: the decree article the exchange relies on, or
      `consent`. Free text with `[confirm: cite the decree article]` where the
      demo has no real one. **No separate config file and no resolution check** —
      Module 2's decree is not in the pack, so there is nothing to resolve
      against, and a resolution check against a file we also wrote proves
      nothing.
- [ ] **Step 5:** `--fast` green; `topology.json` unchanged. Commit.

---

## Sequencing

Tasks are independent. Tasks 1 and 3 both touch `schema.py` and will conflict
textually if run in parallel — run them sequentially or expect a merge.

**Exit:** one new config file, one new doc page, one new enum, one new required
field, two validator hooks that fail on bad data, and `hurl/topology.json` and
`tests/golden/` byte-identical to their pre-Wave-1 state. **If either moved,
Wave 2 has done something that belongs in Wave 3.**
