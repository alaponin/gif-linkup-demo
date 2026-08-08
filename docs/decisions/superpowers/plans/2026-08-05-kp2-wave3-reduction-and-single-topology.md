# KP2 — Wave 3: the reduction and the single topology

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements **Wave 3** of `docs/decisions/onboarding-alignment-design.md` §4, and decisions **D1, D2, D5**. **Prerequisites: Waves 1 and 2 complete, committed, and `--live` green.**

**Goal:** four Security Servers, three members, one topology, one registration
module, capability-based filenames. One golden regeneration, at the end.

**Read this before starting — this is the largest and only risky plan in the
programme.** It changes topology, so it invalidates `tests/golden/`, the un-join
byte-identity clause, and every profile-conditional caveat in the pack at the
same time. That is deliberate: the design's P3 says **one re-baselining event**,
and splitting this plan buys a second one.

Three things make it tractable:

1. **The target is today's `full` profile minus MoEYS.** Not a new topology — a
   subtraction from an existing, proven one. `tests/golden/full/` is the basis
   for the new golden.
2. **Profile removal was already scoped once.** `2026-08-01-kp2-join-c-own-server-and-unjoin.md`
   Task 6 enumerated the machinery in detail and was gated off when its
   precondition failed (`docs/production-delta.md`, "The Task 6 gate" — the
   answer was **No**). Task 4 below reuses that file list; it is the most
   valuable inherited artefact in this plan. **Read join-c Task 6 before
   starting Task 4.**
3. **The regeneration is last.** Tasks 1–5 are staged so that only Task 6
   regenerates and proves.

**Architecture:** one deployable topology — `cs`, `ca`, and Security Servers
`ss-pdga` (management), `ss-pnia`, `ss-plr`, `ss-pnea`, all own-server. No
canonical member is hosted; hosting is demonstrated by the join API, whose
`default_hosting: hosted_on` is X-Road's own *security server host* pattern.

**Tech Stack:** unchanged.

## Global Constraints

- **One regeneration, in Task 6.** Tasks 1–5 may leave `tests/golden/` stale;
  they must not each regenerate it.
- **`PINNED_PORTS` do not move.** `_allocate_numbers()`'s determinism and the
  un-join byte-identity clause both depend on nothing below `FRESH_PORT_START`
  shifting. MoEYS's pinned ports (6000/6080) stay **reserved** after its removal
  — join-c Task 6 Step 4 makes the same point about `ss-pnia`.
- **Two cross-pack contract surfaces are touched and both need sign-off, not a
  commit:** `manifest.yaml`'s `identifiers:` block (Task 1) and
  `hurl/topology.json`'s `"profile"` key (Task 4). Neither is a local change.
- **The `demo` compose profile is not in scope.** `docker-compose.yml` uses
  `profiles: ["demo"]` for the console and join-api. Only `profiles: ["full"]`
  is being removed. Deleting the wrong one takes the console out of `--full`.
- **`--fast` green after every task**; `--live` where a stack is available;
  **one** `--full` at the end.
- **The console breaks silently until `--full`.** `apps/console/tests/` runs in
  `--fast`, so the Python side is covered — but `static/index.html` and
  `static/app.js` are exercised **only** by `--full`'s console smoke pass, and
  the console is not in the acceptance path at all. **Three of this plan's six
  tasks touch the console** (1, 3, 4). After each of them, re-read the console
  files named in that task rather than trusting a green `--fast`.
- Commit after every task.

## Design decisions

1. **MoEYS is retired, not hosted.** D1. Its only demo role — the unauthorised
   caller in 2.6's negative check — moves to `PLR:ENROLMENT`, which already
   holds no grant on PNIA's `identity-api` and so proves the same point using a
   member that is already present and already a provider.
2. **All own-server; no canonical member is hosted.** D5. The intermediate
   three-server option was withdrawn: "consumer-only bodies are hosted" is the
   onboarding path's own inference rather than sourced practice, and the
   real-world host is a commercial third party, not the operator. See
   `docs/decisions/topology-profile-decision.md` §2.3.
3. **The hosted golden is generated from a fixture *config*, not a profile
   flag.** This refines design §8.6. `resolve_hosted_on_map()` already handles an
   explicit `security_server.hosted_on` — the path every joined member uses — so
   a fixture member config that sets it exercises the real mechanism with **no
   `--profile` flag at all**. The flag is deleted rather than renamed.
