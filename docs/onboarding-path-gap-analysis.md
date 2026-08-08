# KP2 build pack vs. the member onboarding path — gap analysis

**Status: all findings closed** — Waves 1-5 of `docs/onboarding-alignment-design.md`
addressed every K-/G-/S- finding below (Wave 5 closed 2026-08-08); §4-6 gives
the compact resolution table. Kept for: §1's reusable "is the step visible?"
test, §2's toolkit-gap pattern, §7's KP3/KP4 handoffs, and §8's toolkit
amendments — all still forward-looking, unlike the finding write-ups.
**Version:** v3 (2026-08-05). Supersedes v1 and v2 of the same date.
**Analysed:** `10-Knowledge-Products/KP2-GIF/KP2-build-pack/`
**Against:** `08-Interoperability/GEATDM-Interop-Member-Onboarding-Path-v0.2.md`
**And against:** KP2's own curriculum — `KP2-GIF/gitbook/KP2_Module{1..6}_Script_Bundle_v0.1.md`

---

## 0. Revision note — what v1 got wrong

v1 anchored scope on one sentence in the onboarding path:

> "Treat it as evidence that the sequence is automatable, not as the component
> to adopt." (§4)

and concluded the pack owes only G4–G5. That was the wrong anchor. It is the
*path author's* framing of KP2 as external corroboration — not KP2's statement
of what KP2 is for. Using it to set scope let five findings be filed as "Type C —
out of scope, correctly" when they are squarely in KP2's own curriculum.

The right anchor is Module 1's own scope sentence:

> "KP2 teaches how to build the interoperability layer over that plan: the legal
> mandate, the governance, the technical bus, the standards portfolio, and **the
> member-onboarding workflow — demonstrated end-to-end**."

And `README.md`'s claim for the pack:

> "The videos teach the build; this pack **is** the ready solution — the
> configuration the modules generate."

That sets a different and in one dimension *stricter* test, which v1 never
applied. Restated in §1 below.

**Net effect of the re-scoping:** four new findings that v1 missed entirely, all
curriculum-grounded and two of them the most important in the document; three v1
findings re-graded; one v1 finding withdrawn as a KP3 dependency rather than a
KP2 defect. The strongest v1 finding (identifier charset) survives unchanged.

---

## 1. The right test for a knowledge product

A build pack inside a knowledge product is not a production system and is not
trying to be. `docs/production-delta.md` exists precisely to hold that line, and
it holds it well — thirty-odd rows of "demo shortcut → production requirement,"
several of them measured rather than asserted. Nothing below asks the pack to be
more real.

The test is not **is the step real?** It is **is the step visible?**

| | Acceptable in a KP build pack | Not acceptable |
|---|---|---|
| **Fidelity** | A Test CA that vets nobody, mock registries, synthetic data, a single Docker host | — |
| **Completeness** | — | A necessary step with no artefact, no stub and no named absence |

A simulated step teaches the step. A *missing* step teaches that the step is
optional — and a learner who reuses the pack as a template inherits the omission
without ever seeing it. That is the failure mode this document is looking for.

By that test, `backend.auth: none` is fine (declared, with the production
requirement beside it) and the absent membership agreement is not (a learner
following the pack end to end never encounters one).

---

## 2. The through-line: the pack inherits the toolkit's gaps

The single most useful pattern across all findings, and the one v1 stated only
once in passing:

**Where the GEATDM toolkit has a gap, the build pack faithfully reproduces it.**

Three instances, all independently confirmed:

1. **Admission authority.** The path's §5 gap 2: "TK-IO-10 Phase 1 shows only
   'operating-authority review'; Ref Model §5.3 makes the Steering Committee
   accountable." The pack's `configs/x-road-bus/2.7.yaml` sets `approval:
   explicit` — one operator bearer token, no second role. It reproduces the
   toolkit's contradiction exactly.
2. **The orphaned SLA.** The path's §5 gap 5: "No TK-IO-10 step signs it." The
   pack's `prompts/2.4.md:13` and `prompts/2.5.md:13` cite "a signed SLA (Module
   5.3; Interop Toolkit)" as a *precondition* and no step in the pack signs,
   attaches or carries one. Same gap, one layer down.
3. **Semantic conformance.** The path's §5 gap 4: "Semantic conformance absent
   from onboarding." The pack has a `semantic:` block, but nothing validates it
   against any published map, and no join step checks it.

This is worth stating plainly because it changes what the fixes are *for*. These
are not pack bugs to be quietly patched. They are the toolkit's own gaps, made
visible and concrete by an implementation — which is the most valuable thing a
build pack can do for a framework. Fixing them in the pack without fixing
TK-IO-08/09/10 would destroy that signal.

**Recommendation: fix them in the pack and feed each one back as a toolkit
amendment**, citing the pack as the demonstration. §7 lists these.

---

## 3. Register

New in v2 marked **NEW**; re-graded from v1 marked **↑**/**↓**.

| # | Gap | Severity | Basis |
|---|---|---|---|
| **K-01** | **NEW** Video subtopics 5.2 (Member Requirements) and 5.3 (SLA) have no build-pack artefact at all | **High** | Curriculum |
| **K-02** | **NEW** Module 3's Governance Pack — the "organisational-layer artefact of the runnable build pack" — does not exist in the pack | **High** | Curriculum |
| **G-01** | Identifier validation is a denylist, not X-Road 7.3+'s allowlist; wrong in both directions | **High** | Path §1a / defect |
| **G-02** ↑ | No G0/G1 layer: no eligibility, no legal-mandate test, no membership agreement, no admission role | **High** | Path §2, curriculum M3 |
| **K-03** | **NEW** Semantic map taught in 4.4 (OneRoster, CEDS, ISO 11179) is not anchored anywhere in the pack | Medium | Curriculum |
| **G-05** ↑ | No SLA artefact and no service catalogue entry at G5 | Medium | Path §2 G5, curriculum 5.3 |
| **G-06** ↑ | Monitoring add-ons not installed at G4 — the path's asymmetric-cost item | Medium | Path §2 G4, §6 |
| **G-07** ↑ | No per-member onboarding file; `out/join/*.json` is a job log, not a gate register | Medium | Path §7 |
| **G-04** ↓ | No tier-1 BB pattern classification on a service | Medium | Path §6a, curriculum 4.7 |
| **S-01** | **NEW (v3)** Registration is taught four times: modules 2.2–2.5 all realise one video subtopic (5.4) | **High** | Structural |
| **S-02** | **NEW (v3)** Files named after module numbers that collide with curriculum topic numbers | Medium | Structural |
| **K-04** | **NEW** `manifest.yaml` carries `video_ref: "?"` for module 2.7 | Low | Curriculum |
| **G-03a** | GX destroys the message-log archive without naming retention | Low | Path §2 GX |
| **G-08** | Ports 5500/5577 published only in a brainstorm doc | Low | Path §2 G4 |
| **G-10** | `acceptance/2.7.md` records a defect `job.py` has since fixed | Low | Internal drift |
| ~~G-03b~~ ↓ | ~~Inbound ACL revocation at GX~~ — **withdrawn as a KP2 defect**, re-filed as a KP3 dependency | — | See §7 |

---

## 4-6. Resolution status

**All findings below are closed.** They drove `docs/onboarding-alignment-design.md`'s
five waves, all now implemented (Wave 5 closed 2026-08-08) — that document
carries the actual decision reasoning and implementation detail; this table
keeps only what each finding was and how it closed, for anyone tracing a
current file back to the gap that produced it.

| ID | Was | Severity | Closed by |
|---|---|---|---|
| K-01 | Subtopics 5.2/5.3 (Member Requirements, SLA) had no build-pack artefact | High | Wave 4 — `member_requirements`/`sla` on `JoinPayload`, rendered per member |
| K-02 | Module 3's Governance Pack (RACI, admission role) did not exist in the pack | High | Wave 2 — `configs/governance/governance.yaml`; `POST /approve` requires the accountable role |
| K-03 | The Module 4 semantic map (OneRoster/CEDS/ISO 11179) had no anchor | Medium | Wave 2 — `configs/semantic/semantic-map.yaml`, checked by `validate.py` |
| K-04 | `video_ref: "?"` for module 2.7 | Low | **Withdrawn**, not fixed — the pack correctly records a capability the curriculum does not yet teach (`docs/onboarding-alignment-design.md` §1) |
| G-01 | Identifier validation was a denylist, not X-Road 7.3+'s allowlist | High | Wave 1 — `_BAD_CHARS` replaced with the allowlist |
| G-02 | No G0/G1 layer: no eligibility test, no membership agreement, no admission role | High | Wave 2 (governance) + Wave 4 (K-01's fields) — G0/G1 remain named absences, not stubs, per P2 |
| G-03a | GX teardown deleted the message-log archive with no retention note | Low | Wave 1 — two sentences beside the teardown instruction |
| G-03b | Inbound ACL revocation at GX (v1's finding) | — | **Withdrawn as a KP2 defect** — unreachable until a joined member consumes another member's service; re-filed as a KP3/KP4 dependency, §7 below |
| G-04 | No tier-1 BB pattern classification on a service | Medium | Wave 2 — `pattern:` field on `schema.Semantic`, registered in `join-policy.yaml` |
| G-05 | No SLA artefact, no service-catalogue entry at G5 | Medium | SLA half closed by Wave 4 (K-01); catalogue half deferred — see design doc §8.4 |
| G-06 | Monitoring add-ons not installed at G4 | Medium | Wave 5 — both add-ons confirmed running on every Security Server; collector remains a documented, deliberate gap |
| G-07 | No per-member onboarding file; `out/join/*.json` is a job log, not a gate register | Medium | Wave 4 — `onboarding/<key>/` covering the three gates Topic 5 teaches (5.2/5.3/5.4); seven path-backed gates named as absences, not stubbed (P2) |
| G-08 | Ports 5500/5577 documented only in a brainstorm doc | Low | Fixed — see `docs/production-delta.md`'s gap table |
| G-10 | `acceptance/2.7.md` recorded a defect `job.py` had already fixed | Low | Reconciled — `R1_RETRY_BUDGET = 54` re-verified live, `docs/production-delta.md` |
| S-01 | Registration taught four times (modules 2.2-2.5, one video subtopic) | High | Wave 3 — collapsed to one `register-member` module; MoEYS retired, negative check reassigned to `PLR:ENROLMENT`; three members, one topology |
| S-02 | Numbered filenames collided with the curriculum topic numbers they pointed at | Medium | Wave 3 — renamed to capability-based names (`federation-core.*`, `register-member.*`, `once-only-exchange.*`, `join-member.*`), landed with S-01 in one re-baselining pass |

---

## 7. Deferred to KP3 and KP4

Recorded explicitly so these are not re-found as KP2 defects.

| Item | Lands in | Note |
|---|---|---|
| Running BB implementations (Registration, Consent, Payments) | **KP3** | KP2 owns the `pattern:` classification only (G-04) |
| BB contract shapes per pattern | **KP3** | KP2 publishes the register; KP3 fills it |
| Real Joget DX apps behind the OpenAPI contracts | **KP4** | The pack's "Joget-free by design" seam is built for this |
| **Inbound ACL revocation at GX** (v1's G-03b — **withdrawn**) | **KP3/KP4** | See below |

**On the withdrawn G-03b.** v1 flagged that `REVERSAL_ORDER` revokes only the
departing member's *own* service ACLs, never grants naming it as a subject. That
is accurate but currently unreachable: `requested_access` is "recorded and
surfaced to the operator, never acted on" (`schema.py`), so no joined member ever
holds an inbound grant. Filing it as a KP2 defect was wrong.

It becomes reachable the moment KP3 or KP4 adds a joined member that consumes —
which both will. Worth a comment in `steps.py` beside `REVERSAL_ORDER` now, so
whoever adds the grant path finds the reversal gap already named rather than
discovering it as a dangling grant on a live federation.

**Two KP4 handoffs worth flagging while the analysis is open:**

1. `scripts/member.sh drift` becomes load-bearing. `docs/production-delta.md`
   already states the case — "a real third-party backend (a Joget app someone
   edited in a browser) drifts silently from what the federation publishes" — and
   notes that the pack *detects* and does not *remedy*. With real Joget apps that
   moves from hypothetical to routine.
2. The path's G5 **adapter service** pattern ("where the member's system cannot
   expose a conformant interface directly… rather than modifying the legacy
   system") gets its first real test. "Adapter" appears twice in the pack, both
   incidental. If any Joget app cannot expose a conformant contract, the adapter
   is the answer the path already names — worth deciding before KP4 rather than
   during.

---

## 8. Toolkit amendments this analysis supports

Per §2 — these findings are evidence for the framework, not just work for the
pack. Each is a §5 row of the onboarding path with an implementation behind it.

| Path §5 gap | Amendment | Evidence from the pack |
|---|---|---|
| 2 — admission authority | TK-IO-10 Phase 1: Steering Committee gate | `2.7.yaml`'s `approval: explicit` reproduces the defect exactly |
| 4 — semantic conformance | TK-IO-08 §5: semantic map as G5 evidence | K-03 — a `semantic:` block citing a map that does not exist |
| 5 — orphaned SLA | TK-IO-10: sign at G5, carry as catalogue metadata | K-01 — four prompts cite the SLA as a precondition; nothing attaches it |
| 10 — TK-IO-08 Annex A empty | Publish sizing, host naming, ports | Pack has measured sizing (~2.1 GiB/SS, ~16 GiB for five); G-08 has the ports |
| 12 — BB layer of the semantic map | Publish the pattern register | G-04 — the pack is an Information Mediator that never says so |

Two further notes for the path document itself:

- **§4's automation table** says "G4–G5 (technical): minutes." The pack has
  measured figures — 64–93s hosted; ~163s own-server after the member's server is
  up, plus 76–100s to stand it up, plus `BLOCKED`. Worth quoting: "minutes" and
  "163 seconds plus days of BLOCKED" land differently on a funder.
- **§2 G4's exit test and §6's add-on point** would both be stronger with the
  pack as counter-example: a G4 implementation that is otherwise complete and
  live-proven, and still cannot answer "is its monitoring data arriving
  centrally?" That is the retrofit campaign arriving on schedule.

---

## 9. Recommended sequence

> **Superseded — see `docs/onboarding-alignment-design.md`.** This section's
> ordering was rebuilt into five waves on a different rule (one re-baselining
> event; member-light work before it, member-heavy after), and two findings were
> withdrawn there: **K-04**, because `video_ref: "?"` is the pack correctly
> recording that it exceeds the curriculum rather than a defect; and the claim
> that the join module should become the pack's *centre of gravity*, which the
> D3 decision (no curriculum change) reverses. The join API stays fully built and
> live-verified as a **labelled surplus** — demonstrated, not taught.

---

## Sources

**Build pack:** `README.md`, `manifest.yaml`, `deployment.yaml`, `runbook.md`,
`REVIEW.md`, `configs/x-road-bus/2.7.yaml`, `configs/member-*/`,
`apps/join-api/{schema,validate,job,app}.py`, `hurl/steps.py`,
`acceptance/2.7.md`, `acceptance/member.md`, `prompts/2.2–2.5.md`,
`prompts/member.md`, `docs/production-delta.md`, `docs/xroad-770-notes.md`,
`docs/do-terraform-brainstorm.md`, `out/join/*.json`.

**Curriculum:** `KP2-GIF/gitbook/KP2_Module{1..6}_Script_Bundle_v0.1.md` —
scope statements, Module 5 subtopic table, subtopic 5.2 and 5.3 scripts,
subtopic 4.7 script, Module 4 tag lines.

**Path:** `08-Interoperability/GEATDM-Interop-Member-Onboarding-Path-v0.2.md`.

Character-set findings (G-01) were produced by executing the pack's own
`_bad_identifier()` against the path's stated X-Road 7.3+ permitted set. Absence
findings (K-02, K-03, G-04, G-05, G-06, G-08) were established by full-tree grep
excluding `.venv/`, `out/` and bytecode caches.
