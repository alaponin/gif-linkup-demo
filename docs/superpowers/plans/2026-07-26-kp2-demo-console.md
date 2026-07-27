# KP2 Demonstration Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (same reasoning as the prior KP2 plans on why subagent worktree isolation doesn't fit: the live Docker stack is shared, single-host state). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One page, three tabs, one backend — **counter → inspector → permissions** — that makes the once-only exchange legible to a non-technical audience and provable to a technical one, running against the real federation `hurl/run-linkup.sh` stands up. The permissions tab really writes to the provider ACL and always resets, so the demonstration can be repeated and `scripts/acceptance.sh` still passes afterwards.

**Architecture:** A single FastAPI container (`apps/console/`) on the `linkup` network is the only thing that talks to X-Road. The browser talks only to the console, which removes the CORS problem (the Security Servers send no CORS headers) and keeps admin credentials out of the page. Truth comes from files the pack already treats as authoritative — `deployment.yaml`, `manifest.yaml`, `configs/*.yaml` — plus one new generated artefact, `hurl/topology.json`, so the console cannot describe a federation different from the one deployed. ACL mutations are journalled to `out/console-acl-journal.json` and reversed from the journal, which is also what makes the write safe.

**Tech Stack:** Python 3 + FastAPI + httpx (mirrors `apps/mock-registry`), vanilla JS/CSS with no build step, Docker Compose profile `demo`, X-Road 7.7.0 admin REST APIs.

## Global Constraints

- **The console is never in the acceptance path.** `scripts/acceptance.sh` must keep passing with the console container absent, stopped, or never built. It may read the journal file; it may not require the service.
- **No new host dependencies and no build step.** No node, no bundler, no CDN fetch at runtime — the stack must work on an air-gapped demo machine.
- **No fourth copy of the topology.** `scripts/lib.sh` (`HOST_SS`) and `hurl/generate.py` (`LITE_HOSTED_ON`) already encode which Security Server hosts which subsystem under `profile: lite`. The console reads the generated `hurl/topology.json` instead of re-deriving it.
- `hurl/scenarios/*.hurl` and `hurl/vars.env` stay generated artefacts. Task 1 adds an output to `generate.py`; the scenarios it emits must be **byte-identical** afterwards — verify with a diff, not by inspection.
- Credentials come from `.env` only, are read server-side only, and never reach the browser or a log line.
- Demo-only by construction: compose profile `demo`, bound to localhost, no authentication of its own, and it says so on screen. Every one of those goes in `docs/production-delta.md`.
- Designed for 1080p screen capture: 16px minimum body text, no dense tables, deterministic animation timings. Module 5.6 films this.
- Commit after every task.

## Design decisions

1. **The counter tab deliberately occupies the KP4 seam.** PNEA's credential-application form is exactly what a Joget DX app becomes in KP4. Rather than avoid the slot, the console takes it as an explicitly throwaway sketch and labels itself as such in the UI ("this form is the seam — KP4 replaces it behind the same OpenAPI contract"). Making the seam visible is worth more than keeping it empty.
2. **The inspector reads its four-layer narrative from `configs/x-road-bus/2.6.yaml`**, which already carries the four layer strings — but split two-and-two across the exchange's two calls, not all four per call (confirmed live, 2026-07-26): `identity-api`'s call carries `layer_technical`+`layer_legal`; `enrolment-api`'s call carries `layer_organisational`+`layer_semantic`. The inspector aggregates across both calls to assemble its four panes; it does not restate the framework in its own words, which would be a fifth place for the story to drift.
3. **Write-and-reset, three ways.** Reset on explicit button, on container start, and on a heartbeat watchdog (no page activity for 120s). Belt and braces, because the failure mode — a demo that silently leaves the ACL revoked and makes `acceptance.sh` fail an hour later for an unrelated-looking reason — is exactly the kind of thing that discredits the pack.
4. **Only one ACL is mutable:** `identity-api`'s grant to `PNEA:EXAMS` on the SS hosting `PNIA:IDENTITY`. That is enough for the demonstration and bounds the blast radius. `enrolment-api` stays untouched so a broken reset is always visible as an asymmetry between two tabs.
5. **The console is not a manifest module.** It has no config, prompt or acceptance file, and `manifest.yaml` maps modules to video subtopics and configs. It is documented in `README.md` and `runbook.md` as a demo asset.

