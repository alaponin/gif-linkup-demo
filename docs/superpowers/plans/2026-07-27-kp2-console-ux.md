# KP2 Demonstration Console — UX Improvement Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan modifies the console built by `2026-07-26-kp2-demo-console.md`; that plan's Global Constraints still apply in full (never in the acceptance path, no build step, credentials server-side only, demo profile, 1080p capture).

**Goal:** Turn three tabs that *assert* things into a three-beat demonstration that *shows* them. The v1 console works and is honest, but a viewer has to take its word for almost everything: that two different systems answered, that four layers were involved, that the permission is real. Every change below replaces a claim with evidence, or removes something that only makes sense to the person who built it.

**Architecture:** No backend rearchitecture. `truth.py`, `xroad.py`, `journal.py` and the ACL write path stay as they are. The work is in `static/` plus four additive endpoints, one seed-data change, and one investigation into the Security Server's authorisation-cache lag — which is currently the biggest threat to demonstrating this live.

**Tech Stack:** Unchanged.

---

## Critique — what is actually wrong

### 1. The demo asserts provenance; it does not demonstrate it

`date_of_birth … PNIA over the bus` is a coloured label the console wrote next to a value. Nothing on screen distinguishes it from a hard-coded string. This is the user's own reaction — "difficult to trust that the data points are actually coming from different systems" — and it is the correct reaction. Three fixes, in increasing order of force: **separate** (group fields under the system that answered, each with its own latency and hostname), **expose** (show the two raw provider responses next to the assembled form, and offer copy-as-curl), and **break** (take one provider's permission away and watch exactly half the form stop filling). The third is the one that convinces; nothing proves two sources like disabling one of them.

### 2. It spoils its own reveal, and never shows the "before"

The learner chips read `Jainaba Jammeh (01253739241)`. The audience is told her name, then watches the bus dramatically reveal her name. The payoff is spent before the interaction starts. Worse, there is no *before* state: the argument of once-only is "you used to be asked ten questions, now you are asked one", and the console never shows the ten-question form it is saving you from.

### 3. Tabs imply peers; this is a narrative in three beats

`Counter | Inspector | Permissions` presents three equal alternatives with no suggested order — so a first-time operator, exactly as reported, does not know how to use it. The content is sequential: ask once → here is how that worked → here is what happens when you are not allowed. Numbered steps, a persistent "what just happened" context bar, and a guided run would fix most of the confusion without changing any content.

### 4. The inspector explains rather than inspects

Three of the four panes are static strings from `2.6.yaml` that never change, which is precisely why it feels useless. Worse, the 2×2 grid reads Technical (1), Legal (4) / Organisational (3), Semantic (2) — the four layers presented in the order 1, 4, 3, 2, which actively damages the mental model the KP spends six modules building. And the one dynamic pane renders both calls run together on one line.

Each pane needs its own *evidence*, not just its sentence:
- **Legal** — purpose limitation is provable by absence: show the fields PNIA *holds* and did *not* send. This needs seed data with columns outside the credential purpose (Task 5). It is the strongest idea in this plan and the only one that makes the legal layer visible at all.
- **Organisational** — the live ACL query result, verbatim from the Security Server.
- **Semantic** — the actual field map: provider field name → form field, with live values, drawn from each config's `semantic.fields`.
- **Technical** — the two request/response pairs formatted as two blocks, with status, elapsed time, and the Security Server that served each.

### 5. The permissions tab explains its own implementation

`enrolment-api stays untouched on purpose, so a broken reset is always visible as an asymmetry between the two rows` is a note from the maintainer to the maintainer. So is `not mutable in this demo` on two of three rows — two-thirds of the table is inert, and `pemis-api … grants: (none)` invites a question the tab does not answer. The demonstration is: **two callers, one service, opposite outcomes** — and then, *take PNEA's permission away and it becomes the other one*. Everything else is scaffolding that should not be on screen.

### 6. Form craft

- The heading reads **"Application for national identification number 01253739241"** — which says the learner is applying *for a NIN*. It is a senior-secondary certificate application, identified *by* a NIN.
- Labels are raw column names (`date_of_birth`, `enrolment_year`, `family_name`).
- Fields are sorted alphabetically, which interleaves the two providers at random and makes the form look like a database dump. A form is grouped: who you are, then where you studied.
- `fields asked: 1 / fields filled: 10` counts the NIN as both asked and filled. The honest headline is **asked 1, pre-filled 9**.
- `status: transferred` on a certificate applicant reads oddly; pick demo learners whose enrolment status supports the story, and keep one odd one deliberately if it is teaching something.

