# KP2 — Wave 6: path-conformance closeout

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Prerequisites: the 2026-08-08 review landed — `docs/path-conformance.yaml`, `scripts/render_path_conformance.py`, `tests/test_path_conformance.py` present and `--fast` green.**

> **Simplicity and security pass, 2026-08-08.** Applied after review. Four
> simplifications — the heading parser is gone (Task 1), the second spec fetch
> is gone (Task 2), the shell→venv→writer hop is gone (Task 4), and the
> `PLAN.md` split is out of scope (Task 7). Three security constraints added as
> design decisions 10–12: field **names** only, never values; no second fetch of
> an applicant-controlled URL from the post-approval path; every new message
> into a persisted record goes through `job.scrub()`. A **comment guardrail** was
> added to the global constraints: comments carry the reason, not a citation of
> where it was decided.

**Goal:** close the remaining points from the 2026-08-08 external review — the
one open defect, the two cheapest missing gate artefacts, the lawful-basis gap
at G0, one duplicated configuration key, and the documentation reclassification
the review's root cause argues for.

**Context in one paragraph.** The review found the pack's G4–G5 technical
sequence faithful to the onboarding path and its organisational gates largely
absent-by-design, which is correct. What it also found was three findings
recorded as closed by files that had never been created. That is now
structurally prevented: `docs/path-conformance.yaml` holds one row per path
clause, and `tests/test_path_conformance.py` fails if any cited evidence path or
symbol stops existing. This wave finishes the substantive items behind those
rows.

**The move that made Task 1 possible.** The onboarding path document now lives
at `docs/GEATDM-Interop-Member-Onboarding-Path-v0.2.md` — inside the pack, not
in a sibling framework folder. The conformance matrix can therefore be checked
against the path *itself*, not just against the pack.

---

## Read this before starting

**What this wave is not.** Five items are deliberately out of scope, and the
matrix already records the first four as named absences with their reasons:

| Not in this wave | Status row | Why |
| --- | --- | --- |
| Service catalogue | `S6.2`, `G5.6` | Needs a curriculum or framework driver (D3). The SLA landing on the member record instead is the accepted consequence. |
| Monitoring collection layer | `G4.8`, `S6.3`, `S6.4` | The add-ons are the G4 item; the collector is a roadmap item. Deliberately separate decisions — see the v0.3 amendment note A7. |
| A second (development-track) environment | `P1.1`, `P1.2` | Structurally expensive and teaches nothing the single track does not. The pack self-labels as the development track. |
| Enforcing G2's role-compatibility exit test | `G2.3` | Has no mechanical form: "authoritative publisher of personal data" is a property of the member's mandate, not its payload. Amendment A5 proposes the path say so. |
| **Splitting `PLAN.md`** | — | A judgement-heavy content edit. It was inside Task 7 and has been removed: a mechanical `git mv` task must stay reviewable as a move. Do it separately, whenever. |

**The one open defect.** `G5.9` is the only row in the matrix carrying
`OPEN DEFECT` rather than a named absence, and it is Task 2. Everything else
here is either an artefact the path names and the pack can cheaply produce, or
housekeeping.

---

## Global Constraints

- **No topology change.** `tests/golden/` must be byte-identical before and
  after. This wave adds checks and records, not members.
- **Every task updates `docs/path-conformance.yaml` and re-renders.** A task
  that changes what the pack does and leaves the matrix saying the old thing
  has reintroduced the exact failure the matrix exists to prevent. Re-render
  with `python3 scripts/render_path_conformance.py`; `--fast` fails if you
  forget.
- **Generated, never hand-maintained.** Applies to every new record file, same
  as Wave 4.
- **No new runtime dependency.** Task 2's check is set arithmetic over an
  already-parsed spec, not JSON Schema validation. If it starts wanting
  `jsonschema`, it has grown past its clause.
- **No new outbound fetch.** This wave adds no HTTP call that the pack does not
  already make. See design decision 11.
- **Comments state the reason, not its provenance** — see below.
- Commit after every task.

### Comment guardrail (applies to every task in this wave)

