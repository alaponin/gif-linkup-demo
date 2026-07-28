# KP2 — Testing Strategy and Feedback-Loop Time

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan changes only how the pack is verified — never what it deploys. It should land **before** `2026-07-28-kp2-simplification.md`, because Task 1 is what makes that refactor safe to execute quickly.

**Goal:** Cut the everyday verification loop from ~15 minutes to under a minute, without weakening what is checked. Today there are effectively two modes — a handful of static scripts, or a full `teardown.sh --purge` → `hurl/run-linkup.sh` cycle measured at **880–898 seconds** — so anything that feels like real verification costs a quarter of an hour, and an agent with no cheaper option will spend it.

**Architecture:** Four moves. (1) A committed **golden corpus** turns the pack's central invariant — byte-identical generated artefacts for both profiles — from a manual `cp -r /tmp` ritual into a two-second test. (2) **Federation snapshots** turn "reset to a known-good deployed state" from 15 minutes into about a minute, because the state is only 19 named volumes. (3) A single **tiered entry point** so the choice of how much to verify is made by the tool, not by whoever is typing. (4) **Instrumentation**, so the next optimisation is chosen from data rather than intuition.

**Tech Stack:** Unchanged — bash, Python 3 + PyYAML, pytest, Docker Compose v2, Hurl.

## Global Constraints

- **This plan does not change deployment behaviour.** `hurl/run-linkup.sh` must still produce the same federation, and `scripts/acceptance.sh` must still assert the same things. If a generated artefact changes, something is wrong.
- **The fast tier needs no Docker, no network and no federation.** If any check in it requires a container, it belongs in a different tier.
- **A tier that is not honest is worse than a slow one.** No check may be moved to a cheaper tier by weakening it. Where a check genuinely cannot run cheaply, it stays expensive and is documented as such.
- **Generated artefacts stay gitignored.** The golden corpus under `tests/golden/` is a *fixture*, which is a different thing from a working artefact, and `.gitignore` should say so where it excludes the working copies.
- Commit after every task.

## Design decisions

1. **Tiers are named, and the names go in the plan template.** `--fast` per step, `--live` per task, `--full` once per plan. Right now there is only "static" or "everything", so "everything" is the default and the loop is 15 minutes.
2. **The live tier assumes a running federation and does not create one.** Creating state and verifying against it are separate concerns and separate costs; conflating them is why the loop is expensive.
3. **The federation is a fixture, not a build artefact.** `teardown.sh` without `--purge` already keeps volumes. Purging is for the reproducibility proof, not for iteration — and this plan makes that explicit in tooling rather than leaving it as folklore.
4. **What the live tier is actually for is X-Road behaviour discovery.** Reviewing what past plans found: the 60-second authorisation-cache lag, `grant`/`revoke` returning 409, `read_acl` 404-ing instead of returning `[]`, registration propagation timing — those needed a federation. The PIN drift, undefined Hurl variables, byte-identical regressions, port exposure and the `topology.json` profile mismatch did not. Once a behaviour is discovered it should become a recorded fixture and stop costing 15 minutes to re-confirm.

## Out of scope

Parallelising the Hurl run. It is the obvious speedup — the four member certificate sequences are independent once the anchor exists — but the approval step does `GET /management-requests?...WAITING` and takes `items[0]`, which is a race that parallelism turns from latent into certain. Fixing that (filter approvals by server ID) is a prerequisite, not part of this plan. Also out of scope: any change to what `acceptance.sh` asserts.

---

## Task 1: Golden corpus for the generator

**Files:** `hurl/generate.py`, `tests/golden/` (new), `tests/test_golden.py` (new), `.gitignore`

The byte-identical guarantee is currently a `cp -r /tmp/base-$p-*` ritual pasted into each plan. An agent can skip it, or baseline after the change instead of before, and nothing notices.

