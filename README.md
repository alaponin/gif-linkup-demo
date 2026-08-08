# KP2 build pack — Government Interoperability Framework

The runnable companion to the KP2 video bundle. The videos teach the build; this
pack **is** the ready solution — the configuration the modules generate, the prompts
that generate it, the scripts that deploy it, and the acceptance checks that prove it.

- **Track:** interoperability
- **Depends on:** none (foundation)
- **Stand it up:** see `runbook.md` — start with `scripts/preflight.sh`
  (checks the host has what the pack needs; installs nothing), then
  `scripts/gen-secrets.sh` (writes a real `.env`; `.env.example` is a
  placeholder template and cannot work by itself). An `.env` from before
  join-b (missing `KP2_JOIN_APPLICANT_TOKEN`/`KP2_JOIN_OPERATOR_TOKEN`)
  breaks every `docker compose` invocation, not just the join-related ones
  (Compose interpolates `${VAR:?...}` for the whole file before profile
  filtering) -- re-run `scripts/gen-secrets.sh` with no flags to append just
  the two missing keys (no `--force`, no PIN/password rotation, safe against
  a running federation)
- **Index:** `manifest.yaml` (module → BB → config → prompt → acceptance, with
  `video_ref` to the Topic 5 subtopic each module realises, and the frozen
  Progressa identifiers that are the KP3/KP4 join keys)
- **Status against the onboarding path:** `docs/path-conformance.md` — the
  only place the pack states what it does and does not implement of
  `docs/GEATDM-Interop-Member-Onboarding-Path-v0.2.md`.
  Generated from `docs/path-conformance.yaml`; every cited evidence path is
  existence-checked by `tests/test_path_conformance.py`, so a status claim
  cannot outlive the file it cites. Four statuses and no tick mark. **Where a
  narrative document disagrees with it, it wins** — that divergence is exactly
  what produced the 2026-08-08 corrections in
  `docs/decisions/onboarding-path-gap-analysis.md`.
- **Plan / review:** `PLAN.md` (build plan, doc-verified X-Road sequence),
  `docs/decisions/onboarding-alignment-design.md` (design record — frozen; reasoning,
  not status)