4. **KP3 is not touched.** D2. Record the convention where KP3's eventual build
   plan will find it; do not rename its scaffolding.
5. **Rename and reduce in the same plan, reduce first.** Renaming four
   registration modules and then collapsing them to one is wasted motion.

## Out of scope

- The onboarding record, Member Requirements and SLA fields (Wave 4).
- Monitoring add-ons (Wave 5).
- Renaming KP3-build-pack (D2).
- Any change to `apps/join-api`'s hosting logic — it is already correct.

---

## Task 1: Retire MoEYS and amend the frozen contract (D1)

**Files:** `manifest.yaml`, `configs/member-moeys/`, `docker-compose.yml`, `apps/data/`, `apps/specs/pemis.openapi.yaml`, `acceptance/2.2.md`, `acceptance/2.6.md`, `prompts/2.2.md`, `hurl/scenarios/`, `scripts/acceptance.sh`, **`apps/console/{static/index.html,static/app.js,truth.py}`**, `apps/console/tests/test_truth.py`

**Gate — do not start without sign-off.** `manifest.yaml`'s `identifiers:` block
is labelled *"Frozen identifiers — cross-pack join keys for KP3/KP4"* and lists
`PROGRESSA/GOV/MOEYS:PEMIS`. `hurl/check_scenarios.py` enforces that every entry
resolves to an `identity.members` entry, so removing MoEYS **requires amending
that contract**.

The evidence that it is safe: `grep -rn "MOEYS\|PEMIS" 10-Knowledge-Products/KP3-DPI/`
returns nothing, and KP3's config skeleton is `identity-pnia` / `registry-plr` /
`registration` / `payment-paypro` — it builds on PNIA and PLR. Re-run that grep
before starting; if KP3 has gained content since, re-open the decision.

- [ ] **Step 1:** re-run the KP3 grep. Record the result in the commit message.
      Obtain and record the sign-off. If either fails, stop — the rest of this
      plan does not depend on Task 1 and Tasks 2–6 can proceed with four members.
- [ ] **Step 2:** reassign the negative check **before** removing anything.
      `acceptance/2.6.md` and `scripts/acceptance.sh`'s `check_265` use
      `MOEYS:PEMIS` as the unauthorised caller; move to `PLR:ENROLMENT` calling
      PNIA's `identity-api`. Verify live that the denial is still the specific
      `Server.ServerProxy.AccessDenied` and not a transport error — that
      specificity is the whole value of the check.
- [ ] **Step 2b — the console's negative check, which is a demonstration
      surface.** MoEYS is not only in configs; it is hardcoded into the
      console's **"3 · Who's allowed"** tab: `static/index.html:88–90` (the
      `MOEYS:PEMIS ✗ not admitted` panel, `ask-as-moeys-btn`, `moeys-result`),
      `static/app.js:562` (`askAsMoeys()`), `:611` (its click handler) and
      **`:892`, inside the one-click guided demonstration**. Retiring MoEYS
      without this leaves the console's headline demo calling a member that no
      longer exists — it fails live, on the tab built to teach access control.
      Move all of it to `PLR:ENROLMENT`, and update `truth.py`'s
      `negative_check_entrypoint` (`http://ss-moeys:8080` → `http://ss-plr:8080`)
      and `test_truth.py:21`'s assertion with it.
- [ ] **Step 2c — relabel, do not just rename.** The panel currently reads
      "MOEYS:PEMIS ✗ **not admitted**", which was always slightly wrong: MoEYS
      *is* admitted to the bus, it simply holds no grant on `identity-api`. PLR
      makes that unavoidable — it is a provider everyone can see is a member. Use
      wording like "PLR:ENROLMENT — on the bus, not granted this service." The
      forced change sharpens the lesson the tab exists to teach: **on the bus ≠
      may call everything.**
- [ ] **Step 3:** remove `identity.members.moeys` and the `identifiers.members`
      entry for `PROGRESSA/GOV/MOEYS:PEMIS`; delete `configs/member-moeys/`;
      remove `ss-moeys` and `app-pemis` from `docker-compose.yml`; delete
      `apps/specs/pemis.openapi.yaml` and the PEMIS seed data. Keep MoEYS's
      pinned ports **reserved** in `PINNED_PORTS` with a comment saying why.
