# KP2 build pack — run book

Stand up the Linkup demonstration federation (X-Road 7.x) from zero on one Docker
host, and run the once-only exchange that proves it. Demo only — see
`docs/production-delta.md` before anyone mistakes this for a deployable platform.

## Prerequisites

- Run `scripts/preflight.sh` first -- checks for Docker, Docker Compose v2,
  `jq`, `curl`, `python3` 3.9+ with PyYAML, a SHA-256 tool, and bash 4+, and
  reports every gap at once rather than one at a time as the deploy hits
  each of them (D11). Checks only -- it prints the install line for each
  gap it finds instead of running it. `hurl/run-linkup.sh` also runs this
  automatically before it does anything expensive; running it by hand here
  first is what turns a mid-deploy failure into a pre-deploy one.
- Docker ≥ 24 with Docker Compose ≥ 2.24.
- ~13 GB RAM in steady state for the full profile (5 Security Servers), measured
  P0 2026-07-25 on a 16 GB colima VM via `docker stats --no-stream`: ~2.0–2.3 GB
  per Security Server (≈10.7 GB), 1.7 GB for the Central Server, under 100 MB
  each for the Test CA and the three mock providers. Fits in 16 GB with modest
  headroom (~3 GB) — tight enough that a smaller host should prefer the lite
  profile: ~8.9 GB (measured P5, 2026-07-26). Set it in `deployment.yaml`
  (`profile: lite`, not `.env` — deployment shape lives in `deployment.yaml`,
  secrets stay in `.env`): the scripts then skip the compose "full" profile,
  ss-pnia/ss-moeys do not run, and their subsystems are hosted on ss-plr
  (cross-server calls stay real: ss-pnea → ss-plr).
- `curl`, `jq`, `python3` on the workstation.
- No ITU cloud dependency: this run book targets the local stack. The ITU cloud
  (Linkup) deployment re-targets the same scripts later — see PLAN.md §9.

## Steps

1. `scripts/gen-secrets.sh` — writes a real `.env` with a random token PIN
   and admin password (mode `600`). `.env.example` ships placeholders that
   cannot work, on purpose — do not copy it by hand
   (docs/reviews/2026-07-28-branch-review.md finding S2).
2. **Deploy** — `scripts/deploy.sh` (a wrapper over `hurl/run-linkup.sh`)
   Brings up the containers and drives the full stand-up over the admin REST APIs:
   CS init (instance `PROGRESSA`, class `GOV`, configuration signing keys) → Test CA /
   OCSP / TSA registration → members and subsystems → configuration anchor → PDGA +
   management SS → each member SS (anchor, PIN, AUTH + SIGN keys, Test CA signing,
   auth-cert registration and its explicit approval on the CS, subsystem) → service
   publishing (OpenAPI3) → ACLs. Global-conf propagation is asynchronous, so a stretch
   of HTTP errors and retries partway through is expected, not a failure. Measured
   P0 2026-07-25 (16 GB colima VM, cold `--purge`d state): ~9–10 minutes end to end.

   The sequence is a Progressa retargeting of `development/hurl/scenarios/setup.hurl`
   at X-Road tag **7.7.0**. The scenarios live in `hurl/`, generated from `configs/` —
   see `hurl/README.md` to run or retarget them, and `docs/xroad-770-notes.md` for
   what reading the reference corrected.
3. **Seed** — `scripts/seed.sh`
   Regenerates the Progressa demonstration data (Gambia-grounded, Progressa-named)
   and restarts the mock providers with it.
4. **Prove** — `scripts/acceptance.sh`
   Runs `acceptance/2.1.md` … `2.6.md` in order; exits non-zero on first failure.
   2.6 is the framework's acceptance: the once-only exchange resolves, the right
   learner returns, nothing is asked twice, and the unauthorised caller is denied.
5. **Demonstrate (optional)** — `scripts/console.sh up`
   A one-page demo console (counter / inspector / permissions tabs) at
   `http://localhost:8090` for a non-technical audience — not a module, not part
   of acceptance, and never production (`docs/production-delta.md`). It really
   revokes and grants the `identity-api` ACL live, journals every change, and
   resets on demand, on startup, and on a 120s no-activity watchdog, so a demo
   can't be left in a state that breaks `scripts/acceptance.sh` afterwards.
   `scripts/console.sh {down|reset|status}` manages it; `acceptance.sh` itself
   refuses to run while its journal is dirty, with a message telling you to
   `scripts/console.sh reset` first.

