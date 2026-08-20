# KP2 build pack — run book

Stand up the Linkup demonstration federation (X-Road 7.x) from zero on one Docker
host, and run the once-only exchange that proves it. Demo only — see
`docs/production-delta.md` before anyone mistakes this for a deployable platform.

Three ways in, in increasing depth:

- **Read** — `README.md` for what this pack is and what it claims;
  `docs/path-conformance.md` for how far it goes against the onboarding path.
- **Run** — `scripts/demo.sh` stands the federation up from zero (~10 minutes)
  and tells you what it is doing at each step.
- **Do** — `exercises.md`: five exercises over the same operations
  documented below, each with the observations to expect.

Everything below is the engineering depth under those three.

## Prerequisites

- **A `git` clone of the monorepo, with the pack at
  `<repo>/10-Knowledge-Products/KP2-GIF/KP2-build-pack`.** Not an unpacked
  zip of the pack alone, and not the pack moved elsewhere: `join-api`
  bind-mounts `../../..` as `/repo` and its approval step runs `git status`
  against that root before it writes anything, so every join approval fails
  outside this layout — while the federation itself deploys, which is what
  makes the failure confusing rather than obvious. `scripts/verify.sh
  --fast` additionally expects the sibling `ITU-Giga-KP-Plugin` checkout
  beside that root for the ship gate; without it the gate is skipped with a
  warning and the rest of the tier still runs. `scripts/preflight.sh`
  refuses on the layout, in words.
- Run `scripts/preflight.sh` first -- checks all of the above plus Docker,
  Docker Compose v2,
  `jq`, `curl`, `python3` 3.9+ with PyYAML, a SHA-256 tool, and bash 4+, and
  reports every gap at once rather than one at a time as the deploy hits
  each of them. Checks only -- it prints the install line for each
  gap it finds instead of running it. `hurl/run-linkup.sh` also runs this
  automatically before it does anything expensive; running it by hand here
  first is what turns a mid-deploy failure into a pre-deploy one.
- Docker ≥ 24 with Docker Compose ≥ 2.24.
- **~10.9–11.1 GiB RAM in steady state**, measured live for the current
  4-Security-Server topology (`docker stats --no-stream`: four Security
  Servers ~2.2–2.3 GiB each, Central Server ~1.8–2.0 GiB, Test CA ~88 MiB,
  two mock providers ~32 MiB each). Fits comfortably in 16 GB. There is one
  topology: no smaller alternative to opt into — an earlier 5-server
  topology including MoEYS measured ~13 GB before MoEYS was retired.
- `curl`, `jq`, `python3` on the workstation.
- An older `.env` missing `KP2_JOIN_APPLICANT_TOKEN`/`KP2_JOIN_OPERATOR_TOKEN`
  still deploys: those two are interpolated with `${VAR:-}`, so their absence
  costs the join demo, not the federation — `join-api` refuses to start and the
  console's join tab renders the remedy instead of a queue. `preflight.sh` warns
  about it; re-run `scripts/gen-secrets.sh` with no flags to append just the two
  missing keys (no `--force`, no PIN/password rotation, safe against a running
  federation).
- **Host clock synchronised (NTP).** X-Road signs and timestamps every
  message; a drifting clock produces failures that present as certificate
  errors, not time errors. Check before deploying, don't just assume it:
  `timedatectl status` (Linux, look for `System clock synchronized: yes`) or
  `sntp -sS time.apple.com` / System Settings → Date & Time (macOS).
- No ITU cloud dependency: this run book targets the local stack. The ITU cloud
  (Linkup) deployment re-targets the same scripts later — see
  `docs/deployment-targets.md`.
- **Firewalled host, conference network, or air-gapped machine?** `deploy.sh`
  pulls images from Docker Hub and `ghcr.io` as it needs them. Run
  `scripts/preload-images.sh` first, while the host still has network, to
  fetch every pinned image ahead of time. For a genuinely offline deploy,
  `docker save`/`docker load` the images it pulled onto removable media —
  the tarball is large. This does not cover `apps/mock-registry` (built
  locally) or the Hurl runner image; see `docs/deployment-targets.md`.

## Steps

First time? `scripts/demo.sh` runs steps 1–5 below and tells you what it is
doing at each one. It refuses if a federation is already deployed.

1. `scripts/gen-secrets.sh` — writes a real `.env` with a random token PIN
   and admin password (mode `600`). `.env.example` ships placeholders that
   cannot work, on purpose — do not copy it by hand.