- **Verify a change:** `scripts/verify.sh --fast|--live|--full` — three
  tiers, chosen by the tool, not by whoever is typing. `--fast` (static checks, the ship gate, exposure,
  `pytest tests/ apps/console/tests/ apps/join-api/tests/ apps/mock-registry/tests/` — no running containers, no network,
  no federation, but the Docker CLI is required: `check-exposure.sh` reads
  the *rendered* Compose config, profiles and `${VAR}` interpolation
  resolved, which is what makes it worth having, and that read needs
  neither a running Docker daemon nor `.env`, see `tests/test_tiers.py`)
  **~50s** for the full test suite (no running containers, no network; `--full`
  runs this tier inside `hurl/run-linkup.sh`, so every test added here is
  also added to the reproducibility proof);
  `--live`
  (`--fast`, then `acceptance.sh` against a running stack; refuses rather
  than deploying one if nothing is reachable)
  **~80s** against the standard topology;
  `--full` (purge, deploy, seed, acceptance, console smoke — the
  reproducibility proof) **~13 min** cold against the standard topology (four
  Security Servers, no lite/full split): container start-up plus the Hurl
  admin-API run make up the bulk of the deploy time, with `--fast`,
  teardown, seeding, acceptance and the console smoke pass around it. RAM:
  **~11 GiB** measured live (`docker stats --no-stream` steady state: four
  Security Servers ~2.2–2.3 GiB each, Central Server ~1.8 GiB, Test CA
  ~88 MiB, two mock providers ~32 MiB each), including the operational- and
  environmental-monitoring add-ons' acceptance check, which cost nothing
  extra to deploy since they ship on the Sidecar image this pack already
  uses. See `docs/production-delta.md` "An own-server join and its
  un-join, live end to end".

  There is one topology — no lite/full split to develop against or measure
  separately; run `--fast` after each step and one `--full` before closing
  out a plan. **When to run which, inside a plan:** `--fast` after each
  step (it's the one that's always cheap enough to run every time), `--live`
  once a task is done (proves it against a real running stack, not just
  statically), `--full` once before the plan is closed out (the
  reproducibility proof, not a per-task ritual). A plan's own "verified
  live" notes should say which tier backed them, so a later reader can
  tell a `--fast`-only claim from a `--full` one.
  **`--live` does not itself perform a real member join**
  (`acceptance/join-member.md`'s checks discover already-joined members
  generically and pass vacuously when none exist — they never submit,
  approve, or unjoin one; unjoin is discovered from `out/join/*.json`'s
  `RETIRED` records). A real hosted join
  (`apps/join-api`, `POST /requests` → approve → `ACTIVE, verified: true`)
  takes on the order of a minute end to end, and an own-server join takes
  roughly **~2–3 minutes after the member's server is up** (plus 76–100s to
  stand the server up, plus whatever `BLOCKED` really costs, which in
  production is days) — comfortably under the ~2-minute threshold past
  which `--live` "stops being the run-it-when-a-task-is-done tier it is
  documented as." So **`--live` stays vacuous-by-default**, and a real join
  is a deliberate, separate, manual procedure (`runbook.md`'s "Join via the
  API (automated)"), not something bolted onto the routine `--live` tier.

  A hosted join (PTSB, `awards-api` published and granted to PNEA:EXAMS)
  reaches `ACTIVE, verified: true` in well under two minutes and un-joins
  back to `RETIRED` in a few seconds. An own-server join (same PTSB
  identity, `security_server.own_server: true`) reaches `BLOCKED` almost
  immediately, then `ACTIVE, verified: true` once its Security Server is
  brought up and resumed — see `docs/production-delta.md`'s own record of
  this flow for full detail.

What's here: `deployment.yaml` (the analyst-facing deployment spec — X-Road
version pins, network bind, and (`cs_digest`/`ss_digest`/`testca_tag`) the
digest pins that back them; `.env` carries only secrets), `docker-compose.yml`
(X-Road 7.7.0: Central Server, Test CA, four Security Servers — PDGA plus
PNEA, PLR and PNIA each on their own; MoEYS is retired),
`configs/` (declarative YAML per module),
`prompts/` (the bb-config-gen plays that generate the configs), `hurl/` (the
federation as config-as-code — Hurl scenarios driving the admin REST APIs,
generated from `configs/`, retargeted from X-Road 7.7.0's own `setup.hurl`),
`acceptance/` (given/when/then per module; 2.6 is the once-only exchange, the
framework's acceptance; `member.md` is the generic per-member check every
joined member gets automatically; 2.7 is the join API's own transition +
reachability check), `scripts/` (deploy / seed / acceptance /
teardown / `member.sh list|remove|drift` — reports on, retires, and checks
drift for joined members / `join.sh up|down|status` — the join API's own
service lifecycle /
`verify.sh` — the tiered entry point above), `tests/` (the golden corpus for
`hurl/generate.py` — `test_golden.py`, no Docker), `apps/` (mock REST registries behind
the Security Servers + OpenAPI contracts +
Gambia-grounded, Progressa-named seed data; `apps/console/` is the optional
one-page demonstration UI, `scripts/console.sh up` — a demo asset, not a
module, never in the acceptance path, whose **4 · Join a member** tab is a
thin, server-side-token-holding proxy onto `apps/join-api/` — module 2.7's own
service, which validates and drives a real member join from a submitted
payload to `ACTIVE` over the live X-Road admin API), `docs/` (production delta
per Module 5.7; X-Road 8 note; what reading the 7.7.0 reference corrected).

The number and identity of members is a property of `configs/member-*/` plus
`manifest.yaml`'s `identity.members`, not of this pack's source code. There is
still no `scripts/member.sh add`, and that stays true on purpose — writing
member config by hand is exactly what this pack demonstrates you don't need
to do — but as of module 2.7 there is now an API for it:
`apps/join-api` (`scripts/join.sh up`, or the console's **4 · Join a member**
tab) drives a real, hosted member from a submitted payload through validation,
operator approval, config generation and the live X-Road admin-API sequence to
`ACTIVE, verified: true`, live-verified end to end: submit →
approve → `ACTIVE` → `acceptance.sh` green → `member.sh list` → `member.sh
remove` → regenerate → `acceptance.sh` green again, well under two minutes
for the join step itself. `prompts/member.md`'s manual flow — running the prompt against an
agency brief and committing what it produces — is still there for anyone
without a running stack to submit against. `apps/join-api` itself covers both
shapes of join: a hosted member (the default) and one that brings up its own
Security Server (`security_server.own_server: true`, `runbook.md`'s "A join
with the member's OWN Security Server"). On a single-host demo
deployment, default a joining member to `hosted_on` an existing Security
Server rather than its own (the join API does this by default —
`configs/x-road-bus/join-policy.yaml`'s `default_hosting: hosted_on`): it costs zero
extra containers and RAM, and sidesteps every own-server finding in
`docs/production-delta.md` (a real port-allocation bug, two real Compose gaps,
and host-CPU-contention risk under several concurrent JVMs) — reserve a
joined member's own server for when the demonstration specifically needs one.
A submitted payload's `code` and `subsystem` must satisfy the identifier and
member-code conventions `docs/conventions.md` publishes — the onboarding
path's §0.5/§1a prerequisite this pack now states rather than leaving
implicit in `validate.py`. `security_server.dns_name` follows the same doc's
`ss-<key>` host-naming convention, which the pack applies consistently but
does not check at request time.

What this pack is an instance of: X-Road as the message bus, join-api as the
onboarding gate, and `configs/semantic/semantic-map.yaml` (Module 4, checked
by `apps/join-api/validate.py` check 8, not merely published) as the
shared field dictionary together realise GovStack's **Information Mediation**
building block (GovStack subtopic 4.7) — a member joins the mediator once
(module 2.7) and reaches every other member's declared exchanges through it,
rather than negotiating a bilateral integration per pair.

By design, KP2's slice is **Joget-free**: the member systems are mocks behind
stable OpenAPI contracts — the seam where KP4's Joget DX apps plug in later
without touching the X-Road configuration.

Built and proven with the `itu-giga-kp` kit: `bb-config-gen` fills the configs,
`kp-solution-verify` proves the pack runs. **Status: VERIFIED** —
`check_pack.py --ready`
passes and the live acceptance suite is green, including the reproducibility
proof (`teardown.sh --purge` → cold redeploy → reseed → acceptance, unattended
— PLAN.md §7) and a full console up/exercise/reset pass (PLAN.md §11). Scope:
Education only, public anchors only. Demo only — never production
(`docs/production-delta.md`).