- [ ] **Step 4:** delete `acceptance/2.2.md` and `prompts/2.2.md`. Task 2
      collapses the remaining three into one, so do not renumber here.
- [ ] **Step 5:** add a `docs/production-delta.md` note recording that the
      frozen contract was amended, when, and on whose sign-off. This is the one
      change in the programme another pack could have been building against.
- [ ] **Step 6:** `--fast` green (golden will now differ — expected, do not
      regenerate). Commit.

---

## Task 2: Collapse registration into one module (S-01)

**Files:** `manifest.yaml`, `prompts/`, `acceptance/`, `configs/member-*/`

`manifest.yaml` maps modules 2.2, 2.3, 2.4 and 2.5 all to `video_ref: "5.4"` —
four modules realising one video subtopic. `acceptance/2.2.md` states the
redundancy itself: *"What 2.2 proves is the 5.4 claim that the registration shape
is identical for every member."*

- [ ] **Step 1:** write one `register-member` module — prompt and acceptance
      document — parameterised over `configs/member-*/`. `acceptance/member.md`
      is already exactly this generically; the new acceptance document should
      reference it rather than restate it, the way `acceptance/2.7.md` already
      refuses to restate `member.md`.
- [ ] **Step 2:** keep all three member configs as **data**. The collapse is of
      modules, not members. `manifest.yaml`'s `modules:` gains one entry where it
      had four; `identity.members` is untouched.
- [ ] **Step 3:** delete `prompts/2.3.md`, `2.4.md`, `2.5.md` and
      `acceptance/2.3.md`, `2.4.md`, `2.5.md`. Preserve anything module-specific
      worth keeping — PNEA's consumer-only `connection_type` note, PLR's and
      PNIA's semantic blocks — by moving it into the member config or the
      parameterised acceptance document, not by losing it.
- [ ] **Step 4:** `check_scenarios.py` asserts every scenario file is claimed by
      a module. Four modules' claims become one module's; confirm the claim set
      still resolves before committing.
- [ ] **Step 5:** `--fast` green. Commit.

---

## Task 3: Capability-based filenames (S-02, D2)

**Files:** `manifest.yaml`, `prompts/`, `acceptance/`, `configs/`, `README.md`, **`apps/console/{truth.py,app.py}`**

Three numbering systems disagree today: curriculum topics 1–6, build modules
"2.1"–"2.7" (leading `2` = KP2), and `video_ref` 5.4–5.7. So `prompts/2.2.md`
reads as *Topic 2, subtopic 2* — a decree component — and means MoEYS
registration.

The pack already voted: `configs/member-ptsb/ptsb.yaml` (written by
`writer.py`), `prompts/member.md` and `acceptance/member.md` are all name-based.

- [ ] **Step 1:** rename. `2.1` → `federation-core`; the Task 2 collapse →
      `register-member`; `2.6` → `once-only-exchange`; `2.7` → `join-member`
      (config: `join-policy.yaml`). Member configs
      `configs/member-<key>/<key>.yaml`, matching `ptsb.yaml`.
- [ ] **Step 2:** `manifest.yaml`'s `modules[].id` becomes the capability name;
      **`video_ref` is retained** — it is the curriculum traceability, and P4
      says the manifest holds the mapping so filenames do not have to.
- [ ] **Step 3:** `discover_members()` globs `configs/member-*/*.yaml` and
      requires exactly one file per directory — it never reads the filename, so
      member config renames are free. `manifest.yaml` is the only place holding
      explicit `config:`/`prompt:`/`acceptance:` paths; update them in the same
      commit. `check_scenarios.py`'s `scenario_member_re` keys off generated
      `hurl/scenarios/` names, not `configs/` — untouched.
- [ ] **Step 3b — the console reads two renamed paths by literal string.**
      `truth.py:118` does
      `yaml.safe_load((pack_dir / "configs/x-road-bus/2.6.yaml").read_text())` —
      that file becomes `once-only-exchange.yaml`, and `truth.py` references it
      by name a further six times in comments that would become lies. `app.py:117`
      loads `manifest.yaml` and derives each member's config path from it, so it
      follows the member-config renames. Neither is caught by `--fast` in a
      useful way: `truth.py` raising at import is caught, a stale comment is not.
      Update both, and re-read `truth.py`'s module docstring end to end — it is
      the console's entire model of the federation and Task 4 Step 5 rewrites it
      again.