2. **Deploy** — `scripts/deploy.sh` (a wrapper over `hurl/run-linkup.sh`)
   Brings up the containers and drives the full stand-up over the admin REST APIs:
   CS init (instance `PROGRESSA`, class `GOV`, configuration signing keys) → Test CA /
   OCSP / TSA registration → members and subsystems → configuration anchor → PDGA +
   management SS → each member SS (anchor, PIN, AUTH + SIGN keys, Test CA signing,
   auth-cert registration and its explicit approval on the CS, subsystem) → service
   publishing (OpenAPI3) → ACLs. Global-conf propagation is asynchronous, so a stretch
   of HTTP errors and retries partway through is expected, not a failure. Measured
   live for the current 4-server topology (`out/deploy-timings.txt`):
   **~156s containers-healthy + ~395s Hurl run ≈ 9.2 minutes end to end**.

   The sequence is a Progressa retargeting of `development/hurl/scenarios/setup.hurl`
   at X-Road tag **7.7.0**. The scenarios live in `hurl/`, generated from `configs/` —
   see `hurl/README.md` to run or retarget them, and `docs/decisions/xroad-770-notes.md` for
   what reading the reference corrected.
3. **Seed** — `scripts/seed.sh`
   Regenerates the Progressa demonstration data (Gambia-grounded, Progressa-named)
   and restarts the mock providers with it.
4. **Prove** — `scripts/acceptance.sh`
   Runs `acceptance/federation-core.md` … `once-only-exchange.md` in order; exits non-zero on first failure.
   2.6 is the framework's acceptance: the once-only exchange resolves, the right
   learner returns, nothing is asked twice, and the unauthorised caller is denied.
5. **Demonstrate (optional)** — `scripts/console.sh up`
   A one-page demo console (five tabs: 1 · Ask once, 2 · How it worked,
   3 · Who's allowed, 4 · Join a member, 5 · What's on the bus) at
   `http://localhost:8090` for a non-technical audience — not a module, not part
   of acceptance, and never production (`docs/production-delta.md`). It really
   revokes and grants the `identity-api` ACL live, journals every change, and
   resets on demand, on startup, and on a 120s no-activity watchdog, so a demo
   can't be left in a state that breaks `scripts/acceptance.sh` afterwards.
   `scripts/console.sh {down|reset|status}` manages it; `acceptance.sh` itself
   refuses to run while its journal is dirty, with a message telling you to
   `scripts/console.sh reset` first.

## Verifying a change

`scripts/verify.sh --fast|--live|--full` — three tiers, chosen by the tool,
not by whoever is typing.

- **`--fast`** — static checks, the ship gate, exposure, and
  `pytest tests/ apps/console/tests/ apps/join-api/tests/
  apps/mock-registry/tests/`. **~50s.** No running containers, no network, no
  federation — but the Docker CLI *is* required: `check-exposure.sh` reads the
  *rendered* Compose config, profiles and `${VAR}` interpolation resolved,
  which is what makes it worth having, and that read needs neither a running
  Docker daemon nor `.env` (`tests/test_tiers.py`). `--full` runs this tier
  inside `hurl/run-linkup.sh`, so every test added here is also added to the
  reproducibility proof.
- **`--live`** — `--fast`, then `acceptance.sh` against a running stack;
  refuses rather than deploying one if nothing is reachable. **~80s** against
  the standard topology.
- **`--full`** — purge, deploy, seed, acceptance, console smoke: the
  reproducibility proof. **~13 min** cold against the standard topology (four
  Security Servers; there is one topology, no lite/full split to develop
  against or measure separately). Container start-up plus the Hurl admin-API
  run make up the bulk of the deploy time, with `--fast`, teardown, seeding,
  acceptance and the console smoke pass around it. RAM and disk: README.md's
  Requirements. The operational- and environmental-monitoring add-ons'
  acceptance check is included and costs nothing extra to deploy — they ship
  on the Sidecar image this pack already uses. See
  `docs/production-delta.md` for what a join and un-join do to Central-Server
  state.

**When to run which:** `--fast` after every change, because it is the one
always cheap enough to run every time; `--live` once a change is finished,
which proves it against a running federation rather than statically; `--full`
before handing the pack to anyone, as the reproducibility proof rather than a
routine step. Whenever you record that something was verified, say which tier
backed it — a `--fast` claim and a `--full` one are different claims.

