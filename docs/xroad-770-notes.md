# Sourcing the stand-up sequence from X-Road 7.7.0

The federation stand-up in `hurl/` is not designed from the NIIS knowledge-base
articles. It is taken from the upstream reference implementation:
**`nordic-institute/X-Road` at tag `7.7.0`, `development/hurl/scenarios/setup.hurl`**
— 1,227 lines of Hurl that drive the admin APIs of a Central Server and two
Security Servers from empty containers to a working ecosystem. That file is what
`Docker/xrd-dev-stack/local-dev-run.sh --initialize` runs, and it is the sanctioned
config-as-code path: it works against real installs, not only dev ones. Hurl is
incidental — the same sequence drives equally well from Terraform's `http`
provider, Ansible `uri`, or plain `curl`.

This note records what reading it at 7.7.0 changed, and the traps around it.

## 1. What the earlier plan had wrong

`PLAN.md` §3 was sequenced correctly from the KB articles, but seven of its
implementation assumptions did not survive contact with the reference. Each was
carried in `scripts/deploy.sh` as a `[confirm P0]` marker; all seven are now
resolved.

| Assumption in the draft | What 7.7.0 actually does |
| --- | --- |
| Authenticate with an API key created via `POST /api/v1/api-keys` | **Session login.** `POST /login` with form params `username`/`password`, capture the `XSRF-TOKEN` cookie, and send it as an `X-XSRF-TOKEN` header on every subsequent call. `scripts/lib-stack.sh`'s `api_key()` was never going to work as written. |
| Write `auto-approve-*` flags into `/etc/xroad/conf.d/local.ini` on the CS and restart `xroad-center`, so registration requests self-approve | **Not needed.** The scenario approves explicitly: `GET /api/v1/management-requests?sort=id&desc=true&status=WAITING` → `POST /api/v1/management-requests/{id}/approval`. No file is written into a container, nothing is restarted, and the approval step stays visible in the run — which is also better teaching. The flags remain a legitimate convenience for someone configuring by hand. |
| The Test CA's certificates must be renamed to `ca.pem` / `ocsp.pem` / `tsa.pem` before upload | **Already named that way.** The `xrddev-testca` image writes them into `/home/ca/certs`; upstream mounts that directory into the runner's `--file-root` as `ca/`, and the multipart upload references `file,ca/ca.pem;` directly. `hurl/compose.hurl.yml` reproduces the shared volume. |
| Generate a key, then generate a CSR against it | **One call:** `POST /api/v1/tokens/0/keys-with-csrs` returns both `key.id` and `csr_id`. |
| The Test CA "accepts DER CSRs only" | The SS generates the CSR in DER (`"csr_format": "DER"`), but the CSR is then **downloaded as PEM** (`GET /api/v1/keys/{id}/csrs/{csr_id}?csr_format=PEM`) and posted to `http://{ca}:8888/testca/sign` as multipart with `type=auth\|sign` and a **filename** — the test CA requires the filename, which is why upstream hand-writes the multipart body instead of using Hurl's `[MultipartFormData]`. |
| Subsystems are created under `POST /members/{id}/subsystems` | Flat: `POST /api/v1/subsystems` with the full `subsystem_id` object. |
| One step assigns the management service provider | **Two:** `PATCH /api/v1/management-services-configuration` sets `service_provider_id` (the subsystem), and later `POST /api/v1/management-services-configuration/register-provider` binds it to `security_server_id` once that server's auth certificate is registered. |

## 2. Status codes that disagree with the OpenAPI model

Upstream asserts the observed code and carries a TODO against the spec. The
generated scenarios assert the same observed values, with the discrepancy noted
inline so nobody "fixes" them back to the documented value:

| Request | Documented | Actual at 7.7.0 |
| --- | --- | --- |
| `POST /api/v1/initialization` (CS) | 201 | **200** |
| `POST /configuration-sources/{INTERNAL\|EXTERNAL}/signing-keys` | 201 | **200** |
| `POST /tokens/0/keys-with-csrs` | 201 | **200** |
| `PUT /token-certificates/{hash}/register` | 200 | **204** |
| `PUT /service-descriptions/{id}/enable` | 204 | **200** |

`PUT /tokens/0/login` is asserted as `HTTP *` — it returns a different code
depending on whether the token was already logged in, and the run must be
re-runnable.

## 3. Two things that are Test-CA artefacts, not Progressa

- **`C: FI` in every CSR.** The demonstration trust anchor uses the
  `FiVRKCertificateProfileInfoProvider` certificate profile, which validates the
  country code. Upstream uses `FI`; so must we. It appears in `hurl/vars.env` as
  `csr_country` with this note attached, so it is never read as a claim about
  where Progressa is. A production CA in a real country supplies its own profile
  and the field follows the country.
- **`acme_server_directory_url: http://{ca}:8887`** on the certification service.
  ACME support is recent; the field is populated because the test CA exposes an
  ACME directory on 8887, not because the demonstration issues certificates that
  way.

## 4. Version traps

- `Docker/xrd-dev-stack/` **does not exist before 7.5.0**. It 404s at 7.3.0 and
  7.4.0. Anything written against a 7.3.0 guide has no Docker path to follow.
- On `develop` (currently 7.8.0-SNAPSHOT) the top-level `Docker/` directory is
  **gone**; the development environment has been reorganised around LXD
  (`development/native-lxd-stack/`) and k8s. The fingerprint is in `vars.env`:
  at 7.7.0 it reads `cs_host=cs`, `ca_host=testca`, `ss0_host=ss0` — Compose
  service names — while on `develop` the same file reads `cs_host=xrd-cs.lxd`,
  `ca_host=xrd-ca.lxd`. Same `setup.hurl`, different target topology.
- `develop` also carries dataspace/DSP scaffolding and endpoints that do not
  exist on a 7.7.0 server. **Read the scenario at the tag you deploy**, or the
  run will call endpoints the server has never heard of.
- This pack pins **7.7.0** in `docker-compose.yml` (`niis/xroad-central-server:noble-7.7.0`,
  `niis/xroad-security-server-sidecar:7.7.0`). The scenarios and the images must
  move together.

## 5. What we deliberately did not take from upstream

`xrd-dev-stack` itself. It uses `ghcr.io/nordic-institute/xrddev-*` development
images, hard-codes `DEV:COM` identifiers, keeps no persistent volumes, and ships
a topology of two Security Servers plus example adapters. The pack keeps its own
compose file — release images, named volumes so the federation survives a
restart, Security Servers mapped to Progressa's institutions — and takes
from upstream only what is genuinely reusable: **the call sequence**. The one
development image retained is `xrddev-testca`, which has no release equivalent
and is demo-only by nature.

This reverses `PLAN.md` §1 decision 3, which had rejected the whole of
`xrd-dev-stack` including its Hurl scenarios and committed the pack to bespoke
bash. The distinction that decision missed: the scenarios are the *reference
implementation of the admin API sequence*, and reimplementing them in bash bought
nothing except seven unresolved `[confirm]` markers.

## 6. The proxy's own authorization-cache lag, and tuning it for a demo

