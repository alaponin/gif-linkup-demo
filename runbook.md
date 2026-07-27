# KP2 build pack — run book

Stand up the Linkup demonstration federation (X-Road 7.x) from zero on one Docker
host, and run the once-only exchange that proves it. Demo only — see
`docs/production-delta.md` before anyone mistakes this for a deployable platform.

## Prerequisites

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

1. `cp .env.example .env` and adjust (PIN, admin password).
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
| Test CA | https://localhost:8888/testca/ | — |
| ss-pdga / ss-pnea / ss-plr / ss-pnia / ss-moeys | https://localhost:1000 / 2000 / 3000 / 5100 / 6000 | `.env` admin user |

## Teardown

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