- [x] **Step 1:** add `--out <dir>` and `--profile <full|lite>` to `generate.py`, used only by tests. Both default to today's behaviour — writing into `hurl/` and reading the profile from `deployment.yaml` — so nothing else changes. The test must not have to mutate a tracked file to generate the other profile.
- [x] **Step 2:** verify the flags are inert: regenerate with no flags and confirm all four artefacts are byte-identical to what is on disk now.
- [x] **Step 3:** commit `tests/golden/full/` and `tests/golden/lite/`, each holding `scenarios/`, `vars.env`, `topology.json`, `topology.sh` and `compose.members.yml` exactly as the generator currently emits them.

  `vars.env` contains the token PIN and admin password. **Golden `vars.env` must be generated from a fixed, obviously-fake `.env`** — never from a developer's real one. Add that fixture under `tests/golden/env.fixture` and have the test point `generate.py` at it.

- [x] **Step 4:** `tests/test_golden.py` regenerates both profiles into a temp dir and diffs against the corpus, reporting the first differing file and line rather than "not equal".
- [x] **Step 5:** document the update path in `hurl/README.md`: when a change *should* alter output, regenerate the corpus in the same commit as the change, so the diff is reviewable. A golden test whose corpus is updated blindly is theatre.
- [x] **Step 6:** `python3 -m pytest tests -q` green in under five seconds. Commit.

**Verified live (2026-07-28):** added a third flag, `--env <file>`, beyond
the two Step 1 names explicitly — Step 3 requires pointing `generate.py` at
a fake `.env` fixture, and nothing else in the plan offered a mechanism for
that. Found and fixed a real bug on the first real `--out` run: `vars.env`
writes *before* any scenario file in `main()`'s own order, which never
mattered when the output directory was always the real, already-existing
`hurl/` — a fresh `--out` target has no such guarantee and needs its own
`mkdir`. Confirmed the flags are genuinely inert: diffed all five artefacts
(not four — `compose.members.yml` too) against an on-disk snapshot taken
*before* touching `generate.py`, byte-identical. Confirmed both profiles
resolve correctly through the new flags (`lite` → 3 security servers,
`full` → 5, read straight from the generated `topology.json`). Proved the
test's own failure-reporting works, not just its happy path: deliberately
corrupted a byte in the committed `topology.json`, confirmed the test names
the exact file and line and shows both values, then restored it and
reconfirmed green. Used the `.venv` at the repo root (already set up for
`apps/console/tests`, pytest 9.1.1) to run the suite, since the host's
system `python3` (3.7.9, the runtime `generate.py` itself must keep
working under, and what the test's own subprocess call deliberately still
invokes) has no `pytest` module. `python3 -m pytest tests -q`: 2 passed in
~1s. `hurl/check_scenarios.py` and `scripts/acceptance.sh` both
re-confirmed green on the live stack afterward, and the hurl/README.md
update-path recipe was run for real and confirmed it reproduces the
committed corpus exactly (no drift) before being written down as fact.

## Task 2: One entry point, three tiers

**Files:** `scripts/verify.sh` (new), `hurl/run-linkup.sh`, `README.md`

- [ ] **Step 1:** write `scripts/verify.sh`:

```
scripts/verify.sh --fast    # static + golden + pytest. No Docker. Target: <30s
scripts/verify.sh --live    # --fast, then acceptance.sh against a RUNNING stack. ~1 min
scripts/verify.sh --full    # purge, deploy, seed, acceptance, console smoke. ~15 min
```

- [x] **Step 2:** `--fast` runs, in this order, cheapest first: `check_scenarios.py`, the ship gate, `check-exposure.sh`, `check-python-floor.sh` (if the simplification plan has landed it), and pytest across `tests/` and `apps/console/tests/`.
- [x] **Step 3:** `--live` refuses, with a clear message, when no federation is reachable — it must never silently deploy one. Distinguishing "the checks failed" from "there was nothing to check" is the whole point of the tier.
- [x] **Step 4:** **fail fast in the deploy path.** `hurl/run-linkup.sh` runs the `--fast` tier before it starts any container. A typo currently costs fifteen minutes to discover; it should cost five seconds.
- [x] **Step 5:** measure and record each tier's actual wall time in `README.md`, so the numbers in this plan are replaced by real ones.
- [x] **Step 6:** commit.