The Security Server proxy caches server configuration (including access
rights) separately from the admin API's own database read — confirmed live
across the kp2-demo-console builds: revoking a grant is reflected
**instantly** in `GET .../access-rights` (the admin API's own read), but a
real r1 call through the *proxy* keeps succeeding for up to a minute
afterward. This is `CachingServerConfImpl`'s ACL cache
(`org.niis.xroad.serverconf.impl.CachingServerConfImpl`, in `proxy.jar`),
not a bug — the property is `xroad.proxy.server-conf-cache-period`
(`SystemProperties.getServerConfCachePeriod()`), documented in X-Road's
System Parameters User Guide as **`server-conf-cache-period`, default 60
seconds** — not listed in the shipped `proxy.ini`'s own comments, so it is
easy to miss unless you go looking for the JVM system property directly.
Measured at 7.7.0, 5 runs, revoke → the proxy's own
`Server.ServerProxy.AccessDenied`: 59.9s–60.5s, matching the documented
default almost exactly.

It is a genuine `local.ini` setting (`[proxy] server-conf-cache-period =
<seconds>`), not a compose/env-var knob — the sidecar image has no generic
environment-variable-to-ini mechanism for it. This pack sets it to **5**
seconds for every Security Server via `xroad-demo-local.ini`, bind-mounted
over `/etc/xroad/conf.d/local.ini` in `docker-compose.yml`. Re-measured
under the override, same 5-run methodology: **4.5s–5.6s**. A demo can
afford trading a little proxy CPU for a faster-to-reflect ACL change; a
production federation should not tune this down without understanding the
trade-off, which is why it is called out in `docs/production-delta.md`. The
response this lag eventually produces is recorded as a fixture in §10.

## 7. Retiring a member from a running federation, without `teardown.sh --purge`

Investigated live (member-parameterisation plan, Task 9) with a throwaway
joined member (`PHIB:CLAIMS`, its own Security Server): **yes, a member can
be retired from a running federation without a full purge** — but it takes
four admin-API calls across two servers, in a specific order, taking a few
minutes end to end (same order of magnitude as registration's own
propagation lag), and nothing in this pack scripts it.

1. `PUT /clients/{id}/unregister` on the member's **own** Security Server.
   Sends a `CLIENT_DELETION_REQUEST` to the Central Server. Unlike a
   registration request, this one has **no approval step**: `POST
   /management-requests/{id}/approval` against it returns `403` (nothing to
   approve — it auto-processes). The client's local `status` becomes
   `DELETION_IN_PROGRESS` and stays that way until propagation catches up.
2. Wait. `GET /clients/{id}` on the same server keeps returning
   `DELETION_IN_PROGRESS` until the Central Server has processed the
   request and this server's `confclient` has downloaded the updated global
   configuration (polls every ~60s — same mechanism as every other
   propagation delay in this pack). Confirmed independently via the Central
   Server: `DELETE /subsystems/{id}/servers/{server_id}` (the CS-side
   "unregister a subsystem from a server" call) returned `404
   subsystem_not_registered_to_security_server` once step 1 had actually
   taken effect — i.e. step 1 alone already broke the server↔subsystem
   link; step 2 is purely a propagation wait, not a second action.
3. `DELETE /clients/{id}` on the member's own Security Server, retried once
   the wait is over. Failed with `409 action_not_possible` when tried
   immediately after step 1 (before propagation caught up); succeeded
   (`204`) once it had. This is what actually removes the local client
   record. Confirmed live: a call from another member to the retired
   member's service now fails cleanly with `Server.ClientProxy.UnknownMember`
   — the same clean failure a caller should see, not a hang or a stale
   success.
4. Optional, only if the member's *identity* (not just its bus registration)
   should disappear from the federation's directory: `DELETE
   /subsystems/{subsystem_id}` then `DELETE /members/{member_id}` on the
   Central Server (both `204`, both plain synchronous CS-side deletes, no
   propagation wait). Confirmed live: `GET /clients?q=<member_code>` on the
   Central Server returns empty afterward.

**Two traps found live, not from reading the spec:**

- **Order matters between this and `teardown.sh --purge`.** If the member's
  *config* is removed (`scripts/member.sh remove`) before its container is
  torn down, `hurl/compose.members.yml` regenerates to `services: {}` and
  `teardown.sh --purge`'s `docker compose down -v` no longer references that
  member's container or volumes at all — they become orphaned (found live:
  `ss-phib` and its three `kp2-phib-*` volumes survived a `--purge` run and
  had to be removed by hand, `docker rm -f` + `docker volume rm`). Retire the
  member live first (the four steps above), or purge/teardown *before*
  removing its config, not after.
- A demonstration join **can** be undone on camera, but not instantly and
  not with one click — budget a few real minutes of dead air for step 2, or
  narrate through it, the same way this pack already narrates through
  registration's own propagation lag.

## 8. Reducing manual toil if you configure by hand anyway

Two things worth knowing for anyone walking the UIs instead:

- the `auto-approve-auth-cert-reg-requests`, `auto-approve-client-reg-requests`
  and `auto-approve-owner-change-requests` flags in `/etc/xroad/conf.d/local.ini`
  on the Central Server;
- the `xroad-autologin` package on a Security Server, which feeds the soft-token
  PIN after a restart instead of making you re-enter it in the UI.

Both are demo/convenience measures and belong in `docs/production-delta.md`, not
in a production build.

## 9. Changing `.env`'s PIN after a deployment already exists

Investigated live (exposure-and-secrets plan, Task 6), using a real
occurrence rather than a manufactured one: `scripts/gen-secrets.sh --force`
against an already-deployed federation, containers recreated (no purge) so
their persisted `/etc/xroad` state — including the software token, still
initialised with the OLD PIN — survived while the new PIN was injected via
`.env`.

The mismatch is real and confirmed at two different layers, and they say
two different things:

- **The admin API's own `/tokens` endpoint is completely explicit about
  it**: `"status": "USER_PIN_INCORRECT"`, `"logged_in": false`, both the
  auth and sign keys `"available": false`. Anyone who knows to check this
  endpoint gets an unambiguous diagnosis in one call — the autologin
  process's own log line (`(re)trying to enter PIN`, repeating) is the same
  signal surfacing before anyone queries the API at all.
- **A real cross-server call sees something completely different and
  actively misleading**: `Server.ClientProxy.SslAuthenticationFailed`,
  `"message": "Security server has no valid authentication certificate"`.
  This is the exact failure mode the plan predicted before testing it — it
  reads as a *certificate* problem (expired, revoked, wrong CA) and sends
  whoever is debugging it looking at OCSP responses and cert chains, not at
  `.env`. The certificate itself is fine the whole time (confirmed via
  `/tokens`: `ocsp_status: OCSP_RESPONSE_GOOD`, `active: true`) — it simply
  cannot be *used*, because the signer never unlocked the token that holds
  it.

Neither the admin login endpoint (`/login`) nor `docker ps`/`docker logs`
health status is disturbed by this at all — the container reports healthy,
the operator can log into the admin UI, and only a call that actually needs
the signer (a real cross-server exchange, or an explicit `/tokens` check)
reveals anything is wrong. This is not benign: it is exactly the confusing
failure mode the plan set out to catch before it wastes someone's afternoon,
which is why Task 6's fingerprint check is a hard failure at deploy time,
not a warning.

## 10. The console's ACL-mutation error shapes, recorded not guessed

Four behaviours the demo console (`apps/console/`) depends on cost a live
federation to discover the first time, and used to cost hand-written
approximations to re-confirm. Real, live-recorded fixtures now back the
tests that exercise each one (`apps/console/tests/fixtures/xroad/`,
testing-strategy plan Task 6), and `scripts/capture-xroad-fixtures.sh
--check` re-captures and diffs them so drift is caught rather than assumed
away:

- **`read_acl_404.json`** — a subject with zero access rights is not a
  service-client at all, so `GET .../service-clients/{subject}/access-rights`
  404s rather than returning `[]`. Real body:
  `{"status":404,"error":{"code":"service_client_not_found"}}` — the
  hand-written `{"detail": "not found"}` this replaced happened to exercise
  the same code path in a test without ever matching what the admin API
  actually sends.
- **`grant_409_duplicate.json`** — granting a right already held returns
  `409` with `{"status":409,"error":{"code":"duplicate_accessright"}}`,
  not the hand-written `{"error": "already granted"}` this replaced.
- **`revoke_409_not_found.json`** — revoking a right already revoked
  returns `409` with `{"status":409,"error":{"code":"accessright_not_found"}}`
  — the one fixture of the four whose hand-written predecessor already
  matched reality (it was itself written from a live observation, dated
  2026-07-26); re-recording confirmed that rather than assuming it still
  held.
- **`exchange_access_denied.json`** — a denied r1 call's real body is
  `{"type":"Server.ServerProxy.AccessDenied","message":"Request is not
  allowed: SERVICE:<the exact service id>","detail":"<a random per-request
  trace UUID>"}`. The hand-written version this replaced dropped the
  `SERVICE:...` suffix and the `detail` field entirely — a parser that
  happens to check only `.type` would never have caught either omission.
  `detail` is excluded from the drift check specifically because it is a
  fresh UUID every single call by design, not a stable part of the shape.

The fourth behaviour the plan named, the proxy's own authorisation-cache
lag, is §6 above — a timing property, not a distinct response shape (the
"denied" response it eventually produces is the exact same
`exchange_access_denied.json` shape, just delayed).

## 11. Un-joining a *hosted* member: the reverse sequence, measured

§7 above established that a member can be retired from a running federation
at all, using a member with its **own** Security Server. This section is the
join-c plan's Task 1 spike, and its subject is the case Plan B's topology
makes normal and §7 never covered: a **hosted** member, whose subsystem lives
as one client among several on a Security Server somebody else owns and keeps
running. Established live, twice, against `profile: lite` from cold — PTSB:
SCHOLARSHIP joined onto `ss-plr` through the join API (`ACTIVE, verified:
true`), then taken apart by hand with `curl`. Every exchange below is
recorded in `apps/join-api/tests/fixtures/xroad/unjoin.*.json`.

**The working sequence is six calls, and nothing in it had to be retried or
waited on.** No call in either cycle returned anything but its `204` on the
first attempt — in particular there is no approval round to poll and no `409`
window was encountered (but see finding 3 for what that does and does not
establish about `DELETE /clients/{id}`). Four calls the
join-c plan budgeted for turned out not to exist: a `PUT .../disable` before
the service-description delete, a Central-Server approval round after the
unregister, a separate certificate delete before the SIGN-key delete, and a
separate `DELETE /subsystems/{id}` before the member delete. See below for
each.

| # | Reverses | Call | Status | Probe (the read that proves it is gone) | Probe answer |
| --- | --- | --- | --- | --- | --- |
| 1 | `service.acl` | `POST /clients/{id}/service-clients/{subject}/access-rights/delete` on the **host** SS | **204** | `GET .../service-clients/{subject}/access-rights` | **404** `service_client_not_found` |
| 2 | `service.publish` | `DELETE /service-descriptions/{id}` on the host SS | **204** | `GET /clients/{id}/service-descriptions` | **200** `[]` |
| 3 | `ss.client_register` | `PUT /clients/{id}/unregister` on the host SS | **204** | `GET /clients/{id}` | **200** `status: DELETION_IN_PROGRESS` |
| 4 | `ss.client_add` | `DELETE /clients/{id}` on the host SS | **204** | `GET /clients/{id}` | **404** `client_not_found` (with the id in `metadata`) |
| 5 | `ss.sign_key_csr` | `DELETE /keys/{key_id}` on the host SS | **204** | `GET /token-certificates/{hash}` | **404** `certificate_not_found` |
| 6 | `cs.members_member` | `DELETE /members/{member_id}` on the **Central Server** | **204** | `GET /clients?q=<member_code>` | **200** `{"clients": []}` |

Repeating any of them is safe and distinguishable: 1 → `409
accessright_not_found` (the shape `apps/console/tests/fixtures/xroad/
revoke_409_not_found.json` already records), 2 → `404
service_description_not_found`, 4 → `404 client_not_found`, 5 → `404`,
6 → `404 member_not_found`.

### Five things that came out different from what the plan assumed

1. **Un-registration is NOT approved by the Central Server.** The plan's
   design decision 3 assumed unregistering a subsystem raises a management
   request the operator must approve, symmetrically with registration. It
   does not. `PUT /clients/{id}/unregister` raises a `CLIENT_DELETION_REQUEST`
   on the CS (id 10 in `unjoin.cs_management_requests.json`) which is
   **auto-processed and has no `status` field at all** — every other request
   in the same list carries `"status": "APPROVED"`; this one carries none.
   It follows — by filtering that same capture, not from a separate live
   query — that `GET /management-requests?status=WAITING`, which is exactly
   what the forward path polls, cannot return it: no item in the response
   carries `WAITING`, and the deletion request carries no `status` to match
   on at all. And `POST /management-requests/{id}/approval` against it
   returns **`403`** (`unjoin.cs_deletion_approval_refused.json`) with the
   bare body `{"status":403}` — no error code, no message.
   This confirms §7's finding on the own-server case and extends it to the
   hosted one. There is no approval gate to wait on, in either topology.
2. **A service description does not have to be disabled before it is
   deleted.** `DELETE /service-descriptions/{id}` returns 204 against a
   description that is `"disabled": false` and actively serving. The
   `PUT .../disable` half of the plan's "disable then delete" is not a
   precondition and is not in the sequence.
3. **`DELETE /clients/{id}` is not blocked by `DELETION_IN_PROGRESS`, and no
   `409` window was ever observed.** §7 recorded `409 action_not_possible`
   for an own-server member, requiring a propagation wait of a few minutes.
   That does **not** reproduce here. What the committed fixtures show: the
   delete was issued **once**, was accepted **`204`**
   (`unjoin.client_delete.json`), and `GET /clients/{id}` answered `404` on
   the next call (`unjoin.client_delete.probe.json`) — while the status read
   taken right after the unregister still said `DELETION_IN_PROGRESS`
   (`unjoin.client_unregister.probe.json`). Nothing was retried and no `409`
   was seen at any point in either cycle.

   **How large the acceptance window is, this spike does not establish.** The
   two committed captures are 62s apart (`12:08:01Z` → `12:09:03Z`), but that
   is simply the elapsed time between two hand-driven calls with several
   Central-Server reads in between — it is an upper bound on when the delete
   became acceptable, not a measurement of it. Do not read it as "you must
   wait 62s"; do not read it as "there is no window" either.

   The practical consequence is the same under either reading, and is the
   part worth acting on: **`status == DELETION_IN_PROGRESS` is not a usable
   gate.** It is not a signal that the delete will be refused, and it is not
   a signal that it will be accepted. A reversal should attempt the delete
   and treat `409 action_not_possible` as retryable, rather than poll the
   status first — which is the right design precisely *because* the window is
   unknown. (The own-server case is *not* re-verified here — nothing in this
   pack can stand up a member's own Security Server yet. §7's 409 stands as
   the precedent for that topology, and whether the difference is
   hosted-vs-own-server or simply that §7's attempt landed inside a window
   this one stepped over, this spike cannot say.)
4. **Deleting a member on the Central Server cascades to its subsystems.**
   §7 paired `DELETE /subsystems/{subsystem_id}` with `DELETE
   /members/{member_id}`. The subsystem call is redundant: `DELETE
   /members/{member_id}` returns 204 with the subsystem record still present,
   and `GET /clients?q=PTSB` is empty immediately afterward — a subsequent
   `DELETE /subsystems/{id}` returns `404 subsystem_not_found`. Note also
   that there is **no** `GET /subsystems/{id}` on the CS at all (it returns
   **`405`**, not 404), so the CS-side probe has to be
   `GET /clients?q=<member_code>` and its absence signal is an **empty list,
   not a 404**.
5. **The reversal is not the forward order reversed.** Forward is
   `ss.client_add` → `ss.sign_key_csr` → `ss.client_register` (that order is
   load-bearing — `hurl/steps.py` records why). The order that works
   backwards is `client_register` → `client_add` → `sign_key_csr`: delete the
   client first, its SIGN key after. This is what was established live; the
   strict mirror (key before client) was not tried, so a reversal walk cannot
   be implemented as a blind `reversed(completed_steps)`.

### What happens to a hosted member's SIGN key (the §7 gap)

Nothing. That is the finding. When the hosted client is deleted, its SIGN key
and certificate **survive completely intact** on the hosting server's token:
`status: REGISTERED`, `active: true`, `ocsp_status: OCSP_RESPONSE_GOOD`,
`saved_to_configuration: true` — recorded in
`unjoin.token_after_client_delete.json`, taken *after* `GET /clients` had
stopped listing PTSB at all. Deleting the client does not touch the key, and
nothing in the admin API will ever collect it. On an own-server join this is
invisible because the whole server is destroyed; on a hosted join the
hosting server keeps running and accumulates one orphaned SIGNING key per
member that ever left.

So the SIGN-key delete is a **required, explicit step**, and it must
correlate the key by `keys[].certificates[].owner_id` — `ss-plr` under
`profile: lite` carries four keys all labelled `"Sign key"` (PNIA, PLR,
MOEYS and the joined member), so a label match would delete a *different*
agency's key. This is the same correlation
`hurl/templates/fragments/PROBE_SS_SIGN_KEY.hurl.tmpl` already documents for
the forward path; the reversal needs it for a much less forgiving reason.

One call does it: `DELETE /keys/{key_id}` returns **204** and removes the key
*and* its still-`REGISTERED` certificate together. The certificate does not
have to be deleted, disabled or unregistered first. The host's other three
SIGN keys and its AUTH key are untouched, and `scripts/acceptance.sh` is
green afterward.

### The round trip closes

Both cycles ended with `scripts/member.sh remove ptsb`, and both left
`hurl/topology.json` **byte-identical** to `tests/golden/lite/topology.json`,
with the regenerated `hurl/scenarios/` tree identical too. A real call from
an authorised consumer to the departed member's service fails cleanly and
specifically — `Server.ClientProxy.UnknownMember`, `"Could not find addresses
for service provider ..."` (`unjoin.r1_after_unjoin.json`), the same clean
failure §7 recorded — not a hang and not a stale success.

**No global-configuration residue was found, and no restart was needed.** The
"if clean live de-registration turns out not to be achievable" branch of the
join-c plan does not apply.

Two carry-overs from §7 that this spike does **not** overturn:

- The orphaned-container/volume trap is unchanged, and only bites a member
  with its own Security Server. A hosted member has no container and no
  volumes, so `member.sh remove` before teardown is harmless for it. Retire
  an own-server member live *before* removing its config, or purge first.
- A demonstration join can be undone on camera. For a hosted member it is now
  genuinely quick — six calls, none of them retried, no approval round to wait
  through. Both hand-driven cycles ran end to end in about four minutes
  including the reads in between; a scripted one has no known reason to be
  slower than its six round trips, subject to finding 3's open question about
  the `DELETE /clients/{id}` window.

## Sources

`nordic-institute/X-Road` at tag `7.7.0`: `development/hurl/scenarios/setup.hurl`,
`development/hurl/scenarios/vars.env`, `development/hurl/Dockerfile`,
`Docker/xrd-dev-stack/compose.yaml`, `compose.dev.yaml`, `local-dev-run.sh`,
`development/hurl/scenarios/test-proxy-rest.hurl`. Admin API definitions:
`src/central-server/openapi-model/.../openapi-definition.yaml` and
`src/security-server/openapi-model/.../openapi-definition.yaml`.