**`--live` does not itself perform a real member join.**
`acceptance/join-member.md`'s checks discover already-joined members
generically and pass vacuously when none exist — they never submit, approve,
or unjoin one (unjoin is discovered from the join store's newest `RETIRED`
record for the member — `scripts/acceptance.sh`'s own 2.7 un-join discovery
still globs the now-unused `out/join/` directly and needs the same cutover;
tracked as a follow-up, not part of this change). A real hosted join (`apps/join-api`, `POST /requests` → approve →
`ACTIVE, verified: true`) takes on the order of a minute end to end, which is
inside the ~2-minute threshold past which `--live` would stop being the
run-it-when-a-task-is-done tier it is documented as. An own-server join is not:
it takes roughly **~2–3 minutes after the member's server is up**, plus 76–100s
to stand that server up, plus whatever `BLOCKED` really costs — days, in
production. So **`--live` stays vacuous-by-default**, and a real join is a
deliberate, separate, manual procedure ("Join via the API (automated)", below),
not something bolted onto the routine `--live` tier.

A hosted join (PTSB, `awards-api` published and granted to PNEA:EXAMS) reaches
`ACTIVE, verified: true` in well under two minutes and un-joins back to
`RETIRED` in a few seconds. An own-server join (same PTSB identity,
`security_server.own_server: true`) reaches `BLOCKED` almost immediately, then
`ACTIVE, verified: true` once its Security Server is brought up and resumed —
see `docs/production-delta.md`'s own record of this flow for full detail.

## Admin UIs (manual fallback)

Every scripted step can be done by hand per the NIIS KB guides. Concurrent UI
sessions in one browser log each other out — use separate browsers/profiles.

| Component | URL | Credentials |
| --- | --- | --- |
| Central Server (PDGA) | https://localhost:4000 | xrd / secret (fixed, test image) |
| Test CA | http://localhost:8888/testca/ | — |
| ss-pdga / ss-pnea / ss-plr / ss-pnia | https://localhost:1000 / 2000 / 3000 / 5100 | `.env` admin user |

## Teardown

The federation is a fixture, not a build artefact: it exists to be reused
across a session's work, not rebuilt every time something needs checking
against it. Plain `scripts/teardown.sh` between sessions, never `--purge` —
`--purge` throws the fixture away and pays the ~9-10 minute rebuild
(`hurl/run-linkup.sh`) again for no reason. Reserve `--purge` for the one
thing only a from-zero rebuild can prove: the reproducibility proof (below), or `scripts/verify.sh --full`, which performs that same proof.

- `scripts/teardown.sh` — stops containers; named volumes survive, so the
  federation's configuration persists across restarts. **To resume, do not
  rerun `deploy.sh`/`hurl/run-linkup.sh`** — the Hurl scenario set always runs
  the full stand-up sequence and is not idempotent against already-configured
  state: `POST /api/v1/initialization` returns `409 init_already_initialized`
  on a persisted CS, and every later registration call would fail the same
  way. Resume with the containers
  directly: `docker compose -f docker-compose.yml -f hurl/compose.hurl.yml
  up -d` — the persisted `/etc/xroad` state in each volume is everything the
  federation needs; nothing else has to run.
- `scripts/teardown.sh --purge` — also deletes the volumes: full reset to zero.
  The reproducibility proof is: `--purge`, redeploy (`hurl/run-linkup.sh`,
  the from-zero path), reseed, acceptance green.

## Joining a member

- **Add:** run `prompts/member.md` against the joining agency's brief; it
  produces `configs/member-<key>/<key>.yaml` and the
  `identity.members.<key>` entry to paste into `manifest.yaml`. Then
  `python3 hurl/generate.py`, `scripts/deploy.sh` (or `hurl/run-linkup.sh`
  from zero). There is no script that does this step — writing member
  config by hand is what the prompt replaces.
- **List:** `scripts/member.sh list` — key, origin, host server, ports, read
  from `hurl/topology.json`.
- **Remove:** `scripts/member.sh remove <key>` — deletes
  `configs/member-<key>/` and the `manifest.yaml` entry, regenerates.
  Refuses on a canonical member (the four never renumber or leave). Does
  **not** touch a running federation: the member stays registered there
  until `scripts/teardown.sh --purge` — or until you un-join it properly
  (below), which calls this script for you at the end.