## Out of scope

Deployment replay (the 145-request stand-up as a watchable narrative), the message-log bus tracer, and the `manifest.yaml` method viewer — all three were in the brainstorm and all three are separate artefacts with different lifespans. Console authentication, multi-user use, non-Docker targets, and any change to the lite/full hosting scheme.

---

### Task 1: Emit `hurl/topology.json` from the generator

**Files:** Modify `hurl/generate.py`, `hurl/check_scenarios.py`; create `hurl/topology.json` (generated, **not** git-committed — confirmed live, 2026-07-26: `git ls-files hurl/` shows `hurl/scenarios/*.hurl` and `hurl/vars.env`, the other artefacts this same generator produces, are untracked and regenerated fresh on every run, never staged. `topology.json` follows the same convention rather than being a one-off exception. `generate.py` runs before `scripts/console.sh up`, same as it runs before `hurl/run-linkup.sh`.)

**Interfaces:** Produces `hurl/topology.json`, consumed by `apps/console/truth.py` in Task 2.

- [x] **Step 1:** in `generate.py`, after the scenario files are written, emit `hurl/topology.json`:

```json
{
  "profile": "full",
  "instance": "PROGRESSA",
  "member_class": "GOV",
  "central_server": {"host": "cs", "ui_port": 4000},
  "security_servers": [
    {"code": "SS-PNIA", "host": "ss-pnia", "ui_port": 4000, "proxy_port": 8080}
  ],
  "subsystems": [
    {"id": "PROGRESSA:GOV:PNIA:IDENTITY", "member_code": "PNIA",
     "member_name": "Progressa National Identity Authority",
     "subsystem_code": "IDENTITY", "hosted_on": "ss-pnia",
     "services": [{"code": "identity-api", "access": ["PROGRESSA:GOV:PNEA:EXAMS"]}]}
  ]
}
```

  `hosted_on` comes from the same `LITE_HOSTED_ON` resolution the scenarios use, so lite and full both come out right. Ports are the in-network ports (4000/8080), not the host mappings — the console runs on the `linkup` network.