**Verified live (2026-07-28):** `check-python-floor.sh` doesn't exist yet
(it belongs to the simplification plan, sequenced to land after this one)
— `--fast` checks for it and runs it only if present, per Step 2's own
"if it has landed" wording, rather than requiring it. Measured all three
tiers for real rather than estimating: `--fast` ~8s, `--live` ~23s
(against the already-running stack, both well under target), `--full`
~918s (one real cold cycle, timed end to end). Proved `--live`'s refusal
path by pointing its own reachability probe at a port nothing listens on
— refused with the exact message, restored, re-confirmed the real path
still passes. Proved `run-linkup.sh`'s fail-fast wiring for real: injected
the same bare-port fault from the exposure-and-secrets plan's Task 3 test,
confirmed the deploy stopped exactly at `check-exposure.sh`'s failure line
— "bringing the federation containers up" never printed, and the running
container count never changed. Found and fixed a real bug during the
`--full` timing run itself: `console.sh up` returning is not the same as
the FastAPI app inside actually accepting connections yet, so the bare
health-check that followed it failed on a container that was healthy two
seconds later — fixed with a bounded retry and reproduced the exact race
against a real container restart to confirm the fix, rather than trusting
the fix without re-triggering the failure.

## Task 3: Federation snapshot and restore

**Files:** `scripts/federation.sh` (new), `.gitignore`, `docs/production-delta.md`

The deployed state is 19 named Docker volumes. Restoring them is about a minute; recreating them is fifteen.

- [ ] **Step 1:** `scripts/federation.sh snapshot [name]` — **stop the containers first** (`teardown.sh` without `--purge`), then for each volume `docker run --rm -v <vol>:/v -v <dir>:/s alpine tar czf /s/<vol>.tgz -C /v .`, then bring them back up. Snapshotting a running PostgreSQL volume yields a torn database; this is not optional.
- [ ] **Step 2:** `scripts/federation.sh restore <name>` — stop, remove the volumes, recreate them empty, untar, start. Then run `--live` to prove the restored federation actually works.
- [ ] **Step 3:** `list` and `rm`. Snapshots live in `.snapshots/`, gitignored, and the script warns about their size.
- [ ] **Step 4: the investigation that decides whether this is useful.** Restored state ages: X-Road's global configuration has an expiry and certificates have validity windows, so a snapshot is not restorable forever. Measure it — take a snapshot, restore it after an hour, a day, and several days, and record at which point the federation stops working and with what symptom. Write the shelf life into the script's help text and into `docs/production-delta.md`.
- [ ] **Step 5:** record the measured snapshot and restore times. If restore is not meaningfully faster than a redeploy, say so and stop — that is a legitimate outcome and worth knowing.
- [ ] **Step 6:** commit.

## Task 4: Selectable acceptance

**Files:** `scripts/acceptance.sh`

- [ ] **Step 1:** add `--only <id>` and `--from <id>` (e.g. `--only 2.6`, `--from 2.4`), defaulting to everything so existing invocations are unchanged.
- [ ] **Step 2:** keep the ordering guarantee — `--from` runs the remaining checks in order, it does not reorder them.
- [ ] **Step 3:** make the selection visible in the output, so a green run cannot be mistaken for a full one. A partial pass that reads like a full pass is a trap.
- [ ] **Step 4:** commit.

## Task 5: Measure the deploy

**Files:** `hurl/run-linkup.sh`, `docs/production-delta.md`

Nobody knows which part of the 880–898 seconds dominates: container boot, global-conf propagation, or the certificate sequences.

