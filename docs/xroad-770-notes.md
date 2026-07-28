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
| Authenticate with an API key created via `POST /api/v1/api-keys` | **Session login.** `POST /login` with form params `username`/`password`, capture the `XSRF-TOKEN` cookie, and send it as an `X-XSRF-TOKEN` header on every subsequent call. `scripts/lib.sh`'s `api_key()` was never going to work as written. |
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
restart, five Security Servers mapped to Progressa's institutions — and takes
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
trade-off, which is why it is called out in `docs/production-delta.md`.

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

## Sources

`nordic-institute/X-Road` at tag `7.7.0`: `development/hurl/scenarios/setup.hurl`,
`development/hurl/scenarios/vars.env`, `development/hurl/Dockerfile`,
`Docker/xrd-dev-stack/compose.yaml`, `compose.dev.yaml`, `local-dev-run.sh`,
`development/hurl/scenarios/test-proxy-rest.hurl`. Admin API definitions:
`src/central-server/openapi-model/.../openapi-definition.yaml` and
`src/security-server/openapi-model/.../openapi-definition.yaml`.