A comment explains why the code is the way it is, **in the code's own terms**. It
does not cite where the decision was recorded. Provenance belongs in git history
and in `docs/decisions/`; a reader of `validate.py` should not have to open a
plan to understand a check.

**Do not write, in any code file:**

- a path to `docs/superpowers/**`, or any plan or wave name;
- `spec S<n>`, `design decision <n>`, `decision <n>`, `join-<x> plan`, `P<n>`,
  or any other pointer to a numbered item in a planning document — including
  the numbered decisions in *this* plan.

**Do write:** the reason itself; a path to a **Reference** document
(`docs/production-delta.md`, `docs/conventions.md`) when the reader genuinely
needs it; an upstream ticket or version (`XRDDEV-1960`, `7.7.0`); a sibling
source file the reader must open anyway.

**The test:** if the cited document were deleted, would the comment still be
useful? If not, the reason is in the wrong place — inline it. **And:** if a
comment is longer than the code it explains, the explanation belongs in a
Reference document and the comment should be one sentence plus a pointer.

**Why this is a constraint and not a preference.** There are already ~230
provenance citations across `apps/`, `hurl/`, `scripts/` and `tests/` — 102
`spec S<n>`, 49 `decision <n>`, 45 `join-<x> plan`, and six full plan paths.
One of the six, `docs/superpowers/plans/2026-08-01-kp2-reproducible-builds.md`
in `scripts/lib-stack.sh`, **cites a plan that does not exist**. That is the same
failure this whole wave exists to close, one layer down: a citation nothing
checks, outliving the thing it cites. Task 7 moves the plans directory, which
would break the other five.

**Scope:** new and modified code only. **Do not sweep the existing ~230** — that
is a separate mechanical change, and folding it into a feature wave makes every
diff in this plan unreviewable. Task 7 Step 4 fixes only the six that its own
move breaks.

---

## Design decisions

1. **The contract check is set equality, not schema validation.** The path's
   G5 exit test is *"the response carries exactly the fields the contract
   declares"* — a field-name comparison. Validating types, formats and enums
   would be a larger and different promise, and the failure it would catch
   (a `date` that is not a date) is not the failure the clause is about. The
   failure the clause is about is a field that should not be on the wire.

2. **The contract is parsed once, at validation, from the spec already
   fetched.** `_check_backend_reachability` already ends with
   `ctx.fetched_specs[svc.code] = spec_doc` — the parsed document is in hand.
   Add `contract_fields(spec)` in `validate.py`, compute the declared and
   required sets there, and **persist them on the request record**. `job.py`
   neither fetches nor parses: it reads two sets off the record it is already
   holding. This is simpler than re-fetching *and* more correct — the gate then
   verifies against the contract the member was **admitted on**, not against
   whatever is at that URL after approval.

3. **`apps/mock-registry/app.py`'s copy stays, with a cross-reference.** It
   derives `DECLARED_FIELDS` from the same expression, and it must — it is a
   separate container that cannot import `join-api`. That duplication is *why*
   `G5.9` went unnoticed (the provider and the contract could not disagree), so
   leave a comment in both places naming the other. A shared library here would
   hide the very coupling the check exists to break.

4. **A field mismatch sets `verified: false`; it does not fail the job.**
   Consistent with `R1_RETRY_BUDGET` exhaustion, which already produces
   `ACTIVE, verified: false` rather than `FAILED`. The member is joined; its
   service does not conform. Those are different facts and the record should
   carry both, with the diff named.

5. **`01-admission.md` is written only for a real join.** The three canonical
   members never passed an admission — writing them an admission record would
   be fiction, and Wave 4's rule is that nothing backfills a plausible-looking
   stub. `render_onboarding_tree()` already takes `request_id: str | None`;
   `None` means "rendered from config, not from a decision". Their gates table
   keeps the G1 named absence, which is the truthful state.

6. **The gate register names every gate the path defines, including the ones
   the pack passes.** `00-gates.md` currently names G0, G1, G3 and G6 as
   absences and is silent on G2, G4 and GX. Silence reads as "not applicable",
   and GX — the pack's most completely implemented gate — being absent from its
   own register is the register failing at its one job.

