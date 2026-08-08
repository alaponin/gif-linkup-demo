# KP2 — Wave 5: the monitoring add-ons

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements **Wave 5** of `docs/onboarding-alignment-design.md` §4. **Prerequisites: Wave 3 complete (four servers, one topology). Wave 4 is not a prerequisite.**

**Goal:** install the operational- and environmental-monitoring add-ons during
Security Server bring-up, so the pack demonstrates the one G4 step whose cost is
asymmetric.

**Read this before starting.** This is last for cycle-time reasons, not
importance. It changes bring-up for **every** server and adds time to every
subsequent deploy, so it is deliberately sequenced after the topology shrank from
five servers to four.

The onboarding path is emphatic, twice:

> "Installing them at G4 is trivial; retrofitting them across an installed base
> is a campaign."
> "the add-ons must be installed during G4 — the collection layer can come
> later, but the add-on cannot."

`grep -ri "op-monitoring\|opmonitoring\|environmental-monitoring"` across the
pack currently returns **0**, and no step in `hurl/steps.py`'s registry installs
either.

**This wave installs the add-ons and does not build a collector.** That is a
conscious, documented incompleteness: G4's third exit test — *"is its monitoring
data arriving centrally?"* — **remains unmet** afterwards. The lesson G-06
carries is *install the add-on at G4 or run a retrofit campaign*, and an
installed add-on with a documented absent collector teaches exactly that (design
§8.4). Adding `xroad-metrics` (NIIS, open source) later closes it properly.

**Architecture:** two add-ons installed on each Security Server during bring-up.
No new services.

**Tech Stack:** unchanged. The add-ons ship with the X-Road Security Server
Sidecar image the pack already uses.

## Global Constraints

- **Measure before and after.** The cost of this wave is deploy time on every
  server. Wave 3 Task 6 produced the baseline; this plan must produce the delta.
- **If the add-ons push `--full` past roughly 15 minutes**, stop and re-scope
  rather than accepting it — that was the number that made the profile split feel
  necessary in the first place, and this programme just removed it.
- **Do not build a collector.** If a task starts standing up X-Road Metrics, it
  has left this plan.
- `--fast` unchanged (this is deploy-time work, not static).
- Commit after every task.

## Design decisions

1. **Add-ons at bring-up, not as a post-step.** Installing them as a later pass
   would model exactly the retrofit the path warns against, and the demo would
   teach the wrong shape.
2. **Both add-ons, not one.** Operational monitoring gives the OA ecosystem-wide
   visibility and the member visibility of its own traffic; environmental
   monitoring is what certificate-expiry and version alerting key off. The path
   lists them as separate operator building blocks with different triggers.
3. **The absent collector is documented, not hidden.** A reviewer who finds
   add-ons emitting to nothing should find the reason next to them.

## Out of scope

- X-Road Metrics or any central collection.
- Alerting, dashboards, certificate-expiry notification.
- G6's exit test (*"does the member's own monitoring see what the OA's monitoring
  sees?"*), which needs the collector.

---

## Task 1: Install the add-ons during bring-up

**Files:** `hurl/steps.py`, `hurl/templates/`, `docker-compose.yml`, `tests/test_steps.py`

- [ ] **Step 1:** confirm how the add-ons are enabled on the Sidecar image at
      7.7.0 — package install, environment variable, or admin API call. This
      determines whether the change lands in `docker-compose.yml` (image
      configuration) or as a new registry step in `hurl/steps.py` (admin API).
      **Record the answer before writing anything**; the rest of this task
      branches on it.
- [ ] **Step 2:** implement per Step 1's finding. If it is a registry step, it
      belongs immediately after `ss.activate` and before `ss.client_add`, and it
      needs the same 409-safety classification every other step carries — a
      re-run must be safe, and if it is not naturally so it needs a `probe`.
- [ ] **Step 3:** the step (or config) applies to **every** Security Server,
      including `ss-pdga`. A management server invisible to monitoring is the
      same defect as a member one.
- [ ] **Step 4:** `tests/test_steps.py` asserts the new step's placement and its
      safety class, in the style of the existing audit (3 read-only / 10
      409-safe / 8 probe-guarded / 0 unsafe).
- [ ] **Step 5:** `--fast` green. Commit.

---

## Task 2: Prove, measure, and document the gap

**Files:** `acceptance/`, `docs/production-delta.md`, `README.md`, `tests/golden/`

- [ ] **Step 1:** regenerate the golden trees — a new bring-up step changes the
      emitted scenarios. Review the diff: it should differ **only** by the
      add-on step, on every server. Anything else is a Task 1 defect.
- [ ] **Step 2:** one `--full` from cold. Confirm each Security Server reports
      both add-ons enabled.
- [ ] **Step 3:** extend the parameterised member acceptance document with an
      add-ons-enabled assertion, so every member — canonical or joined — is
      checked for it automatically. A joined member that skips the add-on is
      exactly the retrofit case, and it should fail loudly.
- [ ] **Step 4:** **measure** `--full` from cold and RAM, and compare against
      Wave 3 Task 6's figures. Record the delta in `production-delta.md` and
      `README.md`. Apply the Global Constraint: if `--full` is past ~15 minutes,
      stop and re-scope.
- [ ] **Step 5:** document the deliberate gap in `production-delta.md` — the
      add-ons are installed at G4 as the path requires; **no collector exists**,
      so G4's third exit test is not met; `xroad-metrics` (NIIS, open source) is
      the component that would close it. Cross-reference design §8.4 so the
      decision is traceable rather than looking like an oversight.
- [ ] **Step 6:** confirm the un-join walk is unaffected — the add-ons are
      server-level, not client-level, so a departing hosted member should not
      touch them. If it does, that is a finding worth its own note. Commit.

---

## Sequencing

Task 1 → Task 2. Task 1 Step 1 is a spike: if it finds the add-ons are not
available on the Sidecar image at 7.7.0, stop and re-scope the whole plan rather
than forcing it — that finding would itself be worth recording in
`docs/production-delta.md`, because it would mean the path's "trivial at G4"
claim does not hold for containerised deployments.

**Exit:** both add-ons enabled on all four Security Servers and on every joined
member, asserted generically in acceptance, `--full` green from cold with a
measured delta, and the absent collector documented as a known, reasoned gap.
