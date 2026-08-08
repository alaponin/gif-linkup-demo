# From demonstration to production (Module 5.7 — the honest gap)

The demonstration proves the pattern; it is not a production system. The shape of
the configuration (subsystem registrations, service descriptions, semantic map)
does not change — the scale, resilience and operations around it do. Plan and
budget this gap in the multi-agency phase of the four-phase plan; never ship the
demo as production.

## What this pack does that production must not

| Demo shortcut (where) | Production requirement |
| --- | --- |
| Test CA as trust anchor (`configs/x-road-bus/federation-core.yaml`) | Accredited certification authority + real OCSP/TSA |
| Single Docker host, containers (`docker-compose.yml`) | Separate sized hosts per component, HA/redundancy |
| Fixed CS admin creds `xrd/secret` (test image, cannot be rotated — not read from `.env`) | Hardened access, individual accounts, audit |
| Loopback binding (`deployment.yaml`'s `network.bind`, exposure-and-secrets Task 1) is the *only* network control | Network segmentation, a reverse proxy terminating real TLS, and authenticated admin access — a bind address is not a substitute for any of these once the stack leaves one trusted host |
| Plain-HTTP service URLs, TLS-verify off (`configs/member-{plr,pnea,pnia}/*.yaml`'s `spec_url`) | HTTPS to information systems, certificates verified |
| Consumer connection type HTTP (`configs/member-pnea/pnea.yaml`) | HTTPS + client TLS certificate |
| Mock CSV registries (`apps/`) | The agencies' real systems (e.g. Joget DX apps) behind the same OpenAPI contracts |
| Add-ons installed and running (Wave 5, `acceptance/member.md`), but no collector, no alerting, no 24/7 support | A collector (`xroad-metrics`, NIIS OSS) receiving what the add-ons already emit, alerting on it, and an Operating Authority standing team to act on the alerts — see "Wave 5: monitoring add-ons installed, collector deliberately absent" below |
| Sized for demo calls | Capacity for real volumes; security hardening + audit |
| Demo console has no authentication of its own (`apps/console/`) | Real access control on any tool that can read/mutate ACLs |
| Console holds admin credentials server-side; loopback bind plus a CSRF guard (custom header + Origin check, request-boundary plan S13) are its only access controls — neither is authentication, and the guard defends the write/exchange endpoints against a cross-origin *browser*, not against anyone who can already reach `:8090` directly | Credentials never colocated with a public-facing demo tool; network-level isolation, and real authentication in front of any tool that can read/mutate ACLs |
| Console's ACL write path exists purely to be theatrical for an audience | No tool mutates production ACLs for demonstration purposes, ever |
| Proxy's `server-conf-cache-period` tuned to 5s (`xroad-demo-local.ini`, default is 60s) so an ACL change is filmable | Leave at the documented default (or size deliberately) — a short cache period trades proxy CPU for faster-to-reflect ACL changes, a trade a real federation's traffic volume should make on purpose, not by copying a demo value |
| The join API's operator does not provision the joining member's own server — a hosted join defaults it onto an existing Security Server, and even an own-server join (Plan C) has the pack's own host agent simulate the joining agency's infrastructure team (`apps/join-api/`, design spec §6.1) | In production, `BLOCKED` is satisfied by the member, on the member's own hardware, with the member's own CA-issued certificates — and takes days, not seconds |
| One shared `KP2_JOIN_APPLICANT_TOKEN` for every applicant (`scripts/gen-secrets.sh`) | One credential per agency; should prefer mTLS (design spec §7's "Token model, and its limit") |
| A joining member's AUTH and SIGN certificates are signed by the Test CA (`http://ca:8888`) with no identity vetting whatsoever, same as every canonical member | In production this step — verifying who is actually asking to join — is the entire trust decision, not a formality the join API can automate |
| `backend.auth: none` is what every mock in this pack actually accepts, demo-only posture (`apps/join-api/schema.py`'s `BackendAuth`, join-b Task 6's PTSB fixture) | A real joining member must use `network_allowlist` or `proxy_injected`; the consumer must never hold the provider's own API credential (design spec §2.5) |
| A joined member's service description is never automatically refreshed — X-Road reloads only on explicit refresh, so a real third-party backend (a Joget app someone edited in a browser) drifts silently from what the federation publishes | `scripts/member.sh drift <key>` *detects* this (design spec §2.4); nothing in this pack *remedies* it — a production operator still has to act on what drift reports |
| The join policy admits `GET` operations only (`configs/x-road-bus/join-policy.yaml`'s `allowed_methods`, design spec §2.3) | A production federation that needs to admit write endpoints from a joined member needs endpoint-level access rights and a different acceptance assertion — service-level `access:` grants the whole service, not the specific operations a review actually approved |
| **Live-but-uncommitted window:** `writer.apply_real()` writes `configs/member-<key>/` and the `manifest.yaml` entry, and the job then makes the member live on the running federation, all before anyone runs `git commit` — confirmed live (join-b Task 6): a member can be `ACTIVE, verified: true` on the stack while `git status` still shows it untracked. Two mitigations exist, not a fix: `apply_real()` refuses to *start* a new job while `configs/`/`manifest.yaml` are already dirty (spec S9), and the console's join tab surfaces the fact live (an "uncommitted" flag on the request, `apps/join-api/app.py`'s `_live_uncommitted`) | A production join workflow should not have a window where the running system and its version-controlled description of itself can disagree at all — e.g. gate "live" on a successful commit, not the other way around |
| No rate limiting, no quota — the join API accepts as many requests as it is given | Production needs both, plus abuse monitoring on an endpoint that can register federation members |
| Job context (`out/join/*.json`) lives on local disk only | Not durable, not replicated, and not access-controlled beyond filesystem permissions — production needs a real datastore behind this, with its own access control |
| TCP **5500** (message exchange) and **5577** (OCSP) are never opened — this demo is single-host on a loopback bind (`deployment.yaml`'s `network.bind`) and never needs either port reachable from outside that host | A real member's Security Server needs both ports reachable to and from every peer; opening them is a ministry firewall change that takes weeks, not a config edit (onboarding path §2 G4) |
| Un-join deletes `kp2-<key>-archive`, the message-log archive volume, with no retention step (`runbook.md`'s un-join section) | The message log is subject to a statutory retention period; deleting the archive before that period elapses converts a retirement into an evidence gap (onboarding path §2 GX) |
| This pack is an instance of the onboarding path's §1 development track — synthetic data, Test CA, loopback bind — with the prohibition on real personal data enforced only by whoever writes the config (authorship) | A real development track enforces the same prohibition via the membership terms (onboarding path §1), not by who happens to be typing |
| `POST /requests/{id}/approve` requires a `decision_reference` string and records it verbatim (`apps/join-api/app.py`, Wave 2 Task 2) — the demo verifies only that it is non-empty, not that it refers to anything real | In production this is a minuted Steering Committee decision (Ref Model §5.3); the endpoint actuates that decision, it does not authorise one — an operator's bearer token was never the accountable party, and `decision_reference` is evidence of the real one, not a replacement for it |
| `onboarding/<key>/00-gates.md` names four gates (Application/G0, Admission/G1, Certificates/G3, Go-live/G6) as **not implemented in this demo** rather than building a stub file for each (D3: no curriculum change, Wave 4) — the onboarding path's §7 specifies all ten files; this pack builds the three Topic 5 teaches (5.2, 5.3, 5.4) and names the rest, P2's "a named absence teaches as well as an implementation" | Each of the four is a real organisational or third-party act with no per-request field this pack could carry honestly: a signed membership agreement, a Steering Committee minute, a CA/TSA issuance record, a monitored go-live handover. **What would change the decision:** a later wave adding a join subtopic to Topic 5 for one of these gates (matching how 2.7's join module already exceeds the curriculum, P6) — until then, building the file would be the pack teaching a gate no video covers |

## The task the hardening list forgets

Migrate each agency off its legacy point-to-point links and **retire them** —
parallel-run the once-only exchange beside the old link, confirm the two agree,
cut consumers over, decommission. Schedule per agency in the multi-agency phase;
a new bus does not retire old links by itself.

## Where the ~900s deploy actually goes (testing-strategy Task 5)

Nobody knew which part of the ~880–918s deploy dominated — container boot,
propagation, or the certificate sequences — until `hurl/run-linkup.sh`
started emitting phase timings (`out/deploy-timings.txt`) and a Hurl
`--report-json` per-request breakdown. Two cold runs, back to back:

| | Run 1 | Run 2 |
| --- | --- | --- |
| Containers healthy | 234s | 215s |
| Hurl admin-API run | 504s | 462s |
| **Total** | **738s** | **677s** |

Both runs land a bit under the ~880–918s figures measured for the whole of
`run-linkup.sh`/`scripts/verify.sh --full` in earlier plans — expected, not
a discrepancy: this instrumentation starts timing right before `docker
compose up`, after `generate.py` and the `--fast` tier have already run,
and stops at the end of the Hurl run, before `seed.sh`/`acceptance.sh`. It
measures a narrower window on purpose, to isolate exactly the two phases
the plan asked about.

**The real finding is inside the "Hurl admin-API run" phase.** Summing
every individual request's own `time` from the JSON report gives only
**~131s** of actual HTTP work — identical across both runs, since the
requests themselves are deterministic. The other **~373s (run 1) / ~331s
(run 2)** is Hurl's own `--retry-interval 10000` sleeping between
whole-file retries (37 retries in run 1, 34 in run 2 — consistent, not a
fluke), and it is **overwhelmingly concentrated on four specific entries**:
the four members' own "Register the subsystem" `PUT
.../clients/{id}/register` calls, each retried 8–10 times in both runs
before the Central Server's global configuration had propagated far
enough for the next step to see the registration. Confirmed by cross-
referencing the retried entry indices against `hurl/.build/setup.hurl`'s
own line numbers, not guessed from the label.

**Conclusion:** the deploy is dominated by **waiting for X-Road's own
asynchronous global-configuration propagation after each member's
subsystem registration** — not container boot (~215–234s, a distant
second), and not raw HTTP request latency (~131s, a rounding error by
comparison). This is inherent to X-Road's own design, not something this
pack's tooling can shortcut by itself. It does, however, directly answer
this plan's own "Out of scope" question: **parallelising the four
independent member sequences (blocked today only by the
`GET /management-requests?...WAITING` race the plan already flagged as a
prerequisite fix) is very likely worth revisiting** — the bottleneck is
exactly the shape parallelism collapses, four *serial* per-member
propagation waits that could instead overlap, and the four retried entries
above are precisely the four sequences that would run concurrently.

## Bumping X-Road means bumping three digests together (reproducible-builds plan Task 2)

`deployment.yaml`'s `xroad.cs_digest` and `xroad.ss_digest` (added alongside
`cs_tag`/`version` for readability, same `tag@sha256:…` style as
`testca_tag`) pin the Central Server and Security Server sidecar images by
digest, resolved 2026-08-01 from the images this pack was actually running
(`docker image inspect --format '{{index .RepoDigests 0}}'`), the same rule
`testca_tag` already followed. `docs/xroad-770-notes.md` §4 explains why the
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

## An own-server join and its un-join, live end to end (join-c plan Task 5)

Everything before this was inferred from a cold deploy's own certificate
sequences or from `docs/xroad-770-notes.md` §7's hand-driven retirement.
This section is the first time the pack actually drove one through
`apps/join-api` — join, and un-join — and it confirms two things about how
X-Road's admin API behaves during a reversal, still true of the current
registry (`hurl/steps.py`).

**What was run**, 2026-08-03: three live join/un-join cycles through
`apps/join-api` — two own-server (PVTB, `ss-pvtb`), one hosted (PHTB, on
`ss-plr`) — each reaching `ACTIVE`/`ACTIVE, verified: true` and then
`DELETE /members/<key>` back to `RETIRED` in low single-digit seconds, 5-6
reversals each, nothing retried.

### `DELETE /clients/{id}` did NOT need the `409 action_not_possible` retry

`docs/xroad-770-notes.md` §7 recorded a multi-minute `409` window for an
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

## In production, nobody runs `scripts/join-agent.sh` (join-c plan Task 5)

The table at the top of this document already carries the one-line version.
Having now run it live, the gap is worth stating at full size, because
`BLOCKED` is the single place in this pack where the demonstration and a
real federation differ in *kind* rather than in scale.

`scripts/join-agent.sh <key>` is a `docker compose up --wait` against a
service block `hurl/generate.py` already wrote. It took **76–100 seconds**
to bring `ss-pvtb` from nothing to a healthy Security Server, on the same
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
- **It is not 76 seconds.** It is a procurement, an installation, a firewall
  change, a certificate issuance, and an approval, in some order, run by
  people who do not work for the operator. Days is optimistic; weeks is
  normal.
- **`BLOCKED` therefore is not a spinner.** `apps/join-api/job.py` never
  expires it into `FAILED`, and a resume that still finds the server absent
  returns to `BLOCKED` as many times as it takes (spec S6.1) — that is not
  defensive coding, it is the only honest model of a state whose exit
  condition is another organisation finishing its work. A demonstration that
  clears `BLOCKED` in 76 seconds should say out loud that it is compressing
  weeks into a minute; the console's join tab helps by naming the exact
  command rather than pretending the wait is the API's own.

The reverse direction inherits the same split. `DELETE /members/{key}`
finishes in seconds and then hands the operator two Docker commands, because
this API has no Docker socket by design (spec decision 8). In production
those two commands are again the member's own work, on the member's own
infrastructure, and the federation has no way to compel them — which is
exactly why the *federation-side* reversal has to be complete on its own,
and is: the Central Server forgets the member whether or not anyone ever
turns the member's server off.

## The frozen `identifiers:` contract was amended (Wave 3 Task 1, D1)

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

**When:** 2026-08-06.

**Why it is safe to amend a contract labelled frozen:** the block exists so
KP3/KP4 have stable join keys to build against, not so it can never change.
`grep -rn "MOEYS\|PEMIS" 10-Knowledge-Products/KP3-DPI/` returns no hits, and
KP3's own config skeleton (`identity-pnia` / `registry-plr` / `registration`
/ `payment-paypro`) already builds on PNIA and PLR, not MoEYS. Re-run at the
start of this task (2026-08-06) to reconfirm KP3 had not gained content
since the original design analysis (`docs/onboarding-alignment-design.md`
§1.2): still no hits.

**Sign-off:** obtained from the repo owner, 2026-08-06, recorded here per
this task's brief — this is the one change in Wave 3 another pack (KP3,
KP4) could have been building against, so it gets a record even though it
was a local decision in practice (KP3 was not yet building against it).

**MoEYS's reserved capacity, kept, not reused:** `hurl/generate.py`'s
`PINNED_PORTS["moeys"] = (6000, 6080)` stays in the table (commented, not
deleted) so `allocate_ports()`'s determinism and the un-join byte-identity
clause both keep holding for every *other* pinned or freshly-allocated
member — freeing 6000/6080 would change what a fresh member gets allocated
today. `PINNED_SCENARIO_NO`/`PINNED_SERVICE_SCENARIO_NO`'s `"moeys"` entries
(scenario numbers 22/32) are left similarly reserved-but-unused; nothing
currently walks into them since `discover_members()` never returns a
`"moeys"` key.

**Known follow-up, not done here:** `tests/golden/{full,lite}/` still
describe the four-member topology and are now stale by construction — Task 1
of the wave-3 plan explicitly defers regeneration to Task 6, after Tasks 2–5
land, so there is exactly one re-baselining event instead of one per task.
`hurl/README.md`'s lite/full scenario-numbering table and prose were not
touched for the same reason: Task 4 (profile-machinery removal) rewrites
that material wholesale, and touching it twice would be wasted motion.

## Wave 3 Task 6: proved live from cold, and every estimate replaced (2026-08-07)

The closing task of the wave 3 plan — regenerate the golden corpus, run one
`scripts/verify.sh --full` from cold (Tasks 1–5 had never been proved live
together), run a real join and un-join through `apps/join-api`, and replace
every estimated figure in this document, `README.md` and
`docs/topology-profile-decision.md` with a measured one.

**Golden regeneration:** `python3 hurl/generate.py --out <tmp> --env
tests/golden/env.fixture` and `tests/test_golden.py`'s own
`_generate_hosted_fixture()` both reproduced `tests/golden/deployment/` and
`tests/golden/hosted-fixture/generated/` byte-identical to what Task 5 already
committed — nothing to update. Diffed the new `deployment/` tree against the
pre-Wave-3 `tests/golden/full/` (checked out from `d658566^`) by eye: every
difference is either MoEYS's removal (its Security Server, its subsystem
entry, its scenario files, its `vars.env`/`topology.sh` lines) or the
`"profile"` key's removal from `topology.json` — plus the capability-based
config filenames Task 3 already renamed (`configs/x-road-bus/2.1.yaml` →
`federation-core.yaml`, etc., visible in the scenarios' `# Source of truth:`
comments). No late-surfacing defect from Tasks 1–5.

**`scripts/verify.sh --full` from cold:** green, first time all of Wave 3's
changes ran together, including the console smoke pass exercising Task 4's
rewritten `apps/console/truth.py`/`static/app.js`/`static/index.html` for the
first time live. `2.6.4` (the once-only-exchange negative check) confirmed the
open tripwire Task 1's ledger flagged for this run: the unauthorised caller is
now `PROGRESSA/GOV/PLR/ENROLMENT` (moved off the retired MoEYS), and the
denial came back as the specific X-Road fault
`{"type": "Server.ServerProxy.AccessDenied", "message": "Request is not
allowed: SERVICE:PROGRESSA/GOV/PNIA/IDENTITY/identity-api", ...}` — not a
transport error, not a hang. `--full`'s own `xroad fixture drift check`
(`scripts/capture-xroad-fixtures.sh --check`) independently re-captured the
same fault live and confirmed it still matches the committed fixture.

**A real hosted join and un-join, end to end (`apps/join-api`):** PTSB
("Progressa Tertiary Scholarship Board"), the fixture identity
`apps/join-api/tests/test_job.py`'s own `_payload()`/`_own_payload()` already
use, publishing `awards-api` (the `app-ptsb` mock `docker-compose.yml` already
ships specifically for this live-proof purpose) with access granted to
PNEA:EXAMS. `POST /requests` → validated, `SUBMITTED` → `POST
.../approve` (`decision_reference` supplied) → `ACTIVE, verified: true` in
**~73s**, `verified_by` a real `r1` call returning `HTTP 404` from the
service root (the "registry-perfect but dead" check passing on a live
backend that has no `/` route — exactly the non-X-Road-response proof
`job.py`'s own comment says any such response gives). `DELETE
/members/ptsb` walked it back to `RETIRED` in **~3s**, and `configs/` /
`manifest.yaml` came back git-clean.

**A real own-server join and un-join, end to end — the first live
confirmation of the `R1_RETRY_BUDGET` fix:** same PTSB identity, re-submitted
with `security_server.own_server: true` after the hosted record above was
fully retired (freeing the code). Reached `BLOCKED` almost immediately
(waiting for `ss-ptsb`); `scripts/join-agent.sh ptsb` brought it healthy in
**102s** (just above the documented 76–100s range); `POST .../resume` ran the
full own-server bring-up sequence (`ss.bringup_init` through
`ss.client_register`, then `service.publish`/`service.acl`, then
`join.r1_verify`) and reached **`ACTIVE, verified: true` in 131s** — the
shared run budget (`RETRY_BUDGET = 12`) had 7 of its 12 retries left when
`join.r1_verify` started (i.e. `ss.client_register`'s propagation wait
consumed about 5, not the 11 of 12 the original defect report measured), and
`join.r1_verify` itself succeeded well inside its own `R1_RETRY_BUDGET = 54`
(9-minute) window without needing more than a handful of retries. **This
closes the open item both this document ("An own-server join could not reach
`verified: true` — fixed, not yet re-verified live", above) and
`acceptance/join-member.md` (its own-server case's un-met clause) recorded**:
the fix genuinely works live, not just in `apps/join-api/tests/test_job.py`'s
synthesised-response test. Un-joined back to `RETIRED` (fast — the same
federation-side walk as the hosted case), then the two documented manual
Docker commands (`docker rm -f ss-ptsb`; `docker volume rm kp2-ptsb-db
kp2-ptsb-conf kp2-ptsb-archive`).

**A real, live-found defect in `scripts/join-agent.sh` — fixed in the same
commit.** Bringing `ss-ptsb` up left `cs` and `ca` with `Config.Healthcheck:
null` — their Docker `HEALTHCHECK` had silently disappeared. Root cause:
`cs`/`ca`'s healthchecks are defined only in `hurl/compose.hurl.yml` (see
that file's own comment), never in the base `docker-compose.yml`.
`join-agent.sh` was invoking `docker compose` with `lib-stack.sh`'s
`COMPOSE` array (`docker-compose.yml` + `compose.members.yml` only) — and
`ss-<key>`'s `x-sidecar` anchor declares `depends_on: [cs, ca]`, so bringing
up `ss-ptsb` still touches `cs`/`ca` via that dependency. Compose computes
each service's up-to-date-ness from a hash of its *own invocation's* merged
config; against the narrower `COMPOSE` file set that hash no longer matches
what `run-linkup.sh` originally started `cs`/`ca` with (under
`COMPOSE_HURL` = `COMPOSE` + `hurl/compose.hurl.yml`), so Compose silently
recreated them — using the config that has no healthcheck at all. Harmless
functionally (their state lives in named volumes, and both came back up
fine), but a real, reproducible regression in the operator's own health
signal, discovered only because this task actually ran the manual own-server
join path live — something no automated tier (`--fast`, `--live`, or even
`--full`'s own console smoke pass) exercises, because `--full` never
automates past `BLOCKED` (design decision 8: this API has no Docker socket).
**Traces to the join-c plan (Wave 1), which wrote `scripts/join-agent.sh`
using `COMPOSE` from the start** — not something Wave 3 introduced; Wave 3
only exposed it by being the first `--full`-plus-manual-own-server-join
proof run since. **Fixed:** `join-agent.sh` now uses `COMPOSE_ALL` (already
defined in `lib-stack.sh`, already includes `hurl/compose.hurl.yml`), so its
view of `cs`/`ca` matches what is already running and Compose has no drift
to "fix" by recreating them.

**Measured, replacing every estimate:**

| | Estimate (pre-Task-6) | Measured (2026-08-07) |
|---|---|---|
| `--fast` | ~49s (2026-08-03, 291 tests) | **~53s**, 331 tests [330 passed, 1 skipped] |
| `--live` | ~78s | **~81s** — confirms, does not correct |
| `--full` (cold, single D5 topology) | ~670s (~11 min), `docs/topology-profile-decision.md` §2 | **~763s (~12.7 min)** — `out/deploy-timings.txt`: 200s containers-healthy + 404s Hurl admin-API run = 604s deploy subtotal, plus `--fast`/teardown/seed/acceptance/console-smoke around it |
| RAM (steady state, canonical topology up) | ~11 GB | **~10.9 GiB** (`docker stats --no-stream`: 4× Security Server 2.23–2.25 GiB, `cs` 1.81 GiB, `ca` 88 MiB, `app-pnia`/`app-plr` 32 MiB each) — confirms, does not correct |
| Hosted join → `ACTIVE, verified: true` | ~64–93s (join-b/join-c) | **~73s** — within noise |
| Hosted un-join → `RETIRED` | seconds | **~3s** |
| Own-server bring-up (`join-agent.sh`) | 76–100s | **102s** — just above range |
| Own-server resume → `ACTIVE, verified: true` | not previously reached | **131s**, well inside `R1_RETRY_BUDGET`'s 540s ceiling |

`--fast` and `--live` are within noise of their prior figures — no
correction needed. RAM likewise confirms the design's ~11 GB estimate almost
exactly. **`--full` is the one figure that moved**: 763s measured against
~670s estimated, about 14% (~93s) higher. `docs/topology-profile-decision.md`
§2 and §5.3 are corrected accordingly — §5.3's crossover point (where
dropping the profile split stops being a net time win across a plan) moves
from the estimated N≈4 down to N≈2.9; the qualitative recommendation still
holds for plans with 1–2 `--full` cycles, is roughly a wash at 3 (not clearly
a win, as the estimate had it), and is a net loss at 4+. `README.md`'s
`--fast`/`--live`/`--full`/join timing paragraphs and this section together
are the two places those figures are now recorded as measured rather than
estimated.

**Un-join byte-identity clause, confirmed against the single golden:** after
both un-joins above, `hurl/topology.json` diffed byte-identical against
`tests/golden/deployment/topology.json`, and `scripts/acceptance.sh` itself
confirmed it: `PASS 2.7.unjoin(PTSB)` and `PASS 2.7.unjoin.topology`, discovered
generically from the newest `RETIRED` record in `out/join/*.json` (the
own-server one) — there is no more `lite`/`full` choice to make (Task 4/5
already collapsed that), so this is simply "byte-identical to the golden,"
full stop.

## Wave 5: monitoring add-ons installed, collector deliberately absent (G-06)

**Task 1 spike, before writing anything (per the plan's own instruction):**
how are the operational- and environmental-monitoring add-ons enabled on the
NIIS Sidecar image at 7.7.0 — package install, environment variable, or
admin-API call? Checked against
`nordic-institute/X-Road-Security-Server-sidecar`'s own
`security_server_sidecar_user_guide.md` (§1.1, §2.2): the Sidecar ships two
tags per version, a `-slim` tag (bare packages) and a plain "full" tag that
additionally bundles message logging, operational monitoring
(`xroad-opmonitor`) and environmental monitoring (`xroad-monitor`),
pre-installed and supervisord-managed with **no separate admin-API call and
no environment variable** — confirmed against a live container's own
`supervisorctl status`. **Finding: this branches to neither of the plan's two
anticipated cases.** `docker-compose.yml`'s `x-sidecar` anchor has never
carried a `-slim` suffix (confirmed by resolving `deployment.yaml`'s pinned
`ss_digest` against `docker pull` — the digest is exactly the plain `7.7.0`
tag's), so **all four Security Servers already ran both add-ons before this
wave touched anything**; there was nothing to install. Task 1 became: document
the mechanism at the image line (`docker-compose.yml`), and add a regression
guard (`tests/test_addons.py`, renders the real Compose config the same way
`scripts/check-exposure.sh` does and asserts no `-slim` image resolves for any
`ss-*` service) against a future change silently switching it off. Golden
trees (`tests/test_golden.py`) regenerated byte-identical — expected, since
nothing in `hurl/generate.py` or the templates changed.

**Task 2: proved live, not just declared.** `scripts/acceptance.sh` gained a
per-*server* check (not per-member — an add-on is a Security Server property,
and `hurl/topology.sh`'s `SS_ORDER` already includes any joined member's own
server, so a hosted or own-server join needs no separate assertion): for every
host in `SS_ORDER`, `docker exec <host> supervisorctl status` must show both
`xroad-monitor` and `xroad-opmonitor` `RUNNING`. **A real bug found live on
the first run:** `supervisorctl status` itself exits non-zero whenever *any*
managed process is not `RUNNING` — `xroad-autologin` legitimately `EXITED`
after unlocking the software token, which made the naive `... || return 1`
form fail the check even though both add-ons were correctly running; fixed by
letting only the two `grep` matches decide the result, ignoring
`supervisorctl`'s own exit code. Re-verified live after the fix: `PASS
2.x.addons(ss-pdga|ss-pnea|ss-plr|ss-pnia)`, every run.

**A pre-existing flake, ruled out as unrelated by a live control test.** Three
consecutive cold `--full` runs while landing this task's fix all failed at the
*same, pre-existing* check — `2.x(PNEA:EXAMS) REGISTERED` — timing out its
60s retry budget; `acceptance.sh`'s own comment already documents this exact
asynchronous-propagation risk ("a cold reproducibility run hit PNEA:EXAMS
still short of REGISTERED the instant acceptance.sh started ... though it
settled seconds later"). Rather than assume the new add-ons loop was
responsible, ran a control: `git stash` the `acceptance.sh` change and repeat
`--full` cold with the *unmodified, pre-Wave-5* script. **It failed at the
identical check.** That rules out this wave's change as the cause — the flake
predates it and is host-load-related (this session ran five consecutive cold
`--full` cycles inside about an hour; `docs/production-delta.md`'s own
"Host CPU contention is real" finding, above, already documents this class of
risk on a laptop-class single Docker host running several JVM Security
Servers concurrently). Restored the fix and re-ran; the sixth cold run passed
clean end to end, including this exact check, on the first try.

**Measured, against the Wave 3 Task 6 baseline (2026-08-07, single D5
topology, four Security Servers):**

| | Wave 3 Task 6 baseline | Wave 5 (2026-08-08) |
|---|---|---|
| `--full` (cold, wall clock) | 763s (~12.7 min) | **783s (~13.0 min)** — +20s (+2.6%), within noise |
| Deploy subtotal (`out/deploy-timings.txt`) | 200s containers-healthy + 404s Hurl run = 604s | **156s + 395s = 551s** — faster, not slower (ordinary run-to-run variance; the add-on ships with the image already in use, so the Hurl admin-API sequence itself is byte-for-byte unchanged, confirmed by the golden diff above) |
| RAM, steady state (`docker stats --no-stream`) | ~10.9 GiB (4× SS ~2.2–2.3 GiB, CS 1.81 GiB, CA 88 MiB, 2 mocks 32 MiB each) | **~11.1 GiB** (4× SS 2.21–2.29 GiB, CS 1.96 GiB, CA 88 MiB, 2 mocks 32 MiB each) — within noise |

**Global Constraint applied:** the plan's own ceiling was "if the add-ons push
`--full` past roughly 15 minutes (900s), stop and re-scope." 783s is nowhere
close — the constraint that motivated the original `lite`/`full` profile
split (§8.2/D5) does not re-apply here, because the add-ons cost nothing to
*deploy*: they were already running before this wave started measuring.

**The deliberate gap, restated here per the plan's Task 2 Step 5 (see design
doc §8.4's "Monitoring collection layer" row for the fuller reasoning):** both
add-ons are now installed and *proven running* on every Security Server this
pack brings up, at bring-up, as G4 requires — but no collector exists.
`xroad-monitor`/`xroad-opmonitor` emit to nothing today; G4's third exit test
("is its monitoring data arriving centrally?") **remains unmet**, on purpose.
The onboarding path's own framing is the reason this is a defensible
incompleteness rather than an oversight: "Installing them at G4 is trivial;
retrofitting them across an installed base is a campaign" — this wave proves
the "trivial" half live (four servers, +0s deploy cost), and documents the
absent collector next to it rather than hiding it. `xroad-metrics` (NIIS,
open source) is the component that would close G4's third exit test; adding
it is out of scope for this wave (Global Constraint: "do not build a
collector") and left for a later wave or KP.

**Un-join walk, confirmed unaffected — by reasoning and live.**
`hurl/steps.py`'s `REVERSAL_ORDER` (`service.acl`, `service.publish`,
`ss.client_register`, `ss.client_add`, `ss.sign_key_csr`,
`cs.members_member`) is a fixed list of X-Road admin-API calls that delete a
client, a key, or a CS member record — none of it touches a container's
image or its supervisord configuration, so a departing hosted member's
un-join is categorically unable to affect the add-ons, which are
server-level, not client-level. Confirmed live, not just by reasoning: with
the federation from the timing run above still up, submitted a real hosted
join (PTSB on `ss-plr`, same identity and shape as Wave 3 Task 6's live
proof, adjusted for two validation rules Wave 2/Wave 4 added since — a
`semantic.entity` the map declares, and an `sla:` block per published
service) through `apps/join-api`, reaching `ACTIVE, verified: true`, then
`DELETE /members/ptsb` to walk it back to `RETIRED`. `docker exec ss-plr
supervisorctl status` before the join, right after `ACTIVE`, and right after
`RETIRED` all showed `xroad-monitor`/`xroad-opmonitor` `RUNNING` under the
**same PIDs** throughout — the un-join never touched the supervised
processes, and `git status` on `configs/`/`manifest.yaml` came back clean
afterward. No finding on the add-ons — this is the expected, unremarkable
result the plan's Step 6 asked to have confirmed rather than assumed.

**A suspected bug in `POST /requests`'s response, investigated and ruled
out — false positive in the diagnostic tooling, not `apps/join-api`.** The
first live join above appeared to return invalid JSON: capturing the
response into a shell variable (`RESP=$(curl ...)`) and writing it out with
`echo "$RESP" > file` produced a `diff` field with literal, unescaped
newline bytes where `\n` should have been, and both `python -m json.tool`
and `jq` refused to parse the result. Rather than patch `app.py`'s response
construction on the strength of that alone, re-ran the same request writing
the response straight to disk with `curl -o` (no shell variable in the
path) and inspected the raw bytes: properly escaped, valid JSON, `\n`
present as the two-byte sequence throughout. Bisected the difference by
capturing into a variable again and comparing `echo "$RESP"` against
`printf '%s' "$RESP"` byte-for-byte — `echo` was the one that mangled it.
**Root cause: this session's shell is zsh, whose builtin `echo` interprets
backslash escape sequences by default (unlike bash's), so `echo "$RESP"`
silently rewrote every `\n` inside the JSON string into a real newline
byte before it ever reached the file** — a defect in the diagnostic
command, not in `apps/join-api`. No code change needed; recorded here
because the wrong conclusion was reported first and the correction belongs
next to it, not silently dropped.