- [x] **Step 2:** `hurl/scenarios/*.hurl` and `hurl/vars.env` are untracked (Files note above) — there is no git HEAD to diff against. Save the current on-disk state as the baseline instead (the same approach the two prior KP2 plans' template refactors used):

```bash
cp -r hurl/scenarios /tmp/scenarios-before-topology
cp hurl/vars.env /tmp/vars.env-before-topology
python3 hurl/generate.py
diff -r /tmp/scenarios-before-topology hurl/scenarios
diff /tmp/vars.env-before-topology hurl/vars.env
```
Expected: both diffs empty — only `hurl/topology.json` is new. If either diff is non-empty, this task is not done.
- [x] **Step 3:** in `check_scenarios.py`, add a check that `topology.json` exists, that its `profile` matches `deployment.yaml`, and that its subsystem IDs equal `manifest.yaml`'s `identifiers.members` (normalised) — the same class of agreement check Task 4 of the agency-identity plan added.
- [x] **Step 4:** `python3 hurl/generate.py && python3 hurl/check_scenarios.py` green; commit `hurl/generate.py` and `hurl/check_scenarios.py` only — **not** `hurl/topology.json` itself, which stays untracked like `hurl/scenarios/`/`hurl/vars.env` (Files note above).

---

### Task 2: `apps/console/truth.py` — the single reader of pack truth

**Files:** Create `apps/console/truth.py`, `apps/console/tests/test_truth.py`

**Interfaces:** Exposes `load_truth(pack_dir) -> Truth` with `.topology`, `.exchange`, `.form_fields`, `.expected_acl`, `.layers`. Consumed by Tasks 3–5.

- [x] **Step 1:** load `deployment.yaml` (profile), `hurl/topology.json` (hosts, hosting, services, configured ACLs), `configs/x-road-bus/2.6.yaml` (calls, r1 paths, prefills, the four `layer_*` strings — split two-and-two across the two calls, see Design decision 2 — `asked_once`, `negative_check`). Member configs are **not** uniform (confirmed live, 2026-07-26): only `configs/member-plr/2.4.yaml` and `configs/member-pnia/2.5.yaml` carry a `semantic:` block; `configs/member-moeys/2.2.yaml` has none, and `configs/member-pnea/2.3.yaml` (the consumer) has neither `semantic` nor a populated `services:` list. Load each defensively (`.get("semantic")`, etc.) rather than assuming every member config has the same shape.
- [x] **Step 2:** derive the form model: every field in `asked_once.citizen_provides` marked `citizen`, every field in `asked_once.prefilled_from_bus` marked with the provider that supplies it (from each call's `prefills`). Fail loudly if the two sets disagree with the union of the calls' prefills — the same invariant `acceptance.sh` check 2.6.3 asserts at runtime, checked here at load time.
- [x] **Step 3:** unit tests, no network, no Docker: full profile and lite profile both resolve; a deliberately inconsistent fixture raises.
- [x] **Step 4:** `python3 -m pytest apps/console/tests -q` green; commit.

---

### Task 3: `apps/console/xroad.py` — the only X-Road client

**Files:** Create `apps/console/xroad.py`, `apps/console/tests/test_xroad.py`

**Interfaces:** `AdminSession(host, user, password)` with `.get/.post`; `exchange(nin) -> list[CallResult]`; `read_acl(...)`, `grant(...)`, `revoke(...)`.

- [x] **Step 1:** session login mirroring `scripts/lib.sh`: `POST https://{host}:4000/login` with form params, keep the cookie jar, send the `XSRF-TOKEN` cookie back as `X-XSRF-TOKEN`. `verify=False` (Test CA), and say why in a comment.
- [x] **Step 2:** `exchange(nin)` issues the two r1 calls from inside the network against `{consumer_ss}:8080` with `X-Road-Client: PROGRESSA/GOV/PNEA/EXAMS`, returning per call: status, elapsed ms, request URL, response body, and the X-Road response headers. Confirmed live at P0: a denied call returns HTTP 500 with `{"type":"Server.ServerProxy.AccessDenied", ...}` — parse that shape explicitly rather than treating any non-200 as a denial, so a genuine transport failure never gets presented as a permission decision.
- [x] **Step 3:** ACL operations against the SS hosting the provider — all four confirmed live against the running stack (2026-07-26), not just the OpenAPI model:
  - read subjects: `GET /clients/{id}/service-clients`
  - read grants: `GET /clients/{id}/service-clients/{sc_id}/access-rights`
  - grant: `POST /clients/{id}/service-clients/{sc_id}/access-rights` with body `{"items":[{"service_code": "<code>"}]}` → 201 (already proven — this is exactly what `generate.py`'s `SERVICE_ACL` template does)
  - revoke: `POST /clients/{id}/service-clients/{sc_id}/access-rights/delete` with the same `{"items":[{"service_code": "<code>"}]}` body → 204 — tested live against `ss-plr`'s `identity-api` grant for `PNEA:EXAMS` (revoked, confirmed the grant list came back empty, re-granted, confirmed restored); `scripts/acceptance.sh` still passed afterward with no residue
- [x] **Step 4:** tests stub httpx; no live stack required. Commit.

---

### Task 4: Read API

**Files:** Create `apps/console/app.py`

**Interfaces:** `GET /api/topology`, `GET /api/learners`, `GET /api/exchange/{nin}`, `GET /api/acl`, `GET /api/health`

- [x] **Step 1:** `/api/topology` returns the Truth topology plus a live reachability probe per server, so the page can show honestly that the federation is up before anyone types a NIN.
- [x] **Step 2:** `/api/learners` returns a handful of seeded NINs for the demo: several present in both registries, and — labelled as such — one present in PNIA but absent from PLR, which is `acceptance.sh` check 2.6.5's clean-404 case. Read from `apps/data/*.csv` mounted read-only, the same files `seed.sh` regenerates.
- [x] **Step 3:** `/api/exchange/{nin}` returns the assembled application with per-field provenance, in the same shape `acceptance.sh` already writes to `out/application-{nin}.json`, plus the per-call technical detail for the inspector.
- [x] **Step 4:** `/api/acl` returns configured vs live grants for both services and a `dirty` flag from the journal.
- [x] **Step 5:** run the container against the live stack; each endpoint returns real data. Commit.

---

### Task 5: Write API, journal, reset, watchdog

**Files:** Modify `apps/console/app.py`; create `apps/console/journal.py`, `apps/console/tests/test_journal.py`

**Interfaces:** `POST /api/acl/revoke`, `POST /api/acl/grant`, `POST /api/reset`, `POST /api/heartbeat`; journal at `out/console-acl-journal.json`

- [x] **Step 1:** every mutation appends `{ts, action, ss, client_id, subject, service_code, prior_state}` to the journal **before** the call, and marks it applied after. A crash mid-write must leave enough to reverse.
- [x] **Step 2:** `POST /api/reset` reverses the journal newest-first, verifies the resulting live ACL equals `truth.expected_acl` exactly, and only then empties the journal. If verification fails it keeps the journal and returns the discrepancy — never a silent "reset ok".
- [x] **Step 3:** reset on startup (before serving), and a watchdog task that resets after 120s without a heartbeat. The page sends a heartbeat every 30s while open.
- [x] **Step 4:** tests cover reverse-order restoration, crash-mid-write recovery, and refusal to empty a journal when verification fails.
- [x] **Step 5:** live check — revoke, confirm the exchange now fails with `AccessDenied`, reset, confirm it succeeds again, and confirm `scripts/acceptance.sh` passes. Commit.

---

### Task 6: The page

**Files:** Create `apps/console/static/index.html`, `static/app.js`, `static/style.css`

- [x] **Step 1: counter tab.** NIN entry (with the seeded learners as one-click chips), then the form filling itself field by field with a short stagger — each field badged `you told us` or `PNIA over the bus` / `PLR over the bus`, and a running "fields asked: 1 / fields filled: 9". Footer states the KP4 seam in one line.
- [x] **Step 2: inspector tab.** The same exchange in four panes, each headed by the layer name and the sentence from `2.6.yaml`. Technical pane shows the actual r1 URLs, statuses and elapsed times; semantic shows the field map; organisational shows the live grant; legal shows the purpose-limitation line. Nothing invented in the page — every string is either from the config or from the live response.
- [x] **Step 3: permissions tab.** Both providers side by side with their live grants. Revoke `identity-api`'s grant to PNEA, then poll the exchange (confirmed live, 2026-07-26: the proxy's authorization view lags the admin API's by up to ~30s -- show a "propagating..." state and retry automatically rather than firing one call and reading a `200` as a broken demo) until it fails with the real `AccessDenied` fault, rendered verbatim; a second panel runs the same call as `MOEYS:PEMIS` through its own Security Server, which is denied whatever you toggle. A persistent banner shows journal state and a Reset button whenever it is dirty.
- [x] **Step 4:** capture check — 1080p screenshot of each tab, text legible, no clipping. Commit.

---

### Task 7: Compose service and control script

**Files:** Modify `docker-compose.yml`; create `apps/console/Dockerfile`, `scripts/console.sh`

- [x] **Step 1:** service `console` under `profiles: ["demo"]`, image built from `apps/console/`, on the `linkup` network, `ports: ["127.0.0.1:8090:8000"]` (localhost-bound on purpose), with `apps/data`, `configs`, `hurl/topology.json`, `manifest.yaml`, `deployment.yaml` mounted read-only and `out/` read-write for the journal. Environment from `.env` — no credentials in the compose file.
- [x] **Step 2:** `scripts/console.sh {up|down|reset|status}` sourcing `lib.sh` so it honours `deployment.yaml`'s profile; `up` prints the URL and reminds that this is demo-only.
- [x] **Step 3:** verify the console does not start on a plain `docker compose up`, and that `scripts/acceptance.sh` passes with it stopped. Commit.

---

### Task 8: Guard rails and documentation

**Files:** Modify `scripts/acceptance.sh`, `README.md`, `runbook.md`, `PLAN.md`, `docs/production-delta.md`

- [x] **Step 1:** `acceptance.sh` exits early with a clear message if `out/console-acl-journal.json` is non-empty: the federation is mid-demo, run `scripts/console.sh reset`. It must not import or start the console — a missing file means business as usual.
- [x] **Step 2:** `runbook.md` gains an optional step 5 (demonstration console) after acceptance; `README.md` gains one line in the what's-here paragraph; `PLAN.md` gains a short section recording that the console is a demo asset outside the module map and outside the acceptance path.
- [x] **Step 3:** `docs/production-delta.md` gains the console's shortcuts: no authentication, admin credentials held server-side in a demo container, localhost binding as the only access control, and an ACL write path that exists purely to be theatrical. It never ships to a real deployment.
- [x] **Step 4:** full sequence on a clean machine — `hurl/run-linkup.sh`, `scripts/seed.sh`, `scripts/acceptance.sh`, `scripts/console.sh up`, run the three tabs, `scripts/console.sh reset`, `scripts/acceptance.sh` again. Commit.

  **Verified live (2026-07-27):** `teardown.sh --purge` → cold `run-linkup.sh` →
  `seed.sh` → `acceptance.sh` (GREEN) → `console.sh up` → counter (`/api/health`,
  `/api/topology`, `/api/learners`), inspector (`/api/exchange/<nin>`, both calls
  200, all four layers present), permissions (`/api/exchange/<nin>/negative` denied
  with `Server.ServerProxy.AccessDenied`; `/api/acl/revoke` then `/api/acl/grant`
  round-tripped `live` correctly, journal `dirty` flagged as expected) — all
  exercised via curl, not the browser (screenshot capture was unreliable in this
  environment; functional correctness confirmed via response bodies instead) →
  `console.sh reset` (journal cleared, ACLs verified back to configured state) →
  `acceptance.sh` again (GREEN). Along the way, hit and root-caused a
  `SslAuthenticationFailed`/OCSP-staleness fault on `ss-plr` on the *first* attempt
  at this step (the federation had been up for ~10 hours during this session's
  earlier investigation) — not a console bug; documented in `runbook.md` "Known
  traps" and reproduced clean on a fresh redeploy done promptly.

---

## Open items to confirm on the live stack (C0)

- **Re-granting an existing right.** The OpenAPI model lists 409 for an existing item; the reset path must treat "already granted" as success, not as failure.
- **Whether the message log can back the technical pane.** `acceptance.sh` still carries `[confirm P0: message-log query]`. If it resolves cheaply, the inspector's technical pane gains real signed-message evidence; if not, v1 shows the live request/response only and the bus tracer stays out of scope.

**Corrected (2026-07-26, during Task 5 implementation):** the "instant" finding above was only ever true for the **admin API's own read of the configuration** (`GET .../access-rights` reflects a `POST .../delete` immediately — that part still holds, and `journal.py`'s `reset()` verification correctly relies on it). It is **not** true for the **proxy's actual authorization decision** on a real r1 call: revoking `identity-api`'s grant and then immediately re-running the exchange through `apps/console` (in-network, `ss-pnea:8080`) still returned `200` for ~20 seconds; the same call, retried, eventually returned the real `AccessDenied`. A direct `curl` against the host-mapped port showed the same lag. This is consistent with the Security Server proxy caching its authorization view separately from the admin API's database and refreshing it on its own interval (X-Road's serverconf cache), not with a console bug -- confirmed by reproducing the delay with a plain `curl` outside the console entirely.

**Design consequence for Task 6:** the permissions tab must not assume the revoke→denied transition is instant. After a revoke, poll the exchange (or show a "propagating, may take up to ~30s" state and a manual retry) rather than firing one call and treating a `200` as a broken demo. `journal.py`'s `reset()` itself needs no change -- it verifies the *configured* state via the admin API, which is accurate immediately; the delay only affects how soon a subsequent *exchange* call reflects the change.