7. **A retired member keeps its onboarding folder, and gains one file.**
   (Resolves OD-2.) `member.sh remove` deletes `onboarding/<key>/` today,
   arguing that the record is evidence the member passed its gates and a removed
   member has nothing left to be evidence of. That is half the story: the record
   is also evidence of **what the operator revoked**, which is the half GX's
   exit test is about. Keep the folder, stop the delete, add
   `99-retirement.md`.

8. **The retirement record is written by the API, not by the shell, and is
   static text plus three facts.** Two simplifications in one. *Who writes it:*
   `DELETE /members/{key}` already imports `writer` and already performs the
   federation-side retirement, so it writes the record; `member.sh remove`
   simply stops deleting the folder. `member.sh remove` is **config removal, not
   retirement** — a member removed only that way has had no federation-side
   retirement, so no retirement record is the truthful outcome. *What it says:*
   retired-at, the request id, one fixed sentence naming the standard reversal,
   and one line pointing at the message-log archive as a **separate** retention
   question this file does not answer. Do not enumerate the reversal
   dynamically — `steps.REVERSAL_ORDER` already states it once.

9. **A published service must state its own lawful basis.** (Resolves OD-3.)
   Not new design — finishing an intent the configs already record. Both
   provider configs carry *"lawful_basis intentionally absent: a provider's own
   service states it (design decision 1) … not yet populated for the canonical
   services."* Wave 6 populates them and makes the validator require it, which
   turns the path's G0 exit test from untestable into testable for exactly the
   applicant class it was written for.

10. **Field names, never field values.** *(Security.)* The contract diff
    reports set differences of **field names** and nothing else. It must never
    log, persist or return a response body or any field value. The undeclared
    set is by construction exactly the fields the contract withheld — for PNIA,
    `mother_name`, `birth_registration_no`, `residence_address`. A
    purpose-limitation check that writes withheld personal data into
    `out/join/*.json` is worse than no check. Synthetic data today; KP4 puts
    real applications behind these contracts.

11. **No second fetch of an applicant-controlled URL.** *(Security.)*
    `Service.spec_url` is a plain `str` with no scheme or host restriction, and
    it is fetched from inside the `join-api` container — which holds
    `JOB_SECRETS` (admin user, admin password, token PIN) and can reach every
    admin API on `:4000`. That surface is pre-existing and stays in scope for
    `production-delta.md`; what this wave must not do is **add a second fetch of
    the same URL from the post-approval, unattended job path**. Design decision
    2 is how that is avoided.

12. **Every new message into a persisted record goes through `job.scrub()`.**
    *(Security.)* Every existing error path into `out/join/*.json` is scrubbed
    against `JOB_SECRETS`. The contract-mismatch message is a new path into the
    same record and gets the same treatment.

---

## Open decisions — one left

**OD-2 and OD-3 are resolved** (design decisions 7–9). One remains, and it split
into two independent halves once the scope question was asked properly.

### OD-1 — the `approval` key. Two decisions, not one.

**The finding that splits it.** `configs/x-road-bus/federation-core.yaml`
already carries `policy.management_request_approval: explicit`, and
`hurl/generate.py`'s `check_policy()` genuinely enforces it — it refuses to
generate against any other value, and refuses outright if a `policy.auto_approve`
key appears. So the ecosystem-level key exists and works. What
`configs/x-road-bus/join-policy.yaml` adds is `approval: explicit`: **a second
copy of the same fact, in a file whose scope cannot apply it**, whitelisted by
`JOIN_POLICY_KEYS` and read by nothing.

- **OD-1a — delete the duplicate. Recommended; no judgement needed.** Approval
  mode is a property of how the Central Server was deployed, not of a join
  request, so `join-policy.yaml` is the wrong file for it regardless of which
  value it holds. Task 6.

- **OD-1b — should `federation-core.yaml`'s key accept `automatic`?** Deferred
  to its own plan: `docs/superpowers/plans/2026-08-08-kp2-wave7-approval-policy-branch.md`,
  which carries the mechanism, the cost, and a start gate. **Recommendation:
  not in this wave**, and not at all without a driver.