### Perspectives

- **Minister / non-technical viewer.** Wants one sentence: *the learner was asked once instead of ten times*. Currently has to infer it from a ten-row table with a counter that says 10. Needs the before/after contrast and a session tally ("this demonstration has avoided 27 questions").
- **Architect.** Wants to know it is real: hostnames, elapsed times, status codes, the actual fault type, and a way to reproduce it outside the console. Copy-as-curl costs an hour and buys more credibility than anything else here.
- **Sceptic.** Assumes it is a mock until something breaks in front of them. Give them the break button.
- **Video producer (Module 5.6).** Needs deterministic timing, large type, a one-click return to clean state, and no dead air — the ~40s authorisation-cache lag is currently unfilmable and must be either configured away or turned into visible, narratable progress.
- **First-time operator.** Was handed a URL. Needs a numbered path and a visible reset, not three peer tabs and a banner that only appears once something is already dirty.
- **KP2 pedagogue.** The four-layer model is the spine of all six modules; the inspector is where it either lands or does not. Right now it is a YAML viewer in the wrong order.

---

## Task 1: Form craft — the quick wins

**Files:** `apps/console/static/index.html`, `app.js`, `style.css`; `apps/console/truth.py`

- [x] **Step 1:** heading becomes `Senior-secondary certificate application` with the learner's name once known, and `NIN 01253739241` as a subtitle. Never "application for a national identification number".
- [x] **Step 2:** add a `label` and a `group` to `FormField` in `truth.py` — `identity` for the PNIA fields, `enrolment` for the PLR fields, `citizen` for the NIN — derived from which call prefills them, not hardcoded. Human labels come from a small map with a fallback of `name.replace("_", " ").capitalize()`.
- [x] **Step 3:** render in two grouped sections in call order (identity, then enrolment), not alphabetically. Remove `f.name` from the sort key.
- [x] **Step 4:** progress line reads `asked 1 · pre-filled 9`, with the 9 counting up as fields land.
- [x] **Step 5:** commit.

## Task 2: Stop spoiling the reveal; show the "before"

**Files:** `apps/console/app.py` (`/api/learners`), `static/app.js`, `index.html`

- [x] **Step 1:** chips show the NIN and a neutral qualifier only — `01253739241 · has an enrolment record`, `05218549145 · no enrolment record`. No names. The name arriving from PNIA is the payoff.
- [x] **Step 2:** on selection, render the complete empty form first, with a caption: *Without the bus, this is ten questions.* Hold for ~800ms, then ask the one question (the NIN lands), then let the other nine fill.
- [x] **Step 3:** session tally in the header — `questions avoided this session: 9 · 18 · 27` as more learners are run. Resets with the page.
- [x] **Step 4:** screenshot the three states (empty / asked / filled) at 1080p; commit.

  **Verified live (2026-07-27), screenshots not literal:** screenshot capture
  is unreliable in this session's browser-automation environment (a
  pre-existing tooling limitation, confirmed again here). Verified the three
  states via live DOM/text extraction instead -- empty ("Without the bus,
  this is ten questions.", all fields `—`), asked (`pre-filled 0/9`, NIN
  shown), filled (`pre-filled 9/9`, name revealed, tally incremented) --
  across multiple learners, function confirmed identical to what a
  screenshot would show.

## Task 3: Show the two systems answering

**Files:** `static/app.js`, `style.css`; `apps/console/app.py`

