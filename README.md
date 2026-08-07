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
- **Plan / review:** `PLAN.md` (build plan, doc-verified X-Road sequence),
  `REVIEW.md` (self-review; open decisions)
- **Verify a change:** `scripts/verify.sh --fast|--live|--full` — three
  tiers, chosen by the tool, not by whoever is typing. `--fast` (static checks, the ship gate, exposure,
  `pytest tests/ apps/console/tests/ apps/join-api/tests/ apps/mock-registry/tests/` — no running containers, no network,
  no federation, but the Docker CLI is required: `check-exposure.sh` reads
  the *rendered* Compose config, profiles and `${VAR}` interpolation
  resolved, which is what makes it worth having, and that read needs
  neither a running Docker daemon nor `.env` — confirmed 2026-07-31 with the
  daemon itself stopped, see `tests/test_tiers.py`) **~53s** (measured
  2026-08-07, Wave 3 Task 6, 331 tests [330 passed, 1 skipped], post-reduction
  — was ~49s/291 tests on 2026-08-03, ~29s/203 tests on
  2026-08-02, ~16s/66 tests on 2026-08-01 and ~8s/48 tests on 2026-07-28
  before that; the growth is tests added across several plans, not a
  regression in any single one of them, but note it compounds: `--full`
  runs this tier inside `hurl/run-linkup.sh`, so every test added here is
  also added to the reproducibility proof);
  `--live`
  (`--fast`, then `acceptance.sh` against a running stack; refuses rather
  than deploying one if nothing is reachable)
  **~81s** (re-measured 2026-08-07, Wave 3 Task 6, against the single D5
  topology, confirming — not materially different from — the earlier ~78s
  measured 2026-08-03, two consecutive runs, both 78s — the earlier "~29s"
  figure predated both `--fast`'s growth and 2.7's own
  checks); `--full` (purge, deploy, seed, acceptance, console smoke — the
  reproducibility proof) **~763s (~12.7 min)** — measured cold 2026-08-07,
  Wave 3 Task 6, the first `--full` run against the single D5 topology (four
  Security Servers, no lite/full split): `out/deploy-timings.txt`'s own
  phase split was 200s containers-healthy + 404s Hurl admin-API run = 604s
  deploy subtotal, plus `--fast` (~53s), teardown, seeding, acceptance and
  the console smoke pass around it for the 763s wall-clock total. This
  **supersedes** two numbers: the older **~872s (~14.5 min)** figure
  (measured 2026-08-03, two independent cold runs of the now-retired
  five-server `full` profile, 825s/918s) it replaces outright — that
  topology no longer exists — and `docs/topology-profile-decision.md`
  §2/§5.3's **~670s estimate** for this exact (T1/D5) topology, which this
  measurement shows ran about 14% (~93s) longer live than estimated; see
  that document for the corrected per-plan arithmetic. RAM: **~10.9 GiB**
  measured live (`docker stats --no-stream`, this same run's steady state:
  four Security Servers ~2.2–2.3 GiB each, Central Server ~1.8 GiB, Test CA
  ~88 MiB, two mock providers ~32 MiB each) — within noise of the ~11 GB
  design estimate it confirms rather than corrects. See
  `docs/production-delta.md` "An own-server join and its un-join, live end
  to end". There is one topology now (Wave 3 Task 4, design decision 5) —
  no lite/full split to develop against or measure separately; run
  `--fast` after each step and one `--full` before closing out a plan. See
  `docs/superpowers/plans/2026-07-28-kp2-testing-strategy.md` for what each
  tier replaced. **When to run which, inside a plan:** `--fast` after each
  step (it's the one that's always cheap enough to run every time), `--live`
  once a task is done (proves it against a real running stack, not just
  statically), `--full` once before the plan is closed out (the
  reproducibility proof, not a per-task ritual). A plan's own "Verified live
  (date)" notes should say which tier backed them, so a later reader can
  tell a `--fast`-only claim from a `--full` one.
  **`--live` does not itself perform a real member join** (join-b Task 5's
  own design: `acceptance/join-member.md`'s checks discover already-joined members
  generically and pass vacuously when none exist — they never submit or
  approve one; join-c Task 5 added `2.7.unjoin(<member>)` on the same
  terms, discovered from `out/join/*.json`'s `RETIRED` records). Task 6's
  own live proof measured a real hosted join
  (`apps/join-api`, `POST /requests` → approve → `ACTIVE, verified: true`)
  at **~93s** end to end — re-measured at **64s** by join-c Task 5, and an
  own-server join at **~163s
  after the member's server is up** (plus 76–100s to stand it up, plus
  whatever `BLOCKED` really costs, which in production is days) —
  comfortably under the
  ~2-minute threshold past which the brief that drove this decision says
  `--live` "stops being the run-it-when-a-task-is-done tier it is
  documented as." That confirms Task 5's call was right, not just
  convenient: **`--live` stays vacuous-by-default**, and a real join is a
  deliberate, separate, manual procedure (`runbook.md`'s "Join via the API
  (automated)"), not something bolted onto the routine `--live` tier.

  ***Wave 3 Task 6, post-reduction, both re-run live end to end on the
  single D5 topology (2026-08-07):*** a hosted join (PTSB, `awards-api`
  published and granted to PNEA:EXAMS) reached `ACTIVE, verified: true` in
  **~73s** and un-joined back to `RETIRED` in **~3s** — both within noise
  of the figures above. An own-server join (same PTSB identity,
  `security_server.own_server: true`) reached `BLOCKED` almost immediately,
  took **102s** for `scripts/join-agent.sh ptsb` to bring `ss-ptsb` healthy
  (within the documented 76–100s range), and **131s** from `resume` to
  `ACTIVE, verified: true` — the first live confirmation that
  `apps/join-api/job.py`'s `R1_RETRY_BUDGET = 54` fix (join-c plan Task 5
  review fix 2) actually works: the shared run budget had 7 of 12 retries
  left when `join.r1_verify` ran, and `join.r1_verify` itself needed only a
  handful of its own 54, nowhere near exhausting it. This closes the open
  item `acceptance/join-member.md` and `docs/production-delta.md` both
  recorded ("fixed, not yet re-verified live") — an own-server join really
  does reach `verified: true` now, not just `ACTIVE`. Un-joined cleanly back
  to `RETIRED` plus the two documented `docker rm`/`docker volume rm`
  commands. See `docs/production-delta.md`'s own record of this run for the
  full detail, including a real defect it found in `scripts/join-agent.sh`
  (fixed in the same commit).

