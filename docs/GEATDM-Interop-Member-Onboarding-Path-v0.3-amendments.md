# Member onboarding path — proposed amendments for v0.3

**Status:** amendment note against `GEATDM-Interop-Member-Onboarding-Path-v0.2.md` (rev. 4 Aug 2026).
**Date:** 8 August 2026.
**Basis:** an independent review of `10-Knowledge-Products/KP2-GIF/KP2-build-pack/` against v0.2, clause by clause. The review's per-clause result is now a maintained artefact in the pack itself — `docs/path-conformance.md`, generated from `docs/path-conformance.yaml`, with every cited evidence path existence-checked by `tests/test_path_conformance.py`.
**Nature of the note:** v0.2 §4 says of the pack, "Treat it as evidence that the sequence is automatable, not as the component to adopt." This note takes that at face value and reports what the evidence turned out to show — including two places where it contradicts the path, and one where the pack's own failure is the better argument for a clause than the clause currently makes.

---

## Summary of proposed amendments

| # | Section | Amendment | Weight |
|---|---|---|---|
| A1 | §5 gap 2, G1 | Reframe admission authority from *a gate the workflow enforces* to *a decision reference the workflow cannot proceed without* | **Substantive** |
| A2 | §4 | Replace "minutes" with the measured figures, and name what dominates instead | Substantive |
| A3 | §2 G5 | Add the field-conformance exit test's failure mode, and say where the check has to live | Substantive |
| A4 | §8 q5 | Record the concrete instance the consumer-only SLA question now has | Minor |
| A5 | §2 G2 | Note that the hosting/role compatibility test has no mechanical form | Minor |
| A6 | §7 | Permit a folder name other than `members/`, and state the rule the layout is *for* | Minor |
| A7 | §6 | Strengthen the add-on argument with the counter-example it now has | Minor |
| A8 | §3 | Correct the audit-log claim (measured, does not hold) and reframe the time saving as organisational, not technical | **Substantive** |

---

## A1 — Admission authority: the committee is not the enforceable thing (§5 gap 2, G1)

**What v0.2 says.** §5 gap 2: *"Admission authority contradicts the RACI → Amend TK-IO-10 Phase 1 to show the Steering Committee gate."* G1's "Amend the material here" box: *"As written, the workflow lets the OA admit members under its own authority."*

**What the evidence shows.** The implementation reproduced the contradiction faithfully, and then trying to fix it in software produced something worse than the gap: an endpoint that requires a `decision_reference` string and checks only that it is non-empty. `"x"` passes. Two separate documents recorded the finding as closed by a governance-configuration file that was never written. The failure is instructive rather than embarrassing: **there is no software fix for "a committee decided this", and an implementation that pretends otherwise degrades into a text box.**

The path's own §4 already says the right thing — *"G0–G3 … Not automatable, and should not be"* — but §5 gap 2 asks for the opposite, and the two are read by different audiences. A vendor reading §5 will build the text box.

Both reference instantiations support §4 over §5. Estonia's admission decision is not in X-tee at all; what is systematised is the **RIHA** record the decision produces, and X-tee registration follows it. Finland's is a form to DVV, decided in a back office. NIIS assigns member management to the Operator with no off-the-shelf implementation. In neither case does an X-Road component hold an admission role.

**Proposed amendment.**

Rewrite §5 gap 2 as:

> | 2 | Admission authority contradicts the RACI | Amend TK-IO-10 Phase 1 to show the Steering Committee as **accountable** for the decision, and to show the decision as **taken outside the workflow**. What TK-IO-10 must enforce is not the committee but the **coupling**: no technical registration proceeds without a reference to the admission record, and that reference is written into the member's onboarding file at G1. |

And add to G1, replacing the "Amend the material here" box:

> **What to build, and what not to.** Do not build an admission workflow. In both reference instantiations the decision is minuted outside any system — Estonia's is carried by a RIHA registry entry, Finland's by a decision on an emailed application — and §4 already says G0–G3 should not be automated. What the operator's tooling must refuse is a technical join that cannot name the decision that authorised it. Concretely: the registration step requires an **admission reference**, the reference is recorded in `01-admission.md`, and the person holding the operator credential is not the person who submitted the application. That is the whole of the enforceable part. An admission "gate" in software with no register behind it is a text box, and will be filled in with a single character the first time it is inconvenient.