---

## Task 1 — Check the matrix against the path document itself

**Now possible because the path lives in-tree.** Until this task, the matrix
proves every claim points at something real; it does not prove the matrix covers
the path.

> **Simplified.** An earlier draft parsed headings out of the path document and
> maintained an `uncovered_sections` list with reasons. That is a parser plus
> its own test suite for a document that changes once a version, and a parser
> that guesses wrong fails *open* — the one thing this file must not do. The
> two assertions below cover the same risk (a new path version lands and the
> matrix is silently not re-read) in about fifteen lines, and fail closed.

- [ ] **Step 1:** add `test_the_path_document_exists()` to
      `tests/test_path_conformance.py`, checking `meta.path_document`. It is the
      citation the whole file rests on and the only one currently unchecked.
- [ ] **Step 2:** add `test_every_section_title_has_at_least_one_clause()`. The
      renderer's `SECTION_TITLES` already fails on a clause in an *unknown*
      section; this asserts the reverse direction — a section title with no
      clauses. **No new data structure:** `SECTION_TITLES` is already the one
      list of sections, so the test is a set comparison against it.
- [ ] **Step 3:** add `meta.path_document_sha256` to
      `docs/path-conformance.yaml` and a test that recomputes it. The failure
      message is the point, so write it deliberately: *the onboarding path
      changed — re-read it against this matrix, then update the hash*. This is
      what makes a v0.3 landing an action rather than a silence.
- [ ] **Step 4:** fix whatever Steps 2–3 surface.
- [ ] **Step 5:** re-render; `--fast` green. Commit.

---

## Task 2 — The response-vs-contract check (`G5.9`, the open defect)

**The failure this closes.** Nothing compares a live response against the
registered contract. The property currently holds *by construction* — the mock
provider derives its own output from the same OpenAPI file — and the nearest
assertion (`scripts/assert_record.py`) compares returned values to the seed row
field by field, which catches neither direction: a response that drops a
required field produces no mismatch, and a response that adds a field the CSV
carries and the contract withholds matches the seed and passes. The second is
the serious one — purpose limitation failing silently, on the exchange whose
whole point is purpose limitation — and it becomes live the moment a real
application replaces the mock.

**Read design decisions 10, 11 and 12 before writing any code in this task.**
All three are about this check specifically.

- [ ] **Step 1:** add `contract_fields(spec: dict) -> tuple[frozenset[str],
      frozenset[str]]` to `apps/join-api/validate.py`, returning the 200
      response schema's declared properties and its `required` list. Call it
      from `_check_backend_reachability`, which already has `spec_doc` in hand.
      Unit-test it against `apps/join-api/tests/fixtures/specs/`, including a
      spec with no `required` block.
- [ ] **Step 2:** persist the two sets per service onto the request record at
      validation time (design decision 2). **No re-fetch anywhere** (design
      decision 11) — if a diff of this task adds an HTTP call, it is wrong.
- [ ] **Step 3:** add the cross-reference comments in both
      `apps/join-api/validate.py` and `apps/mock-registry/app.py` (design
      decision 3), each naming the other and saying why the duplication is
      deliberate.
- [ ] **Step 4:** extend `_default_r1_call` in `apps/join-api/job.py` to take
      the two sets and, on a non-fault response, compare:
      `returned - declared` is **undeclared** (the serious case) and
      `required - returned` is **missing**. Report **both sets of field names,
      and nothing else** (design decision 10). Note the DI seam:
      `run(..., r1_call=...)` means the signature change touches
      `apps/join-api/tests/test_job.py`'s fakes — update them rather than
      defaulting the new parameters to `None` and letting the check silently not
      run.
- [ ] **Step 5:** apply design decision 4 — a mismatch sets
      `record["verified"] = False` with the named diff, state still becomes
      `ACTIVE` — and design decision 12: the message goes through
      `job.scrub(..., JOB_SECRETS)` like every other message written into that
      record. Do not conflate a mismatch with an X-Road fault: a fault means no
      route, a mismatch means a route to something that does not match its
      contract.
