# KP2 console — implementation plan

**Date:** 9 August 2026
**Addresses:** the console items of `KP2_Learning_Integration_Review_2026-08-09.md` §3.1
**Scope:** `apps/console/` (app.py, static/index.html, static/app.js), `docker-compose.yml` (console service only), `runbook.md` (one wording fix). Nothing here touches the acceptance path, `apps/join-api/`'s own routes, or the deploy sequence.
**House rules honoured:** the console stays "a demo asset, not a module, never in the acceptance path" (README); the journal/`_MUTATE_LOCK` discipline is untouched; no token ever reaches the browser.

**Comment discipline (applies to every task below).** Any comment this plan's work leaves in the code is terse and generic: what the line does and, at most, one line of why. No references to WIP plans, dated reviews, decision records, task ids from this plan, or "found live on <date>" narrations — the existing code carries many of these (e.g. app.js's "UX plan: design spec S2.7", "review finding, 2026-08-02"), and this plan's changes must not add more. Anything that genuinely needs recording — a design decision, a defect narrative, a rejected alternative — goes into a separate file (`docs/decisions/` or the working record outside the pack), and the code comment, if one is needed at all, says only what the code does. Concretely for this plan: C1's compose-interpolation caveat, C2's stale-vs-live catalogue semantics decision, and C6's role-asymmetry argument are documented in their task's own decision note or the runbook — never as block comments at the change site.

---

## 0. Current state, as verified in the code

- `app.py:40` reads `JOIN_OPERATOR_TOKEN = os.environ["KP2_JOIN_OPERATOR_TOKEN"]` at import — a missing key kills the whole console process. Upstream of that, `docker-compose.yml` interpolates `${KP2_JOIN_OPERATOR_TOKEN:?…}` for the *whole file*, so a stale `.env` stops every service, not just the console (README documents this; `gen-secrets.sh:52–71` already self-heals by appending the two keys).
- Join-api **unreachability** is already handled gracefully: `_proxy_join()` (app.py:498–514) returns `{"error": "join-api unreachable: …"}` and the tab renders it — the review's "degrade gracefully" item is therefore only about the env var, not the network path.
- The console proxies exactly four join-api routes: `GET /requests`, `approve`, `resume`, `reject` (app.py:517–546). **There is no submit proxy and no submit form** — `runbook.md`'s "drive the whole flow from the console's 4 · Join a member tab … instead of curl" overstates what the tab can do. Submission is curl-only today.
- join-api's `require_applicant` (apps/join-api/app.py:120–129) accepts **either** token — the operator token the console already holds satisfies `GET /catalogue`. A catalogue view needs no new secret and no join-api change.
- The console container mounts the pack at `PACK_DIR=/pack` (app.py:28) and already parses `manifest.yaml` at startup (app.py:131) — it can read `onboarding/catalogue.yaml` directly, with no join-api dependency at all.
- The ACTIVE-state renderer (app.js:734–747) shows `verified: false` as the generic "the reachability check has not passed yet". The record's `payload.security_server.own_server` is available in the same scope (schema.py:59), so the known own-server defect (runbook: retry budget spent by the propagation wait; never flips afterwards) can be branched on client-side with no API change. The consume-only case is already handled separately via `record.note`.
- The inspector cites `configs/x-road-bus/once-only-exchange.yaml` in prose only (index.html:67); layer content comes from `TRUTH.layers` (app.py:325). `out/application-<nin>.json` is written by `acceptance.sh`, and the console computes the same shape live (app.py:280–329) but never links the file.
- Tabs are data-driven (`initTabs`/`switchToTab`, app.js:45–62): a new tab is one `<button data-tab>` + one `<section>` + one loader function.

## 1. Tasks

### C1 — Console survives a pre-join-b `.env` (env hardening)

1. app.py: replace the two import-time reads with lazy access —
   `JOIN_OPERATOR_TOKEN = os.environ.get("KP2_JOIN_OPERATOR_TOKEN", "")`; in `_join_api()`, if the token is empty, short-circuit to the same error-dict shape `_proxy_join` already returns: `{"error": "KP2_JOIN_OPERATOR_TOKEN not set -- re-run scripts/gen-secrets.sh (no flags), then scripts/console.sh up"}`. Tabs 1–3 keep working; tab 4 renders the remedy.