## Admin UIs (manual fallback)

Every scripted step can be done by hand per the NIIS KB guides. Concurrent UI
sessions in one browser log each other out — use separate browsers/profiles.

| Component | URL | Credentials |
| --- | --- | --- |
| Central Server (PDGA) | https://localhost:4000 | xrd / secret (fixed, test image) |
| Test CA | http://localhost:8888/testca/ | — |
| ss-pdga / ss-pnea / ss-plr / ss-pnia / ss-moeys | https://localhost:1000 / 2000 / 3000 / 5100 / 6000 | `.env` admin user |

## Teardown

The federation is a fixture, not a build artefact: it exists to be reused
across a session's work, not rebuilt every time something needs checking
against it. Plain `scripts/teardown.sh` between sessions, never `--purge` —
`--purge` throws the fixture away and pays the ~9-10 minute rebuild
(`hurl/run-linkup.sh`) again for no reason. Reserve `--purge` for the one
thing only a from-zero rebuild can prove: the reproducibility proof (P5,
below), or `scripts/verify.sh --full`, which performs that same proof.

- `scripts/teardown.sh` — stops containers; named volumes survive, so the
  federation's configuration persists across restarts. **To resume, do not
  rerun `deploy.sh`/`hurl/run-linkup.sh`** — the Hurl scenario set always runs
  the full stand-up sequence and is not idempotent against already-configured
  state (confirmed at P0 2026-07-25: `POST /api/v1/initialization` returns
  `409 init_already_initialized` on a persisted CS, and every later
  registration call would fail the same way). Resume with the containers
  directly: `docker compose -f docker-compose.yml -f hurl/compose.hurl.yml
  --profile full up -d` — the persisted `/etc/xroad` state in each volume is
  everything the federation needs; nothing else has to run.
