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
reads the SQLite join store, same as everything else in this section). A real hosted join (`apps/join-api`, `POST /requests` → approve →
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
- **The spec fetch at request-validation time runs in a separate,
  credential-free container.** When a payload's `spec_url` and the
  `servers[].url` inside the OpenAPI document it returns are fetched
  during `POST /requests` (check 9a `spec_url_origin` and check 9
  `backend_reachability`, `validate.py`), that fetch is delegated over HTTP
  to `apps/spec-fetcher/` rather than made in-process from `join-api` —
  `join-api` holds `JOB_SECRETS` and a route to every admin API on
  `linkup`; `spec-fetcher` holds neither, and sits on its own `specs`
  network (`internal: true`), which has no route to `cs`/`ca`/any `ss-*`
  and no external egress (`docs/production-delta.md` row 41). `join-api`
  fails the request closed — `backend_reachability`, naming
  `SPEC_FETCHER_URL` — if that service is unreachable, rather than ever
  falling back to fetching the applicant's URL itself. `scripts/join.sh up`
  brings `spec-fetcher` up alongside `join-api` automatically; there is no
  separate command to start it. **This is a different fetch from `member.sh
  drift`/`refresh` below**, which run *after* a member is already joined,
  from an operator-invoked shell inside `join-api` on `linkup` — that path
  did not move, is not applicant-triggered at request time, and is a
  narrower, already-vetted-member surface than the one row 41 closes.
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
  generated by `scripts/gen-secrets.sh`; under the droplet posture
  (`KP2_JOIN_APPLICANT_TOKEN=disabled`, below) this shared value no longer
  authenticates anything — submit on an issued per-agency token instead,
  "Issuing an agency its own credential" below), review its computed config diff,
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
  - **Committing before go-live (`join_workflow.commit_gate: required`,
    the droplet target's posture):** `deployment.yaml`'s default,
    `advisory`, is what everything above describes — the console shows a
    live-but-uncommitted flag, nothing gates. Flip it to `required` and
    approval inserts a `config.commit` step before the job ever touches the
    running federation: it checks (read-only — `git status`, never `git
    commit`) that `configs/member-<key>/`, `manifest.yaml`,
    `onboarding/<key>/` and `onboarding/catalogue.yaml` are committed —
    `writer._written_paths()`'s own four paths, everything a join actually
    writes, scoped to this one join rather than `writer.apply_real()`'s
    whole-tree pre-write refusal. Clean, it passes silently and the job
    proceeds. Dirty (the ordinary case — the config was only just written),
    the request goes **`BLOCKED`** with `{step: "config.commit"}` and a
    message naming the exact host-side commands:

    ```
    git add configs/member-<key>/ manifest.yaml onboarding/<key>/ onboarding/catalogue.yaml
    git commit -m "join: add member <CODE>"
    ```

    Commit, then **Resume** (the console's button, or
    `POST /requests/{id}/resume`, same endpoint every other `BLOCKED`/`FAILED`
    record uses) — the gate re-checks and, now clean, the job carries on to
    make the member live. `docs/production-delta.md` row 33 is the row this
    closes; `apps/join-api/tests/test_job.py` is where "dirty → BLOCKED",
    "clean → proceeds" and "resume re-enters the gate" are each their own
    test.
  - **Disabling the shared applicant token (`KP2_JOIN_APPLICANT_TOKEN=disabled`,
    the droplet target's posture):** the shared credential described above
    ("Issuing an agency its own credential") is what makes a zero-setup
    demo possible, and it is exactly the row `docs/production-delta.md`
    (row 28) calls out for a real federation: "the shared demo credential
    must be disabled". Set `.env`'s `KP2_JOIN_APPLICANT_TOKEN` to the
    literal string `disabled` — not empty; `docker-compose.yml`'s `:-`
    default already passes an absent value through, so absence would
    silently mean "disabled" the moment this line was forgotten, and
    `disabled` is meant to be a deliberate, greppable act instead.
    `require_applicant` (`apps/join-api/app.py`) then skips the
    shared-token comparison entirely: every applicant call must arrive on
    an issued per-agency credential (above). The console is unaffected —
    its join tab holds the *operator* token server-side
    (`docker-compose.yml`'s own comment on that service), never the
    applicant one. `KP2_JOIN_OPERATOR_TOKEN` cannot be disabled the same
    way — `join-api` refuses to start if it is set to the literal string
    `disabled`.
  - **Per-request ownership (`join_workflow.enforce_ownership: true`, the
    droplet target's posture):** `deployment.yaml`'s default, `false`, is
    what everything above describes — any applicant or operator credential
    may read any request record. Flip it to `true` and `GET /requests/{id}`
    **404**s (not 403 — no existence oracle, the same posture the
    path-traversal case above has) for anyone but the operator, the issued
    `applicant:<name>` credential whose name matches the record's
    `submitted_by`, or — only while the shared token above is still
    enabled — the shared applicant reading a `submitted_by: null` record.
    Submission and the operator queue are unchanged. This switch is only
    meaningful in combination with disabling the shared token above: with
    it still enabled, every hand-typed applicant call shares one identity,
    so ownership only ever protects per-agency (issued-token) records from
    each other, nothing more. `docs/production-delta.md` row 28 is the
    second half of the row this closes.
  - **Narrowing `allowed_backend_auth` (`configs/x-road-bus/join-policy.yaml`,
    the droplet target's posture):** the committed policy's demo default
    lists all three `schema.BackendAuth` values (`none`, `network_allowlist`,
    `proxy_injected`), so the PTSB fixture and every mock backend in this
    pack — which all actually speak `none` — keep passing. A real
    federation should list `[network_allowlist, proxy_injected]` only:
    `backend.auth: none` means the consumer holds the provider's own API
    credential (`docs/production-delta.md` row 30). With `none` removed, a
    join declaring it is **REJECTED** at request time
    (`apps/join-api/validate.py`'s `allowed_backend_auth` check), naming
    `join-policy.yaml` in the rejection message, rather than merely
    discouraged in prose.
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
  - **Commit the removal.** `scripts/member.sh remove` (which the walk calls
    for you, at the end) deletes `configs/member-<key>/` from the checkout
    the same way `apply_real()` writes it on the way in — before anyone
    commits. This phase does **not** gate un-join on a commit the way it
    gates a join (`join_workflow.commit_gate: required` above): a member no
    longer on the federation but still described in git is the safe
    direction to drift in, so blocking the removal itself would trade a
    smaller risk for a bigger one (a request an operator cannot finish
    retiring). Instead the `RETIRED` record carries `commit_pending: true`,
    set once and cleared by nothing — evidence, like `retire_instruction`,
    that this is still owed. Commit it the same way a join is committed:
    `git add configs/ manifest.yaml onboarding/<key>/ onboarding/catalogue.yaml
    && git commit`.

## Observability: structured logs and `/metrics`

Backend-agnostic (docker-local and the droplet target alike) — Task 5,
Phase E of `docs/plans/production-hardening-plan.md`, `docs/production-
delta.md` row 34. A *surface*, not a monitoring system: nothing scrapes
`/metrics` and nothing ships the logs anywhere by default. Turning that
into monitoring is an operator action, not something this pack does for
you.

**Structured logs.** Both `join-api` and `console` write one JSON object
per line to stdout (`docker logs join-api` / `docker logs console`), built
by `apps/join-api/join_logging.py` / `apps/console/console_logging.py` --
stdlib `logging` only. Every record is scrubbed of the credentials this
process holds (`job.scrub(..., JOB_SECRETS)` on join-api; the equivalent on
console) before it is formatted, the same guard subprocess output already
gets -- a log line can never carry the admin password, the token PIN, or a
bearer token. join-api additionally:

- stamps a fresh `request_id` (contextvar, also returned as the
  `X-Request-Id` response header) onto every log line for the duration of
  one HTTP call, and into that call's `request_events.detail` row, so a log
  line and its audit-table row join on the same value;
- stamps `join_id` -- the join request's own record id -- onto every log
  line touching that join (submit, approve, resume, every job/unjoin step,
  the terminal state), so `docker logs join-api | grep '"join_id":"<id>"'`
  greps one join's whole lifecycle across the several separate HTTP calls
  and the background thread the job runs on.

**`GET /metrics`.** Prometheus text format, hand-rolled (no
`prometheus_client`). Gated by the operator token, the same
`require_operator` dependency every other operator route uses -- **not**
the console-origin header, which a real Prometheus scrape never sends.
Exposes: join requests by state, the store's held-record count and quota
ceiling (`store.count_requests`, the same query the 200-record refusal
uses -- not a second counter), 429 refusals since process start, job/unjoin
steps completed vs. failed, and a job-duration summary.

```
curl -sk -H "Authorization: Bearer $KP2_JOIN_OPERATOR_TOKEN" \
  http://127.0.0.1:8091/metrics
```

Prometheus scrape config (`prometheus.yml`), for whichever host can reach
`XROAD_BIND:8091` -- loopback-bound by default (`docker-compose.yml`), so a
Prometheus server not on the same host needs the same reverse-proxy/
network story `docs/deployment-targets.md` already asks for everything
else on this stack:

```yaml
scrape_configs:
  - job_name: kp2-join-api
    metrics_path: /metrics
    static_configs:
      - targets: ["127.0.0.1:8091"]
    authorization:
      credentials_file: /run/secrets/kp2_join_operator_token  # KP2_JOIN_OPERATOR_TOKEN, not inline
```

## The Postgres join store (droplet target)

Everything above assumes `datastore.kind: sqlite` — docker-local's default,
`out/join-store/join-store.sqlite3`. The droplet target can instead point
the join store at a DigitalOcean Managed PostgreSQL cluster. This section
is the operator-facing "how do I run this" for that target.

**No DigitalOcean credentials were available while this section was
written.** Every procedure below is a real, runnable operator procedure,
but wherever it calls for a *live* drill against an actual cluster or
droplet, it is marked **Status: documented, not yet run** — a plain fact
to say, not something to paper over.

**Provisioning.** `cd infra/terraform-db && terraform init
-backend-config=backend.hcl`, then the standard `terraform apply` flow —
the module's own comments (`main.tf`, `variables.tf`) are the reference for
what each resource and variable does; this is "how an operator invokes it,"
not a second explanation of the module.

`var.droplet_id` has no default and must come from the droplet module
itself, in two steps: apply (or confirm already applied) `infra/terraform/`
first, then read its output —

```
cd infra/terraform/ && terraform output -raw droplet_id
```

— and pass that value into the DB module's own apply. `do_token` is a
secret — export it as `TF_VAR_do_token` rather than putting it on the
command line with `-var`, the same reason `$KP2_JOIN_DB_URL` never reaches
argv in the export/import scripts below (`ps auxww` shows any local user
the full command line for as long as `apply` runs); `droplet_id` isn't a
secret and stays on argv as `-var`, matching `infra`'s own implementation
plan and `variables.tf`'s own `do_token` description, which already
document `TF_VAR_do_token` as the convention:

```
cd infra/terraform-db
export TF_VAR_do_token="$DO_TOKEN"
terraform apply -var="droplet_id=<the id just read>"
```

There is no cross-module state reference by design (`variables.tf`'s own
comment on `droplet_id`) — a plain variable, not `terraform_remote_state`,
so destroying the droplet module can never touch the DB module's state.

After a successful apply:

- `scripts/fetch-db-ca-cert.sh` — fetches the cluster's CA certificate (a
  public cert, not a secret) to wherever `KP2_DB_CA_CERT` points, per
  `.env.example`'s documentation.
- `deployment.yaml`'s `datastore.kind: postgres` — an explicit, deliberate
  switch an operator makes for the droplet target only. It stays `sqlite`
  by default and for docker-local. **Set this before the bootstrap step
  below** — `python -m store init` (like every other `store.py` entry
  point) reads `deployment.yaml`'s `datastore.kind` to decide which
  backend to talk to, and it defaults to `sqlite` — running the bootstrap
  step before this one silently initialises a local SQLite file instead
  of the Postgres cluster, and neither the joinapi role nor the append-only
  guarantee this whole procedure exists to establish ever gets touched.
- `deployment.yaml`'s `join_workflow.commit_gate: required` — likewise, an
  explicit switch for the droplet target only ("Committing before go-live"
  above); `docker-local` keeps the default `advisory`. Order does not matter
  against the two steps above (it is read by `join-api` at approval time, not
  by the store bootstrap), but set it before the first real join on this
  target — flipping it after a member has already gone `ACTIVE` closes the
  window for every join from that point on, not retroactively for one that
  already went live under `advisory`.
- **Bootstrap the schema — using the ADMIN DSN, before `.env` ever points
  at the joinapi DSN below.** This step must run first (now that
  `datastore.kind: postgres` is set above), and it must run as admin,
  precisely so `joinapi` never becomes table owner:
  ```
  export KP2_JOIN_DB_URL="$(cd infra/terraform-db && terraform output -raw db_admin_dsn_template)"
  docker compose run --rm -T -e KP2_JOIN_DB_URL join-api python -m store init
  ```
  `migrations/grants.sql`/`001_init.sql` both say the deployment shape is
  "schema bootstrapped once by an owner/admin role, joinapi never owning
  the tables" — this is the step that makes that actually true, not just
  documented. Skip it (or start join-api against the joinapi DSN first
  instead) and `_pg_init` runs the migration connected AS joinapi on
  join-api's own first start: on a correctly-locked-down PG15+/16 cluster
  `joinapi` lacks `CREATE` on `public` and the app crash-loops with a raw
  `InsufficientPrivilege`; on a cluster where DO's default role setup
  happens to grant `CREATE`, the migration "succeeds" but `joinapi`
  becomes table **owner**, which silently defeats every GRANT in
  `grants.sql` (ownership bypasses GRANTs entirely). `store.py`'s startup
  path now refuses to start in that second case too, loudly — but doing
  this step, in this order, is what avoids hitting either failure mode at
  all.
- `terraform output -raw kp2_join_dsn_template` (from `infra/terraform-db/`)
  gives the ready-to-paste `KP2_JOIN_DB_URL` line for `.env` — quote it
  (`.env` is shell-sourced, and the DSN's `&` backgrounds the rest of an
  unquoted assignment, silently truncating it). It carries the real,
  DO-generated `joinapi` password: handle it the same way every other
  `.env` secret already is — mode `600`, never logged, never echoed in
  full. `terraform output -raw joinapi_ro_dsn_template` is the same shape
  for the optional `KP2_JOIN_DB_URL_RO` (the `joinapi_ro` role's DSN,
  preferred by `store.py`'s host-side `dump-records`/`check` when set —
  see `.env.example`).
- Only now, with `datastore.kind: postgres` already set and the schema
  already bootstrapped as admin above, set `.env`'s `KP2_JOIN_DB_URL` to
  the **joinapi** DSN (not the admin DSN used for bootstrapping) and start
  join-api normally.

**Rotation.** Reset the role's password in DO's console, update `.env`'s
`KP2_JOIN_DB_URL`, `docker compose up -d join-api` — measured in seconds of
downtime, documented rather than automated.

**Backup, restore and recovery — Postgres join store.** This is a
**different mechanism** from this file's own "Backup / inspect the join
store" bullet above (that one is `sqlite3 ... VACUUM INTO`, and does not
apply once `datastore.kind: postgres`), and from
`docs/deployment-targets.md`'s "Backup, restore and recovery time" section
(that one is about X-Road's own admin-API backups — GPG-signed, tied to
the `*-conf` volumes `teardown.sh --purge` deletes — which has nothing to
do with the join store either way). What follows is the Postgres-specific,
droplet-only mechanism.

- **Restore drill. Status: documented, not yet run.** Fork or
  restore the cluster to a new instance from DO's point-in-time recovery
  (console or API), point a scratch join-api at the restored instance's
  DSN, then read the records back:
  ```
  docker compose run --rm -T join-api python -m store dump-records
  ```
  A correct restore is every pre-fork record present, byte-identical.

- **Two monitoring queries, or scrape `/metrics`** — a starting point, not a
  monitoring system; `docs/production-delta.md` says so explicitly. The two
  queries run against the schema in `apps/join-api/migrations/001_init.sql`
  (Postgres/droplet only, below); `GET /metrics` (Task 5, Phase E) is the
  same starting point on **either** backend — see "Scraping `/metrics`"
  right after this bullet.

  Refusals per token per hour (`actor` is a 12-character SHA-256 prefix of
  the bearer token, never the token itself — `app.py`'s `_refusal_actor`;
  `event` is `rate_limit` for the per-minute bucket or `quota` for the
  200-record ceiling):
  ```
  docker compose run --rm -T join-api sh -c 'exec psql "$KP2_JOIN_DB_URL" -c "SELECT actor, date_trunc(\$\$hour\$\$, at) AS hour, count(*) AS refusals FROM request_events WHERE event IN (\$\$rate_limit\$\$, \$\$quota\$\$) GROUP BY actor, hour ORDER BY hour DESC, refusals DESC;"'
  ```

  Requests by state:
  ```
  docker compose run --rm -T join-api sh -c 'exec psql "$KP2_JOIN_DB_URL" -c "SELECT state, count(*) AS n FROM requests GROUP BY state ORDER BY n DESC;"'
  ```

  Both read `$KP2_JOIN_DB_URL` from the container's own environment
  (`docker-compose.yml`'s pass-through) rather than the host command line —
  the same discipline `scripts/join-store-export.sh`/`-import.sh` use, for
  the same reason: a DSN on the host's own argv is visible to any local
  user via `ps auxww` for the run's duration.

- **`pg_restore` version skew — a known, live-confirmed false failure.**
  The image's `pg_restore` (Debian's client, 17.11 as built) emits a
  `transaction_timeout` GUC error against any DO cluster running Postgres
  below 17, on the very first statement it issues — a PG17+-only setting an
  older server does not recognise. `join-store-import.sh` deliberately does
  not try to distinguish that from a real failure, so it reports the run as
  **failed** even when the data landed correctly. **Mandatory, not
  optional:** after every import, regardless of `join-store-import.sh`'s
  own exit code, verify with
  ```
  docker compose run --rm -T join-api python -m store dump-records
  ```
  and confirm the expected records are present. If the only error text
  `join-store-import.sh` printed was about `transaction_timeout` (or any
  other GUC a newer client emits that the target server does not
  recognise) and `dump-records` shows the records, the import succeeded
  despite the exit code.

- **Import needs the admin DSN, not the app's own DSN.**
  `join-store-import.sh` restores via `KP2_JOIN_DB_ADMIN_URL` when it is
  set (falling back to `KP2_JOIN_DB_URL` otherwise) — set it from
  `infra/terraform-db`'s `db_admin_dsn_template` output for a real restore.
  The dump's `ALTER TABLE ... OWNER TO` and `request_events_seq_seq`'s
  `setval(...)` calls both need admin-level privilege the app's own
  restricted `joinapi` role deliberately lacks (`migrations/grants.sql`
  grants `joinapi` `SELECT` on that sequence, for `pg_dump`'s benefit, but
  never `UPDATE`). `KP2_JOIN_DB_ADMIN_URL` is a one-off, provision-time
  value — export it for the one import run; nothing in this pack writes it
  into `.env`, and it should not be kept alongside `KP2_JOIN_DB_URL` as a
  standing secret.

**Lifecycle procedures — the deployment's actual operating mode.**
This deployment is not always on: that is not an edge case for this
target, it is how it runs. The cluster is the persistent evidence layer;
the droplet's own stack is ephemeral around it. Four events, four rules.

- **§6.1 — `teardown.sh --purge` → redeploy → reconciliation.** After a
  redeploy, run:
  ```
  docker compose run --rm -T join-api python -m store check
  ```
  Non-empty output is a **named finding, not an error**. Two directions,
  two remedies:
  - A store record (`ACTIVE`/`RETIRING`) with no matching `manifest.yaml`
    entry — the member's config did not survive whatever changed between
    destroy and redeploy (a config rolled back in git, for instance).
    Remedy: `scripts/member.sh remove <key>` if the member is genuinely
    gone, or restore the config and re-join if it should still be there.
  - A `manifest.yaml` entry with `origin: joined` and no matching
    **`ACTIVE`** record (a `RETIRING` record does not count here — a
    retiring member's manifest entry outliving its last `ACTIVE` record is
    expected, not drift) — the manifest says a member joined but the store
    disagrees. Remedy: a re-join, or a `git revert` of whatever added the
    manifest entry.

  Status: the mechanism itself (`python -m store check`) is implemented and
  exercised by this pack's own test suite; running it against a real
  purge/redeploy cycle on a live droplet is documented, not yet run.

- **§6.2 — droplet destroyed and re-created.** Three things must be
  restored, and today nothing in this pack's CI automates any of them — a
  human has to notice and act:
  1. **`.env`'s `KP2_JOIN_DB_URL`.** The droplet's copy dies with it. The
     DB password is DO-issued, not regenerated the way
     `scripts/gen-secrets.sh` regenerates the X-Road demo credentials —
     carry the existing value forward, or reset it in DO's console (the
     Rotation procedure above) and update `.env` either way.
  2. **The DB firewall's trusted-source entry.** DO's firewall trusts the
     droplet as a *resource*, not a stable IP — a re-created droplet is a
     new resource, and until it is trusted, every connection is refused at
     the network layer. Re-adding it **is** re-running `terraform apply` in
     `infra/terraform-db/` with the new droplet's `droplet_id` (same
     two-step sequence as first provisioning, above) — there is no
     separate "add to trusted sources" script, by design; the
     firewall resource's one rule is keyed on the droplet id, so a new
     apply with a new id replaces the old rule rather than stacking it.
  3. **The CA cert.** Stable per cluster, but it lives on the droplet's
     disk — re-run `scripts/fetch-db-ca-cert.sh`.
  **Named explicitly:** step 2 is a manual
  step today. Nothing watches for a droplet replacement and re-applies the
  DB module automatically — miss it, and the cluster firewall keeps
  trusting a droplet that no longer exists; join-api loses connectivity
  with no automated signal beyond the resulting connection failures.
  Status: the drill that would exercise this end to end (destroy the
  droplet, re-provision, confirm the trusted-sources step is what makes the
  first connect succeed) is documented, not yet run.

- **§6.3 — cluster destruction gate.** Destroying the cluster is:
  `scripts/join-store-export.sh` (which verifies its own output with
  `pg_restore --list` internally), move the resulting dump somewhere
  durable, **only then** `terraform destroy` in `infra/terraform-db/`.
  `lifecycle { prevent_destroy = true }` on the cluster resource
  (`infra/terraform-db/main.tf`) means a bare `terraform destroy` refuses —
  this is structural, not just procedural, by design: destroying the
  cluster for real requires first either removing that `lifecycle` block or
  `terraform state rm`-ing the resource. Treat having to edit the `.tf`
  file as the backstop working as intended, not friction to route around.
  Status: the export/verify script is implemented and live-verified
  against a local Postgres cluster; the full export → destroy →
  provision-fresh → import → read-back drill against a real DO cluster is
  documented, not yet run.

- **§6.4 — the posture decision.** **Posture (a), cluster persists / stack
  is ephemeral** — chosen over (b) export-then-destroy and over staying on
  the SQLite backend. Evidence continuity is automatic under (a); the
  export script above is a periodic safety net, not a gate that has to run
  on every teardown. The cost: the cluster keeps billing (~$15/month) while
  the droplet is torn down between sessions. The alternative, (b), would
  have made every teardown cycle carry a manual, skip-it-once-lose-records-
  permanently evidence-handling step — judged the worse trade for how this
  deployment is actually used.

**Running `member.sh drift`/`refresh` and the export/import scripts on this
target:**

- **Invocation context.** `scripts/join-store-export.sh` and
  `scripts/join-store-import.sh` shell out to `docker compose run --rm
  join-api ...` to reach the store and touch nothing else network-wise —
  run them from **the droplet's own host shell** (wherever
  `docker`/`docker compose` are actually installed), never from inside the
  join-api container itself: that container has no Docker socket, by
  design. `member.sh drift`/`refresh`'s new Postgres-path branches do the
  same `docker compose run` calls, but — unlike the export/import
  scripts — both commands *also* fetch a joined member's live OpenAPI spec
  from a docker-internal hostname (`spec_url`), which only resolves from
  inside a container on the `linkup` network
  (`docker-compose.yml`'s `networks: [linkup]`), never from the droplet's
  bare host shell. That is two different network requirements in one
  invocation, and they do not fit in the same place — see the two bullets
  below for what each command actually needs. Both of `member.sh`'s new
  Postgres-path subprocess calls at least fail with a clear `docker not
  found — ...` message rather than a raw traceback if run from the wrong
  place.

- **`member.sh drift` on a Postgres deployment: run it from the droplet's
  own host shell, not via `docker compose exec join-api` (unlike the
  SQLite-path guidance above).** `cmd_drift`'s Postgres branch checks for
  `docker` and shells out to `docker compose run --rm join-api python -m
  store dump-records` *before* it ever gets to the spec-fetch step, so
  running it inside the join-api container (which has no Docker socket)
  fails immediately at that check. Running it from the host instead gets
  the store lookup right, but the second half — fetching each service's
  live spec at its docker-internal `spec_url` — then hits the same
  pre-existing trap this file already documents for the SQLite path
  ("could not fetch current spec ... a docker-internal demo hostname like
  this one is only reachable from inside the linkup network"): `drift`
  does not crash, but it cannot verify any service's live endpoint set
  from the host, and reports every service as unable-to-fetch rather than
  as a real drift finding. There is no single invocation context that gets
  both halves of `drift` right on this target with the code as built —
  choosing the host shell is the one that at least reaches the store.

- **`member.sh refresh`'s existing guidance still holds, for the common
  case.** The X-Road admin-API login and the joined member's spec fetch
  (`cmd_refresh`'s pre-existing logic, unchanged by the Postgres addition) need the
  same docker-internal name resolution `drift` does. So `docker compose
  exec join-api bash
  /repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack/scripts/member.sh
  refresh <key>` (the existing guidance, above) is still correct, and it
  still works unmodified on a Postgres deployment **as long as join-api
  itself is up**: the record amendment then goes through join-api's own
  HTTP API (`http://join-api:8000`), which needs no Docker access at all
  from inside that container.

  The Postgres-path direct-write fallback (the new `elif datastore_kind ==
  "postgres":` branch in `cmd_refresh`, only reached when join-api is
  unreachable) has the same collision as `drift` above, but with a harder
  failure mode: `cmd_refresh` calls `sys.exit` on a failed spec fetch
  (rather than `drift`'s per-service warn-and-continue), so it does not
  even degrade gracefully from the host — it stops at that step, before
  ever reaching its own Postgres-path Docker calls. **Practical guidance:**
  if `member.sh refresh` needs to run and join-api is down, bring it back
  up first (`scripts/join.sh up`) and use the normal, API-first path
  rather than relying on the direct-write fallback — on this target, that
  fallback cannot complete end to end from any single invocation context
  as currently built.

- **Audit trail note.** The direct-write refresh fallback records a
  different `request_events.event` value depending on backend:
  `refresh:direct-write` on SQLite, plain `refresh` on Postgres (the
  Postgres path goes through `python -m store amend-refresh`, which calls
  `save_request(..., event="refresh")` — the same event name a normal,
  API-driven refresh uses). An operator grepping the audit log for one
  string will not find the other backend's equivalent entries.

- **CA cert refresh.** `scripts/fetch-db-ca-cert.sh` writes atomically
  (temp file, then move) — but an *already-running* join-api container
  keeps using whatever cert it bind-mounted at its own start. Re-running
  the fetch script alone does not update a live container; a cert refresh
  needs `docker compose up -d join-api` (or equivalent) afterward to
  actually take effect.

- **Cluster tuning: `idle_in_transaction_session_timeout`.** Postgres's
  `apply_lock` (`store.py`) holds one idle-in-transaction connection for
  the whole duration of an approval's apply, including
  `writer.apply_real`'s filesystem/subprocess work — inherent to
  `pg_advisory_xact_lock`, not a bug (see `apply_lock`'s own docstring).
  The cluster's `idle_in_transaction_session_timeout` needs to tolerate
  that. DigitalOcean's managed Postgres exposes this as a configurable
  cluster parameter; this pack does not set it (`infra/terraform-db/main.tf`
  configures no `digitalocean_database_postgresql_config` resource for it),
  and no DO-documented default for it was confirmed while writing this —
  check the cluster's actual configured value against how long this pack's
  approvals typically take to apply, rather than assuming either
  "disabled" or any other specific number.

- **Negative test. Status: documented, not yet run.** From a
  source the cluster's firewall does not trust — a second droplet in the
  same VPC is the meaningful case (its private-network route to the
  cluster's hostname exists, but the firewall trusts only the original
  droplet's resource id, so the refusal is the firewall rule actually
  working, not just an absent route; a workstation off the VPC entirely
  fails even more trivially, with no route to the private hostname at
  all) — attempt:
  ```
  psql "postgresql://joinapi@<db_private_host>:<db_port>/kp2_join?sslmode=verify-full"
  ```
  using the host/port from `terraform output` (password omitted
  deliberately — the connection should never get far enough to need it).
  Expected: a timeout or connection refusal at the network layer, never an
  authentication prompt — DO managed databases have a public endpoint by
  default, but `infra/terraform-db/main.tf`'s `digitalocean_database_firewall`
  trusts only the droplet's resource id, which is what makes that endpoint
  practically unreachable from anywhere else. Doubles, per §6.2 above, as
  the check that a stale trusted-sources entry is actually gone after a
  droplet replacement.

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

  It is read-only, has no write path, and never talks to X-Road. Under the
  droplet posture (`KP2_JOIN_APPLICANT_TOKEN=disabled`, "Disabling the
  shared applicant token" above) that shared value 403s — use an issued
  per-agency token, or the operator token (`require_applicant` accepts it
  too; only *approve/reject/resume* and the operator queue are
  operator-only).
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

`docker-compose.yml`'s `mem_limit`/`cpus` (base-compose hardening,
`docs/plans/production-hardening-plan.md` Phase A) are a ceiling sized with
real headroom over the measured steady-state figures above, not a corset --
but if a container genuinely leaks past it, the OOM kill does **not** show up
as an OOM message anywhere this pack's own tooling looks, and on a Security
Server it usually does **not** even stop the container. A Security Server
runs supervisord over several JVMs (signer, proxy, the admin service, the
add-ons); the kernel's cgroup OOM killer picks whichever process is using
the most memory at the moment the cgroup's limit is hit, which is normally
one of those JVMs, not PID 1 (supervisord). supervisord restarts the killed
process on its own, the container itself never exits, `docker inspect
--format '{{.State.OOMKilled}}'` stays `false`, and `restart: unless-stopped`
never fires -- so that check only tells you something when the *whole*
container was killed (a smaller process, like a mock or console/join-api,
where the app itself is PID 1). What `acceptance.sh`/`scripts/verify.sh`
actually see in the JVM case is a `healthy` Security Server that starts
failing its healthcheck (the killed JVM stops answering :4000, or the
add-ons stop running under `supervisorctl status`) without the container
ever restarting. Two diagnostics that work regardless of which process was
picked: `docker inspect <container> --format '{{json .State.Health.Log}}'`
(the healthcheck's own recent failures, which may show the transition even
when `OOMKilled` doesn't) and, on the host, `journalctl -k | grep -i oom` (or
`dmesg | grep -i oom` where journald isn't available) -- the kernel logs
every OOM kill it performs, container-internal or not. Check both before
assuming an unexplained healthcheck flap is the propagation flake described
below.

One more consequence of setting `mem_limit` worth knowing, not just a
ceiling: a container-aware JVM (the default since JDK 10, and what these
images ship) sizes its default heap off the **cgroup memory limit**, not the
host's total RAM, once one is set. Before Phase A, every JVM in this pack
sized itself off the laptop's full RAM; now it sizes off whatever
`mem_limit` says instead -- a behaviour change, not just a ceiling. The
measured steady-state figures in the table above **predate** `mem_limit`
existing at all (they were captured while every JVM still sized off host
RAM), and `mem_limit` was set with headroom over those pre-`mem_limit`
numbers, not re-measured against a JVM now sizing its heap off the new
cgroup limit -- worth a `docker stats --no-stream` pass against a live
`cs`/`ss-*` once this has run for a while, to confirm the new sizing
ergonomics land where headroom was assumed rather than closer to the
ceiling. A future change to `mem_limit` is therefore not purely "raise or
lower the ceiling" -- it also changes how much heap the JVM inside decides
to use, which can itself change steady-state memory behaviour.

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