**Why this matters beyond wording.** As written, §5 gap 2 sets an acceptance criterion a tender can be marked against, and the cheapest way to pass it is the text box. The amended version sets a criterion that cannot be passed cheaply: it requires a register to point at.

---

## A2 — §4's automation table: measured figures (§4)

**What v0.2 says.** *"G4–G5 (technical) | **minutes** | Fully automatable via the management APIs."*

**What the evidence shows.** True, and now measurable. From a submitted payload through validation, operator approval, configuration generation and the live admin-API sequence to `ACTIVE, verified: true`:

| Shape | Technical time | What dominates |
|---|---|---|
| Hosted member | well under 2 minutes | X-Road's own propagation waits between registration and first successful call |
| Own server | ~2–3 minutes **after the member's server is up** | as above, plus 76–100 s to stand the server up |
| Own server, real conditions | the above **plus a `BLOCKED` wait** | procurement, VM provisioning and firewall change — days to weeks, and not compressible by tooling |
| Un-join (GX) | seconds | — |

**Proposed amendment.** Replace the G4–G5 row's "minutes" with:

> **~1 minute hosted; ~2–3 minutes own-server once the server exists — plus however long the server takes to exist.** Measured end to end against X-Road 7.7.0 (`KP2-build-pack`). Fully automatable via the management APIs. The figure that matters for planning is not this one: an own-server join spends most of its wall-clock *blocked* on the member standing its server up, which is G4's procurement-and-firewall time, not the technical sequence's.

And add a sentence under the table:

> Quote the technical figure carefully. "Minutes" and "a couple of minutes, plus days of waiting for a firewall change" support very different claims to a funder, and only the second one survives contact with the first cohort of members.

---

## A3 — G5's third exit test needs a stated failure mode and a home (§2 G5)

**What v0.2 says.** The third clause of G5's exit test: *"the response carries exactly the fields the contract declares."* Stated once, with no elaboration — unlike the middle clause, which gets a paragraph explaining why it is the one usually skipped.

**What the evidence shows.** The implementation asserted the first two clauses and not the third, and the reason it went unnoticed is worth putting in the path: **the mock provider derives its own response from the same OpenAPI contract**, so conformance held by construction. Nobody writes a test for a property their own test fixture guarantees. The nearest assertion compared returned values to a seed record field by field, which catches neither direction of the real failure:

- a response that **drops** a required field produces no mismatch, because the comparison iterates what was returned;
- a response that **adds** a field the source system holds and the contract withholds also passes, because the extra field matches the source record.

The second one is the serious case, and it is precisely the purpose-limitation property the exchange exists to demonstrate. It becomes live the moment a real application replaces the fixture — which is the normal direction of travel for every one of these programmes.

**Proposed amendment.** Expand G5's third exit-test clause to:

> - the response carries **exactly** the fields the contract declares — no fewer, and **no more**.
>
> The last clause is the one that fails silently. A backend built to the contract satisfies it by construction, so nobody writes the check; a backend that was already there, or that someone later edits in a browser, does not. Over-disclosure is the failure that matters: an authoritative source that returns three fields more than its published contract has broken purpose limitation without breaking anything a consumer would notice or report. Assert it as a set comparison against the registered contract, at registration and on every subsequent conformance review — not against the source record, which is exactly the comparison that hides it.

And add to the "Legacy backends" paragraph:

> The adapter is also where this check earns its keep: an adapter over a legacy system is the most likely place for a field the contract never declared to reach the wire.

---

## A4 — §8 question 5 now has a concrete instance

**What v0.2 asks.** *"Does a consumer-only member need an SLA? TK-IO-09 is written for providers; a consumer's obligations (rate, purpose limitation, log cooperation) have no template."*

**What the evidence shows.** A consumer-only member in the pack publishes no services and therefore receives no SLA record at all, while both providers receive one per service. The asymmetry is visible in the onboarding folder: one member's record simply has no SLA in it. The question is not hypothetical — the artefact structure already answers it "no", by omission, without anyone having decided that.