2. docker-compose.yml, **console service only**: change the interpolation for the two join tokens from `${VAR:?…}` to `${VAR:-}`. The join-api service keeps `:?` — its guard is correct and load-bearing (`_required_token`, join-api app.py:61–78). Note the compose-file-wide interpolation caveat: `:?` anywhere still breaks everything, so this only helps once the console's own references are the `:-` form and no `:?` reference to those keys remains outside `join-api`'s stanza. Verify with `docker compose config` against a token-less `.env`.
3. Add a console test (apps/console/tests): import app with the env var absent; `GET /api/join/requests` returns the remedy error, `GET /api/health` returns ok.

**Verify:** `--fast` (unit tests) + one manual `--live`-adjacent check: `console.sh up` against a `.env` with the keys removed.
**Risk:** low. The only behavioural change is "crash → informative tab".

### C2 — Catalogue view: "What's on this bus"

1. app.py: new endpoint `GET /api/catalogue` (guarded by `_require_console_origin` like every read that reaches beyond the page — it costs nothing and keeps one rule). Implementation: read `PACK_DIR/onboarding/catalogue.yaml` (the generated artefact — display the register's own output rather than re-deriving it), return it plus the `publication_is_not_permission` string verbatim. **No join-api dependency**: the view works with `join.sh down`, which is its normal state in a Tier-1 demo. Optionally include `"api_form": "GET :8091/catalogue (applicant token)"` as a string for the UI to show the API-shaped alternative.
2. index.html: fifth tab `5 · What's on the bus`; panel with: the disclaimer banner (verbatim from the YAML), one card per service (service id, provider, semantic entity + anchor, tier-1 pattern, lawful basis, ACL subjects, links rendered as file paths `onboarding/<key>/04-catalogue/<code>.md` — paths, not hyperlinks: the console must not become a file server for the pack tree).
3. app.js: loader on tab switch; re-read on each activation (the file changes when a join/un-join regenerates it — cheap, and matches `GET /catalogue`'s own re-read-per-call semantics).
4. Cross-link: the permissions tab's "on the bus, not granted this service" caption gains one line pointing at the new tab ("what *is* published: tab 5 — publication is not permission, and these two tabs are the pair that proves it"). That sentence is the teaching payload of the whole feature.

**Verify:** `--fast` (a unit test: endpoint returns every service in `catalogue.yaml`, disclaimer present) + console smoke in the next `--full`.
**Risk:** low; read-only. One design decision recorded: reading the YAML file rather than proxying `GET /catalogue` means a member joined while `render-onboarding.sh` regeneration hasn't run shows stale — which is *the documented semantics of the file* (runbook, "The service catalogue"), so the tab states its source ("reading `onboarding/catalogue.yaml`, regenerated by joins/un-joins").

### C3 — Own-server `verified: false` honesty in the join tab

app.js, ACTIVE branch (app.js:734–747): when `!record.verified && !record.note && payload.security_server && payload.security_server.own_server`, replace the generic pending line with the runbook's own explanation, compressed: *"verified: false — known demo defect for own-server joins, not a broken join: the bring-up's propagation wait spends the retry budget before the reachability check runs. `scripts/acceptance.sh`'s 2.7.r1 check a minute later is the real answer; this flag never flips afterwards."* Hosted joins keep the current generic line (they reach `verified: true`; a false there is genuinely pending).

**Verify:** review + the existing join-tab test fixtures extended with one own-server ACTIVE record.
**Risk:** trivial. Pure presentation; the record already carries every field needed.

### C4 — Link the tangible artefact from the counter tab

1. app.py: extend `get_exchange`'s response with `"artifact_hint": "out/application-<nin>.json"` *only if* the file exists on disk for that NIN (the console mounts `OUT_DIR`, app.py:29 — check `(OUT_DIR / f"application-{nin}.json").exists()`). The console must not write it — writing is `acceptance.sh`'s act, and the console writing pack outputs would blur the "never in the acceptance path" contract.
2. app.js: after `renderReceipts`, if `artifact_hint` present, render one line: *"This exchange's assembled application, with per-field provenance, is on disk at `out/application-<nin>.json` — the object the M5.6 video ends on."* If absent: *"Run `scripts/acceptance.sh` to write this exchange to disk as `out/application-<nin>.json`."*

**Verify:** review; covered by console smoke in `--full` (which runs acceptance before console).
**Risk:** trivial.

### C5 — Inspector deep-links to the artefacts

app.py already serves `TRUTH.layers` (sourced from `once-only-exchange.yaml`). Extend each layer pane (app.js `renderInspector`, ~447–530) with a "where this lives" footer per layer, as file paths + one-line quotes served from a new small `TRUTH` field: legal → the provider's `lawful_basis` string (already in `catalogue.yaml`; join at load time in `truth.py`), semantic → `configs/semantic/semantic-map.yaml` entity/anchor for each provider, organisational → the ACL subjects from the member config (`_CONFIG_BY_MEMBER` already maps member→config, app.py:132–146), technical → the r1 URL template already shown in receipts. Implementation lives in `truth.py` (one loader, startup-time, consistent with "loaded once, restart on redeploy", app.py:107–110).

**Verify:** `--fast` (extend `test_console_config_map.py` — it already tests the manifest-derived map) + review.
**Risk:** low-medium — touches `truth.py`'s load path; keep additions additive and optional-keyed so a pack without `configs/semantic/` still loads.

### C6 — Submission: close the runbook gap, in one of two ways (decision needed)

- **(a) Minimal (recommended for now):** fix `runbook.md`'s sentence to what the code does: the tab *reviews, approves, rejects, resumes and watches* a join; submission is `POST /requests` with the applicant token (curl). Add a "copy as curl" button on the join tab's empty state that emits the full submit command with a reference payload skeleton — the token stays in `.env`, the command references `$KP2_JOIN_APPLICANT_TOKEN` unexpanded, exactly like the runbook's own examples.
- **(b) Full: an applicant submit form** in the tab (textarea accepting the JSON payload, proxied server-side with the applicant token, same pattern as the operator proxy). Pedagogically attractive (both roles visible in one screen — the asymmetry join-api's docstring calls "the teaching point") but it puts both credentials in one UI and makes the console the applicant *and* the operator, which flattens exactly that asymmetry. If chosen: label the two panels by role, loudly.

**Verify:** review (a) / `--fast` UI tests (b).
**Risk:** (a) none; (b) medium — role-conflation is a teaching regression if the labelling is weak.

## 2. Sequencing and cost

C1 first (it de-risks every demo), then C2 (the biggest learner win), C3+C4 together (both are small join/counter-tab patches), C5, C6 last (its (a) form can ship any time; (b) needs the C2/C3 dust settled). Everything is inside `apps/console/` + one compose stanza; nothing requires a redeploy of the federation — `console.sh up --build` picks all of it up.

## 3. Reviewed against the code — corrections to the 9 Aug review

1. **"Degrade gracefully when join-api is down" was half-wrong:** unreachability already degrades (`_proxy_join`, app.py:498–514). The real fragility is import-time env reads plus compose-wide `${VAR:?}` interpolation — C1 as specified is the accurate fix.
2. **The catalogue tab needs no applicant token and no join-api at all** — `require_applicant` accepts the operator token anyway (join-api app.py:124), and better still the console can read the generated `catalogue.yaml` directly from `/pack`. The review's phrasing ("the console already proxies join-api the same way") suggested a proxy; the file-read design is simpler and survives `join.sh down`.
3. **New finding:** `runbook.md`'s "drive the whole flow from the console tab instead of curl" is not true of the code — no submit path exists (app.py:517–546 is the complete proxy surface). C6 resolves the discrepancy in whichever direction is chosen.
4. Confirmed as stated: the own-server `verified: false` generic message (app.js:746), the missing artefact link, and the inspector's prose-only citation (index.html:67).