- **Drift:** `scripts/member.sh drift <key>` — re-fetches a joined member's
  *current* OpenAPI spec and diffs its endpoint set against the baseline
  captured at join time. No auth, no HTTP to the join
  API — opens `out/join-store/join-store.sqlite3` read-only and reads the
  live spec URL directly, works whether or not `join-api` is even running (a
  read-only, WAL-mode SQLite open needs no running writer). The spec URL is an
  internal `linkup`-network hostname (`app-<key>:8000`), so this needs to
  run from inside that network (or from any other container already on
  `linkup`) if a plain host-side run reports "nodename nor servname
  provided" — that error is the trap working as designed, not a bug.
  `join-api` mounts the monorepo at `/repo`, not the pack at its own
  working directory, and `member.sh` is bash, not Python — so the command
  is:

  ```
  docker compose exec join-api \
    bash /repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack/scripts/member.sh drift <key>
  ```

  Start `join-api` with `scripts/join.sh up` first if it is not running —
  `scripts/acceptance.sh` stops it when it finishes.
- **Remediating drift:** `scripts/member.sh refresh <key>` — detect, review,
  refresh, in that order. X-Road reloads a service description only on an
  explicit refresh, so a backend someone edited in a browser drifts from
  what the federation publishes until an operator acts. This is the act.

  It is a separate subcommand and not a `--fix` flag on `drift`, because the
  two are different kinds of thing: `drift` is a read that works against a
  federation that is not even running, while `refresh` authenticates to a
  Security Server's admin API and mutates federation state. It runs from the
  same place, for the same networking reason:

  ```
  docker compose exec join-api \
    bash /repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack/scripts/member.sh refresh <key>
  ```

  **What it refuses.** Before refreshing anything it re-runs the
  `join.allowed_methods` check `validate.py` applies at join time, against
  the spec as served *now*. A member whose spec has grown a `POST` is
  refused, in full, with the offending operations named: a refresh makes the
  federation *publish* the current contract, it does not make the current
  contract *approved*. A contract that has moved beyond what the member was
  admitted on is a re-admission decision, not a refresh.

  **What it records.** The act is appended to the member's join record as a
  `refreshes:` entry (timestamp plus the endpoint set each service serves
  after the refresh). `endpoint_baseline` is never touched — it is evidence
  of the contract the member was *admitted* on, and refreshing does not
  re-admit anybody. `drift` then reports both facts: still drifted from
  join (permanently, and correctly), and clean since the last refresh —
  which is the half that clears, so the warning means something again.

  **What it does not do.** Review. A changed field set, a changed lawful
  basis or a changed SLA passes this command untouched; the only policy it
  re-applies is `allowed_methods`. The organisational review around it is
  the operator's, and `docs/production-delta.md` says so.