- [ ] **Step 6:** add the assertion to `scripts/acceptance.sh` — a `check_266`
      for the canonical once-only exchange (both legs) and a `check_r1_fields`
      beside `check_r1_denied` in the per-service 2.7 loop. **Read the declared
      fields from `apps/specs/*.openapi.yaml` on disk**, not over HTTP: the
      canonical specs are in-repo, so the shell path needs no fetch either.
      Update `acceptance/once-only-exchange.md` and `acceptance/join-member.md`
      given/when/then.
- [ ] **Step 7 — prove it fails.** The check is worthless unless the negative
      case is demonstrated. Temporarily add `mother_name` to
      `apps/specs/pnia-identity.openapi.yaml`'s declared properties (so the mock
      starts returning it), confirm `check_266` fails naming
      `undeclared: [mother_name]` **and that no value appears in the output**,
      then revert. Record the observation in `docs/production-delta.md`.
      **This step is the point of the task** — a conformance check nobody has
      seen fail is the same category of artefact as the claim that started this
      review.
- [ ] **Step 8:** add a `docs/production-delta.md` row for the **pre-existing**
      `spec_url` fetch surface (design decision 11): a demo fetches whatever URL
      an applicant submits, from a container holding the federation's admin
      credentials; a production join API restricts scheme and host and fetches
      from somewhere that cannot reach the admin plane. Not this wave's defect,
      but this wave is where it was noticed and the note is nearly free.
- [ ] **Step 9:** flip `G5.9` from `named-absence` (`OPEN DEFECT`) to
      `implemented`, citing the new symbols. Re-render. `--fast`, then `--live`.
      Commit.

---

## Task 3 — `01-admission.md` (`G1.2`, `S7.2`)

**Why this one and not the other five §7 files.** The API already holds
everything this record needs — request id, decision reference, approving
principal, timestamp — and writes none of it. Every other missing gate file
would require inventing its content. This is the v0.3 amendment note's A1 made
concrete: the enforceable part of G1 is not the committee, it is the reference
the join cannot proceed without, and a reference that is never written down is
not a reference.

- [ ] **Step 1:** add `render_admission_record()` to `apps/join-api/writer.py`
      — request id, decision reference, approved-at, the approving role
      (`operator`), and a one-line statement that the admission decision itself
      is taken outside this system and this file records only the coupling.
- [ ] **Step 2 — sanitise free text at render time.** `decision_reference` is
      operator-supplied free text going into a markdown table. Strip newlines
      and escape pipes, exactly as `scripts/render_path_conformance.py`'s
      `_cell()` already does. One helper in `writer.py`, reused by Task 5's
      `lawful_basis` rendering. Without it one pasted value breaks the record's
      structure.
- [ ] **Step 3:** write the record from `render_onboarding_tree()` **only when
      `request_id` is not None** (design decision 5). Confirm the three
      canonical records are byte-identical after this task —
      `scripts/render-onboarding.sh` must produce no diff.
- [ ] **Step 4 — ordering bug to avoid.** In `apps/join-api/app.py`,
      `apply_real()` is called *before* `record["approved_at"]` and
      `record["decision_reference"]` are assigned. Pass both into `apply_real`
      (and on to `render_onboarding_tree`) rather than reading them off the
      record afterwards, or the record will be written with them empty.
- [ ] **Step 5:** update `render_gates_table()`'s G1 row: no longer a flat named
      absence, but *decided outside this system; reference recorded in
      `01-admission.md`* — for a joined member. A canonical member's row is
      unchanged.
- [ ] **Step 6:** tests in `apps/join-api/tests/test_writer.py` (record
      rendered, fields present, a pipe-and-newline reference survives intact)
      and `test_app_approve.py` (a real approve produces the file). Update
      `G1.2` and `S7.2`; re-render. `--fast`, `--live`. Commit.

---

## Task 4 — Complete the gate register, and keep the retirement record (`S7.3`, `S7.5`, `S7.8`, `S7.9`, `G2.2`, `G0.3`, `GX.4`)