- [ ] **Step 4 (D2):** leave one line in `10-Knowledge-Products/KP3-DPI/KP3-build-pack/README.md`
      pointing at `docs/decisions/onboarding-alignment-design.md` for the naming
      convention. **Do not rename KP3's scaffolding.**
- [ ] **Step 5:** `--fast` green. Commit.

---

## Task 4: Delete the profile machinery (D5)

**Files:** `hurl/generate.py`, `hurl/check_scenarios.py`, `hurl/topology.json`, `scripts/lib-stack.sh`, `docker-compose.yml`, `deployment.yaml`, `apps/console/{truth.py,app.py,static/app.js}`, `apps/console/tests/test_truth.py`, `apps/console/tests/fixtures/`, `acceptance/`, `README.md`, `runbook.md`, `PLAN.md`, `docs/production-delta.md`

**Read `2026-08-01-kp2-join-c-own-server-and-unjoin.md` Task 6 first.** It
enumerated this machinery in detail and was correctly gated off. Its file list is
the input to this task; what changes is the *direction* — join-c Task 6 would
have collapsed onto `lite`, this collapses onto `full` minus MoEYS.

- [ ] **Step 1:** `generate.py` — delete `LITE_HOSTED_ON`, the `--profile` flag
      and its validation. `resolve_hosted_on_map(members, profile)` loses its
      parameter and its `if profile == "lite"` branch; the explicit-`hosted_on`
      path it already has does all the work (design decision 3).
- [ ] **Step 2:** `deployment.yaml` — delete the `profile:` key.
      `scripts/lib-stack.sh` — delete the `lite)`/`full)` cases, the `LITE`
      variable, the `[ "${LITE:-0}" != "1" ] && COMPOSE+=(--profile full)` line
      and the profile-drift checks. **`COMPOSE_ALL`'s `--profile full` for
      teardown needs care** — teardown must still see every service; re-derive
      what it should be with the `full` profile gone rather than deleting the
      flag blindly.
- [ ] **Step 3:** `docker-compose.yml` — delete the `profiles: ["full"]` tags on
      `ss-pnia` and `ss-moeys` **and only those**. `profiles: ["demo"]` on the
      console and join-api services **stays**. `ss-moeys` is already gone from
      Task 1. This removes the `depends_on` workaround: `ss-pnia` can now be a
      real dependency of the runner rather than covered by retries — make it
      one, and delete the `hurl/README.md` "Known limits" entry that explains
      the hole.
- [ ] **Step 4:** `hurl/topology.json`'s `"profile"` key. **This is
      KP3/KP4-visible** — check `manifest.yaml`, the console and anything in
      KP3 that reads `topology.json` **before** deleting it, and fold it into
      Task 1's sign-off rather than treating it as internal.
- [ ] **Step 5 — the console, which is larger than one field.** `truth.py`'s
      whole reason for existing is that a member's entrypoint "is only correct
      under `profile: full`" (its module docstring) — with one topology that
      caveat goes and the docstring must go with it or it becomes a lie about
      code that no longer branches. Also: `truth.py`'s `profile` attribute and
      its `deployment.get("profile", "full")` read; `app.py:212`'s health
      endpoint returning `{"status": "ok", "profile": TRUTH.profile}` — drop the
      key rather than return a constant; `test_truth.py`'s
      `test_full_profile_resolves`/`test_lite_profile_resolves` pair collapsing
      to one; and `apps/console/tests/fixtures/{full,lite}/` collapsing to one
      (**keep `inconsistent/`**).
- [ ] **Step 5b — the landmine.** `apps/console/static/app.js:105` does
      `$("#profile-badge").textContent = \`profile: ${TOPOLOGY.profile}\`;`. The
      browser caches `/api/topology` once on load, so a missing field fails at
      **render** time, not request time — it would surface first in front of an
      audience. Remove the badge and its element together, and confirm nothing
      else in `app.js` reads `profile`.
