# KP2 — Wave 1: corrections

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements **Wave 1** of `docs/onboarding-alignment-design.md` §4.

**Goal:** fix the one place the pack gives a wrong answer, and close four
documentation absences. No topology change, no regenerate, no golden touched.

**Read this before starting.** This is the cheapest wave and the only one that
can land while anything else is in flight. Everything here is isolated: one
validator constant, three doc rows, one comment. If a step here needs a
regenerate, you have misread it — stop and re-read the design.

The one substantive item is Task 1. It is not cosmetic: `validate.py` currently
**accepts member codes X-Road will reject** and **rejects one X-Road permits**,
so the demo teaches the wrong identifier rule and a learner reusing the pack
inherits it.

**Architecture:** unchanged. No new files except doc rows.

**Tech Stack:** unchanged.

## Global Constraints

- **No topology change.** `hurl/topology.json` and `tests/golden/` are
  byte-identical before and after this plan. If either moves, something is
  wrong.
- **`--fast` stays green throughout** and does not grow measurably.
- Commit after every task.

## Design decisions

1. **The charset stays a constant.** Task 1 hardcodes the corrected pattern.
   Wave 2 adds `docs/conventions.md` as its stated source and points a comment at
   it — it does **not** move the pattern into config. One rule, one place, no
   indirection.
2. **Do not implement retention.** G-03a was over-graded in the v1 gap analysis
   (see design §8.4). Demo teardown deleting a volume is correct behaviour; the
   defect is that the procedure never mentions retention. Two sentences, not an
   archive mechanism.
3. **Reconcile, do not re-verify.** Task 2 fixes documents that describe a
   defect the code has already fixed. Re-running the own-server case to confirm
   is `--full`-tier work and belongs to whoever next runs one — Step 2 records
   that it is unconfirmed rather than claiming it.

## Out of scope

- `docs/conventions.md` (Wave 2), any member or module change (Wave 3), the
  onboarding record (Wave 4), monitoring add-ons (Wave 5).
- Implementing message-log archival or a retention policy.
- Re-running `--full` to re-verify the own-server join.

---

## Task 1: Identifier validation — denylist to X-Road's allowlist (G-01)

**Files:** `apps/join-api/validate.py`, `apps/join-api/tests/test_validate.py`

`_BAD_CHARS = frozenset("/:;%.")` is a denylist. X-Road ≥7.3.0 enforces the
allowlist `a-zA-Z0-9'()+,-.=?`, strict by default on fresh installations
(XRDDEV-1960). The two disagree in both directions:

| Candidate | Pack today | X-Road ≥7.3 | |
| --- | --- | --- | --- |
| `MOE_YS`, `PTSB_2` | accepts | **rejects** | false accept |
| `P&B`, `PT#B`, `PT@B`, `PT$B`, `PT~B`, `PT*B`, `PTSB!` | accepts | **rejects** | false accept |
| `PT"B`, `PT\B`, `PT<B`, `PT[B`, `PT{B` | accepts | **rejects** | false accept |
| `PTSB.X` | **rejects** | accepts | false reject |

- [ ] **Step 1:** replace `_BAD_CHARS` and `_bad_identifier()` with a positive
      match: `re.fullmatch(r"[a-zA-Z0-9'()+,\-.=?]+", value)`. Keep the empty
      and whitespace rejections — they fall out of the pattern, but assert them
      in tests rather than assuming.
- [ ] **Step 2:** leave `_check_key_derivation`'s `[a-z0-9]+` **unchanged**. It
      is a different, stricter constraint on a different thing — the
      `configs/member-<key>/` directory name and `manifest.yaml` map key — and
      it is correct as it stands. Add a comment saying so, because the two
      patterns now sit near each other and look like a contradiction.
- [ ] **Step 3:** rewrite the rejection message and the docstring. Both
      currently cite "X-Road: Message Protocol for REST" as the authority for
      banning `.`, which is wrong — `.` is *inside* X-Road's permitted set. Cite
      the 7.3.0 identifier restriction (XRDDEV-1960) and name the permitted set
      in the message.