Four rows, one small renderer, one line, one deletion removed. Design decisions
6–8 apply.

> **Simplified.** An earlier draft had `member.sh remove` shell into the dev
> venv to import `writer.py` to emit four lines of markdown, plus a step to
> check whether the API's retire path also reached it. Design decision 8 makes
> the API the single writer, which removes the invocation path and the check
> together.

- [ ] **Step 1:** add a **G2** row to `_GATES_TABLE` pointing at
      `05-registration.md`'s hosting row. G2 is *passed*, not absent — the
      register currently implies otherwise by omission.
- [ ] **Step 2:** add a **G4** row. Platform conformance is genuinely partial:
      the add-ons are confirmed per server by `acceptance/member.md`, and the
      path's third exit test (monitoring arriving centrally) is unmet. Say both
      — link the acceptance file, name the absent collector.
- [ ] **Step 3:** add `render_retirement_record(key, retired_at, request_id)` to
      `writer.py` (design decision 8). Four lines of content: retired-at; the
      request id; one fixed sentence — *the standard reversal was applied:
      service ACLs revoked, service descriptions deleted, client unregistered
      and deleted, signing key deleted, member removed from the Central Server
      (`hurl/steps.py`'s `REVERSAL_ORDER`)*; and one line stating that
      message-log retention is a **separate** question governed by the archive
      volume, not by this file.
- [ ] **Step 4:** call it from `apps/join-api/app.py`'s `DELETE /members/{key}`
      handler once `job.unjoin` has succeeded. That module already imports
      `writer` and holds `PACK_DIR`; nothing new is wired.
- [ ] **Step 5:** in `scripts/member.sh`'s `cmd_remove`, **delete** the
      `rm -rf "$PACK_DIR/onboarding/$key"` line and nothing else — no new
      invocation, no venv dependency. Rewrite the comment above it to design
      decision 8's reasoning: the record is also evidence of what the *operator*
      revoked, and `member.sh remove` is config removal rather than retirement,
      so it neither writes nor destroys the record.
- [ ] **Step 6:** add a **GX** row to `_GATES_TABLE` pointing at
      `99-retirement.md`, with the honest status: written at exit by the API;
      the absence half of the exit test asserted by `acceptance/join-member.md`;
      the message-log retention half unmet by demo teardown.
- [ ] **Step 7 — the hosted delegation (`G2.2`).** Add one line to
      `render_registration_record()`: for a hosted member, state that its
      signing key is held on the host's token and name the host. This is the
      path's own G2 warning — *"a delegation with no counterpart in the
      obligation set"* — and the pack implements the delegation faithfully while
      recording it nowhere a member would read.
- [ ] **Step 8 — the DPO (`G0.3`).** Currently a *silent* absence: no field, and
      no mention anywhere a reader meets it. Add it to the G0 row's absence text
      (`application + signed membership agreement; Technical Focal Point and,
      where personal data flows, a DPO`). A named absence teaches; a silent one
      does not, and this is the pack's own P2.
- [ ] **Step 9:** re-render the three canonical records
      (`scripts/render-onboarding.sh`) and commit the diff — expected and wanted
      here, unlike Task 3's. Update `S7.3`, `S7.5`, `S7.8`, `S7.9`, `G2.2`,
      `G0.3`, `GX.4`; re-render. `--fast`, `--live` (Step 4 changes a real
      retirement). Commit.

---

## Task 5 — Require a lawful basis of providers (`G0.4`)

**Design decision 9.** This finishes an intent the configs already state and
never delivered. The consequence today is that `_check_lawful_basis` returns
early whenever a payload declares services, so the requirement lands only on
consumer-only members — the inverse of the applicant class the path's G0 exit
test exists to catch, and neither authoritative provider states a basis for the
data it publishes.

- [ ] **Step 1:** in `apps/join-api/validate.py`, remove `_check_lawful_basis`'s
      early return. One check, two shapes: **every published service must carry
      `Service.lawful_basis`**; a member with no services must carry
      `member_requirements.lawful_basis`. Name the missing service in the
      rejection message, not just the fact.
- [ ] **Step 2:** populate `lawful_basis` on the published service in
      `configs/member-plr/plr.yaml` and `configs/member-pnia/pnia.yaml`,
      replacing the "intentionally absent" comment with the basis itself and a
      note that the wording is illustrative — the same honesty marker the SLA
      values carry. PNIA's is the interesting one: a purpose-limited person
      lookup for credential issuance is exactly the case the decree governs.
- [ ] **Step 3:** reuse Task 3 Step 2's sanitiser wherever `lawful_basis` is
      rendered. Same free-text-into-markdown surface.
- [ ] **Step 4:** update `apps/join-api/tests/test_validate.py` — a provider
      payload with no service `lawful_basis` must now be **rejected**, and the
      existing consumer-only case must still pass unchanged. Add the negative
      test before the change and watch it fail.
- [ ] **Step 5:** `tests/golden/hosted-fixture/member-configs/configs/member-*.yaml`
      are generate-time inputs and `lawful_basis` is not read by
      `hurl/generate.py`, so `tests/golden/` output must be **unchanged** —
      assert that rather than assuming it. Update the fixture configs anyway so
      they stay representative of a valid member.
- [ ] **Step 6:** check whether the canonical `02-requirements.md` records move.
      `writer.py` falls back to a "see `03-sla/`" pointer when
      `member_requirements.lawful_basis` is unset, and this task sets the basis
      per **service** — so they may not change at all. Either outcome is fine;
      commit whichever diff appears.
- [ ] **Step 7:** update `G0.4` from `named-absence` to `implemented`, with a
      note that what is enforced is the *statement* of a basis, not its truth,
      which no validator can check. Re-render. `--fast`, `--live`. Commit.

---

## Task 6 — Delete the duplicated `approval` key (`S3.4`, OD-1a)

Approval mode is a property of how the Central Server was deployed, not of a
join request. `federation-core.yaml`'s `policy.management_request_approval:
explicit` already says it and `check_policy()` already enforces it.
`join-policy.yaml`'s `approval` is a second copy in a file whose scope cannot
apply it.

- [ ] **Step 1:** remove `approval` from `configs/x-road-bus/join-policy.yaml`
      and from `JOIN_POLICY_KEYS` in `hurl/generate.py`.
- [ ] **Step 2:** update `tests/test_join_policy.py`'s four-key assertion to
      three, and its docstring: the deleted key is the worked example of the
      rule the file exists to enforce.
- [ ] **Step 3:** add a `docs/production-delta.md` row — manual approval is
      hard-wired; path §3 fact 1's automatic/manual choice is a **redeploy-level
      federation decision** (three Central Server `local.ini` flags) that the
      pack states and does not model, with a pointer to the Wave 7 plan for what
      implementing it would take.
- [ ] **Step 4:** leave a comment in `join-policy.yaml` saying where approval
      mode actually lives, so the key is not re-added by someone who notices its
      absence.
- [ ] **Step 5:** update `S3.4` — still a named absence, but for the right
      reason and pointing at the right file. Re-render. `--fast`. Commit.

---

## Task 7 — Reclassify the documentation

**Last, deliberately.** This task moves files that Tasks 1–6 cite as evidence in
`docs/path-conformance.yaml`. Running it earlier means every other task rebases
onto moved paths; running it last means one mechanical fix-up pass, which
`tests/test_path_conformance.py` will drive for you — a wrong path is now a test
failure, not a silent stale citation. That is the first time this pack has been
able to do a documentation move safely.

**Keep this task mechanical.** The `PLAN.md` split that was here has been
removed to the out-of-scope table: a task that is `git mv` plus deletions can be
reviewed as a move, and one that also rewrites a document cannot.

Four kinds, and a document may only be one:

| Kind | Rule | Members |
| --- | --- | --- |
| **Reference** — true right now | No history, no dates, no status tables | `README.md`, `runbook.md`, `docs/conventions.md`, `docs/production-delta.md`, `manifest.yaml`, `deployment.yaml`, `hurl/README.md`, `PLAN.md` |
| **Decisions** — frozen once written | Dated; superseded, never edited to look current | → `docs/decisions/`: the `superpowers/plans/`, the `superpowers/specs/`, `topology-profile-decision.md`, `xroad-770-notes.md`, `xroad-8-delta.md`, `onboarding-alignment-design.md`, `onboarding-path-gap-analysis.md` |
| **Generated** — never hand-written | A test regenerates and diffs | `onboarding/*`, `docs/path-conformance.md` |
| **Notes** — deletable without loss | Excluded from any status claim | → `docs/notes/`: `do-terraform-brainstorm.md`, `docs/notes/reviews/*`, `REVIEW.md` |

- [ ] **Step 1:** `git mv` per the table. Keep the path document and the v0.3
      amendment note where they are — they are the *subject*, not a pack
      document, and `docs/` root is the right place for them.
- [ ] **Step 2:** delete the residual status tables from the two onboarding
      documents now moving to `docs/decisions/`. Their 2026-08-08 correction
      blocks already point at `docs/path-conformance.md`; with the move, the
      pointer becomes the only status statement they make. **Keep every word of
      the reasoning** — it is the most valuable prose in the pack and none of it
      was wrong; only the status claims were.
- [ ] **Step 3:** add a four-line `docs/README.md` stating the four kinds and
      the rule that a document may only be one. Without it the categories decay
      back into a folder of markdown within two waves.
- [ ] **Step 4 — the six in-code plan paths this move breaks.** `git mv` of
      `superpowers/` invalidates five live citations (`apps/join-api/app.py`,
      `apps/console/xroad.py`, `hurl/steps.py` ×2, `hurl/generate.py`) and a
      sixth, in `scripts/lib-stack.sh`, is **already dangling**. Per the comment
      guardrail, **do not repoint them — replace each with the reason it was
      standing in for**, in one sentence. If the reason is not recoverable from
      the plan, that is the finding: say what the code does and drop the
      citation. Six sites, mechanical, and it is this task's own breakage rather
      than scope creep. Verify with:
      `grep -rn "docs/superpowers" apps hurl scripts tests --include=*.py --include=*.sh`
      returning nothing.
- [ ] **Step 5:** run `--fast`. `tests/test_path_conformance.py` will fail on
      every moved citation in the matrix; fix `docs/path-conformance.yaml`,
      re-render, repeat until green. Commit.

---

## Sequencing

Task 1 → 2 → 3 → 4 → 5 → 6 → 7, strictly.

Task 1 first because it audits the matrix that every other task edits. Task 2
next because it is the only open defect and the only one with a user-visible
consequence. Task 3 must precede Task 4 so the G1 row is written once, and
Task 4 reuses Task 3's free-text sanitiser. Task 5 changes the canonical member
configs and wants a clean `writer.py` under it. Task 6 is independent and small.
Task 7 last, for the reason stated in the task.

**Verification tier per task:** `--fast` after every step; `--live` at the end
of Tasks 2, 3, 4 and 5 (each changes what a real join or retirement does); one
`--full` before the wave is closed out.

## Exit

- `G5.9` reads `implemented`; its negative case has been observed failing, and
  the failure output names fields and no values.
- The matrix cannot silently fall behind a new path version: a section with no
  clauses fails, and a changed path document fails with an instruction.
- No new outbound HTTP call was added anywhere in this wave.
- A joined member's onboarding record carries an admission reference; a hosted
  one states whose token holds its signing key; a retired one keeps its folder
  and gains `99-retirement.md`.
- The gate register names all eight gates the path defines.
- Every published service states a lawful basis, and a provider that omits one
  is rejected.
- No configuration key in `configs/` is unread by code.
- Every document in the pack is exactly one of the four kinds, and
  `docs/path-conformance.md` is the only place status is stated.
- No code file cites a plan, a spec, a wave or a numbered decision — including
  the ones in this plan — and `grep -rn "docs/superpowers" apps hurl scripts
  tests` returns nothing.
- `tests/golden/` byte-identical to its pre-wave state; `--full` green.