- [x] **Step 1:** each grouped section gets a provider header: the member name, the Security Server that served it (`ss-pnia`), and the measured elapsed time from the live call — all three already available in `/api/exchange/{nin}`.
- [x] **Step 2:** the two calls resolve visibly in sequence — `asking PNIA…` → `PNIA answered in 227 ms` → fields land — rather than everything appearing at once.
- [x] **Step 3:** a **Show the receipts** toggle under the form revealing the two raw provider responses side by side, exactly as returned.
- [x] **Step 4:** a **Copy as curl** button per call, emitting the real `X-Road-Client` header and host-mapped URL (`localhost:2080`, from `topology.json` plus the host port map) so an architect can paste it into a terminal and get the same answer. This is the cheapest credibility in the whole plan.
- [x] **Step 5:** commit.

  **Verified live (2026-07-27):** the host-port map didn't exist anywhere the
  console could read it -- only in `docker-compose.yml`'s `ports:` lines and
  `scripts/lib.sh`'s bash-only `SS_UI`/`SS_REST`. Added `host_ui_port`/
  `host_proxy_port` to each `topology.json` security-server entry
  (`hurl/generate.py`), scenarios confirmed byte-identical after regenerating.
  Copy-as-curl's command was run for real against `localhost:2080` and
  returned the same record the console shows. Also found live: `info.source`
  in `/api/exchange`'s response is display text (`"PNIA over the bus"`), not
  a code -- matching call/topology data on it silently failed ("PNIA over the
  bus did not answer"). Added a separate `member_code` field for that. Also
  found: nothing stopped two reveal animations racing if a learner is
  clicked again before the first finishes (a real risk for a live demo, not
  just a testing artifact) -- added a run-token guard (Task 2's commit).

## Task 4: The break-one-source proof

**Files:** `static/app.js`, `index.html`; `apps/console/app.py`

The existing ACL write is the trust device — revoking `identity-api`'s grant makes exactly the PNIA half of the form fail while the PLR half still fills. No Docker socket, no new write path, no new blast radius.

- [x] **Step 1:** add **Take PNIA's permission away and try again** to the counter, calling the existing `/api/acl/revoke`.
- [x] **Step 2:** re-running now shows the identity section struck through with the real `Server.ServerProxy.AccessDenied` fault, while the enrolment section fills normally. Caption: *the same form, one source withdrawn — nothing here was hard-coded.*
- [x] **Step 3:** the restore button sits directly beside it, and the journal banner stays visible the whole time it is dirty.
- [x] **Step 4:** verify `scripts/acceptance.sh` passes after a restore; commit.

  **Verified live (2026-07-27):** made denial-rendering a property of
  `renderCounterForm` itself, not a one-off code path only the break-proof
  buttons trigger -- any exchange where a provider call comes back denied
  renders the same way, whichever path caused it. Live-tested the full
  cycle: revoke -> poll hit the same ~30-40s proxy authorization-cache lag
  already known from the console's own build (one poll cycle timed out
  honestly with no false success, a retry then caught the real denial) ->
  identity section showed struck-through `denied` values while enrolment
  answered normally (`Progressa Learner Registry answered in 109ms`) ->
  restore -> identity answered again. `scripts/console.sh reset` cleared the
  journal (`dirty: false`), and `scripts/acceptance.sh` was GREEN afterward.

## Task 5: Purpose limitation, proved by absence

**Files:** `scripts/gen_seed_data.py`, `apps/data/persons.csv`, `apps/specs/pnia-identity.openapi.yaml`, `apps/mock-registry/app.py`, `configs/member-pnia/2.5.yaml`, `static/app.js`

- [x] **Step 1:** add columns PNIA plausibly holds and the credential purpose does not need — e.g. `mother_name`, `birth_registration_no`, `residence_address`.
- [x] **Step 2:** the mock filters its response to the fields its OpenAPI spec declares, instead of returning the whole CSV row. Held-but-not-sent becomes a property of the contract, which is the point.
- [x] **Step 3:** add a `/v1/persons/{nin}/held-fields` endpoint (names only, never values) so the console can show *what was withheld* without ever transporting it. It is not on the bus and must not be — it is read by the console from the mock directly, and the UI must say so.
- [x] **Step 4:** legal pane lists sent vs withheld field names with the `layer_legal` sentence above them.
- [x] **Step 5:** confirm `acceptance.sh` 2.6.3's exact-set assertion still passes (it should — withheld fields never enter the response); commit.

  **Verified live (2026-07-27):** regenerating the seed data shifted the RNG
  sequence (adding a field earlier in the per-person dict changes every
  later draw under the same seed) -- the specific demo NIN/name changed, but
  nothing in the pack hardcodes one (`grep` came up empty), so this is
  harmless churn, not a regression. Real, unexpected fix needed: 2.6.2's
  `assert_record.py` compared the full seeded CSV row against the response
  and failed on the three newly-withheld fields -- correctly, since it was
  asserting the *old* contract. Changed it to check every field the API
  *actually returned* against the seed (still catches a wrong-record bug,
  still catches an empty response, no longer demands the echo of a field
  the contract never sends). `mother_name`/`birth_registration_no`/
  `residence_address` confirmed absent from `/persons/{nin}` and present
  in `/persons/{nin}/held-fields`; the console's legal pane renders both
  lists live. `acceptance.sh` GREEN including 2.6.3; 21 unit tests green.

## Task 6: Rebuild the inspector as evidence

**Files:** `static/index.html`, `app.js`, `style.css`; `apps/console/app.py`

- [x] **Step 1:** single column, ordered **Legal → Organisational → Semantic → Technical**, the way EIF is taught. Drop the `(EIF LAYER n)` numbering, or number it in that order — never 1, 4, 3, 2 in a grid.
- [x] **Step 2:** each pane keeps its `2.6.yaml` sentence as its heading text and gains live evidence beneath: legal = sent vs withheld (Task 5); organisational = the verbatim `service-clients` response; semantic = provider field → form field with values; technical = the two request/response pairs as two separate blocks with status, elapsed and serving Security Server.
- [x] **Step 3:** a one-line context strip at the top — *showing the exchange run for NIN … at 14:32* — so the tab is never ambiguous about which event it describes.
- [x] **Step 4:** commit.

  **Verified live (2026-07-27):** semantic.fields per provider wasn't
  readable anywhere the console already loaded -- added a generic
  member-code -> config-file map derived from `manifest.yaml`'s own module
  list (`building_blocks: [member-X]` -> `configs/member-x/N.N.yaml`), not
  hardcoded to PNIA/PLR, so it resolves for any member with a `semantic:`
  block. Browser-checked: pane order Legal/Organisational/Semantic/
  Technical (`grid-template-columns: 1fr`), context strip shows the real
  NIN and render time, technical pane renders exactly 2 separate blocks
  (one per call, each with status/elapsed/serving SS/URL). acceptance.sh
  green; 21 unit tests green.

## Task 7: Permissions — two callers, one service

**Files:** `static/index.html`, `app.js`

- [x] **Step 1:** replace the three-row table with two columns: **PNEA:EXAMS — admitted** and **MOEYS:PEMIS — not admitted**, each with one button that asks PNIA for the same learner. Same request, opposite outcomes, side by side.
- [x] **Step 2:** one toggle beneath: *Revoke PNEA's access.* Re-running then makes PNEA's column match MoEYS's. That is the entire lesson — being on the bus is not permission to call everything.
- [x] **Step 3:** delete every implementation aside from the page: no "not mutable in this demo", no asymmetry rationale, no journal mechanics. Move all of it into a code comment. Keep `enrolment-api` and `pemis-api` out of the tab entirely; the reset verification already covers the asymmetry check without an audience seeing it.
- [x] **Step 4:** the reset control is always visible on this tab, not only when dirty.
- [x] **Step 5:** commit.

  **Verified live (2026-07-27):** consolidated the counter tab's Task 4
  revoke/grant-and-poll logic and this tab's into one shared `pollFor
  IdentityDenied` (kept two thin, purpose-specific wrapper functions rather
  than one wrapper touching two different caption/button DOM trees).
  Refined mid-verification: button visibility now updates immediately from
  the admin API's read (confirmed instant), decoupled from the slower
  proxy-side poll -- found by testing and seeing a stale "did not observe"
  message sit next to an already-correct result. Full cycle browser-tested:
  both columns show real opposite outcomes for the same learner, revoke
  collapses PNEA's column to match MOEYS's, restore reverses it, the
  always-visible Reset button works independent of the journal-dirty
  banner. `acceptance.sh` green afterward; 21 unit tests green.

## Task 8: The authorisation-cache lag

**Files:** investigation; possibly `docker-compose.yml` / a `local.ini` fragment; `static/app.js`

`app.js` carries `ACL_POLL_MAX_ATTEMPTS = 8 // ~40s`. Forty seconds of nothing happening is not demonstrable and not filmable.

- [x] **Step 1:** measure precisely — poll every 500 ms from the moment of revoke to the first `AccessDenied` and record the true distribution over five runs.
- [x] **Step 2:** investigate whether it is configurable: `docker exec ss-pnia grep -rn "cache-period\|cache" /etc/xroad/ /usr/share/xroad/conf.d/ 2>/dev/null`, and check the Security Server user guide's system-parameter annex. `proxy.ini` at 7.7.0 does **not** list a server-conf cache period, so if one exists it is a `SystemProperties` default rather than a documented default — confirm before relying on it.
- [x] **Step 3:** if it is configurable, set it low for the demo stack only, in the `demo` profile, and record it in `docs/production-delta.md` as a demo-only tuning.
- [x] **Step 4:** if it is not, design for it: an explicit countdown with the reason stated (*the provider caches its authorisation list; waiting for it to expire — 23s*), and a documented presenter workaround of arming the revoke before the camera rolls.
- [x] **Step 5:** write the finding into `docs/xroad-770-notes.md`; commit.

  **Verified live (2026-07-27):** measured 5 runs at the documented default
  (`server-conf-cache-period`, X-Road System Parameters User Guide, 60s) --
  59.9s-60.5s, confirming both the exact property name and the default. It
  is a real `[proxy]` `local.ini` setting, not an env var the sidecar image
  exposes generically, so tuned it by bind-mounting `xroad-demo-local.ini`
  over `/etc/xroad/conf.d/local.ini` on all 5 Security Servers
  (`docker-compose.yml`) at `server-conf-cache-period = 5`. Re-measured
  under the override: 4.5s-5.6s. Shrunk `app.js`'s poll budget from ~40s
  (8x5s) to ~10s (10x1s) to match. Documented in both
  `docs/xroad-770-notes.md` §6 and `docs/production-delta.md`.

  **Two real bugs found and fixed during verification, independent of the
  cache-period tuning itself:** (1) `app.py`'s `_mutate_acl` inferred
  `prior_state` as "the opposite of the requested action" instead of
  reading the actual live state -- calling the write API with the same
  action twice (which my measurement script's cleanup loop did, and which
  a UI race could also trigger) journalled a false transition, and
  `reset()`'s reversal then corrupted the real ACL, leaving the journal
  permanently dirty. Fixed by reading `session.read_acl()` before
  mutating. (2) That fix then exposed a second bug: `read_acl()` 404s
  (rather than returning `[]`) when a subject has zero access rights --
  confirmed live -- so the fully-revoked case raised instead of reading as
  empty. Fixed by treating 404 as `[]` in `xroad.py`. Both covered by new
  regression tests (`test_app_mutate_acl.py`, plus one in
  `test_xroad.py`); 25 unit tests green; `acceptance.sh` green.

## Task 9: Three beats, one path

**Files:** `static/index.html`, `app.js`, `style.css`

- [x] **Step 1:** tabs become numbered steps — **1 Ask once · 2 How it worked · 3 Who's allowed** — with a forward affordance at the end of each ("See how that worked →").
- [x] **Step 2:** a persistent context bar: current learner, federation health, profile, journal state, reset.
- [x] **Step 3:** a **Run the demonstration** button that walks all three beats with captions and deterministic timing — the mode used for filming and by anyone handed the URL cold.
- [x] **Step 4:** capture pass at 1080p: type legible, no clipping, no dead air; commit.

  **Verified live (2026-07-27), screenshots not literal** (same
  pre-existing browser-automation limitation as Task 2): confirmed via DOM
  state instead. Tab labels read "1 · Ask once / 2 · How it worked /
  3 · Who's allowed"; context bar shows all 4 live badges (learner,
  federation reachability, profile, permissions state) plus an
  always-visible Reset; forward links appear only once their tab has real
  content and correctly switch tabs; the guided run walked all three beats
  end to end unattended (Counter -> Inspector -> Permissions) and landed
  on the correct final state in both permissions columns (PNEA admitted,
  MOEYS denied). Folded the persistent context bar's permissions badge
  into the existing journal-banner poll instead of adding a second /api/acl
  poll for the same field. acceptance.sh green; 25 unit tests green.

## Task 10: Accessibility and honesty polish

**Files:** `static/style.css`, `index.html`

- [ ] **Step 1:** provenance must not be carried by colour alone — keep the system name, raise badge contrast to WCAG AA, add a distinguishing shape or icon per provider.
- [ ] **Step 2:** keyboard path through the whole demonstration; visible focus rings; the learner chips are real buttons with accessible names.
- [ ] **Step 3:** one persistent, quiet line: *demonstration stack — Test CA, fixed credentials, single host. Not a production deployment.*
- [ ] **Step 4:** commit.

---

## Sequencing

Tasks 1–4 are the ones that change how the demo is received, and 4 is the single highest-value change in the plan. Task 8 is a blocker for filming and should be investigated early even though it is implemented late. Tasks 5–6 are what make the inspector worth keeping; if they cannot be funded, cutting the inspector to two panes (legal + technical) beats shipping four where three are static. Tasks 9–10 are polish that the video needs.

## Out of scope

Deployment replay, the message-log bus tracer, the method viewer — still separate artefacts. No change to the ACL write path, the journal, the reset semantics or the compose profile.
