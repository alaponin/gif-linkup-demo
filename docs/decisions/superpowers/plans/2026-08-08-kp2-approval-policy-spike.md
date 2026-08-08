# KP2 — Spike: what does automatic approval actually cost?

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. **This is a spike, not a wave: it produces knowledge, not capability. Nothing here lands in `configs/`, `hurl/` or `apps/`.** Its output is two documents and a decision about whether `docs/superpowers/plans/2026-08-08-kp2-wave7-approval-policy-branch.md` is worth running at all.

**Goal:** answer three questions about the onboarding path's §3 fact 1 —
*"approval of registration requests is an operator policy choice, automatic or
manual, since v6.21.0"* — by measurement rather than reading.

1. **Is auto-approval settable over the Central Server admin API**, or only by
   writing `/etc/xroad/conf.d/local.ini` and restarting?
2. **What does it save?** The path says *"collapses days into seconds"*.
   In a demo nobody is waiting on the approval, so the technical saving may be
   nil — in which case the claim is about the organisational wait at G0–G1, and
   the path should say so.
3. **What does it cost in evidence?** Under explicit approval the Central Server
   holds an approved management request with an approver and a timestamp. Under
   automatic approval, what is left? The path's §3 fact 3 says
   management-request origin IPs in the CS audit log are *"what makes an
   automated join auditable"* — that claim needs checking when nobody approved
   anything.

**Why this has no start gate.** It is hours of work against a stack that already
exists, it commits no code, and question 3 in particular is something the
framework wants an answer to whether or not the pack ever ships a switch. The
gate belongs on the *capability*, not on the knowledge — which is the whole
reason this file is separate from the Wave 7 plan.

**Prerequisites:** a running `--full` stack. Nothing else.

---

## Constraints

- **Throwaway by design.** Everything is done by hand on a running stack:
  `docker exec`, an edited file, a restart. No generator change, no template, no
  compose edit, no test.
- **Revert the stack when done.** `scripts/teardown.sh --purge` and redeploy.
  The committed configuration must still say `explicit` at the end, unchanged.
- **The output is the artefact.** Two writes-ups (Steps 2 and 6) and a
  recommendation. A number in a terminal scrollback has not been produced.

---

## Task 1 — The mechanism

- [ ] **Step 1:** fetch the Central Server's own OpenAPI description —
      `GET /api/v1/openapi.yaml` — and search it for a system-parameters,
      settings or management-request-policy endpoint. **If auto-approval is
      settable over the API, that is the headline finding**: the Wave 7 branch
      becomes one generated call instead of a generated config file plus a
      bind-mount plus a restart, and the plan should be rewritten before it is
      ever costed again. Do not assume the `local.ini` route without looking —
      the pack's own history is the argument for looking, since the API-key
      assumption in `PLAN.md` did not survive contact with 7.7.0 either.
- [ ] **Step 2:** if there is no API route, read the packaged
      `/etc/xroad/conf.d/local.ini` out of the running Central Server and
      confirm, against the container rather than against memory: the three flag
      names (`auto-approve-auth-cert-reg-requests`,
      `auto-approve-client-reg-requests`,
      `auto-approve-owner-change-requests`) and the section they sit under.
      **Save the packaged file verbatim** — Wave 7 needs it as a fixture, and
      capturing it here is most of that task's risk removed.
- [ ] **Step 3:** determine whether the flags are read at start-up only or on
      change. This decides whether `automatic` is a redeploy-level or a
      runtime-level choice, which is half of question 2's answer on its own.
- [ ] **Step 4:** write Steps 1–3 up as a new numbered section in
      `docs/xroad-770-notes.md`, in that file's existing form — assumption →
      what 7.7.0 actually does. **This section is worth the spike on its own**,
      independently of anything measured below. Commit.

---

## Task 2 — The measurement

- [ ] **Step 1 — control.** On the current stack, run a hosted join
      (`apps/join-api`: submit → approve → `ACTIVE, verified: true`) and record
      the wall-clock, plus how much of it is `ss.client_register`'s propagation
      wait. `R1_RETRY_BUDGET = 54` exists because that wait once ate 95–107 s of
      a 120 s budget; this is the number to compare against.
- [ ] **Step 2 — capture the audit state.** `GET /management-requests` on the
      Central Server for the join just performed. Record what the approved
      request carries: approver, timestamp, origin IP.
- [ ] **Step 3 — flip it by hand.** Set the three flags in the Central Server's
      `local.ini` (`docker exec`, edit, restart `xroad-center` if Task 1 Step 3
      says so). No generator, no compose change.
- [ ] **Step 4 — repeat.** Run the same join again. It will fail at the
      approval half of `ss.client_register` — nothing is `WAITING`, so
      `jsonpath "$.items[0].id"` captures nothing. **That failure is expected
      and is itself a finding**: it is why the Wave 7 branch needs separate
      templates rather than a runtime skip. To get a clean measurement, run the
      join with those two steps' approval halves removed by hand.
- [ ] **Step 5 — capture the audit state again**, and compare with Step 2. This
      is question 3, and the most important comparison in the spike.
- [ ] **Step 6 — write it up** in `docs/production-delta.md`: a small table of
      the two runs, and the honest conclusion whichever way it falls. If the
      technical saving is negligible, say so plainly. If the audit record is
      materially thinner, say that plainly too — it is a stronger finding than a
      time saving would have been.
- [ ] **Step 7:** `scripts/teardown.sh --purge`, redeploy, confirm the committed
      configuration is untouched and `--full` is green. Commit.

---

## Task 3 — Decide

- [ ] **Step 1:** update `S3.4` in `docs/path-conformance.yaml`. It stays a
      **named absence** — the spike measured the choice, it did not implement it
      — but the note now carries what the choice costs, citing the two write-ups.
      A named absence with a measurement behind it is a stronger artefact than
      most implementations. Re-render.
- [ ] **Step 2:** if question 3 found an audit-trail difference, add an
      amendment to
      `docs/GEATDM-Interop-Member-Onboarding-Path-v0.3-amendments.md` against §3
      fact 1: the path presents the choice as a time/control trade and would be
      stronger presenting it as a time/control/**evidence** trade.
- [ ] **Step 3 — the recommendation.** Write two sentences at the top of
      `docs/superpowers/plans/2026-08-08-kp2-wave7-approval-policy-branch.md`
      saying whether that plan should now run, and why. The three outcomes:
      *the API route exists* → rewrite Wave 7, it is now cheap;
      *the saving is real and someone needs to switch* → run Wave 7 as written;
      *the saving is negligible* → close Wave 7 unstarted, the spike already
      produced everything of value.
- [ ] **Step 4:** `--fast`. Commit.

---

## Exit

- `docs/xroad-770-notes.md` states how auto-approval is set at 7.7.0, observed
  rather than read.
- `docs/production-delta.md` carries a two-run comparison of wall-clock,
  operator actions and audit-trail content.
- `S3.4` is still a named absence, and now says what the absent thing costs.
- Wave 7 carries a recommendation to run, rewrite, or close.
- The committed configuration is unchanged and `--full` is green.