**Proposed amendment.** Add to §8 question 5:

> Note that the default answer arrives by omission rather than by decision: an artefact structure keyed on *published services* gives a consumer-only member no SLA at all, and no obligation record of any kind beyond the membership agreement. If the intended answer is "yes, a consumer needs one too", TK-IO-09 needs a consumer variant *and* the onboarding file needs a slot for it; if the intended answer is "no", say so, because a consumer's rate, purpose-limitation and log-cooperation obligations then have to live in the Member Requirements instead.

---

## A5 — G2's exit test has no mechanical form (§2 G2)

**What v0.2 says.** G2's exit test: *"is the hosting choice compatible with the member's role? A body publishing authoritative personal data should not be hosted on a peer's server."*

**What the evidence shows.** This is the only gate exit test in §2 that an implementation cannot check, and the implementation duly did not: hosting *structure* is validated (chains rejected, unknown hosts rejected, the default applied), the role-compatibility question is not, and a payload hosting an authoritative provider on a peer would pass today. It was caught once, by a human, reviewing a proposed default topology — which is the right mechanism, but it is a review, not a test.

**Proposed amendment.** Add to G2, after the exit test:

> **This test has no mechanical form.** "Authoritative publisher of personal data" is a property of the member's mandate, not of its payload, so no validator can decide it. Make it a named review step with a named reviewer — the same architecture function that owns the topology record — rather than leaving it to be discovered when someone proposes a default configuration that violates it. The failure mode is not a rejected join; it is a *default* that quietly delegates a national register's signing key to a peer.

---

## A6 — §7's folder name, and the rule it is for (§7)

**What v0.2 specifies.** `members/<member-code>/`, with ten files.

**What the evidence shows.** `members/` collided with the implementation's existing per-member configuration directory and had to be renamed. Trivial in itself, but it exposed something worth stating: the layout's value is not the names. It is the rule underneath, which §7 states in one sentence and then buries — *an onboarding whose folder is missing a file has not passed that gate.* An implementation that keeps the rule and changes the folder name has conformed; one that keeps the names and hand-maintains the files has not, and hand-maintenance is the likelier failure. The implementation that generated its records rather than authoring them is the one that could not fake a gate.

**Proposed amendment.** Add after the tree:

> The folder and file names are illustrative; the two rules are not. **First: every file corresponds to a gate exit, and a missing file means the gate has not been passed, whatever the calendar says.** **Second: the records are generated from the onboarding system's own data, never hand-authored** — a hand-maintained record can be brought up to date at review time, which is precisely the property the register exists to remove. An operator whose tooling writes these files as a side effect of the gate passing has an auditable path; an operator whose team writes them afterwards has a filing system.

---

## A7 — §6's add-on argument now has a counter-example (§6, G4)

**What v0.2 says.** §6: *"the add-ons must be installed during G4 — the collection layer can come later, but the add-on cannot, without a retrofit campaign."* G4's exit test asks: *"is its monitoring data arriving centrally?"*

**What the evidence shows.** An implementation that did exactly what §6 advises — both add-ons installed on every Security Server at bring-up, with a regression test refusing any server that comes up without them — and that still cannot answer G4's third exit test, because nothing collects from them. The two halves of the advice came apart cleanly, in the predicted direction, and the cheap half was the one that had to be done first.

**Proposed amendment.** Add to §6, after the two consequences:

> The split is worth stating as a planning rule rather than a caution: **installing the add-ons is a G4 line item with a deadline; building the collection layer is a roadmap item without one.** They are not the same decision and should not be funded as one. An ecosystem with add-ons everywhere and no collector can answer G4's third exit test the week it stands a collector up; an ecosystem with a collector and add-ons on two-thirds of its installed base cannot answer it at all, and the missing third is a campaign.

---

## A8 — §3's approval-policy facts: the audit claim does not hold as stated (§3)

**What v0.2 says.** Fact 1: *"automatic approval collapses days into seconds and moves the control to G0–G1, where arguably it belongs."* Fact 3, second sentence: *"Management-request origin IPs are now carried into the Central Server audit log, which is what makes an automated join auditable."*

