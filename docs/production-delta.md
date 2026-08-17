# From demonstration to production (Module 5.7 — the honest gap)

The demonstration proves the pattern; it is not a production system. The shape
of the configuration (subsystem registrations, service descriptions, semantic
map) does not change — the scale, resilience and operations around it do. Plan
and budget this gap in the multi-agency phase of the four-phase plan; never
ship the demo as production.

## What this pack does that production must not

| Demo shortcut (where) | Production requirement |
| --- | --- |
| Test CA as trust anchor (`configs/x-road-bus/federation-core.yaml`) | Accredited certification authority + real OCSP/TSA |
| `scripts/lib-stack.sh` refuses a non-loopback `network.bind` outright while the Test CA (`ca`) is still part of the compose set -- no `acknowledge_public_exposure` setting can override it | A non-loopback target must replace the Test CA with an accredited CA before deploying, not just acknowledge the exposure -- see `docs/deployment-targets.md` |
| Single Docker host, containers (`docker-compose.yml`) | Separate sized hosts per component, HA/redundancy |
| Fixed CS admin creds `xrd/secret` (test image, cannot be rotated — not read from `.env`) | Hardened access, individual accounts, audit |
| Loopback binding (`deployment.yaml`'s `network.bind`) is the *only* network control | Network segmentation, a reverse proxy terminating real TLS, and authenticated admin access — a bind address is not a substitute for any of these once the stack leaves one trusted host |
| Plain-HTTP service URLs, TLS-verify off (`configs/member-{plr,pnea,pnia}/*.yaml`'s `spec_url`) | HTTPS to information systems, certificates verified |
| Consumer connection type HTTP (`configs/member-pnea/pnea.yaml`) | HTTPS + client TLS certificate |
| Mock CSV registries (`apps/`) | The agencies' real systems (e.g. Joget DX apps) behind the same OpenAPI contracts |
| Add-ons installed and running (`acceptance/member.md`), but no collector, no alerting, no 24/7 support | A collector (`xroad-metrics`, NIIS OSS) receiving what the add-ons already emit, alerting on it, and an Operating Authority standing team to act on the alerts — see "Monitoring add-ons: installed by default, verified running, no collector" below |
| Sized for demo calls | Capacity for real volumes; security hardening + audit |
| Demo console has no authentication of its own (`apps/console/`) | Real access control on any tool that can read/mutate ACLs |
| Console holds admin credentials server-side; loopback bind plus a CSRF guard (a custom header plus an Origin check) are its only access controls — neither is authentication, and the guard defends the write/exchange endpoints against a cross-origin *browser*, not against anyone who can already reach `:8090` directly | Credentials never colocated with a public-facing demo tool; network-level isolation, and real authentication in front of any tool that can read/mutate ACLs |
| Console's ACL write path exists purely to be theatrical for an audience | No tool mutates production ACLs for demonstration purposes, ever |
| Proxy's `server-conf-cache-period` tuned to 5s (default is 60s) so an ACL change is filmable. It is now a declared deployment dimension — `deployment.yaml`'s `xroad.server_conf_cache_period`, rendered into `hurl/local.ini` — rather than a value buried in a hand-written ini, so a deployment that wants the default changes one line beside the bind and the digest pins | Leave at the documented default (or size deliberately) — a short cache period trades proxy CPU for faster-to-reflect ACL changes, a trade a real federation's traffic volume should make on purpose, not by copying a demo value. Declaring the knob does not make 5s a production value; it makes changing it a deployment decision instead of an edit to a demo file |
| The join API's operator does not provision the joining member's own server — a hosted join defaults it onto an existing Security Server, and even an own-server join (Plan C) has the pack's own host agent simulate the joining agency's infrastructure team (`apps/join-api/`) | In production, `BLOCKED` is satisfied by the member, on the member's own hardware, with the member's own CA-issued certificates — and takes days, not seconds |
| The shared `KP2_JOIN_APPLICANT_TOKEN` still exists and is what the console uses, alongside operator-issued per-agency credentials (`POST /tokens`, hash-only storage, revocation, `submitted_by` on the record). Bearer tokens either way, with no expiry, and per-request ownership is recorded but not enforced | The shared demo credential must be disabled — one credential per agency, and mTLS preferred over any bearer token. Expiry and rotation are also absent here |
| A joining member's AUTH and SIGN certificates are signed by the Test CA (`http://ca:8888`) with no identity vetting whatsoever, same as every canonical member | In production this step — verifying who is actually asking to join — is the entire trust decision, not a formality the join API can automate |
| `backend.auth: none` is what every mock in this pack actually accepts, demo-only posture (`apps/join-api/schema.py`'s `BackendAuth`, exercised by the PTSB fixture) | A real joining member must use `network_allowlist` or `proxy_injected`; the consumer must never hold the provider's own API credential |
| A joined member's service description is never automatically refreshed — X-Road reloads only on explicit refresh, so a real third-party backend (a Joget app someone edited in a browser) drifts silently from what the federation publishes | `scripts/member.sh drift <key>` *detects* this; nothing in this pack *remedies* it — a production operator still has to act on what drift reports |
| The join policy admits `GET` operations only (`configs/x-road-bus/join-policy.yaml`'s `allowed_methods`) | A production federation that needs to admit write endpoints from a joined member needs endpoint-level access rights and a different acceptance assertion — service-level `access:` grants the whole service, not the specific operations a review actually approved |
| **Live-but-uncommitted window:** `writer.apply_real()` writes `configs/member-<key>/` and the `manifest.yaml` entry, and the job then makes the member live on the running federation, all before anyone runs `git commit` — a member can be `ACTIVE, verified: true` on the stack while `git status` still shows it untracked. Two mitigations exist, not a fix: `apply_real()` refuses to *start* a new job while `configs/`/`manifest.yaml` are already dirty, and the console's join tab surfaces the fact live (an "uncommitted" flag on the request, `apps/join-api/app.py`'s `_live_uncommitted`) | A production join workflow should not have a window where the running system and its version-controlled description of itself can disagree at all — e.g. gate "live" on a successful commit, not the other way around |
| An in-process token bucket now limits `POST /requests` and `POST /requests/{id}/resume` to 30/minute per bearer token (`apps/join-api/app.py`), and a submission is refused once `out/join/` holds 200 records. Reads are unlimited. Nothing is shared between processes, nothing survives a restart, and nothing watches the refusals | Production needs a distributed quota (this one is per-process, so replicas multiply it and a restart clears it), abuse monitoring on an endpoint that can register federation members, and a real datastore behind the records the 200-record ceiling is standing in for |
| Job context (`out/join/*.json`) lives on local disk only | Not durable, not replicated, and not access-controlled beyond filesystem permissions — production needs a real datastore behind this, with its own access control |
| TCP **5500** (message exchange) and **5577** (OCSP) are never opened — this demo is single-host on a loopback bind (`deployment.yaml`'s `network.bind`) and never needs either port reachable from outside that host | A real member's Security Server needs both ports reachable to and from every peer; opening them is a ministry firewall change that takes weeks, not a config edit (onboarding path §2 G4) |
| Un-join now exports `kp2-<key>-archive`, the message-log archive volume, to a tarball under `out/retired/` before the delete — `retire_instruction()`'s printed command block, `runbook.md`'s un-join section. A tarball on the Docker host is the whole of it: no expiry, no access control, no off-host copy | A tarball is not a retention regime. The message log is subject to a statutory retention period; production needs storage with its own access control and expiry, off the host that ran the federation, and a procedure that proves the export happened — deleting the archive before the period elapses still converts a retirement into an evidence gap (onboarding path §2 GX) |
| This pack is an instance of the onboarding path's §1 development track — synthetic data, Test CA, loopback bind — with the prohibition on real personal data enforced only by whoever writes the config (authorship) | A real development track enforces the same prohibition via the membership terms (onboarding path §1), not by who happens to be typing |
| `POST /requests/{id}/approve` requires a `decision_reference` string and records it verbatim (`apps/join-api/app.py`) — the demo verifies only that it is non-empty, not that it refers to anything real | In production this is a minuted Steering Committee decision (Ref Model §5.3); the endpoint actuates that decision, it does not authorise one — an operator's bearer token was never the accountable party, and `decision_reference` is evidence of the real one, not a replacement for it |
| `onboarding/<key>/00-gates.md` names four gates (Application/G0, Admission/G1, Certificates/G3, Go-live/G6) as **not implemented in this demo** rather than building a stub file for each — the onboarding path's §7 specifies all ten files; this pack builds the three Topic 5 teaches (5.2, 5.3, 5.4) and names the rest, following the principle that a named absence teaches as well as an implementation (no curriculum change) | Each of the four is a real organisational or third-party act with no per-request field this pack could carry honestly: a signed membership agreement, a Steering Committee minute, a CA/TSA issuance record, a monitored go-live handover. **What would change the decision:** a later addition of a join subtopic to Topic 5 for one of these gates (matching how the join module already exceeds the curriculum) — until then, building the file would be the pack teaching a gate no video covers |
| `Service.spec_url` and the `servers[].url` inside the spec it returns are both fetched from inside the `join-api` container — which also holds `JOB_SECRETS` (admin user, admin password, token PIN) and can reach every admin API on `:4000`. Both are now restricted before the fetch: check 9a `spec_url_origin` (`validate.py`) admits only `http`/`https`, only host names on `configs/x-road-bus/join-policy.yaml`'s `join.spec_url_hosts`, and refuses every IP literal (loopback, link-local, `169.254.169.254`) and `localhost` regardless of that list; `follow_redirects=False` is pinned on both fetches so a 302 cannot walk past the allowlist. The field-conformance check still adds no second fetch from the post-approval job path | An allowlist is not segregation. Production should resolve and fetch applicant-submitted URLs from a network segment with **no route to the admin plane** at all, so the guard is topology rather than a list one careless edit widens — and the credentials should not be in the fetching container in the first place |
| Manual approval is hard-wired: `configs/x-road-bus/federation-core.yaml`'s `policy.management_request_approval: explicit` is genuinely enforced by `hurl/generate.py`'s `check_policy()`, but nothing generates the alternative — the onboarding path's own §3 fact 1 (automatic/manual is an operator policy choice since 6.21.0) is modelled only on one side | A production federation choosing automatic approval needs the Central Server's `local.ini` auto-approve flags generated and mounted, plus the two templates that assume a `WAITING` status omitted rather than skipped — see `docs/decisions/xroad-770-notes.md` §12 for the mechanism; not started without a driver, since there is no admin-API route, no clean technical saving, and no audit-trail evidence preserved by keeping the switch (`docs/path-conformance.yaml` `S3.4`) |

## The task the hardening list forgets

Migrate each agency off its legacy point-to-point links and **retire them** —
parallel-run the once-only exchange beside the old link, confirm the two agree,
cut consumers over, decommission. Schedule per agency in the multi-agency phase;
a new bus does not retire old links by itself.

## Where deploy time goes within `--full`

`hurl/run-linkup.sh` emits phase timings (`out/deploy-timings.txt`) and a Hurl
`--report-json` per-request breakdown, which is what isolates which part of a
cold deploy dominates — container boot, propagation, or the certificate
sequences. Two cold runs, back to back, illustrate the split:

| | Run 1 | Run 2 |
| --- | --- | --- |
| Containers healthy | 234s | 215s |
| Hurl admin-API run | 504s | 462s |
| **Total** | **738s** | **677s** |

This instrumentation starts timing right before `docker compose up`, after
`generate.py` and the `--fast` tier have already run, and stops at the end of
the Hurl run, before `seed.sh`/`acceptance.sh` — a narrower window than the
whole of `run-linkup.sh`/`scripts/verify.sh --full`, chosen on purpose to
isolate exactly these two phases.

**The real finding is inside the "Hurl admin-API run" phase.** Summing every
individual request's own `time` from the JSON report gives only **~131s** of
actual HTTP work — identical across both runs, since the requests themselves
are deterministic. The other **~373s (run 1) / ~331s (run 2)** is Hurl's own
`--retry-interval 10000` sleeping between whole-file retries (37 retries in
run 1, 34 in run 2 — consistent, not a fluke), and it is **overwhelmingly
concentrated on four specific entries**: the four members' own "Register the
subsystem" `PUT .../clients/{id}/register` calls, each retried 8–10 times in
both runs before the Central Server's global configuration had propagated far
enough for the next step to see the registration. Confirmed by
cross-referencing the retried entry indices against `hurl/.build/setup.hurl`'s
own line numbers, not guessed from the label.

**Conclusion:** the deploy is dominated by **waiting for X-Road's own
asynchronous global-configuration propagation after each member's subsystem
registration** — not container boot (a distant second), and not raw HTTP
request latency (a rounding error by comparison). This is inherent to
X-Road's own design, not something this pack's tooling can shortcut by
itself. It does, however, point at a real opportunity: **parallelising the
independent per-member sequences** (blocked today only by a
`GET /management-requests?...WAITING` race that needs fixing first) is very
likely worth doing — the bottleneck is exactly the shape parallelism
collapses, serial per-member propagation waits that could instead overlap.

## Bumping X-Road means bumping three digests together

`deployment.yaml`'s `xroad.cs_digest` and `xroad.ss_digest` (added alongside
`cs_tag`/`version` for readability, same `tag@sha256:…` style as
`testca_tag`) pin the Central Server and Security Server sidecar images by
digest, resolved from the images this pack runs
(`docker image inspect --format '{{index .RepoDigests 0}}'`), the same rule
`testca_tag` already follows. `docs/decisions/xroad-770-notes.md` §4 explains why the
Hurl scenarios and the images have to move together — a scenario written
against one X-Road tag can call admin-API endpoints a different tag's server
has never heard of. Bumping X-Road therefore means re-resolving and updating
all three pins (`cs_digest`, `ss_digest`, `testca_tag`) in the same change
that bumps the scenarios, not one at a time. `python:3.12-slim`'s digest
(both Dockerfiles) is unrelated to the X-Road version and is pinned/re-pinned
on its own schedule — see the trade-off note at each `FROM` line: a
digest-pinned base image stops receiving security patches until someone
re-pins it, unlike the X-Road images, which only move when X-Road does.
`ghcr.io/orange-opensource/hurl` is pinned inline in `hurl/compose.hurl.yml`
rather than via a `deployment.yaml` key, since it is a build/test tool this
pack depends on, not part of the federation it deploys.

## What a join and un-join do to Central-Server state

Two things about how X-Road's admin API behaves during a reversal, both
true of the current registry (`hurl/steps.py`). They are measured across
join/un-join cycles through `apps/join-api` — two own-server (PVTB,
`ss-pvtb`), one hosted (PHTB, on `ss-plr`) — each reaching `ACTIVE`/`ACTIVE,
verified: true` and then `DELETE /members/<key>` back to `RETIRED` in low
single-digit seconds, 5-6 reversals each, nothing retried.

### `DELETE /clients/{id}` did NOT need the `409 action_not_possible` retry

`docs/decisions/xroad-770-notes.md` §7 recorded a multi-minute `409` window for an
own-server member and §11 could not say whether the hosted spike's clean run
meant the window did not exist or merely that its 62s gap had stepped over
it. **The own-server case now says the same thing the hosted one did, and
says it harder.** In the scripted walk, `PUT /clients/{id}/unregister` and
`DELETE /clients/{id}` are consecutive calls roughly **one second** apart —
far tighter than §11's 62s — and the delete was accepted first time in both
cycles. `record["retry_budget_left"]` came out of every walk at the full
`12`: not one reversal in any of the three cycles was retried at all.

So `_reversal_succeeded()`'s treatment of `409 action_not_possible` as
retryable-within-the-run's-budget is **correct but so far unexercised**: no
run has ever produced that 409. §7's window remains unexplained and is not
reproducible from this pack's own reversal walk; the guard stays, because a
guard that has never fired is much cheaper than the failure it prevents.

### The own-server AUTH certificate needs no reversal of its own

The open question was whether `PUT /token-certificates/{hash}/unregister`
had to run before the member's container and volumes were destroyed, or
Central-Server state would be left behind. **It does not, and none is.**
Measured directly, before and after the walk:

| Central-Server read | Before the un-join | After the walk, before `docker rm` |
| --- | --- | --- |
| `GET /security-servers` | includes `PROGRESSA:GOV:PVTB:SS-PVTB` | **gone** — only the canonical servers |
| `GET /clients?q=PVTB` | `PROGRESSA:GOV:PVTB`, `…:PVTB:AWARDS` | `[]` |

`DELETE /members/{member_id}` **cascades to the member's Security Server
record and with it the AUTH certificate's registration** — the same cascade
§11 finding 4 recorded for subsystems, one level further out than that
finding claimed. There is no `AUTH_CERT_DELETION_REQUEST` and nothing to
approve.

What is left over is entirely on the *member's* side of the line and dies
with the container: `ss-pvtb` kept running for several minutes after the
walk with its own owner client `REGISTERED` and both certificates
`REGISTERED`/`OCSP_RESPONSE_GOOD` on its token, and the federation was
green throughout. The `retire_instruction`'s two Docker commands are
therefore the whole of the remaining cleanup, exactly as
`apps/join-api/job.py` claims — and skipping them is a *host* hygiene
problem (a stale database and `/etc/xroad` a later member reusing the key
would inherit), not a federation-state problem.

The Central Server's `GET /management-requests` history does keep PVTB's
`AUTH_CERT_REGISTRATION_REQUEST`, `CLIENT_REGISTRATION_REQUEST` and the
auto-processed `CLIENT_DELETION_REQUEST` (`status: null`, confirming §11
finding 1 for the own-server topology too). That is an audit log, the same
one every canonical member's registrations sit in — not residue.

## In production, nobody runs `scripts/join-agent.sh`

The table at the top of this document carries the one-line version. The gap
is worth stating at full size, because `BLOCKED` is the single place in this
pack where the demonstration and a real federation differ in *kind* rather
than in scale.

`scripts/join-agent.sh <key>` is a `docker compose up --wait` against a
service block `hurl/generate.py` already wrote. It brings `ss-<key>` from
nothing to a healthy Security Server in roughly 76–100 seconds, on the same
host, from the same operator's shell, with certificates a Test CA signs for
anyone who asks. **Every one of those properties is wrong in production, and
deliberately so:**

- **It is not the operator's machine.** The server belongs to the joining
  agency, on the joining agency's own hardware or tenancy, inside the
  joining agency's own network. The federation operator has no shell on it
  and should not want one.
- **It is not the operator's certificate.** The AUTH and SIGN certificates
  come from an accredited CA after that CA has verified who is actually
  asking — which, as the table above says, *is* the trust decision, not a
  formality. The Test CA's `POST /testca/sign` signs any CSR it is handed.
- **It is not under two minutes.** It is a procurement, an installation, a
  firewall change, a certificate issuance, and an approval, in some order,
  run by people who do not work for the operator. Days is optimistic; weeks
  is normal.
- **`BLOCKED` therefore is not a spinner.** `apps/join-api/job.py` never
  expires it into `FAILED`, and a resume that still finds the server absent
  returns to `BLOCKED` as many times as it takes — that is not
  defensive coding, it is the only honest model of a state whose exit
  condition is another organisation finishing its work. A demonstration that
  clears `BLOCKED` in under two minutes should say out loud that it is
  compressing weeks into that; the console's join tab helps by naming the
  exact command rather than pretending the wait is the API's own.

The reverse direction inherits the same split. `DELETE /members/{key}`
finishes in seconds and then hands the operator two Docker commands, because
this API has no Docker socket by design. In production
those two commands are again the member's own work, on the member's own
infrastructure, and the federation has no way to compel them — which is
exactly why the *federation-side* reversal has to be complete on its own,
and is: the Central Server forgets the member whether or not anyone ever
turns the member's server off.

## The frozen `identifiers:` contract, and the one amendment to it

**What:** `manifest.yaml`'s `identifiers:` block — labelled "Frozen
identifiers — cross-pack join keys for KP3/KP4" — dropped
`PROGRESSA/GOV/MOEYS:PEMIS` from `identifiers.members` and
`PROGRESSA/GOV/MOEYS/PEMIS/pemis-api` from `identifiers.services`. MoEYS is
retired from the pack entirely: `identity.members.moeys`,
`configs/member-moeys/`, `ss-moeys`/`app-pemis` (`docker-compose.yml`),
`apps/specs/pemis.openapi.yaml`, and the PEMIS seed data
(`apps/data/school_records.csv`) are all removed. The federation goes from
four canonical members to three (PNEA, PLR, PNIA) plus the owner (PDGA). The
2.6 negative check's unauthorised caller moves from `MOEYS:PEMIS` to
`PLR:ENROLMENT` — already a bus member, already a provider, already holding
no grant on PNIA's `identity-api` — proving the same "on the bus ≠ granted
this service" point without inventing a new member for it.

**Why it is safe to amend a contract labelled frozen:** the block exists so
KP3/KP4 have stable join keys to build against, not so it can never change.
`grep -rn "MOEYS\|PEMIS" 10-Knowledge-Products/KP3-DPI/` returns no hits, and
KP3's own config skeleton (`identity-pnia` / `registry-plr` / `registration`
/ `payment-paypro`) already builds on PNIA and PLR, not MoEYS. Reconfirmed
against the original finding: still no hits.

**Sign-off:** obtained from the repo owner and recorded here — this is the
one change in the reduction another pack (KP3, KP4) could have been building
against, so it gets a record even though it was a local decision in practice
(KP3 was not yet building against it).

**MoEYS's reserved capacity, kept, not reused:** `hurl/generate.py`'s
`PINNED_PORTS["moeys"] = (6000, 6080)` stays in the table (commented, not
deleted) so `allocate_ports()`'s determinism and the un-join byte-identity
clause both keep holding for every *other* pinned or freshly-allocated
member — freeing 6000/6080 would change what a fresh member gets allocated
today. `PINNED_SCENARIO_NO`/`PINNED_SERVICE_SCENARIO_NO`'s `"moeys"` entries
(scenario numbers 22/32) are left similarly reserved-but-unused; nothing
currently walks into them since `discover_members()` never returns a
`"moeys"` key.

## The golden corpus and a cold deploy agree

Regenerating the golden corpus (`python3 hurl/generate.py --out <tmp> --env
tests/golden/env.fixture` and `tests/test_golden.py`'s own
`_generate_hosted_fixture()`) reproduces `tests/golden/deployment/` and
`tests/golden/hosted-fixture/generated/` byte-identical to what is already
committed. Diffed against the pre-reduction four-member topology by eye:
every difference is either MoEYS's removal (its Security Server, its
subsystem entry, its scenario files, its `vars.env`/`topology.sh` lines) or
the `"profile"` key's removal from `topology.json` — plus the
capability-based config filenames the scenarios' `# Source of truth:`
comments name.

`scripts/verify.sh --full` from cold runs green on the single-topology
baseline, including a console smoke pass exercising
`apps/console/truth.py`/`static/app.js`/`static/index.html`. The
once-only-exchange negative check (`2.6.4`) confirms the unauthorised caller
is `PROGRESSA/GOV/PLR/ENROLMENT` (moved off the retired MoEYS), and the
denial comes back as the specific X-Road fault
`{"type": "Server.ServerProxy.AccessDenied", "message": "Request is not
allowed: SERVICE:PROGRESSA/GOV/PNIA/IDENTITY/identity-api", ...}` — not a
transport error, not a hang. `--full`'s own `xroad fixture drift check`
(`scripts/capture-xroad-fixtures.sh --check`) independently re-captures the
same fault live and confirms it still matches the committed fixture.

**Un-join byte-identity clause:** after an un-join, `hurl/topology.json`
diffs byte-identical against `tests/golden/deployment/topology.json`, and
`scripts/acceptance.sh` confirms it itself (`PASS 2.7.unjoin(<member>)`,
`PASS 2.7.unjoin.topology`), discovered generically from the newest
`RETIRED` record in `out/join/*.json` — there is no `lite`/`full` choice to
make, so this is simply "byte-identical to the golden," full stop.

## The field-conformance check fails when the contract and the response disagree

A conformance check nobody has seen fail proves nothing. This one has been
made to fail, against a running federation:

1. `apps/specs/pnia-identity.openapi.yaml`'s declared properties gained
   `mother_name` (already a column in `apps/data/persons.csv`, already one of
   the three fields PNIA's contract deliberately withholds).
2. `docker compose restart app-pnia` — the mock reloads `DECLARED_FIELDS`
   from the now-changed spec and starts returning `mother_name` for real.
3. The spec file was reverted on disk **without** restarting `app-pnia`
   again — the same timing gap a real, hand-coded backend would have (it
   does not re-derive its own output from the contract on every request the
   way this pack's mock does).
4. `scripts/acceptance.sh --only 2.6.6` against that state:
   ```
   AssertionError: apps/specs/pnia-identity.openapi.yaml: undeclared=['mother_name'] missing=[]
   FAIL 2.6.6 — field conformance — both responses carry exactly the fields their contract declares (G5.9)
   ```
   The failure names the field **NAME** only — no value (`Awa Jallow`, the
   seeded `mother_name` for this NIN) appears anywhere in the check's own
   output, confirming the purpose-limitation guarantee held under a real
   failure, not just in the code that was supposed to guarantee it.
5. `app-pnia` restarted once more (reloading the reverted spec) and
   `2.6.6` passed clean again. `git status` showed no diff on
   `apps/specs/` — the spec file itself was never left changed.

This is the property that "holds by construction" only while the mock
derives its own output from the same file `contract_fields()` reads — see
the "`Service.spec_url`..." row above for the related, pre-existing fetch
surface. The moment a real backend replaces the mock (KP4), this timing gap
is not synthetic; it is the normal case.

## What a hosted join costs, end to end

PTSB ("Progressa Tertiary Scholarship Board"), the fixture identity
`apps/join-api/tests/test_job.py`'s own `_payload()`/`_own_payload()` use,
publishes `awards-api` (the `app-ptsb` mock `docker-compose.yml` ships
specifically for this purpose) with access granted to PNEA:EXAMS. `POST
/requests` → validated, `SUBMITTED` → `POST .../approve` (`decision_reference`
supplied) → `ACTIVE, verified: true` in well under two minutes, `verified_by`
a real `r1` call returning `HTTP 404` from the service root (the
"registry-perfect but dead" check passing on a live backend that has no `/`
route — exactly the non-X-Road-response proof `job.py`'s own comment says any
such response gives). `DELETE /members/ptsb` walks it back to `RETIRED` in a
few seconds, and `configs/`/`manifest.yaml` come back git-clean.

## What an own-server join costs, end to end

Same PTSB identity, re-submitted with `security_server.own_server: true`
after the hosted record above was fully retired (freeing the code). Reaches
`BLOCKED` almost immediately (waiting for `ss-ptsb`); `scripts/join-agent.sh
ptsb` brings it healthy inside the documented 76–100s range; `POST
.../resume` runs the full own-server bring-up sequence (`ss.bringup_init`
through `ss.client_register`, then `service.publish`/`service.acl`, then
`join.r1_verify`) and reaches `ACTIVE, verified: true` in roughly two
minutes — the shared run budget (`RETRY_BUDGET = 12`) still had most of its
12 retries left when `join.r1_verify` started, and `join.r1_verify` itself
succeeds well inside its own `R1_RETRY_BUDGET = 54` (9-minute) window without
needing more than a handful of retries. This confirms the fix genuinely
works live, not just in `apps/join-api/tests/test_job.py`'s
synthesised-response test: an own-server join really does reach
`verified: true`, not just `ACTIVE`. Un-joins back to `RETIRED` cleanly (the
same federation-side walk as the hosted case), plus the two documented
manual Docker commands (`docker rm -f ss-ptsb`; `docker volume rm
kp2-ptsb-db kp2-ptsb-conf kp2-ptsb-archive`).

## Why `join-agent.sh` brings containers up with the full Compose file set

Bringing `ss-ptsb` up left `cs` and `ca` with `Config.Healthcheck: null` —
their Docker `HEALTHCHECK` had silently disappeared. Root cause:
`cs`/`ca`'s healthchecks are defined only in `hurl/compose.hurl.yml` (see
that file's own comment), never in the base `docker-compose.yml`.
`join-agent.sh` was invoking `docker compose` with `lib-stack.sh`'s
`COMPOSE` array (`docker-compose.yml` + `compose.members.yml` only) — and
`ss-<key>`'s `x-sidecar` anchor declares `depends_on: [cs, ca]`, so bringing
up `ss-ptsb` still touches `cs`/`ca` via that dependency. Compose computes
each service's up-to-date-ness from a hash of its *own invocation's* merged
config; against the narrower `COMPOSE` file set that hash no longer matches
what `run-linkup.sh` originally started `cs`/`ca` with (under `COMPOSE_HURL`
= `COMPOSE` + `hurl/compose.hurl.yml`), so Compose silently recreated them —
using the config that has no healthcheck at all. Harmless functionally
(their state lives in named volumes, and both came back up fine), but a
real, reproducible regression in the operator's own health signal,
discoverable only by actually running the manual own-server join path live —
something no automated tier (`--fast`, `--live`, or even `--full`'s own
console smoke pass) exercises, because `--full` never automates past
`BLOCKED` (this API has no Docker socket by design). **Fixed:**
`join-agent.sh` now uses `COMPOSE_ALL` (already defined in `lib-stack.sh`,
already includes `hurl/compose.hurl.yml`), so its view of `cs`/`ca` matches
what is already running and Compose has no drift to "fix" by recreating
them.

## Current measured figures

| | Figure |
|---|---|
| `--fast` | ~50–53s, 331 tests (330 passed, 1 skipped) |
| `--live` | ~80–81s |
| `--full` (cold, single topology, four Security Servers) | ~13 min (~780s) — `out/deploy-timings.txt`: roughly 150–200s containers-healthy, ~400s Hurl admin-API run, plus `--fast`/teardown/seed/acceptance/console-smoke around it |
| RAM, steady state (`docker stats --no-stream`) | ~11 GiB — four Security Servers ~2.2–2.3 GiB each, Central Server ~1.8–2.0 GiB, Test CA ~88 MiB, two mock providers ~32 MiB each; includes the monitoring add-ons, which cost nothing extra to deploy |
| Hosted join → `ACTIVE, verified: true` | well under two minutes |
| Hosted un-join → `RETIRED` | a few seconds |
| Own-server bring-up (`join-agent.sh`) | 76–100s |
| Own-server resume → `ACTIVE, verified: true` | roughly two minutes, well inside `R1_RETRY_BUDGET`'s 540s ceiling |

`README.md`'s `--fast`/`--live`/`--full`/join timing paragraphs and this
table are the two places these figures are recorded.

## Monitoring add-ons: installed by default, verified running, no collector

The operational- and environmental-monitoring add-ons ship on the NIIS
Sidecar image itself. Checked against
`nordic-institute/X-Road-Security-Server-sidecar`'s own
`security_server_sidecar_user_guide.md` (§1.1, §2.2): the Sidecar ships two
tags per version, a `-slim` tag (bare packages) and a plain "full" tag that
additionally bundles message logging, operational monitoring
(`xroad-opmonitor`) and environmental monitoring (`xroad-monitor`),
pre-installed and supervisord-managed with **no separate admin-API call and
no environment variable** — confirmed against a live container's own
`supervisorctl status`. `docker-compose.yml`'s `x-sidecar` anchor has never
carried a `-slim` suffix (confirmed by resolving `deployment.yaml`'s pinned
`ss_digest` against `docker pull` — the digest is exactly the plain `7.7.0`
tag's), so **all four Security Servers already run both add-ons**, with
nothing separate to install. `tests/test_addons.py` renders the real Compose
config the same way `scripts/check-exposure.sh` does and asserts no `-slim`
image resolves for any `ss-*` service, guarding against a future change
silently switching it off.

`scripts/acceptance.sh` carries a per-*server* check (not per-member — an
add-on is a Security Server property, and `hurl/topology.sh`'s `SS_ORDER`
already includes any joined member's own server, so a hosted or own-server
join needs no separate assertion): for every host in `SS_ORDER`, `docker exec
<host> supervisorctl status` must show both `xroad-monitor` and
`xroad-opmonitor` `RUNNING`. **A real bug found live:** `supervisorctl
status` itself exits non-zero whenever *any* managed process is not
`RUNNING` — `xroad-autologin` legitimately `EXITED` after unlocking the
software token, which made the naive `... || return 1` form fail the check
even though both add-ons were correctly running; fixed by letting only the
two `grep` matches decide the result, ignoring `supervisorctl`'s own exit
code. Verified after the fix: `PASS 2.x.addons(ss-pdga|ss-pnea|ss-plr|ss-pnia)`,
every run.

**The deliberate gap:** both add-ons are installed and *proven running* on
every Security Server this pack brings up, at bring-up, as G4 requires — but
no collector exists. `xroad-monitor`/`xroad-opmonitor` emit to nothing
today; G4's third exit test ("is its monitoring data arriving centrally?")
**remains unmet**, on purpose. The onboarding path's own framing is the
reason this is a defensible incompleteness rather than an oversight:
"Installing them at G4 is trivial; retrofitting them across an installed
base is a campaign" — the add-ons prove the "trivial" half live (four
servers, no deploy-time cost), and the absent collector is documented next
to it rather than hidden. `xroad-metrics` (NIIS, open source) is the
component that would close G4's third exit test; adding it is out of scope
here and left for later.

**Un-join walk, confirmed unaffected — by reasoning and live.**
`hurl/steps.py`'s `REVERSAL_ORDER` (`service.acl`, `service.publish`,
`ss.client_register`, `ss.client_add`, `ss.sign_key_csr`,
`cs.members_member`) is a fixed list of X-Road admin-API calls that delete a
client, a key, or a CS member record — none of it touches a container's
image or its supervisord configuration, so a departing hosted member's
un-join is categorically unable to affect the add-ons, which are
server-level, not client-level. Confirmed live too: with the federation up,
a real hosted join (PTSB on `ss-plr`) reaching `ACTIVE, verified: true`, then
`DELETE /members/ptsb` walking it back to `RETIRED`. `docker exec ss-plr
supervisorctl status` before the join, right after `ACTIVE`, and right after
`RETIRED` all showed `xroad-monitor`/`xroad-opmonitor` `RUNNING` under the
**same PIDs** throughout — the un-join never touched the supervised
processes, and `git status` on `configs/`/`manifest.yaml` came back clean
afterward.

## A known propagation flake in `2.x(PNEA:EXAMS) REGISTERED`

Consecutive cold `--full` runs can fail at `2.x(PNEA:EXAMS) REGISTERED`,
timing out its 60s retry budget. This is asynchronous propagation under host
load, not a defect: `acceptance.sh`'s own comment documents the same risk
("a cold reproducibility run hit PNEA:EXAMS still short of REGISTERED the
instant acceptance.sh started ... though it settled seconds later").

It reproduces when several cold `--full` cycles run inside a short window on
a laptop-class single Docker host with several JVM Security Servers
competing for it — the same host-CPU contention this document describes
above. A run on a rested host passes the check first time. Treat a failure
here as a signal about the host, and re-run before looking for a cause in
the pack.

## Capturing a join response through a shell variable corrupts it

A join response written out with `RESP=$(curl ...); echo "$RESP" > file`
arrives as invalid JSON: the `diff` field carries literal newline bytes
where `\n` should be, and both `python -m json.tool` and `jq` refuse to
parse it. **zsh's builtin `echo` interprets backslash escapes by default**
(unlike bash's), so it rewrites every `\n` inside a JSON string into a real
newline before the bytes reach the file.

The API's response is correct; the pipeline reading it is not. Write it
straight to disk with `curl -o`, or use `printf '%s' "$RESP"`, and the JSON
is properly escaped throughout. Worth knowing before concluding that
`apps/join-api` emits malformed JSON.

## What automatic approval actually costs

Measured directly: the same identity (PTSB, hosted on `ss-plr`, one service)
joined twice on the same running stack, once against the committed `explicit` configuration
(control), once with all three `[center]` auto-approve flags set on the
Central Server and its registration/management services restarted
(experiment) — see `docs/decisions/xroad-770-notes.md` §12 for how those flags
were found and why a restart was needed. PTSB was retired between the two
runs (`DELETE /members/ptsb`), freeing the code for reuse.

| | Control (`explicit`) | Experiment (`automatic`) |
| --- | --- | --- |
| `approved_at` → `ACTIVE` | 75.4s | 34.9s |
| Shared retry budget spent | 0 of 12 | 1 of 12 |
| CS management-request | id 9, `APPROVED`, `created_at` only | id 11, `APPROVED`, `created_at` only |
| CS access-log line | 1 line, 1392 bytes, one origin IP | 1 line, 1393 bytes, same origin IP |

**Question 2 — what it saves.** Both runs stayed inside a single shared retry
budget (`RETRY_BUDGET = 12`, `RETRY_INTERVAL_SECONDS = 10.0`s,
`apps/join-api/job.py`) — the control spent none of it, the experiment spent
one retry (~10s) at the step that polls for a `WAITING` request. The 40s
wall-clock gap between the two runs is smaller than one retry interval and
is not a clean signal on its own (N=1, and the control's own earlier hosted
run in this same document reports "well under two minutes" with normal
variance) — **it does not support a claim that automatic approval is
technically faster**. The path's "collapses days into seconds" framing was
never about this call; the seconds are already spent under `explicit`, in
this demo, because `join-api`'s own operator-approval call happens
immediately after submission. Automatic approval collapses the *organisational*
wait — a human available to click approve — which this demo has no way to
measure because it never has one, exactly as the spike predicted before
measuring anything.

**Question 3 — what it costs in evidence, the most important comparison
here.** Contrary to the onboarding path's §3 fact 3, the Central Server's own
records carry **no origin-IP field and no approver field at all**, under
either policy: `GET /api/v1/management-requests/{id}` returns only `id`,
`type`, a categorical `origin` (`SECURITY_SERVER`/`CENTER`, not an address),
`security_server_owner`, `status` and `created_at` — checked against the live
API for both request 9 and request 11 above, byte-identical in shape. The
origin IP the path's claim actually refers to lives one layer down, in the
registration/management service's plain Apache-style access log
(`centralserver-management-service-access.log`), and it is written
identically either way: one `POST /managementservice/manage` line per join,
carrying the Security Server's container IP and a timestamp, with no
per-request correlation beyond matching the timestamp by eye. **Automatic
approval costs nothing at the X-Road layer, because explicit approval was
never providing anything there to lose.** What is genuinely lost is
KP2-specific, not X-Road-specific: `join-api`'s own `decision_reference` —
the only place an approver identity or a reason is recorded anywhere in this
stack — exists only because a human called `POST /requests/{id}/approve`
with one. An automatic policy has no equivalent call and so no equivalent
field; the evidence gap the path should name is in the *pack's own* audit
layer, not the Central Server's.

**A defect found live, incidental to the question asked — since fixed.**
`writer.apply_real` (`apps/join-api/writer.py`) is not atomic: it writes
`configs/<member>/` and `manifest.yaml` before calling
`render_onboarding_tree`, which did a bare `onboarding_dir.mkdir(parents=True)`
with no `exist_ok=True`. Re-joining a member whose `onboarding/<key>/`
directory still exists (the normal state right after that member's own
retirement — retirement does not delete it, by design, it *is* the
retirement record) threw `FileExistsError`, returned an uncaught 500, and
left `configs/` and `manifest.yaml` genuinely modified and uncommitted —
which then blocked every subsequent join attempt via the same dirty-checkout
guard that was supposed to prevent exactly this kind of half-done state. The
pack's own exercise loop (join → un-join → join again, `exercises.md` 2 and
4) walked straight into it.

`render_onboarding_tree` now recognises a retired member's tree by its
`99-retirement.md` (`writer.RETIREMENT_FILE`) and replaces it — the same
`shutil.rmtree` `scripts/render_onboarding.py` already used to re-render a
canonical member — so the loop runs. Replace, not merge: a re-joined member
must not carry the retirement record of the membership that ended. A
leftover `onboarding/<key>/` *without* that file is still refused, now as a
`MemberCollisionError` → 409 naming the directory, rather than a raw 500.

**And the non-atomicity itself is now compensated.** `apply_real` takes a
pre-image of every path a join writes — `manifest.yaml`,
`onboarding/catalogue.yaml`, `configs/member-<key>/`, `onboarding/<key>/` —
before the first write, and restores it on any failure, then re-runs
`generate.py` so `hurl/`'s derived files (which are gitignored, so no
restore can bring them back) match the restored inputs again. A caller that
catches any error from `apply_real` can assume the pack is as it was; the
single exception is `RollbackFailure`, raised only when the restore ITSELF
failed, which is the one case that still needs a human and says so.

A copy under `/tmp`, deliberately, not `git checkout`/`git clean`: the
join-api container's whole justification for `safe.directory = *`
(`apps/join-api/Dockerfile`) is that every git call it makes is a read, and
a rollback built on git would hand a container with the monorepo
bind-mounted read-write the ability to delete a developer's uncommitted
work if a pathspec were ever wrong. The copy needs no such capability and
behaves identically in a checkout, on the droplet, and in a bare copy.

Two ceilings remain, both named rather than hidden. The compensation is
file-level, so a crash *between* the restore and the regenerate leaves a
stale `hurl/`; and concurrency is handled by a lock (`app.py`'s
`_APPLY_LOCK`), not by the filesystem — two approvals in one process
serialise, two processes writing the same checkout do not. A single
transactional write of the whole pack tree would close both, and the live
bind mounts (`docker-compose.yml`) rule that out today: the pack directory
is a mount target, so it cannot be swapped out from under a running
container.

## The catalogue this pack builds, and the one it does not (G5.6, S6.2, S6a.4, S7.6)

**What exists now.** Every published service has a generated catalogue entry
(`onboarding/<key>/04-catalogue/<code>.md`) carrying its X-Road service id, its
contract reference, the semantic entity and tier-1 exchange pattern, the lawful
basis, the ACL subjects, and a **link to the signed SLA** — which is what closed
the orphan-SLA problem in the direction it was actually open. The SLA always
existed and was always reachable from the member; it was not reachable *from the
service*, which is the direction the person who needs it reads in.
`onboarding/catalogue.yaml` aggregates the same data over the instance, and
`GET /catalogue` serves it under the applicant token. All of it is derived from
the registration — no new field is asked of a joining member.

That is the **register's own output**, and it is one half of what the single
word "catalogue" covers. It is complete for members this register admitted and
blind to everything else on the bus. The other half is a **collector**, and this
pack does not build it — see "What production must still add" below, which is
the operative absence in this section now.

**Why the half that was built had to be built first.** X-Road's metaservices make a
large part of a catalogue reconstructible at any time: `listClients` for members
and subsystems, `listMethods` for a provider's published services, `getOpenAPI`
for each service's contract. A collector — NIIS's X-Road Catalog is the reference
implementation, and Finland's Liityntäkatalogi and Estonia's X-tee catalogue
front-end are the two production portals over one — can rebuild all of that on a
timer, for any ecosystem, whenever it is procured.

It cannot rebuild the three fields that carry the governance:

| Field | On the wire? | Recoverable later? |
| --- | --- | --- |
| Endpoints, methods, schemas | yes (`getOpenAPI`) | yes, any time |
| Member and subsystem identity | yes (`listClients`) | yes, any time |
| **Signed SLA** | no | **no — only at registration** |
| **Lawful basis** | no | **no — only at registration** |
| **Tier-1 BB pattern classification** | no | **no — only at registration** |

Metadata not captured at the moment of registration is not recoverable
afterwards by any amount of catalogue engineering. **The table above is the
whole argument for having done this at registration, and it stays true now that
the entry exists** — it is the reason the entry could not wait for the catalogue
product to be procured. Designed in `docs/decisions/service-catalogue-design.md`.

**What production must still add.** The pack covers the register's own output —
derived from the join, complete for members this register admitted, structurally
unable to go stale. A production ecosystem needs the other half too, and the two
are not substitutes. **This table is the live absence; nothing below it is
built:**

| Production needs | Why the register's output does not cover it |
| --- | --- |
| A collector (X-Road Catalog or equivalent) | Sees services *actually* published, including any this register never authorised |
| A portal with search and browse | A YAML file is not a discovery surface for a non-technical service owner |
| A federation-wide view | This register covers one instance; `listClients` is per-instance, per partner |
| A freshness policy on the collector | A derived view cannot be stale; a scraped one always can, and needs its own SLA |
| A system-level registry (RIHA analogue) | Which *system* is authoritative for a dataset, and on what legal basis, is not a property of any service contract — and no amount of OpenAPI answers it |

The last row is the one most often missed. Estonia's discovery story is not the
X-tee catalogue; it is RIHA, and the catalogue is secondary to it. A programme
that builds a service catalogue and calls the discovery problem solved has
answered "what APIs exist" and left "who is authoritative for this data, and may
I have it" exactly where it was.

**One consequence for retirement, easy to lose.**
GX.3 asks the operator to *remove the catalogue entry* at retirement. There is
nothing to remove, and that is by construction rather than by omission: the
aggregate is derived wholesale from `manifest.yaml` and `configs/member-*/`, so
an un-join that deletes the member's configuration also deletes its services
from the next read. A delete path can be forgotten; a derivation cannot.
Asserted live against the running API, not against the file
(`scripts/acceptance.sh`'s `2.7.unjoin.catalogue(<member>)`). The member's own
`onboarding/<key>/04-catalogue/` entries stay on disk deliberately — the
aggregate is the live view, the record is evidence of what the operator revoked.
GX.3's other two halves — certificate revocation and notifying consumers who
held access — stay absent regardless, which is why that row does not move.
