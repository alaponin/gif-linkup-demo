# KP2 build pack — build plan v0.3

Plan for turning the KP2-build-pack scaffold into a **VERIFIED** runnable pack: the
configuration, prompts, scripts and acceptance checks that stand up a real once-only
exchange on the Linkup (X-Road) federation across the Progressa institutions.

| Field | Value |
| --- | --- |
| Pack | `10-Knowledge-Products/KP2-GIF/KP2-build-pack` |
| Proving slice | PNEA issues a credential, pre-filling identity from PNIA and enrolment from PLR over the bus — the learner asked once (Module 5.6) |
| Verification gate | `kp-solution-verify` — static (`check_pack.py --ready`) + live acceptance suite |
| Source modules | KP2 Module 5 (5.4–5.7); Module 4 (semantic map, service contracts); 08-Interoperability Method Steps 5–8 |
| X-Road doc basis | NIIS KB (topology, publishing) + **`nordic-institute/X-Road` @ tag `7.7.0`, `development/hurl/scenarios/setup.hurl`** as the verified call sequence — see §10 and `docs/decisions/xroad-770-notes.md` |

## 1. Decisions taken

1. **X-Road 7.x now, 8.x noted.** Pin the images the NIIS KB uses:
   `niis/xroad-central-server:noble-7.7.0`, `niis/xroad-security-server-sidecar:7.7.0`,
   `ghcr.io/nordic-institute/xrddev-testca:latest` [confirm: latest 7.x].
   A short `xroad-8-delta.md` notes what changes under 8.x; no 8.x implementation in v0.1.
2. **Docker-first; ITU cloud deferred.** The slice runs from one `docker-compose.yml`
   on a workstation/VM. ITU cloud (Linkup) is a later re-targeting of the same scripts
   (§9); no assumptions about that environment are baked in.
3. **Release images + upstream's Hurl sequence, retargeted to Progressa.**
   *(Revised at v0.3; supersedes "release images + our own bash automation".
   Rationale and the seven corrections it forced: `docs/decisions/xroad-770-notes.md`.)*
   `xrd-dev-stack` itself is still not adopted — `xrddev-*` development images,
   hard-coded `DEV:COM` identifiers, no persistent volumes, wrong topology. But
   its `development/hurl/scenarios/setup.hurl` **is** the reference implementation
   of the admin-API call sequence, and reimplementing it in bash bought nothing
   except seven unresolved `[confirm P0]` markers in `deploy.sh`. So: the KB's
   release-image compose file (official images, named volumes, five Progressa
   Security Servers) plus a Progressa retargeting of `setup.hurl` pinned to tag
   **7.7.0**, generated from `configs/` by `hurl/generate.py` and run by
   `hurl/run-linkup.sh`. The teaching claim is unchanged and sharpened:
   bb-config-gen generates the config, and the config *is* the deployment.
   The scenarios and the image tags move together — see §8.
4. **Mock REST providers behind the Security Servers.** PNIA (identity), PLR
   (enrolment) and MoEYS/PEMIS (enrolment source) are small containerised REST APIs
   seeded from CSV; the PNEA consumer is a scripted call. Each provider ships an
   OpenAPI 3 spec — X-Road builds the service description directly from it (the SS
   parses `servers.url` as the forwarding target), and a Joget DX app can replace any
   mock later (KP4) behind the same spec.
5. **Full Gambia grounding, Progressa names only.** Data modelled on The Gambia
   (regions, school structure, NIN-style IDs, WASSCE-style certificate scenario); the
   country's name appears nowhere in any shipped artefact.

## 2. Topology (doc-verified)

The NIIS KB local test environment is five containers: `cs`, `ca` (Test CA + OCSP +
TSA), and three Security Servers — one **management** SS (required: the CS's
management services are provided through a Security Server owned by the CS owner) plus
consumer and provider SSs. Progressa mapping, one Docker bridge network:

| Container | Image | Role | Host ports (UI / REST) |
| --- | --- | --- | --- |
| `cs` | xroad-central-server | Central Server, owner **PDGA**. Instance `PROGRESSA`, member class `GOV` | 4000 |
| `ca` | xrddev-testca | Test CA + OCSP (`http://ca:8888`) + TSA (`http://ca:8899`) | 8888 |
| `ss-pdga` | sidecar | **Management SS** (PDGA + `MANAGEMENT` subsystem) | 1000 / 1080 |
| `ss-pnea` | sidecar | PNEA — consumer (`GOV/PNEA:EXAMS`) | 2000 / 2080 |
| `ss-plr` | sidecar | PLR — provider (`GOV/PLR:ENROLMENT`) | 3000 / 3080 |
| `ss-pnia` | sidecar | PNIA — provider (`GOV/PNIA:IDENTITY`) | 5000 / 5080 |
| `ss-moeys` | sidecar | MoEYS — provider (`GOV/MOEYS:PEMIS`) | 6000 / 6080 |
| `app-plr`, `app-pnia`, `app-pemis` | ours | Mock REST providers (FastAPI/Node), plain HTTP inside the network | — |

Compose details carried over from the KB: named volumes per container for
`/var/lib/postgresql/16/main` + `/etc/xroad` (+ `/var/lib/xroad` on SSs) so the
environment survives container recreation; `XROAD_TOKEN_PIN`, `XROAD_ADMIN_USER`,
`XROAD_ADMIN_PASSWORD` env vars on the sidecars (CS test image is fixed `xrd`/`secret`);
SS DNS names are the container names.

**Sizing.** Full topology = CS + CA + five SSs + three mocks ≈ 13 GB RAM in
steady state, measured via `docker stats --no-stream` on a 16 GB colima VM:
~2.0–2.3 GB per SS (≈10.7 GB), 1.7 GB for the CS, under 100 MB each for the
Test CA and the three mocks. Fits 16 GB with ~3 GB headroom. There is one
topology (design decision 5): no smaller lite alternative that hosts PNIA
(or, before its retirement, MoEYS) as an extra client of `ss-plr` instead of
standing up its own Security Server. `ss-moeys` above is retired, not
deployed — see `docs/production-delta.md`.