What's here: `deployment.yaml` (the analyst-facing deployment spec — X-Road
version pins, network bind, and (`cs_digest`/`ss_digest`/`testca_tag`) the
digest pins that back them; `.env` carries only secrets), `docker-compose.yml`
(X-Road 7.7.0: Central Server, Test CA, four Security Servers — PDGA plus
PNEA, PLR and PNIA each on their own; MoEYS is retired, Wave 3 Task 1),
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
`ACTIVE, verified: true`, live-verified end to end (join-b Task 6: submit →
approve → `ACTIVE` → `acceptance.sh` green → `member.sh list` → `member.sh
remove` → regenerate → `acceptance.sh` green again, ~93s for the join step
itself). `prompts/member.md`'s manual flow — running the prompt against an
agency brief and committing what it produces — is still there for anyone
without a running stack to submit against, or for a member type Plan B's
join API doesn't cover (an own Security Server; Plan C). On a single-host demo
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
by `apps/join-api/validate.py` check 8, not merely published — see
`docs/superpowers/plans/2026-08-05-kp2-wave2-data-layers.md` Task 1) as the
shared field dictionary together realise GovStack's **Information Mediation**
building block (GovStack subtopic 4.7) — a member joins the mediator once
(module 2.7) and reaches every other member's declared exchanges through it,
rather than negotiating a bilateral integration per pair.

By design, KP2's slice is **Joget-free**: the member systems are mocks behind
stable OpenAPI contracts — the seam where KP4's Joget DX apps plug in later
without touching the X-Road configuration.

Built and proven with the `itu-giga-kp` kit: `bb-config-gen` fills the configs,
`kp-solution-verify` proves the pack runs. **Status: VERIFIED (2026-07-25,
re-verified 2026-07-27 including `apps/console/`)** — `check_pack.py --ready`
passes and the live acceptance suite is green, including the reproducibility
proof (`teardown.sh --purge` → cold redeploy → reseed → acceptance, unattended
— PLAN.md §7) and, most recently, the same cycle plus a full console
up/exercise/reset pass (PLAN.md §11). Scope: Education only, public anchors
only. Demo only — never production (`docs/production-delta.md`).