- [ ] **Step 6:** the prose. Remove lite caveats from the acceptance documents
      that carry them (`2.2` is gone; `2.5`, `2.7`, `member.md` remain — under
      their Task 3 names). `acceptance/join-member.md` clause 5's "byte-identical
      to the golden file **for this deployment's profile**" becomes the single
      deployment golden. `README.md` loses its two-profile timings and its
      "develop against lite, run one `--full` under full before closing out"
      guidance — the largest readability win in the wave. `runbook.md`,
      `PLAN.md` §2 and §9, and `production-delta.md`'s lite-vs-full measurement
      sections likewise.
- [ ] **Step 7:** `--fast` green. Commit. **Do not regenerate yet.**

---

## Task 5: Restructure the golden corpus

**Files:** `tests/golden/`, `tests/test_golden.py`, `tests/test_tiers.py`, `tests/test_steps.py`

With no canonical member hosted, cold-deploy hosted **rendering** loses its only
golden. The join API's tests do not cover it — `job.py`'s docstring is explicit
that the job engine differs from what `run-linkup.sh` does with the same
templates ("one invocation per step, not one per run").

- [ ] **Step 1:** `tests/golden/full/` → `tests/golden/deployment/`, minus
      MoEYS. Delete `tests/golden/lite/`.
- [ ] **Step 2:** create `tests/golden/hosted-fixture/` — a fixture **member
      config set** in which one member sets `security_server.hosted_on`
      explicitly, plus its generated tree. No profile flag: this exercises
      `resolve_hosted_on_map()`'s explicit path, which is the same path a joined
      member takes. Never deployed.
- [ ] **Step 3:** `test_golden.py` drops `@parametrize("profile", ...)` and
      instead generates twice — once from `configs/` into `deployment/`, once
      from the fixture config set into `hosted-fixture/`. Same byte-identical
      assertion.
- [ ] **Step 4:** `test_tiers.py` and `test_steps.py` lose their profile
      awareness. `test_tiers.py`'s point — that `check-exposure.sh` reads the
      *rendered* Compose config with `${VAR}` interpolation resolved — survives
      and must keep working with the daemon stopped.
- [ ] **Step 5:** `--fast` green. Commit.

---

## Task 6: Regenerate, prove, and measure

**Files:** `tests/golden/`, `docs/production-delta.md`, `README.md`

- [ ] **Step 1:** regenerate both golden trees. Review the `deployment/` diff
      **by eye against `tests/golden/full/`** — it should differ only by MoEYS's
      removal and the `"profile"` key. Anything else is a Task 1–5 defect
      surfacing late; investigate before accepting.
- [ ] **Step 2:** one `--full` from cold, **including the console smoke pass**.
      Task 4 Step 5 rewrote `truth.py`, the console's entire model of the
      federation, so a green `--fast` proves considerably less here than usual.
- [ ] **Step 3:** a real hosted join and un-join through `apps/join-api`, end to
      end to `ACTIVE, verified: true` and back to `RETIRED`. This is the only
      thing that now exercises hosting live, and it also confirms Wave 1 Task 2's
      unverified `R1_RETRY_BUDGET` claim if an own-server join is included.
- [ ] **Step 4:** **measure** and record: `--fast`, `--live`, `--full` from cold,
      and RAM. The design's ~670s / ~11 GB are estimates extrapolated from two
      measured points — replace them with real figures in
      `production-delta.md` and `README.md`, and correct
      `docs/decisions/topology-profile-decision.md` §5.3's per-plan arithmetic if the
      measurement moves it.
- [ ] **Step 5:** confirm the un-join byte-identity clause passes against the
      single golden. Commit.

---

## Sequencing

Strictly sequential. Task 1 is gated on sign-off; Tasks 2 and 3 depend on Task
1's member set; Task 4 depends on Task 3's filenames; Task 5 depends on Task 4's
generator; Task 6 depends on all of them.

**If Task 1's sign-off does not arrive**, Tasks 2–6 can still run with four
members — the profile removal, the module collapse and the rename are all
independent of MoEYS. Reduce the plan rather than blocking it.

**Exit:** one topology, four Security Servers, three members, one registration
module, capability-based filenames, two golden trees decoupled from deployment,
`--full` green from cold with the console pass, and measured figures replacing
every estimate in the design.