- [ ] **Step 4:** table-driven tests over every row above plus the accepted
      cases (`PTSB`, `SS-PTSB`, `PT.SB`). The dotted-service-code case
      (`awards.list`) that motivated the original `.` ban is now **accepted** —
      assert that deliberately, with a comment, so the next reader does not
      "fix" it back.
- [ ] **Step 5:** `verify.sh --fast` green. Commit.

---

## Task 2: Reconcile the own-server `verified: false` record (G-10)

**Files:** `acceptance/2.7.md`, `docs/production-delta.md`

`acceptance/2.7.md` closes with "**One clause of the own-server case is NOT met
and is a known defect**" — the record reaching `ACTIVE, verified: false` because
the run's 120s retry budget was spent before `join.r1_verify`.
`docs/production-delta.md` §"An own-server join cannot reach `verified: true`
today" describes it as unresolved with two candidate fixes, neither chosen.

`apps/join-api/job.py:92` now carries `R1_RETRY_BUDGET = 54` — the first of
those two candidates, implemented, with a comment describing exactly this
failure.

- [ ] **Step 1:** read `job.py`'s `R1_RETRY_BUDGET` comment and confirm it is
      the same defect. If the budget was added for a different reason, stop and
      re-scope this task.
- [ ] **Step 2:** update both documents to say the fix landed and **has not been
      re-verified live** for the own-server case. Do not mark the clause met —
      no `--full` has run since. Name what would confirm it: an own-server join
      reaching `ACTIVE, verified: true`, which Wave 3's proof will produce
      incidentally.
- [ ] **Step 3:** `production-delta.md`'s "two candidate shapes, neither chosen
      here" sentence names the chosen one and drops the other. Commit.

---

## Task 3: The four documentation absences (G-08, G-03a, G-09)

**Files:** `docs/production-delta.md`, `runbook.md`

Each is one row or two sentences. None is an implementation.

- [ ] **Step 1 — ports (G-08).** TCP **5500** (message exchange) and **5577**
      (OCSP) appear only in `docs/do-terraform-brainstorm.md`, under a caveat to
      verify before use. Add a `production-delta.md` row: the demo is
      single-host on a loopback bind so it never needs them; a real member's
      Security Server needs both reachable, and a ministry firewall change takes
      weeks. Cite the onboarding path §2 G4.
- [ ] **Step 2 — retention (G-03a).** `runbook.md`'s un-join section instructs
      `docker volume rm kp2-<key>-db kp2-<key>-conf kp2-<key>-archive`.
      `kp2-<key>-archive` is the message-log archive. Add two sentences: this is
      correct for a demo; in production the message log is subject to a
      statutory retention period and a retirement that deletes it converts a
      retirement into an evidence gap (path §2 GX). Add the matching
      `production-delta.md` row. **Do not implement archival.**
- [ ] **Step 3 — development track (G-09).** Add a `production-delta.md` row:
      the pack is an instance of the path's §1 development track — synthetic
      data, Test CA, loopback bind — and a real development track additionally
      needs a standing prohibition on real personal data enforced by the
      membership terms, which the pack enforces by authorship only.
- [ ] **Step 4 — inbound ACL reversal (withdrawn G-03b).** Add a comment above
      `REVERSAL_ORDER` in `hurl/steps.py`: the walk revokes the departing
      member's **own** service ACLs; it does not revoke grants naming that
      member **as a subject** on another member's service. Unreachable today
      because `requested_access` is recorded and never acted on
      (`schema.py`), and it becomes reachable the moment KP3/KP4 add a joined
      member that consumes. No code change.
- [ ] **Step 5:** `verify.sh --fast` green. Confirm `hurl/topology.json`
      unchanged. Commit.

---

## Sequencing

Tasks are independent and can run in any order or in parallel. Task 1 is the
only one with code in it and should go first so that a `--fast` failure is
unambiguous.

**Exit:** `verify.sh --fast` green, `hurl/topology.json` and `tests/golden/`
byte-identical to their pre-plan state, four documents updated, one validator
corrected.
