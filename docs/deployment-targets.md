# Deployment targets

`deployment.yaml`'s `target:` field has exactly one supported value today,
`docker-local` — `hurl/generate.py` refuses anything else. This document is
the contract a second target's implementation would be written against: for
each dimension a deployment has to decide, what `docker-local` does, and
what another target has to supply instead. It does not implement a second
target; no `target` branch or unread configuration key exists anywhere in
this pack because of this document. (`infra/terraform/` and
`infra/terraform-db/` do exist now -- droplet and managed-database
provisioning, added since this was written -- but neither is a second
`deployment.yaml` target: `target:` itself is still docker-local-only.)

| Dimension | `docker-local` | What another target must decide |
| --- | --- | --- |
| **Hostnames** | Compose service names in `hurl/vars.env` (`cs`, `ss-pdga`, …) | Real DNS or addresses the moment components split across hosts — retargeting is a change to the host values in `hurl/generate.py` and nothing else (the same move upstream made between X-Road 7.7.0 and `develop`); see `docs/decisions/xroad-770-notes.md` §4 |
| **Bind address** | `network.bind: 127.0.0.1` | Which interface, and the `acknowledge_public_exposure` decision — and, if the Test CA is still part of the stack, binding non-loopback is refused outright regardless (below) |
| **TLS verification** | **Verified against the Test CA on every data-plane hop, not disabled.** The spec fetch (`spec-fetcher`, `SPEC_FETCHER_CA_BUNDLE`), the consumer hop into the client proxy on `:8443` (`apps/console/xroad.py`, `KP2_XROAD_CA_BUNDLE`), the scripts' own r1 calls (`scripts/lib-stack.sh`'s `rest_base()`, `--cacert` + `--resolve`, never `-k`) and `scripts/member.sh`'s drift/refresh fetches all verify a real chain — the anchor is just the Test CA's rather than a public root (`docs/production-delta.md` rows 18/19). The remaining exception is the **`:4000` admin plane**, which still presents each sidecar's self-signed proxy-ui certificate and is still reached unverified (`curl -k`, `xroad.py`'s `SHARED_CLIENT`) — a named, still-open demo compromise, not the general posture | The same code paths, pointed at a real chain: swap the anchor (unset `SPEC_FETCHER_CA_BUNDLE`/`KP2_XROAD_CA_BUNDLE` and the stock trust store applies) — no `verify=` or `-k` to remove on the data plane, because there are none left. The `:4000` admin plane is the one that genuinely has to be decided: an accredited certificate for each Security Server's admin UI, or an operator-facing terminating proxy in front of it |
| **Image provenance** | Digest-pinned (`cs_digest`/`ss_digest`/`testca_tag`) | Already covered — carry forward, nothing new to decide |
| **Certification authority** | `xrddev-testca` | An accredited CA; the Test CA cannot go to a non-loopback target at all (below) |
| **Secrets** | `.env` on disk, mode 600, `scripts/gen-secrets.sh` | Where they come from and where they rest on the target (below) |
| **Persistence** | Named Docker volumes on one host | Backup, restore and recovery time (below) — answered for the droplet target specifically: a managed PostgreSQL backend behind `apps/join-api/store.py` (see `runbook.md`'s "The Postgres join store" section). Any other future target still has this dimension to decide for itself |
| **Time** | The developer's laptop clock | NTP, mandatory (below) |
| **Image acquisition** | Pull from Docker Hub / ghcr at deploy | Pre-pull, mirror, or accept the egress dependency (below) |
| **Commit gate** | `join_workflow.commit_gate: advisory` — the console shows the live-but-uncommitted flag; nothing blocks a join from going live (`docs/production-delta.md` row 33) | `required`: the job BLOCKS at a `config.commit` step until the operator commits `configs/member-<key>/`, `manifest.yaml` and `onboarding/<key>/` — answered for the droplet target specifically (`runbook.md`'s "Committing before go-live"). Any other future target still has this dimension to decide for itself |
| **Request ownership** | `join_workflow.enforce_ownership: false` — any applicant or operator credential may read any request record (`docs/production-delta.md` row 28) | `true`: `GET /requests/{id}` 404s (not 403 — no existence oracle) for anyone but the operator, the issued `applicant:<name>` whose name matches the record's `submitted_by`, or, while the shared token below is still enabled, the shared applicant reading a `submitted_by: null` record — answered for the droplet target specifically (`runbook.md`'s "Per-request ownership"). Only meaningful in combination with the next row: with one shared identity, ownership protects nothing but issued-token records from each other. Any other future target still has this dimension to decide for itself |
| **Shared applicant credential** | `KP2_JOIN_APPLICANT_TOKEN` set to a real value — one credential every applicant uses, which is what makes a zero-setup demo possible and is exactly what `docs/production-delta.md` row 28 says a real federation must not have | The literal sentinel `KP2_JOIN_APPLICANT_TOKEN=disabled` (a deliberate, greppable act — *not* empty, since `docker-compose.yml`'s `:-` default would let a forgotten line mean the same thing silently): `require_applicant` skips the shared-token comparison entirely and every applicant call must arrive on an issued per-agency credential. Answered for the droplet target specifically (`runbook.md`'s "Disabling the shared applicant token"). Any other future target still has this dimension to decide for itself |
| **Backend auth allowlist** | `join.allowed_backend_auth: [none, network_allowlist, proxy_injected]` in `configs/x-road-bus/join-policy.yaml` — all three `schema.BackendAuth` values, so the PTSB fixture and every mock in this pack (which all speak `none`) keep passing | `[network_allowlist, proxy_injected]`: `backend.auth: none` means the consumer holds the provider's own API credential (`docs/production-delta.md` row 30), and with `none` removed a join declaring it is REJECTED at request time naming `join-policy.yaml`, rather than discouraged in prose. Answered for the droplet target specifically (`runbook.md`'s "Narrowing `allowed_backend_auth`"). Unlike the other three switches this one is federation *policy*, not deployment posture — it lives in `join-policy.yaml`, not `deployment.yaml`. Any other future target still has this dimension to decide for itself |
| **`spec_url` scheme** | `join_workflow.require_https_spec_url: false` — a plain-http `spec_url` is accepted, deliberately, so this pack's own fixtures could move to https ahead of the switch rather than only with it (`docs/production-delta.md` row 18) | `true`: an http or scheme-less `spec_url` is REJECTED on check `https_spec_url` before check 9 ever fetches it, so every joining member must serve its OpenAPI description over https with a certificate chaining to a CA this federation trusts. Buys a verified, encrypted fetch at validate time — **not** end-to-end verification, since the Security Server's own later fetch of the same URL does not verify the backend certificate (measured live against 7.7.0). Answered for the droplet target specifically (`runbook.md`'s "Requiring https on `spec_url`"). Any other future target still has this dimension to decide for itself |
| **Spec fetch egress** | `apps/spec-fetcher/`'s `specs` network is `internal: true` — no external egress at all (`docker-compose.yml`, `docs/production-delta.md` row 41). Correct here: docker-local's only real `spec_url` hosts are the three mocks already on that same network, so an egress-capable network would only be attack surface with nothing behind it | A target whose applicant-submitted `spec_url`s name real, external hosts needs `spec-fetcher` on a network that CAN reach them — but that network must still have no route to the admin plane (`cs`/`ca`/any `ss-*`); egress and admin-plane isolation are two separate properties, and this dimension is only "the same as docker-local" for the specific case where every legitimate spec host is already on `specs`. Not yet answered for the droplet target — no target has external spec hosts today, so `internal: true` has never been wrong in practice, but a future target that does needs this decided explicitly rather than inherited by copying `docker-compose.yml`'s `specs` network unchanged |

## Expected wall time and the healthcheck budget

A cold `--full` run (purge, deploy, seed, acceptance) measures **~11–13
minutes** end to end on the reference host: `docker stats`/`out/deploy-
timings.txt`-backed figures in `docs/production-delta.md` ("Current measured
figures", "Where deploy time goes within `--full`") put containers-healthy
at 215–234s and the Hurl admin-API run at 462–504s, matching `runbook.md`'s
"~156s containers-healthy + ~395s Hurl run" for the standard 4-Security-
Server topology. A deployer sizing a first deploy on another target should
budget for the slower end of that range, not the faster.

`hurl/compose.hurl.yml`'s healthchecks on `cs` and every Security Server
retry every 5s for **120 tries (a 10-minute budget)**, doubled from an
initial 60/5min after a persisted-volume restart was observed taking longer
than a from-zero cold boot to re-establish signer/proxy state (see that
file's own comment). If a target's hardware is slow enough that 10 minutes
is not enough — a smaller VM, a constrained CI runner — raise `retries:` in
`hurl/compose.hurl.yml` rather than treating a healthcheck timeout as a
federation defect.

Every healthcheck's steady-state budget now lives in the base
`docker-compose.yml` (30×5s for `cs`/every `ss-*`, sized for a from-zero
laptop boot); `hurl/compose.hurl.yml` only *extends* it to the 10-minute
figure above for the persisted-volume restart case — it is no longer the
only place a healthcheck is declared at all.

## Backup, restore and recovery time

Investigated live against the running federation (X-Road 7.7.0). Both the
Central Server and every Security Server's admin API expose a full
backup/restore surface: `GET /backups`, `POST /backups`, `POST
/backups/upload`, `DELETE /backups/{filename}`, `GET
/backups/{filename}/download`, and `PUT /backups/{filename}/restore` — all
present on both server types' `openapi.yaml`, confirmed reachable on this
pack's own containers (`cs`, `ss-pdga`).

**Creating and downloading a backup works exactly as documented.** `POST
/backups` on a live `cs` returned `201` with a `.gpg` filename in under a
second; the file downloaded correctly (137 KB for `cs`, ~51–54 KB per
Security Server). The same was confirmed for all four Security Servers.

**Restoring that backup onto a container that has lost its `/etc/xroad`
state does not work, and the reason is specific enough to write down.**
Tried live: backed up `cs` and all four Security Servers, `teardown.sh
--purge` (which deletes `cs-conf`/`ss-*-conf`, i.e. `/etc/xroad`, along with
everything else), brought the containers back up empty (no Hurl init), and
attempted to restore the pre-purge `cs` backup. Two failures in sequence:

1. On a container that has never been initialised at all, `PUT
   /backups/{filename}/restore` returns `500` — the admin service itself
   throws `IllegalArgumentException: cannot encode null or empty strings`
   before it even reaches the restore script. Running `POST
   /initialization` first (the same call `hurl/scenarios/00-cs-init.hurl`
   makes, with the original instance identifier) clears this.
2. With the container minimally initialised, the restore endpoint accepts
   the request and calls `/usr/share/xroad/scripts/restore_xroad_center_
   configuration.sh`, which fails with `restore_process_failed`. Run
   directly (`docker exec -u xroad cs
   /usr/share/xroad/scripts/restore_xroad_center_configuration.sh -b -i
   <instance> -f <path>`), the real error is: `gpg: Can't check signature:
   No public key` → `Decrypting backup archive failed`. **Every X-Road
   backup is GPG-signed by a keyring generated inside `/etc/xroad/gpghome`
   the first time a backup is taken on that instance — and that directory
   lives on the exact volume (`cs-conf`, `ss-*-conf`) that `--purge`
   deletes.** A backup taken before a purge is therefore unrestorable after
   one: the file survives (if downloaded first), but the key that would let
   a fresh instance trust it does not.

**The conclusion is "purge and redeploy," not "restore," and it is
measured.** Recovering from a lost federation on this pack's current volume
layout means a full `teardown.sh --purge` + cold `hurl/run-linkup.sh` +
reseed + acceptance, at the wall time recorded above (~11–13 minutes), and
every joined member re-registers — there is no cheaper path with the
current volume layout. A target that genuinely needs backup/restore to work
across a lost host would have to back up `/etc/xroad/gpghome` (or the whole
`*-conf` volume) *separately from and prior to* whatever destroys it, not
rely on the admin API's own backup file — that combination was not tried
live and is not claimed to work; it is the shape a real implementation would
have to test.

**This section is about X-Road's own admin-API backups and does not cover
the join store.** The join store (`apps/join-api/store.py`) has its own,
separate backup/restore/recovery story per deployment target — a `VACUUM
INTO` of the SQLite file for docker-local, or a managed PostgreSQL cluster
with point-in-time recovery for the droplet target — documented in
`runbook.md`'s "The Postgres join store" section, not here.

## Require NTP

X-Road signs and timestamps every message; a host with drifting time
produces failures that present as certificate errors (`OCSP response is too
old`, `IncorrectValidationInfo`), not time errors — the same failure shape
`runbook.md`'s "Known traps" section already documents for a Test CA
response gone stale after ~10 hours idle. A target with no NTP is not a
smaller version of this pack's demo posture; it is a different, harder-to-
diagnose failure mode. `runbook.md`'s prerequisites now carry a preflight
check (`timedatectl status` / `sntp -sS`) — a check, not an install script;
a real target's provisioning is what actually enforces synchronisation.

## The Test CA cannot go to a public target

`xrddev-testca`'s `/testca/sign` endpoint signs any CSR it is handed, with
no authentication — on a non-loopback interface that makes it a public
certificate factory for the federation's own trust anchor. `scripts/lib-
stack.sh`'s bind check now refuses a non-loopback `network.bind` outright
whenever the `ca` service is still part of the compose set, and no
`acknowledge_public_exposure` setting can override that refusal — verified
live (below). This is the one substitution a non-loopback target cannot
defer to "later hardening": replace the Test CA with an accredited CA
*before* changing the bind, not after.

Verified: setting `network.bind: 0.0.0.0` and `acknowledge_public_exposure:
true` together still refuses, naming what `/testca/sign` does; the default
loopback bind is unaffected (no refusal, no warning).

## Where secrets rest

`scripts/gen-secrets.sh` covers the laptop case — a real `.env`, mode 600,
generated locally. A target has three options, in increasing order of
correctness and decreasing order of convenience:

- **`.env` on the target's disk** (what this pack does today). Simplest,
  survives a reboot, but readable by anyone with shell access to the host.
- **Generated into tmpfs per deploy.** Nothing rests on disk, but a reboot
  means regeneration — and regeneration means a new token PIN, which cannot
  be applied to an already-initialised software token under a live
  federation without purging it (`docs/decisions/xroad-770-notes.md` §9).
  Tmpfs trades "secrets at rest" for "reboot forces a purge," which is a
  real cost, not a free upgrade.
- **An external secret store** (Vault, cloud KMS, etc.). Correct, and
  disproportionate for a demonstration — real access control and rotation,
  at the cost of standing up and operating a second system.

**Recommendation: `.env` on disk stays the right default even off a
laptop**, provided the target's disk-level access control (host user
separation, no shared shell access) does the job `.env`'s mode 600 already
assumes on a single-user laptop. Move to tmpfs only if the target's threat
model specifically worries about disk-at-rest exposure more than it values
surviving a reboot without a purge; move to an external store only when a
real federation (not a demonstration) is at stake. This is a
recommendation, not an implementation — a real target's deploy tooling
makes the actual choice.

## Image acquisition at deploy time

The stack pulls from Docker Hub and `ghcr.io` during deployment. A
firewalled host, a conference network, or an air-gapped demo machine needs
the images already present. `scripts/preload-images.sh` pulls every image
`deployment.yaml` pins — `niis/xroad-central-server`, `niis/xroad-security-
server-sidecar`, `ghcr.io/nordic-institute/xrddev-testca`, all by digest —
and reports what it fetched, so a machine can be prepared while it still has
network. It does not cover `apps/mock-registry` (built locally from source,
never pulled) or `hurl/compose.hurl.yml`'s runner image (pinned separately,
by design — see that file's own comment on why the Hurl image doesn't move
in lockstep with an X-Road version bump).

For the genuinely offline case, `docker save`/`docker load` the images
`preload-images.sh` just pulled onto removable media; the resulting tarball
is large (the three images together are several GB) and that is the honest
cost of air-gapping this stack, not a shortcut around it.

**Correcting an overstatement:** this pack's console (`apps/console/`)
genuinely makes no CDN fetch at runtime — its own assets are bundled. The
X-Road images themselves are a different matter: they are not vendored into
this repository, and nothing about the console's offline capability implies
they are. A deploy that has not run `preload-images.sh` (or does not have
the images cached from a prior deploy) needs egress to Docker Hub and
`ghcr.io` the moment `docker compose up` resolves an image it does not have
locally.