- `scripts/teardown.sh --purge` — also deletes the volumes: full reset to zero.
  The reproducibility proof (P5) is: `--purge`, redeploy (`hurl/run-linkup.sh`,
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
  Refuses on a canonical member (the five never renumber or leave). Does
  **not** touch a running federation: the member stays registered there
  until `scripts/teardown.sh --purge` — or until you un-join it properly
  (below), which calls this script for you at the end.
- **Drift:** `scripts/member.sh drift <key>` — re-fetches a joined member's
  *current* OpenAPI spec and diffs its endpoint set against the baseline
  captured at join time (design spec §2.4). No auth, no HTTP to the join
  API — reads `out/join/*.json` and the live spec URL directly, works
  whether or not `join-api` is even running. The spec URL is an
  internal `linkup`-network hostname (`app-<key>:8000`), so this needs to
  run from inside that network: `docker compose exec join-api python3
  scripts/member.sh drift <key>` (or any other container already on
  `linkup`) if a plain host-side run reports "nodename nor servname
  provided" — that error is the trap working as designed, not a bug.
- **Join via the API (automated):** `scripts/join.sh {up|down|status}`
  starts/stops the join API itself (`profile: demo`, like the console) at
  `http://localhost:8091`. Submit a payload matching `apps/join-api/schema.py`
  (`POST /requests` with `Authorization: Bearer $KP2_JOIN_APPLICANT_TOKEN`
  and the `X-KP2-Console: 1` header — see `.env` for the real tokens,
  generated by `scripts/gen-secrets.sh`), review its computed config diff,
  then approve (`POST /requests/{id}/approve`, operator token) to write the
  config for real and start the job, or drive the whole flow from the
  console's **4 · Join a member** tab (`scripts/console.sh up`,
  `http://localhost:8090`) instead of curl — same endpoints, proxied
  server-side so neither token ever reaches the browser. Poll
  `GET /requests/{id}` (or watch the tab; it polls itself) until `state` is
  `ACTIVE` — a hosted join with one published service and one ACL grant
  measured **~93s** end to end (approve to `ACTIVE, verified: true`,
  join-b Task 6's live proof, `deployment.yaml: profile: lite`), well under
  the ~2-minute threshold past which `--live` would stop being cheap enough
  to run routinely (see README.md's `--live` tier note).
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
    and no work-order queue, by design: spec §6.1). A resume that still finds
    the server absent goes back to `BLOCKED` rather than failing, as many
    times as it takes — `BLOCKED` never expires into `FAILED`. Prefer
    `hosted_on` unless the point of the demo is to show a server being stood
    up: it costs zero extra containers, and is the only shape that fits
    alongside a third-party backend on a 16 GB host.
    - **An own-server join ends at `ACTIVE` with `verified: false`, and
      that is a known defect, not a broken join** (join-c Task 5, reproduced
      on both live cycles). The bring-up's own global-configuration
      propagation wait spends the job's whole 120s retry budget before
      `join.r1_verify` runs, so the reachability call gets what is left
      (~20s) and the federation needs 45s–8min. Nothing is wrong with the
      member: `scripts/acceptance.sh`'s `2.7.r1(<code>.<service>)` passes
      against it a minute later, which is the same fact `verified` was meant
      to record. There is no way to flip the flag afterwards —
      `POST /requests/{id}/resume` refuses on an `ACTIVE` record, and
      `join.r1_verify` is already `last_completed_step` so a resume would
      skip it. If you are demonstrating this, say so before the badge
      appears. A **hosted** join is unaffected: measured `ACTIVE, verified:
      true` in 64s with half the budget unspent.
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
    `git` binary, but `writer.apply_real()`'s dirty-checkout guard (spec S9)
    shells out to `git status --porcelain` against the mounted monorepo —
    every approval failed with a 500 until the Dockerfile installed it. If
    approving a request ever 500s with `FileNotFoundError: ... 'git'`
    again (e.g. after rebuilding the image differently), that guard is the
    first place to look.
  - **A long but not misconfigured `r1` URL:** a real third-party backend
    served under its own path prefix (Joget DX serves under `/jw/`, plus an
    app and version segment, per spec §2.5/§14) combines with X-Road's `r1`
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
  the order established live: `docs/xroad-770-notes.md` §11) — then runs
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
    behind.** The API never touches Docker (design decision 8, same split as
    `scripts/join-agent.sh`), so the record carries the instruction and you
    run it: `docker rm -f <dns>` then `docker volume rm kp2-<key>-db
    kp2-<key>-conf kp2-<key>-archive`. Skip it and the next member to reuse
    that key inherits the old database and `/etc/xroad`.
    **On retention:** deleting `kp2-<key>-archive` is correct for this demo,
    since a later member reusing the key should not inherit a stale
    database — but in production the message log is subject to a statutory
    retention period, and doing the same deletion before that period elapses
    converts a retirement into an evidence gap (onboarding path §2 GX). This
    pack implements no archival step; it only names the gap.
  - **A hosted member leaves a SIGN key behind** on somebody else's Security
    Server — `REGISTERED`, active, good OCSP, and nothing in X-Road's admin
    API ever collects it (`docs/xroad-770-notes.md` §11). Deleting it is part
    of the walk, not optional cleanup: without it a host accumulates one
    orphaned signing key per member that ever left.
  - **Interrupted halfway?** Re-issue the same `DELETE`. Every reversal is
    guarded by a read that proves whether it is already gone, so the walk
    re-runs from the top and skips what is done. `POST /requests/{id}/resume`
    is *not* the way back — that one re-enters the forward path.

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

A security server's Test CA-issued OCSP response has a bounded freshness window;
confirmed live (2026-07-27) that after roughly ten hours idle, the signer starts
rejecting the server's own authentication certificate (`IncorrectValidationInfo:
OCSP response is too old`), which then fails every cross-server call through it
with `Server.ClientProxy.SslAuthenticationFailed` — not an access-control problem,
and not specific to the demo console. If a federation has been sitting up for
hours before a demo, redeploy fresh (`scripts/teardown.sh --purge` then step 2)
rather than trusting a stale stack.

macOS hosts: port 5000 is not used for any admin UI here (ss-pnia is 5100) because
macOS's AirPlay Receiver listens on 5000 by default and silently hangs the
connection rather than refusing it — confirmed at P0 (2026-07-25) as a genuinely
confusing failure mode (containers report healthy; the admin API call just hangs).

Pin discipline: the scenarios are written against X-Road **7.7.0** and the compose
images are pinned to it. `Docker/xrd-dev-stack` does not exist before 7.5.0 and is
gone on `develop` — read the reference at the tag you deploy.

> Reproducible: every step is a script in `scripts/` or a scenario in `hurl/`, and
> every one of them is generated from `configs/` by bb-config-gen or
> `hurl/generate.py`. Do not hand-edit a config in `configs/`, a scenario in
> `hurl/scenarios/`, or `hurl/vars.env` — regenerate them.