**Identifiers** (frozen and verified against the live registry — `manifest.yaml`'s `identifiers:` block): owner `PROGRESSA/GOV/PDGA`; members
`GOV/MOEYS:PEMIS`, `GOV/PNEA:EXAMS`, `GOV/PLR:ENROLMENT`, `GOV/PNIA:IDENTITY`.
Service codes: `identity-api` (PNIA), `enrolment-api` (PLR), `pemis-api` (MoEYS).
These are the cross-pack join keys for KP3/KP4 — freeze them in `manifest.yaml`.
`GOV/MOEYS:PEMIS`/`pemis-api` above is historical: MoEYS was retired from the
frozen contract, with KP3/KP4 sign-off — `manifest.yaml`'s `identifiers:`
block now lists only PNEA, PLR and PNIA.

## 3. Federation stand-up sequence (what the Hurl scenarios automate)

Ordered per the NIIS CS-configuration guide and **verified request by request against
`setup.hurl` at tag 7.7.0**. The CS and SS both expose an admin REST API on :4000
(`/api/v1`). Authentication is **session-based**, not API-key: `POST /login` with form
params, capture the `XSRF-TOKEN` cookie, send `X-XSRF-TOKEN` on every call.

Implemented in `hurl/scenarios/`, generated from `configs/` — file numbering is the
execution order (`00`–`03` CS, `10` management SS, `20`–`23` member SSs, `30`–`32`
services and ACLs, `90` acceptance). Captures do not cross Hurl file boundaries, so
`hurl/run-linkup.sh` concatenates them into one run.

1. **CS init** — instance `PROGRESSA`, CS address `cs`, token PIN; log in to the
   software token; generate the INTERNAL **and** EXTERNAL configuration signing keys.
   Without the internal key there is no signed global conf and no anchor to hand out.
   (`00-cs-init.hurl`)
2. **Member class** — add `GOV`. Progressa's federation admits government bodies only.
   (`00-cs-init.hurl`)
3. **Trust services** — `POST /certification-services` as multipart with
   `certificate_profile_info=…FiVRKCertificateProfileInfoProvider`, `tls_auth=false`,
   `acme_server_directory_url=http://ca:8887` and `certificate: file,ca/ca.pem;`;
   then `/{id}/ocsp-responders` (`http://ca:8888` + `ocsp.pem`) and
   `/timestamping-services` (`http://ca:8899` + `tsa.pem`). No renaming and no
   `docker cp`: the `xrddev-testca` image already writes `ca.pem`/`ocsp.pem`/`tsa.pem`
   into `/home/ca/certs`, shared into the runner's `--file-root` by
   `hurl/compose.hurl.yml`. (`01-cs-trust-services.hurl`)
4. **Approve registrations explicitly** — no `local.ini` edit, no container restart.
   After each registration: `GET /management-requests?sort=id&desc=true&status=WAITING`
   → `POST /management-requests/{id}/approval`. The `auto-approve-*` flags remain a
   convenience for manual configuration only (`docs/production-delta.md`).
5. **CS members** — add PDGA, its `MANAGEMENT` subsystem (`POST /subsystems`, flat, not
   nested under the member), and `PATCH /management-services-configuration` with
   `service_provider_id`; then the four Progressa members and their subsystems.
   (`02-cs-members.hurl`) Download the anchor and capture it. (`03-cs-anchor.hurl`)
6. **Per Security Server** (ss-pdga first, then members):
   a. upload the captured anchor; init owner (member class/code, unique SS code, token
      PIN, `ignore_warnings`); log in soft token;
   b. on ss-pdga only: capture `ca_name` from `/certificate-authorities` — reused by
      every later CSR;
   c. `POST /tokens/0/keys-with-csrs` for AUTH then SIGN (one call each returns key and
      CSR; `csr_format: DER`, FiVRK subject fields, `serialNumber` =
      `PROGRESSA/{SS-CODE}/GOV`);
   d. download each CSR as **PEM** (`?csr_format=PEM`), sign it at
      `POST http://ca:8888/testca/sign` (multipart, `type=auth|sign`, and a *filename* —
      the test CA requires one); import both certs; register the AUTH cert with the SS's
      DNS name as its address; approve on the CS per step 4; activate;
   e. set the timestamping service from the global list (captured once on ss-pdga);
   f. add the member's subsystem as a client, register it, approve on the CS.
   On ss-pdga additionally: `register-provider`, publish the management WSDL, point its
   services at the CS services address and grant the `security-server-owners` group.
   (`10-ss-pdga.hurl`, `20`–`23-ss-*.hurl`)
7. **Publish services** (provider SSs) — `POST /clients/{id}/service-descriptions` with
   `type: OPENAPI3` and `rest_service_code` per §2, URL pointing at the mock's spec;
   then `PUT /service-descriptions/{id}/enable` (services are disabled when added).
   The SS parses `servers.url` from the spec as the forwarding target; TLS-verify off
   (plain HTTP inside the network, demo-only — flagged in the production delta).
   (`30`–`32-services-*.hurl`)
8. **ACLs** — `POST /clients/{id}/service-clients/PROGRESSA:GOV:PNEA:EXAMS/access-rights`
   on `identity-api` and `enrolment-api`. Deliberately do **not** grant
   `GOV/MOEYS:PEMIS` — that's the negative check. (`30`–`31-services-*.hurl`)
9. **Consumer connection type** — PNEA:EXAMS is created with `connection_type: HTTP`
   (default HTTPS would require a client TLS cert; demo-only, flagged in delta).
   (`23-ss-pnea.hurl`)
10. **Propagation waits** — global-conf generation/distribution takes minutes;
    upstream's own init "gets HTTP errors and keeps retrying — this is normal". The run
    uses `--retry 12 --retry-interval 10000`, which retries the failing request rather
    than the run, and `compose.hurl.yml` gates the runner on healthchecks so it does not
    start firing before every admin UI answers on :4000.

Manual-fallback note for the runbook: all of the above is also doable through the UIs
(CS `https://localhost:4000` xrd/secret, SSs per §2) — with the KB caveat that
concurrent UI sessions in one browser log each other out.

## 4. Module map (fills the manifest)

Each module = one config artefact + generating prompt (`bb-config-gen` play) + one
acceptance check.

The table below is historical (pre-Wave-3): modules 2.2–2.5 have since been
collapsed into one `register-member` module, MoEYS's module 2.2 was retired,
and everything was renamed from curriculum numbers to capability names
(`federation-core`, `register-member`, `once-only-exchange`, `join-member`) —
see `manifest.yaml` for the current module map.

| Module | BB | Title | Config artefact | Acceptance proves |
| --- | --- | --- | --- | --- |
| 2.1 | x-road-bus | Stand up the federation core | `configs/x-road-bus/2.1.yaml` — instance, member class, trust services (CA/OCSP/TSA), registration-approval policy, management provider | CS initialised; trust services registered; anchor downloadable; global conf generated |
| 2.2 | member-moeys | Register MoEYS/PEMIS | member+subsystem reg, SS code, `pemis-api` service desc (OpenAPI) | subsystem REGISTERED; service enabled |
| 2.3 | member-pnea | Register PNEA (consumer) | member+subsystem reg, connection type, client config | PNEA:EXAMS REGISTERED; can reach global conf |
| 2.4 | member-plr | Register PLR + enrolment service | member+subsystem reg + `plr-enrolment.openapi.yaml` + ACL (PNEA only) | service published; ACL grants exactly PNEA |
| 2.5 | member-pnia | Register PNIA + identity service | member+subsystem reg + `pnia-identity.openapi.yaml` + ACL (PNEA only) | service published; ACL grants exactly PNEA |
| 2.6 | x-road-bus | The once-only exchange | exchange wiring: the two `r1` calls, field maps from the semantic map | §6 — the headline acceptance |

Config format: one declarative YAML per member (codes, SS code, service descriptions,
ACL subjects, connection type) that `hurl/generate.py` turns into the admin-API calls —
generated by bb-config-gen, never hand-edited (runbook rule).

Each module also carries a `scenarios:` field in `manifest.yaml`, so the chain is
config → prompt → scenario → acceptance and nothing in it is orphaned:
`hurl/check_scenarios.py` fails if a scenario is unclaimed or a claim does not
resolve. Module 2.6 is the exception and declares none — the once-only exchange is
proved by `scripts/acceptance.sh`, not by a scenario (§6).

## 5. Prompts, scripts, seed data

**Prompts** (`prompts/2.x.md`): rewrite each stub as a real bb-config-gen play whose
output is the config artefact — opens "Below is …" with the public spec (NIIS X-Road
member/subsystem registration, service description + access rights, EIF layer) + the
Progressa service brief; decomposes into the named fields; ends with the exact output
format; every identifier `[confirm: verify against the live registry]`. The 5.4/5.6
AI-usage-tip prompts are the drafts to industrialise. Cite public specs only.

**`hurl/run-linkup.sh`** — regenerates the scenarios from `configs/`, concatenates them
in order, brings the containers up and runs §3 end-to-end in one Hurl invocation with
`--retry`. `scripts/deploy.sh` becomes a thin wrapper over it (v0.3; the bespoke
admin-API bash it contained is superseded — see decision 3).
**`scripts/seed.sh`** — loads Gambia-grounded CSVs into the mocks: ~50 learners,
Gambian-plausible names, NIN-style IDs [confirm format], regions (Banjul, Kanifing,
West Coast, North Bank, Lower River, Central River, Upper River), Lower/Upper Basic +
Senior Secondary schools, senior-secondary certificate scenario at PNEA. Includes
deliberate mismatch rows (in PNIA, not PLR) for negative tests. Institution names +
BB ids identical across packs.
**`scripts/acceptance.sh`** — runs every `acceptance/2.x.md` in order; non-zero on
first failure; prints given/when/then. It is the **only** implementation of the 2.6
assertions: an earlier v0.3 draft also expressed them as a Hurl scenario, which was
removed because two of the four (exact-set equality of the assembled application, and
the seeded-record comparison in `assert_record.py`) are beyond what a scenario can
assert. A second, weaker copy of the headline check is worse than none — the two drift
and the weaker one passes.
**`scripts/teardown.sh`** — `docker compose down`; `--purge` also removes the named
volumes (full reset — volumes otherwise persist the federation deliberately).

## 6. Acceptance suite

Checks 2.1–2.5 verify registry state via the admin APIs (§4). **2.6** is the
framework's acceptance (Module 5.6) — a real consumer-side call in the documented
X-Road REST format:

```
curl -H 'X-Road-Client: PROGRESSA/GOV/PNEA/EXAMS' \
  http://localhost:2080/r1/PROGRESSA/GOV/PNIA/IDENTITY/identity-api/persons/{nin}
curl -H 'X-Road-Client: PROGRESSA/GOV/PNEA/EXAMS' \
  http://localhost:2080/r1/PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api/enrolments/{nin}
```

Four assertions: (1) **happy path** — both calls resolve cross-server; (2) **right
learner** — fields match the seeded record; (3) **asked once** — the assembled
credential application re-enters no field either registry holds; (4) **negative** —
the same calls with `X-Road-Client: PROGRESSA/GOV/MOEYS/PEMIS` are denied (ACL).
Each assertion mapped to its EIF layer per 5.6. UNVERIFIED until green.

## 7. Work sequence

| Phase | Work | Exit gate | Status |
| --- | --- | --- | --- |
| P0 Spike | Compose up as-published; `hurlfmt --check hurl/.build/setup.hurl` (never yet parsed by Hurl — authored without network access to the binary); `hurl/run-linkup.sh` from zero; measure RAM; confirm the testca cert filenames in `/home/ca/certs`; fix full-vs-lite default | Federation up from the scenarios; §3 assumptions confirmed on a live stack | **Done** |
| P1 Configs + prompts | bb-config-gen plays 2.1–2.5; manifest titles + fixes (`home:` says `KP2-INT`, folder is `KP2-GIF`; freeze identifiers); **dogfood**: run each prompt for real, diff its output against the config, reconcile — only then is the config "generated" | `check_pack.py` passes; every config regenerated from its prompt | **Done** |
| P2 Providers + seed | Mocks from the OpenAPI specs; Gambia-grounded CSVs; `seed.sh` | Mocks answer locally with seeded data | **Done** |
| P3 Deploy automation | `hurl/run-linkup.sh` from zero incl. retries and explicit approvals; `deploy.sh` reduced to a wrapper; `teardown.sh`; runbook rewritten to match reality (incl. teardown + manual fallback) | Clean machine → federation up, one command | **Done** |
| P4 Acceptance | `acceptance.sh` + six checks incl. 2.6's four assertions | Suite green | **Done** |
| P5 Verify + ship | Resolve all `[confirm]` against the live registry; `check_pack.py --ready`; teardown `--purge` → redeploy → re-run (reproducibility proof) | Pack VERIFIED | **Done** |
| P6 Deltas | `xroad-8-delta.md`; production delta (5.7 list + demo-only flags from §3: Test CA, fixed CS credentials, TLS-verify off, HTTP connection type, single host); ITU-cloud re-targeting parked | Docs merged | **Done** — both delta docs reviewed against the live run, no new shortcuts found, no edits needed |

**P0–P5 complete.** Reproducibility proof: `teardown.sh --purge` →
`hurl/run-linkup.sh` (cold, 936s) → `scripts/seed.sh` → `scripts/acceptance.sh`
— all green, unattended. Real bugs found and fixed along the way (each its own
commit): a comma in MoEYS's member_name broke X-Road's server-side DN
construction (`hurl/generate.py` `dn_escape()`); `teardown.sh --purge` missed
the Hurl overlay's `ca-certs` volume; the Security Server healthcheck budget
was too short for a restart-from-persisted-volumes boot (60→120 retries);
`ss-pnia`'s host port 5000 collided with macOS's AirPlay Receiver (moved to
5100); and `acceptance.sh`'s registration-status checks were single-shot
against an asynchronous propagation window (now retried, like everywhere else
this asynchrony shows up). P6 (deltas) below.

## 8. Known traps

Global-conf propagation delays (retry, don't fail); the CSR is generated in DER but
must be **downloaded as PEM** and posted to the test CA with a filename; FiVRK
certificate profile fields differ from other profiles and it validates the country
code (`C=FI`, an artefact of the demo CA — see `docs/decisions/xroad-770-notes.md` §3); services
are disabled after they are added until explicitly enabled; consumer default connection
type HTTPS breaks the demo call unless set to HTTP or a client cert is uploaded;
concurrent admin-UI sessions in one browser log each other out; CS test image admin
creds are fixed `xrd`/`secret` (test/dev only — note in production delta); a Security
Server's Test CA-issued OCSP response has a bounded freshness window: after roughly
ten hours idle, the signer rejects the server's own authentication certificate as
`IncorrectValidationInfo: OCSP response is too old`,
failing every cross-server call through it with `Server.ClientProxy.SslAuthenticationFailed`
(not an ACL problem, and not specific to the demo console — see `runbook.md` "Known
traps"). Redeploy fresh rather than trusting a federation that has sat up for hours.

**Version traps.** `Docker/xrd-dev-stack/` does not exist before 7.5.0 and is gone on
`develop` (7.8.0-SNAPSHOT), which has moved to `development/native-lxd-stack/` and
carries dataspace/DSP endpoints a 7.7.0 server does not have. Read `setup.hurl` at the
tag you deploy. This pack pins 7.7.0 in both the images and the scenarios; they move
together. Several 7.7.0 responses also disagree with the OpenAPI model (init 200 not
201, register 204 not 200, enable 200 not 204) — the scenarios assert the observed
codes, with the discrepancy noted inline so nobody "corrects" them.

## 9. Parked / open items

- Module 2.7, the join API (`apps/join-api/`), is a
  hosted-only member join from a submitted payload through validation,
  operator approval, real config generation, and the live X-Road admin-API
  sequence to `ACTIVE`. The console has a fourth tab (pending queue + diff,
  approve/reject with a reason, live progress as a step list coloured by
  actor, `FAILED` resume, the live-but-uncommitted warning,
  `requested_access:` follow-ups) — a thin, server-side-token-holding proxy
  onto join-api, wired to touch neither the ACL journal nor its watchdog.
  Live-verified end to end: submit → approve → `ACTIVE, verified: true` in
  **~93s** → `acceptance.sh` green → `member.sh list` → `member.sh remove` →
  regenerate → `acceptance.sh` green again — comfortably under the
  ~2-minute threshold past which `--live` would need to stop being vacuous
  by default; a real join stays a separate, manual procedure instead. Two
  real bugs were found and fixed by the live proof, neither caught by
  `--fast`'s fixture-driven tests: the `apps/join-api` container image had no `git`
  binary, but `writer.apply_real()`'s dirty-checkout guard (spec S9) shells
  out to it — fixed in the Dockerfile; and `job.py` ran each step as its own
  Hurl process with no cookie jar, so any step after the one that logged in
  401'd (X-Road's admin API validates the XSRF header against the session
  cookie, not the header alone, which only matters once nothing carries a
  cookie jar between separate process invocations — cold deploy's single
  concatenated Hurl file never hits this) — fixed with one shared
  `--cookie`/`--cookie-jar` file per job run. Also found and fixed:
  `scripts/acceptance.sh`'s own 2.7 r1 check tried an unsubstituted
  `{param}` path (no service in this pack, canonical or joined, publishes a
  parameter-free endpoint) and could never return 2xx against a real
  backend — changed to call the service root and check for the absence of
  an X-Road fault, the same semantics `job.py`'s own `r1_verify` step
  already used correctly.
- **ITU cloud (Linkup)** — same compose + scenarios on the ITU VM; blocked on
  environment specifics (Inception action A4). Retargeting is now a change to the
  host values in `hurl/generate.py` and nothing else — the same move upstream made
  between 7.7.0 (`cs_host=cs`) and `develop` (`cs_host=xrd-cs.lxd`). Tracked as
  the `target:` field in `deployment.yaml` (currently only `docker-local` is
  implemented) — a genuine non-Docker target is a separate, not-yet-started spec.
- **Full rename/reuse support for a different country or sector** — configuring
  this pack to stand up a differently-named federation (not just Progressa) is
  a separate, not-yet-started spec. Touches the bb-config-gen prompts,
  `manifest.yaml`'s frozen-identifier contract, `gen_seed_data.py`, and the
  KP3/KP4 cross-pack join-key story.
- **Joget DX** — replaces mocks behind the same OpenAPI specs in KP4 era.
- **X-Road 8.x** — delta note only.
- `deployment.yaml` (analyst-facing spec: target, X-Road version pins —
  `.env` shrinks to secrets only) is how the pack is configured. It
  originally also carried a topology `profile:` key selecting between the
  full topology and a smaller `lite` one (3 Security Servers — PNIA/MoEYS
  hosted as extra clients on `ss-plr`, ~8.9 GB RAM vs full's ~13 GB, both
  live-verified `ACCEPTANCE GREEN`); the profile split itself is gone —
  `deployment.yaml`'s `profile:` key, `--profile`, and the lite topology it
  selected have all been removed. One topology remains (full minus MoEYS,
  design decision 5). Two ordering bugs found and fixed at the time in
  X-Road's admin API sequence for a hosted member remain relevant: client-add
  must precede its SIGN-key generation, which must precede its registration.
  These findings are unaffected by the profile split's removal — they hold
  for any hosted member under `security_server.hosted_on`, which is how a
  hosted member is expressed now.
- The testca image digest is pinned (`.env.example`
  `TESTCA_TAG=latest@sha256:018e9f...c16c0c5`); the test CA does write
  `ca.pem`/`ocsp.pem`/`tsa.pem` into `/home/ca/certs`, confirmed live; RAM
  measured at ~13 GB steady state (§2); `hurlfmt --check` parses the
  generated scenario set clean via the containerized Hurl image (no local
  `hurl` binary needed — `docker run --rm --entrypoint hurlfmt ... --check`);
  the full federation stands up from zero via `hurl/run-linkup.sh` in ~9–10
  minutes. Two real bugs were found and fixed in the process: MoEYS's
  member_name contains a comma, which broke X-Road's server-side DN
  construction for its AUTH/SIGN CSRs (`hurl/generate.py`'s `dn_escape()`);
  and `scripts/lib-stack.sh`'s `COMPOSE_ALL` never referenced
  `hurl/compose.hurl.yml`, so `teardown.sh --purge` could not remove the
  overlay's `kp2-ca-certs` volume, which would have handed a fresh CA
  container stale certs on the next "clean" run. **Still open:** NIN format;
  the exact access-denied fault shape asserted by `acceptance.sh`.
- **Video calibration (Module 5 bundle §5.5):** resolved by MoEYS's retirement
  (design decision 5, §2) — the runnable topology is now four Security Servers
  total (PDGA's management server plus PNEA, PLR, PNIA), matching what 5.5
  already says. No script change needed.
- **check_pack.py change made here:** `prompts/` is now exempt from the
  `[confirm:` scan (prompts must teach the literal marker; only `configs/` must
  be clean at `--ready`). Applied to the repo copy of the `itu-giga-kp` skill —
  propagate to the plugin's canonical source so KP3/KP4 inherit it.

## 10. Sources

NIIS X-Road Knowledge Base: "How to Set Up a Local Test Environment Using Docker
Compose?" (containers, images, ports, volumes, env vars); "How to Configure Central
Server (≥7.3.0)?" (init → member class → trust services → management org/subsystem →
signing keys → management SS flow, auto-approve `local.ini`); "How to Publish a REST
API to X-Road?" (subsystem add/register, OpenAPI3 service, enable, service URL, TLS
options, access rights, `r1` call format + `X-Road-Client` header).
`github.com/nordic-institute/X-Road` **at tag 7.7.0** — `development/hurl/scenarios/setup.hurl`
and `vars.env` (the verified admin-API sequence, retargeted in `hurl/`),
`development/hurl/Dockerfile`, `Docker/xrd-dev-stack/{compose.yaml,compose.dev.yaml,local-dev-run.sh}`
(runner pattern, healthcheck gating, retry settings), `test-proxy-rest.hurl` (the `r1`
call format used in the acceptance check). The stack's dev images and `DEV:COM`
identifiers are not reused — see `docs/decisions/xroad-770-notes.md` §5. Admin API definitions:
`src/{central,security}-server/openapi-model/.../openapi-definition.yaml`.
docs.x-road.global manuals (CS/SS user guides) for anything the scenario does not cover.

## 11. Demonstration console

`apps/console/` (`scripts/console.sh up`, `http://localhost:8090`) is a demo
asset, deliberately **outside** the module map in §4 and outside the
acceptance path in §6: it has no `config`/`prompt`/`acceptance` file and
`manifest.yaml` does not list it. It reads the same generated
`hurl/topology.json` the scenarios use (no fourth copy of the topology) and
really mutates the `identity-api` ACL live for its permissions tab — every
mutation is journalled and reversed (`journal.py`), with reset on demand, on
container start, and on a 120s no-heartbeat watchdog, and
`scripts/acceptance.sh` itself refuses to run while that journal is dirty.
Two live-confirmed X-Road behaviours from the console's build are worth
knowing about elsewhere in this pack: revoking/granting access-rights is
instant in
the **admin API's own read**, but the **proxy's actual authorization
decision** can lag by up to ~30s (a server-conf cache effect, not a bug);
and re-revoking or re-granting an already-there state both return `409`,
which must be treated as success, not failure.

The console is verified live end to end: a from-zero purge → redeploy → seed →
acceptance → console-up → all three tabs exercised → console-reset →
acceptance cycle runs clean end to end, plus the 21-test unit suite. A security
pass over the console (secret handling, XSS, CORS, path/command injection) found
one real issue — federated-exchange field values and X-Road fault bodies were
interpolated unescaped into `innerHTML` in `static/app.js`, a stored-XSS vector
if a provider (a real agency system, once KP4 replaces the mocks) ever returned
an HTML-bearing field value. Fixed with one escaping helper applied at every
call site; admin credentials themselves were already correctly kept server-side
only and never found in a response, log, or the ACL journal.