- **Backup / inspect the join store:** `sqlite3
  out/join-store/join-store.sqlite3 "VACUUM INTO 'backup.sqlite3'"` while the
  API is up is safe — WAL readers don't block a VACUUM INTO. `teardown.sh`
  never touches this database (parity with today's `out/join/` survival). To
  inspect it: `sqlite3 out/join-store/join-store.sqlite3 .schema`, or a
  sample query like `sqlite3 out/join-store/join-store.sqlite3 "SELECT id,
  state, member_key, submitted_at FROM requests ORDER BY submitted_at DESC
  LIMIT 10"`.
- **Join via the API (automated):** `scripts/join.sh {up|down|status}`
  starts/stops the join API itself (`profile: demo`, like the console) at
  `http://localhost:8091`. Submit a payload matching `apps/join-api/schema.py`
  — `code` and `subsystem` must follow the identifier and member-code
  conventions `docs/conventions.md` publishes, or validation rejects the
  request before it reaches an operator; `security_server.dns_name` follows
  that same doc's `ss-<key>` host-naming convention too, but validation does
  not check it at request time (`docs/conventions.md`'s Security Server host
  naming section)
  (`POST /requests` with `Authorization: Bearer $KP2_JOIN_APPLICANT_TOKEN`
  and the `X-KP2-Console: 1` header — see `.env` for the real tokens,
  generated by `scripts/gen-secrets.sh`), review its computed config diff,
  then approve (`POST /requests/{id}/approve`, operator token) to write the
  config for real and start the job. Everything from the review onwards —
  the diff, approve, reject, resume, and the live step list — is also
  available in the console's **4 · Join a member** tab
  (`scripts/console.sh up`, `http://localhost:8090`), proxied server-side so
  the operator token never reaches the browser. Submission is not: it is the
  applicant's act with the applicant token, so it stays a `curl` from
  outside, and the tab's empty state hands you that command. Poll
  `GET /requests/{id}` (or watch the tab; it polls itself) until `state` is
  `ACTIVE` — a hosted join with one published service and one ACL grant
  takes on the order of a minute end to end (approve to `ACTIVE, verified:
  true`), inside the ~2-minute threshold past which `--live` would stop being
  cheap enough to run routinely ("Verifying a change", above).
  - **Issuing an agency its own credential:** `KP2_JOIN_APPLICANT_TOKEN` is
    shared by every applicant, which is fine for a demo and wrong the moment
    two agencies are real: nothing on a request says who sent it, and
    revoking one agency revokes all of them. The operator can issue a named
    credential instead:

    ```
    curl -X POST -H "X-KP2-Console: 1" \
         -H "Authorization: Bearer $KP2_JOIN_OPERATOR_TOKEN" \
         -H "Content-Type: application/json" -d '{"agency": "ptsb"}' \
         http://localhost:8091/tokens
    ```

    The value comes back **once** — the API stores only its SHA-256, so
    there is no way to read it again; a lost token is revoked and reissued,
    not recovered. `GET /tokens` lists who holds one and since when (names
    and dates, never hashes) and `DELETE /tokens/{agency}` revokes, taking
    effect on the very next request. A request submitted on an issued token
    records `submitted_by: <agency>`, which survives revocation — the record
    is evidence of a decision, and revoking a credential does not unmake the
    submission it was used for. An issued token is an *applicant*
    credential: it can submit and read, never approve.

    The shared token stays, and the console keeps using it. This is the
    per-agency-credential half of `docs/production-delta.md`'s row; the mTLS
    half is not built, and a production federation should disable the shared
    credential entirely.
  - **Recovering a `FAILED` job:** the record's `error` names the step and
    the last thing observed. Fix the underlying cause (a real federation
    problem, not usually this API's own code — see the OCSP trap below),
    then `POST /requests/{id}/resume` (or the tab's **Resume** button) —
    re-runs from `last_completed_step`, re-establishing whatever session
    state is not persisted (`apps/join-api/job.py`'s own docstring: nothing
    named `*_xsrf_token` is ever written to disk) rather than replaying the
    whole sequence from scratch.
  - **A join with the member's OWN Security Server:** set
    `security_server.own_server: true` in the payload and leave `hosted_on`
    out. It has to be asked for explicitly — a payload with neither is
    rejected (`hosting`), because a forgotten `hosted_on` must not silently
    become an own-server join. The job then runs the same bring-up sequence
    cold deploy gives every canonical member (anchor, AUTH key, SIGN key, CS
    registration, activation, timestamping, client), and the middle of it is
    the *member's* work, not the operator's — so the request stops at
    **`BLOCKED`** before the first such step, naming the server it is waiting
    for. Stand it up:

    ```
    scripts/join-agent.sh <key>        # e.g. scripts/join-agent.sh ptsb
    ```

    then **Resume** (the console's button, or `POST /requests/{id}/resume` —
    the same endpoint a `FAILED` job resumes through; there is no callback
    and no work-order queue, by design). A resume that still finds
    the server absent goes back to `BLOCKED` rather than failing, as many
    times as it takes — `BLOCKED` never expires into `FAILED`. Prefer
    `hosted_on` unless the point of the demo is to show a server being stood
    up: it costs zero extra containers, and is the only shape that fits
    alongside a third-party backend on a 16 GB host.
    - **`verified: false` at `ACTIVE` means the reachability call did not
      pass — read `verified_by`, which says why.** Two historical causes are
      fixed and should not recur: a propagation wait that left
      `join.r1_verify` ~20s of a shared budget (fixed by giving that step its
      own `R1_RETRY_BUDGET`), and the contract-conformance comparison being
      applied to the probe's own intended 404, which made *every* join with a
      published service unverified regardless of hosting. If you see it
      again, `verified_by` distinguishes them: `unreachable for 54 attempts`
      is the first shape, `does not match its contract` the second.
      Either way nothing is necessarily wrong with the member —
      `scripts/acceptance.sh`'s `2.7.r1(<code>.<service>)` asserts the same
      fact independently — but there is no way to flip the flag afterwards:
      `POST /requests/{id}/resume` refuses on an `ACTIVE` record, and
      `join.r1_verify` is already `last_completed_step` so a resume would
      skip it.
    - **A busy host port is a failure, not a re-allocation.**
      `scripts/join-agent.sh` checks the two ports `hurl/generate.py`
      allocated to that server (`lsof`) before starting anything, and refuses
      with the port number and the process holding it. It will not pick a
      different port: `hurl/topology.json`, `hurl/topology.sh`,
      `hurl/compose.members.yml` and the console's "copy as curl" all already
      name the allocated one, and the determinism the Global Constraint
      depends on (same member set → same allocation, always) is worth more
      than saving one `kill`. Free the port and re-run. (The AirPlay range —
      5000–5099 and 7000, which macOS's ControlCenter *hangs* rather than
      refuses — is already excluded by `generate.py` at allocation time; this
      check is for everything else on a particular machine.)
    - **Working in a git worktree:** `writer.apply_real()`'s dirty-checkout
      guard runs `git status` inside the container, and a worktree's `.git`
      is a *file* pointing at an absolute host path inside the main
      checkout's `.git` — which the container has no mount for, so every
      approval used to 409 with "could not check whether … is a clean
      checkout". `scripts/join.sh` now exports `KP2_GIT_COMMON_DIR`
      (`git rev-parse --git-common-dir`, absolutised) and `docker-compose.yml`
      mounts it at its own host path. Start `join-api` through
      `scripts/join.sh up`, not a bare `docker compose up join-api`, or that
      variable is unset and the old failure comes back.
  - **The OCSP-staleness trap, as it shows up in a join:** the same fault
    this runbook already documents for a stale federation
    (`Server.ClientProxy.SslAuthenticationFailed`) can surface mid-job, on
    any step that crosses a Security Server boundary — `job.py` detects the
    exact marker string and rewrites the FAILED message with the same
    explanation given below ("this is the Test CA's OCSP responses going
    stale ... not a certificate or configuration fault in this join"),
    naming the fix: redeploy fresh (`scripts/teardown.sh --purge`, then
    `hurl/run-linkup.sh`) and resume — not "debug this join's config",
    which is what the raw fault text alone would suggest.
  - **A real bug this pack's own live proof found and fixed:** the
    `apps/join-api` container image (`python:3.12-slim`) shipped with no
    `git` binary, but `writer.apply_real()`'s dirty-checkout guard
    shells out to `git status --porcelain` against the mounted monorepo —
    every approval failed with a 500 until the Dockerfile installed it. If
    approving a request ever 500s with `FileNotFoundError: ... 'git'`
    again (e.g. after rebuilding the image differently), that guard is the
    first place to look.
  - **A long but not misconfigured `r1` URL:** a real third-party backend
    served under its own path prefix (Joget DX serves under `/jw/`, plus an
    app and version segment) combines with X-Road's `r1`
    call form (`/r1/<instance>/<class>/<member>/<subsystem>/<service>/...`)
    to produce a long, ugly-looking consumer-side URL. That length is
    expected — X-Road's own `servers.url` from the joining member's OpenAPI
    document supplies the forwarding target, path prefix included; nothing
    about a long URL here indicates a misconfiguration.
- **Un-join via the API (automated):** `DELETE /members/<key>`, operator
  token, same `X-KP2-Console: 1` header as everything else:

  ```
  curl -X DELETE -H "X-KP2-Console: 1" \
       -H "Authorization: Bearer $KP2_JOIN_OPERATOR_TOKEN" \
       http://localhost:8091/members/ptsb
  ```

  It walks that member's completed steps **backwards** — revoke the ACL,
  delete the service description, unregister the client, delete the client,
  delete its SIGN key, delete the member on the Central Server (six calls,
  the order established live: `docs/decisions/xroad-770-notes.md` §11) — then runs
  `scripts/member.sh remove <key>` for you. States go `ACTIVE` → `RETIRING`
  → `RETIRED`; poll `GET /requests/{id}` or watch the console's join tab,
  which renders both.
  - **There is no un-join button in the console, deliberately.** The join tab
    shows an agency arriving; a destructive control is a different act for a
    different audience, and the console has none today. It renders `RETIRING`
    and `RETIRED` like any other state (a `RETIRED` card stays in the list —
    a card that vanished mid-demonstration would read as a bug) and leaves
    the DELETE to this command, which is also where the Docker cleanup below
    has to be run anyway.
  - **Canonical members are refused** before anything happens, naming the
    reason: `manifest.yaml`'s `identifiers:` block is the frozen KP3/KP4
    cross-pack contract and a demonstration un-join must never change it.
  - **A member with its OWN Security Server leaves two Docker commands
    behind.** The API never touches Docker (same split as
    `scripts/join-agent.sh`), so the record carries the instruction and you
    run it — four commands, in the order the record prints them: export the
    archive, `docker rm -f <dns>`, then `docker volume rm kp2-<key>-db
    kp2-<key>-conf kp2-<key>-archive`. Skip the deletes and the next member
    to reuse that key inherits the old database and `/etc/xroad`.
    **On retention:** the *first* command is the retention step, and it comes
    before the deletes on purpose:

    ```
    mkdir -p out/retired
    docker run --rm -v kp2-<key>-archive:/from -v "$PWD/out/retired:/to" \
      python:3.12-slim@sha256:<the digest the record prints> \
      tar czf /to/kp2-<key>-archive.tar.gz -C /from .
    ```

    `kp2-<key>-archive` is the message-log archive. The message log is
    subject to a statutory retention period that this retirement does not
    end, and deleting the volume before that period elapses converts a
    retirement into an evidence gap (onboarding path §2 GX). The tarball
    under `out/retired/` is what this pack can honestly do about that: it
    preserves the evidence, and it is **not** a retention regime — a real one
    is a storage and access-control commitment with its own expiry, off this
    Docker host, which remains production's to build. The record carries the
    output path as `retire_instruction.archive_export` so it can be checked
    without reading the message.
    A **hosted** member gets no instruction at all here: its message-log
    records live in its host's archive volume, which the un-join never
    touches. That member's retention story is the host's, not the leaver's.
  - **A hosted member leaves a SIGN key behind** on somebody else's Security
    Server — `REGISTERED`, active, good OCSP, and nothing in X-Road's admin
    API ever collects it (`docs/decisions/xroad-770-notes.md` §11). Deleting it is part
    of the walk, not optional cleanup: without it a host accumulates one
    orphaned signing key per member that ever left.
  - **Interrupted halfway?** Re-issue the same `DELETE`. Every reversal is
    guarded by a read that proves whether it is already gone, so the walk
    re-runs from the top and skips what is done. `POST /requests/{id}/resume`
    is *not* the way back — that one re-enters the forward path.

## The service catalogue

`onboarding/catalogue.yaml` answers, for the whole instance, what is
published on this bus: one row per published service with its X-Road service
id, contract, semantic entity and exchange pattern, lawful basis, the
subjects its ACL names, and links to the signed SLA and the service's own
`onboarding/<key>/04-catalogue/<code>.md` entry. It states on its face that
appearing in it grants nothing — publication is not permission, and the two
are the easiest pair on the bus to confuse.

- **Read it over HTTP:** `GET /catalogue` on the join API returns the same
  derived data as JSON, re-read from the configs on every call. It takes the
  **applicant** token, not the operator one — the reader who needs a
  catalogue is a body that has just joined or is deciding whether to, and
  the operator credential would put discovery back behind the people who
  already know:

  ```
  curl -H "X-KP2-Console: 1" \
       -H "Authorization: Bearer $KP2_JOIN_APPLICANT_TOKEN" \
       http://localhost:8091/catalogue
  ```

  It is read-only, has no write path, and never talks to X-Road.
- **Read it in the console:** tab **5 · What's on the bus**
  (`scripts/console.sh up`, `http://localhost:8090`) renders the same rows
  with the disclaimer on top. It reads `onboarding/catalogue.yaml` off the
  pack mount directly, not `GET /catalogue` — so it needs no applicant token
  and works with `scripts/join.sh down`, which is the normal state in a
  Tier-1 demo. The trade is that it shows the *file*: a member joined since
  the last `scripts/render-onboarding.sh` is absent until the file is
  regenerated. That is the file's documented semantics, not a console bug,
  and the tab names its source on screen. Tab 3 and tab 5 are the pair that
  makes "publication is not permission" concrete: tab 3 shows a caller
  refused a service that tab 5 lists.
- **This is `listMethods`, not `allowedMethods`.** It tells you what was
  registered here, not what the bus will let *you* call. The response says
  so in a field of its own, and the two answers differ: a service can be in
  this catalogue and refuse your call, because the ACL is the provider's.
  There is no `?subject=` filter, deliberately — filtering to the services
  whose ACL already names a subsystem would answer *what the register
  recorded*, which is not the same question, and the gap between the two is
  where an operator gets a wrong answer at the worst moment.
- **Regenerate the file:** `scripts/render-onboarding.sh`. It is derived wholesale
  from `manifest.yaml` + `configs/member-*/` every time — nothing appends a
  row and nothing deletes one, so regenerating from unchanged inputs
  produces the same bytes, and a member whose config is gone is simply not
  found.
- **A join and an un-join through the API regenerate it for you**, the join
  after the member's own record is written, the un-join after
  `scripts/member.sh remove` has taken the config away.
- **`scripts/member.sh remove` on its own does not.** It is the deliberately
  dumb config-removal path with no API behind it, so the catalogue keeps
  naming that member's services until someone runs
  `scripts/render-onboarding.sh`. Removing a member by hand and regenerating
  are two commands, in that order.
- **The member's own `04-catalogue/` entries survive a removal**, exactly as
  the rest of its `onboarding/<key>/` record does — that record is evidence
  of what was published and what the operator revoked. The aggregate is the
  live view; the record is the history.
- **A join refuses to start on an uncommitted `onboarding/`**, and
  `catalogue.yaml` is the first shared file a join writes there. Commit it
  after a join, or the next one stops with `DirtyCheckoutError`.
- **A join that fails partway restores what it wrote.** `writer.apply_real`
  snapshots `manifest.yaml`, `onboarding/catalogue.yaml`,
  `configs/member-<key>/` and `onboarding/<key>/` before its first write and
  puts them back on any failure, then re-runs `hurl/generate.py` over the
  restored inputs. So a failed approval does not leave a dirty tree blocking
  the next one. The single exception announces itself: `RollbackFailure`
  (a 500, and `FAILED` on the request) means the restore itself failed and
  the tree needs a human — check the four paths against git, then re-run
  `hurl/generate.py`.

## Reaching the stack from another machine

Every port binds to `127.0.0.1` by default (`deployment.yaml`'s
`network.bind`) — reachable from this host, from nowhere else. To reach it
from another machine, use SSH local port forwarding, not a bind change:

```
ssh -L 4000:localhost:4000 -L 8090:localhost:8090 user@host
```

(repeat `-L` per port needed — the admin-UI table above, or `8090` for the
console). This keeps the host's own exposure at zero while still letting a
remote workstation reach it. Setting `network.bind` to anything else
publishes the X-Road proxy ports — which have no authentication of their
own — to whatever that address reaches; see `scripts/lib-stack.sh`'s refusal and
`docs/production-delta.md` before ever doing that on a shared or public host.

## Known traps

Global-conf propagation delays (retry, do not fail); the CSR is generated in DER but
must be downloaded as PEM and posted to the Test CA with a *filename*; the Test CA's
certs are already named `ca.pem` / `ocsp.pem` / `tsa.pem` in `/home/ca/certs` and are
shared into the runner by `hurl/compose.hurl.yml` — no renaming, no `docker cp`;
certificate profile is FiVRK, which validates the country code (`C=FI` — an artefact
of the demo CA, not a claim about Progressa); services stay disabled after they are
added until enabled; the consumer subsystem's connection type must be HTTP for the
demo call (default HTTPS expects a client TLS certificate); the admin APIs
authenticate by session login and XSRF token, not by API key.

A security server's Test CA-issued OCSP response has a bounded freshness window:
after roughly ten hours idle, the signer starts rejecting the server's own
authentication certificate (`IncorrectValidationInfo: OCSP response is too old`),
which then fails every cross-server call through it with
`Server.ClientProxy.SslAuthenticationFailed` — not an access-control problem,
and not specific to the demo console. If a federation has been sitting up for
hours before a demo, redeploy fresh (`scripts/teardown.sh --purge` then step 2)
rather than trusting a stale stack.

macOS hosts: port 5000 is not used for any admin UI here (ss-pnia is 5100) because
macOS's AirPlay Receiver listens on 5000 by default and silently hangs the
connection rather than refusing it — a genuinely confusing failure mode
(containers report healthy; the admin API call just hangs).

Pin discipline: the scenarios are written against X-Road **7.7.0** and the compose
images are pinned to it. `Docker/xrd-dev-stack` does not exist before 7.5.0 and is
gone on `develop` — read the reference at the tag you deploy.

> Reproducible: every step is a script in `scripts/` or a scenario in `hurl/`, and
> every one of them is generated from `configs/` by bb-config-gen or
> `hurl/generate.py`. Do not hand-edit a config in `configs/`, a scenario in
> `hurl/scenarios/`, or `hurl/vars.env` — regenerate them.