- [ ] **Step 1:** have `run-linkup.sh` emit phase timings — containers healthy, Hurl run start, Hurl run end — to `out/deploy-timings.txt`.
- [ ] **Step 2:** capture Hurl's own per-request report. Check which report flag this Hurl version supports (`--report-junit`, `--report-html`, or JSON output) before wiring it in, rather than assuming.
- [ ] **Step 3:** run it twice cold and record where the time actually goes, in `docs/production-delta.md` next to the existing boot-time measurements.
- [ ] **Step 4:** state the conclusion: which phase to attack next, and whether the parallelisation this plan put out of scope is worth revisiting once the approval race is fixed.
- [ ] **Step 5:** commit.

## Task 6: Turn live-discovered behaviours into fixtures

**Files:** `apps/console/tests/`, `docs/xroad-770-notes.md`

Four X-Road behaviours cost a live federation to discover and currently cost one to re-confirm: the 409 on `grant`/`revoke`, `read_acl`'s 404-instead-of-`[]`, the `Server.ServerProxy.AccessDenied` fault shape, and the auth-cache lag.

- [ ] **Step 1:** capture the real responses for each — status, headers, body — from the running stack, into `apps/console/tests/fixtures/xroad/`.
- [ ] **Step 2:** back the existing stubbed tests with these recordings rather than with hand-written approximations, so the tests fail if the parsing stops matching what X-Road really sends.
- [ ] **Step 3:** guard against silent rot: a `--full` run re-captures the fixtures into a temp dir and diffs them, failing if X-Road's behaviour has moved. Recorded fixtures that nobody re-records eventually describe a server that no longer exists.
- [ ] **Step 4:** cross-reference each fixture with the paragraph in `docs/xroad-770-notes.md` that documents it. Commit.

## Task 7: CI for the fast tier

**Files:** `.github/workflows/kp2-fast.yml` (new)

There is no CI in the repository. The `--fast` tier runs in under a minute on any runner and would have caught several of the regressions these plans exist to prevent.

- [ ] **Step 1:** a workflow that runs `scripts/verify.sh --fast` on push and pull request, scoped to changes under the pack.
- [ ] **Step 2:** pin the Python version to the pack's **host** floor, not the latest — that is the runtime the host scripts must work on, and CI is where the C8 rule gets enforced for free.
- [ ] **Step 3:** state explicitly in the workflow file that `--live` and `--full` are not run in CI and why (a federation needs ~16 GB and fifteen minutes), so nobody assumes green CI means the stack deploys.
- [ ] **Step 4:** commit.

## Task 8: Make the tiering the convention

**Files:** `README.md`, `runbook.md`, `docs/superpowers/plans/` template usage

- [ ] **Step 1:** document the three tiers and when each applies, in `README.md`, with the measured times from Task 2 Step 5.
- [ ] **Step 2:** state the policy that plans should follow: `--fast` after each step, `--live` at the end of each task, `--full` once before a plan is closed. The existing "Verified live (date)" notes should say which tier was run.
- [ ] **Step 3:** add "the federation is a fixture, not a build artefact" to `runbook.md` — `teardown.sh` without `--purge` between sessions, `--purge` only for a reproducibility proof.
- [ ] **Step 4:** commit.

---

## Sequencing

Task 1 first, and ideally before the simplification plan starts — it is what makes a 1,584-line refactor verifiable in seconds instead of by ritual. Task 2 depends on it. Task 3 is independent and has the largest wall-clock payoff, but Task 5's measurements may change what it should optimise, so start Task 5's instrumentation early even though its write-up lands later. Tasks 4, 6, 7 and 8 are independent and small.

The success criterion is concrete: after this plan, the loop for a change to `generate.py` is `scripts/verify.sh --fast` in under thirty seconds, the loop for a change to the console or the scripts is `--live` in about a minute, and `--full` is run once per plan rather than once per step.