**What the evidence shows.** A spike (`docs/decisions/superpowers/plans/2026-08-08-kp2-approval-policy-spike.md`) measured both facts against a running Central Server rather than reading them. Fact 1's "days into seconds" is correct but easy to misread as a technical saving: a control run (explicit approval) and an experiment run (all three `[center]` auto-approve flags set, CS restarted) reached `ACTIVE` within one retry interval of each other, both inside this pack's own 12-retry shared budget — the seconds were already being spent under `explicit`, because this pack's own operator-approval call happens immediately after submission. There is no propagation delay for automatic approval to remove here; the days it collapses are the *organisational* wait for someone to be available to approve, which this demo cannot measure because it never has that wait.

Fact 3's audit claim does not hold. Checked against the Central Server's own `GET /api/v1/management-requests/{id}` for a request approved explicitly and one approved automatically: both return the same shape — `id`, `type`, a *categorical* `origin` (`SECURITY_SERVER`/`CENTER`, not an address), `security_server_owner`, `status`, `created_at`. No IP field, no approver field, under either policy. The origin IP the fact refers to lives one layer down, in the registration/management service's plain access log (one `POST /managementservice/manage` line per join, an IP and a timestamp, correlatable only by matching the timestamp by eye) — and that line is written identically regardless of approval policy, since the Security Server still submits the same request either way. **Automatic approval does not remove anything from the Central Server's audit trail, because explicit approval was never adding anything there beyond what automatic approval also produces.** What an operator loses under an automatic policy is layer above X-Road entirely: an onboarding tool's own record of who decided and why — this pack's `decision_reference` is the concrete instance, and it exists only because a human called an approve endpoint with one.

**Proposed amendment.** Replace fact 3's second sentence:

> Management-request origin IPs are carried into the Central Server's plain access logs, not a structured audit trail — the same log line is written whether a request is approved manually or automatically, since it records the Security Server's submission, not the approval decision. Neither the admin API's `management-requests` records nor the access log carries an approver identity under either policy. An operator that needs to record *who* approved a join, and *why*, must keep that record in its own onboarding tooling — the choice is a time/control/**evidence** trade, and the evidence half is the onboarding tool's responsibility, not X-Road's.

And append to fact 1:

> This is a saving in *organisational* wait, not technical propagation time — a scripted or otherwise immediately-available approval step spends the same seconds either way. State it as: automatic approval removes the wait for a human to be available, not a wait X-Road itself imposes.

---

## Two things v0.2 got right that are worth keeping unchanged

Recorded because a review that only lists amendments misrepresents the document.

1. **The two-track shape (§1) is the correct headline change, and its cost is real.** The implementation is single-track and self-labels as the development track — which means it cannot demonstrate §1 at all, since the whole claim is that the two are *separate* (own Central Server, own CA/TSA, own trust anchor). That is not a gap in the implementation so much as confirmation that §1 asks for something structurally expensive. Keep it, and keep the operator obligation sentence: the prohibition on real personal data has to sit in the membership terms, because in a single-track instance it is enforced by whoever happens to be typing.

2. **G5's middle clause is stated exactly strongly enough.** *"A registration proven only on the happy path demonstrates that a route exists, not that a fence does."* The implementation got this right — the negative call is routed through the *provider's* Security Server so the denial cannot come from the caller's own server rejecting an unknown client, and the specific X-Road access-denied fault is required rather than any failure. It got it right *because the paragraph explains the trap*. That is the model for A3 above: name the failure mode, not just the assertion.

---

## Recommended disposition

A1 and A3 change what an implementer will build and what a tender can be marked against; they are the two worth carrying into v0.3 even if nothing else is. A2 and A8 change what a funder and an operator are told about a specific technical claim each — both corrections of record, not style. A4–A7 are clarifications that cost a paragraph each.

None of the amendments arise from the implementation being incomplete. Every one arises from a place where a complete-and-live implementation met the clause and something other than the clause decided the outcome — which is the only thing a build pack can tell a framework that the framework could not have worked out on its own.
