# From demonstration to production (Module 5.7 — the honest gap)

The demonstration proves the pattern; it is not a production system. The shape of
the configuration (subsystem registrations, service descriptions, semantic map)
does not change — the scale, resilience and operations around it do. Plan and
budget this gap in the multi-agency phase of the four-phase plan; never ship the
demo as production.

## What this pack does that production must not

| Demo shortcut (where) | Production requirement |
| --- | --- |
| Test CA as trust anchor (`2.1.yaml`) | Accredited certification authority + real OCSP/TSA |
| Single Docker host, containers (`docker-compose.yml`) | Separate sized hosts per component, HA/redundancy |
| Fixed CS admin creds `xrd/secret` (test image, cannot be rotated — not read from `.env`) | Hardened access, individual accounts, audit |
| Loopback binding (`deployment.yaml`'s `network.bind`, exposure-and-secrets Task 1) is the *only* network control | Network segmentation, a reverse proxy terminating real TLS, and authenticated admin access — a bind address is not a substitute for any of these once the stack leaves one trusted host |
| Plain-HTTP service URLs, TLS-verify off (`2.2/2.4/2.5.yaml`) | HTTPS to information systems, certificates verified |
| Consumer connection type HTTP (`2.3.yaml`) | HTTPS + client TLS certificate |
| Mock CSV registries (`apps/`) | The agencies' real systems (e.g. Joget DX apps) behind the same OpenAPI contracts |
| No monitoring/alerting, no 24/7 support | Operational monitoring, alerting, Operating Authority standing team |
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
| The join policy admits `GET` operations only (`configs/x-road-bus/2.7.yaml`'s `allowed_methods`, design spec §2.3) | A production federation that needs to admit write endpoints from a joined member needs endpoint-level access rights and a different acceptance assertion — service-level `access:` grants the whole service, not the specific operations a review actually approved |
| **Live-but-uncommitted window:** `writer.apply_real()` writes `configs/member-<key>/` and the `manifest.yaml` entry, and the job then makes the member live on the running federation, all before anyone runs `git commit` — confirmed live (join-b Task 6): a member can be `ACTIVE, verified: true` on the stack while `git status` still shows it untracked. Two mitigations exist, not a fix: `apply_real()` refuses to *start* a new job while `configs/`/`manifest.yaml` are already dirty (spec S9), and the console's join tab surfaces the fact live (an "uncommitted" flag on the request, `apps/join-api/app.py`'s `_live_uncommitted`) | A production join workflow should not have a window where the running system and its version-controlled description of itself can disagree at all — e.g. gate "live" on a successful commit, not the other way around |
| No rate limiting, no quota — the join API accepts as many requests as it is given | Production needs both, plus abuse monitoring on an endpoint that can register federation members |
| Job context (`out/join/*.json`) lives on local disk only | Not durable, not replicated, and not access-controlled beyond filesystem permissions — production needs a real datastore behind this, with its own access control |
| TCP **5500** (message exchange) and **5577** (OCSP) are never opened — this demo is single-host on a loopback bind (`deployment.yaml`'s `network.bind`) and never needs either port reachable from outside that host | A real member's Security Server needs both ports reachable to and from every peer; opening them is a ministry firewall change that takes weeks, not a config edit (onboarding path §2 G4) |
| Un-join deletes `kp2-<key>-archive`, the message-log archive volume, with no retention step (`runbook.md`'s un-join section) | The message log is subject to a statutory retention period; deleting the archive before that period elapses converts a retirement into an evidence gap (onboarding path §2 GX) |
| This pack is an instance of the onboarding path's §1 development track — synthetic data, Test CA, loopback bind — with the prohibition on real personal data enforced only by whoever writes the config (authorship) | A real development track enforces the same prohibition via the membership terms (onboarding path §1), not by who happens to be typing |
| `POST /requests/{id}/approve` requires a `decision_reference` string and records it verbatim (`apps/join-api/app.py`, Wave 2 Task 2) — the demo verifies only that it is non-empty, not that it refers to anything real | In production this is a minuted Steering Committee decision (Ref Model §5.3); the endpoint actuates that decision, it does not authorise one — an operator's bearer token was never the accountable party, and `decision_reference` is evidence of the real one, not a replacement for it |

## The task the hardening list forgets

Migrate each agency off its legacy point-to-point links and **retire them** —
parallel-run the once-only exchange beside the old link, confirm the two agree,
cut consumers over, decommission. Schedule per agency in the multi-agency phase;
a new bus does not retire old links by itself.

## What a joined member costs on a single demo host (member-parameterisation Task 9)

Measured live, adding a throwaway sixth member that owns its own Security
Server to an already-running 5-server `full`-profile federation:

- **RAM:** ~2.1 GiB per Security Server sidecar (measured 2.07–2.29 GiB across
  all six, `docker stats --no-stream`), regardless of whether it is one of the
  canonical five or a joined member — a joined member's own server is not a
  different resource class, it costs the same as any other. Central Server
  ~1.7–1.8 GiB, Test CA ~90 MiB, each mock provider ~33 MiB. A 6-server `full`
  deploy runs at roughly 15–17 GiB total on a single Docker host; this pack's
  own `docker-compose.yml` header already estimates "~16 GB RAM" for the
  canonical five alone, so budget proportionally more per additional
  own-server member.
- **Boot time:** two independent cold `teardown.sh --purge` → `hurl/run-linkup.sh`
  runs with a sixth, own-server member measured **~880s and ~898s** (≈15 min)
  end to end — driving all the admin APIs to a fully registered, serviced
  state, not just "containers started." The `retries: 120` healthcheck budget
  (10 min per server, `hurl/compose.hurl.yml`) covers this; the request-level
  Hurl retry (`--retry 12 --retry-interval 10000`, 2 min per request) covers
  the individual slow calls within it.
- **Host CPU contention is real and worth planning for, not just RAM.** Running
  six X-Road JVM Security Servers concurrently on a laptop-class host produced
  a live, reproducible failure independent of the two config bugs below: one
  server's admin API became unreachable from the host (TLS handshake accepted,
  never completed — not a refusal, a hang) after a period where its own log
  showed a Hikari connection-pool "thread starvation or clock leap detected"
  warning. `docker restart` on that container did **not** fix it by itself
  (the actual fix, below, was a config bug) — but the underlying symptom
  (a JVM's own admin-API thread pool starving under host contention) is a
  genuine single-host resource-pressure risk this pack's "DEMO ONLY, single
  Docker host" posture accepts and production must not.

**Two real bugs this live test found in the generated Compose overlay for a
joined member's own server** (both fixed in `hurl/generate.py`, byte-identical
proof re-run afterward for the canonical five on both profiles):

- The generated `hurl/compose.members.yml` service block was missing the
  `xroad-demo-local.ini` bind mount every canonical Security Server gets via
  `docker-compose.yml` — the mount that shortens the proxy's authorization-
  cache period from the documented 60s default to 5s (see
  `docs/xroad-770-notes.md` §6). A joined member's own server was silently
  running at the 60s default instead.
- The generated block had **no `healthcheck` at all**. Every canonical
  Security Server gets one from `hurl/compose.hurl.yml` (hand-written,
  scoped to the canonical five by name) so `run-linkup.sh`'s Compose `up`
  waits for the admin API to actually answer before the Hurl runner starts
  firing requests at it. A joined member's own server had nothing waiting
  for it, and nothing generated declares one for it either — fixed by adding
  the same healthcheck directly into `hurl/compose.members.yml`'s generation,
  so a joined member's own-server block is self-sufficient rather than
  depending on the hand-written canonical-five overlay.

**One real bug this live test found in port allocation, the same class as an
already-known one:** `FRESH_PORT_START = 7000` (`hurl/generate.py`) landed
squarely on the **other** port macOS's AirPlay Receiver (`ControlCenter`)
silently hangs on rather than refuses — the pack already knew about port
5000 (`FORBIDDEN_PORT_RANGE`, `docker-compose.yml`'s `ss-pnia` comment) but
not 7000. Confirmed live: `lsof -i :7000` on the host found `ControlCenter`,
not the container, holding the port; a login call TLS-handshake-hung for the
full `curl --max-time` budget every time, on every retry, indefinitely — no
amount of waiting or `docker restart` fixed it, because the container was
never actually reachable on that port to begin with. Fixed by extending
`FORBIDDEN_PORT_RANGE` to also exclude 7000; a fresh allocation now lands on
7100 instead and works immediately. **Anyone reusing this pattern on macOS
should check `lsof -i :<port>` for `ControlCenter` before trusting a fresh
port range, not just the one port a previous investigation happened to hit.**

**One real operational gotcha, not a code bug:** removing a member's config
(`scripts/member.sh remove`) regenerates `hurl/compose.members.yml` to
`services: {}`, so a subsequent `teardown.sh --purge` no longer references
that member's own-server container or volumes at all — they survive the purge
as orphans (found live: `ss-phib` and its three `kp2-phib-*` volumes had to be
removed by hand with `docker rm -f` / `docker volume rm`). Purge or retire the
member live (`docs/xroad-770-notes.md` §7) **before** removing its config,
not after.

**Recommendation:** default a joined member to `hosted_on` an existing
Security Server rather than its own, on a single-host demo deployment. It
costs zero extra containers, zero extra RAM, and sidesteps every finding
above (the port-allocation bug, the missing healthcheck/ini-mount bugs, and
the host-CPU-contention risk) entirely — reserve a joined member's own server
for the specific case the demonstration needs it (this plan's Task 9 Step 3:
proving the Compose overlay and port allocation actually work), not as the
default shape for "just add a member."

## Federation snapshots — measured, and their real shelf life (testing-strategy Task 3)

`scripts/federation.sh snapshot|restore` tars/untars the ~19 named `kp2-*`
volumes. Measured live, twice, on a freshly deployed federation:

- **Snapshot:** ~64s steady state (a one-off `alpine` image pull added ~10s
  the very first time only), 62–64 MiB.
- **Restore mechanics** (purge current volumes, untar, recreate containers):
  ~52s — but the plan's own "about a minute" estimate undersold what
  "restored" actually means in practice. The containers then need their own
  normal boot time to become healthy from the untarred data — measured
  **~315s (~5.25 min) total** from `restore` returning to `scripts/verify.sh
  --live` confirming the restored federation actually works end to end
  (right down to the same seeded record, `02831663233`, resolving correctly
  through the restored ACLs). Still **~3× faster than a full redeploy**
  (~918s, README.md) — a real, worthwhile speedup for the target use case
  (resetting to known-good state between config-only iterations), just not
  the "about a minute" the plan estimated before measuring.
- Found and fixed a real bug while measuring this: `scripts/verify.sh
  --live`'s own reachability probe was a single-shot `curl`, which failed
  when run immediately after `restore` brings containers up — the exact
  same class of race as the `console.sh up` bug Task 2 found, fixed the
  same way (a short, bounded retry that still refuses promptly when nothing
  is deployed at all).

**Shelf life is real, and shorter than "restore any time" would suggest —**
confirmed from a genuine (if partly accidental) live occurrence rather than
a deliberately engineered one: a federation whose underlying volumes had
existed for **~18 real hours** (spanning this session's own background
waits between tasks, not a snapshot specifically) failed a restart with the
exact same `Server.ClientProxy.SslAuthenticationFailed: "Security server has
no valid authentication certificate"` this pack already documents for
~10 hours of idle time (`docs/xroad-770-notes.md`, "Known traps"). The
software token itself reported `status: OK, logged_in: true` — this was not
the PIN-mismatch failure mode from the exposure-and-secrets plan, it was the
OCSP-freshness one, and it did **not** self-heal after several minutes of
retries (unlike the normal propagation-lag pattern this pack expects
elsewhere). **The shelf-life clock starts at snapshot time (whenever the
volumes' OCSP responses were last fetched), not at restore time** — a
snapshot taken today and restored next week inherits however stale it
already was the day it was taken, it does not reset the clock.

**What this session could and could not observe:** the immediate
(effectively t≈0) snapshot→restore cycle above is a real, live-confirmed
data point, and so is the ~18-hour failure. The plan's own ask — restore
after an hour, a day, and several days, to find exactly where the boundary
sits — needs elapsed wall-clock time this single working session cannot
manufacture on demand. Recorded honestly as **not measured**, not
extrapolated as fact: the true boundary is somewhere between "immediate"
(works) and "~18 hours" (fails), consistent with but not a fresh
confirmation of the pack's existing ~10-hour figure. A follow-up that
actually waits — take a snapshot, come back in an hour, a day, several
days — is the only way to narrow this further; guessing at it here would
be exactly the "asserting a failure mode nobody observed" this plan's own
Task 6 sequencing note warns against.

**Recommendation, superseded — see below:** this section originally
recommended using a snapshot soon after taking it, for fast iteration
within roughly the same working session. **`scripts/federation.sh` is
retired as of 2026-08-01** (two-decisions plan Task 2/T2): once `profile:
lite`'s own full cycle was actually timed (~370s, below) instead of
assumed, the snapshot's ~315s restore was only ~15% faster — not enough
to carry its shelf life, its unencrypted key material sitting in
`.snapshots/`, or its 123 lines of script. The measurements above are left
in place as the record of why; the mechanism itself is deleted.

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

## Lite profile's full cycle, measured (two-decisions plan Task 1/T2)

**Retired 2026-08-06 (Wave 3 Task 4, design decision 5):** `profile: lite`
no longer exists — one topology (full minus MoEYS) remains, and
`deployment.yaml` has no `profile:` key to set. The measurements below are
kept as historical record of what the two-tier topology cost while it
existed; nothing past this point is an instruction to set `profile: lite`.

The full-profile figures above measure everything except the alternative:
nobody had timed `--full` under `profile: lite` (three Security Servers —
PDGA, PLR, PNEA — instead of five, PNIA and MoEYS hosted as clients on
`ss-plr`). Two independent cold runs (`teardown.sh --purge` → `scripts/verify.sh
--full`), back to back:

| | Run 1 | Run 2 |
| --- | --- | --- |
| Containers healthy | 119s | 113s |
| Hurl admin-API run | 283s | 223s |
| **Total** | **402s** | **336s** |

**~370s (~6.2 min) average, against ~918s (~15.3 min) for full — lite's
deploy cycle is not merely a bit cheaper, it is roughly 2.5x faster,**
consistent with fewer members needing their own subsystem-registration
propagation wait (the dominant cost identified above): lite runs that wait
for three member sequences instead of four (PNIA and MoEYS's registrations
happen as fragments inside `ss-plr`'s own sequence, not as separate waits).

**What this does not prove:** PNIA and MoEYS run as hosted clients on
`ss-plr` under lite, not as their own servers, so a lite-only cycle never
exercises two of the five certificate sequences or the cross-server path
to those two providers as independent Security Servers. That is exactly
why "prove on full" stays part of the recommendation below, not a reason
to distrust the timing.

**One real bug found and fixed while measuring:** `scripts/capture-xroad-fixtures.sh`
(part of `--full`'s fixture-drift check) hardcoded `SS_UI[ss-pnia]` and
`SS_REST[ss-moeys]`, which do not exist in lite's topology (`unbound
variable`) — the same lite/full trap `scripts/acceptance.sh` already
resolves via `HOST_SS`. Fixed the same way.

**One transient, self-healing race observed, not chased further:** 2 of
the 3 fresh deploys run for this task (one lite, and separately the
full-profile restore in Task 1 Step 4 below) hit the same `MISMATCH ...
empty response` on the first post-deploy exchange call — common enough
that "rare" would undersell it. `fetch_retry`'s success check is the curl
exit code (an HTTP success status), which does not catch an X-Road REST
response that returns 200 with a body that is not yet valid JSON in the
seconds right after a fresh deploy. Re-running `scripts/acceptance.sh`
against the same, unchanged federation moments later passed cleanly both
times — consistent with the propagation-lag pattern this pack already
documents elsewhere, not a new failure mode, but frequent enough that
`fetch_retry`'s success criterion is worth a follow-up fix (validate the
body parses as JSON, not just that curl exited 0) rather than living with
a suite that fails outright on roughly two thirds of fresh deploys.
Recorded honestly; not fixed here, since diagnosing and fixing it is
outside what this measurement task asked.

### Re-measured a week later, both profiles (join-c plan Task 5)

Same methodology — two independent cold `scripts/verify.sh --full` runs per
profile, wall-clock around the whole command — re-run on 2026-08-03 because
this plan changed what `--full` does (2.7's new un-join checks) and because
several plans had grown `--fast` since. All four green.

| | `lite` run 1 | `lite` run 2 | `full` run 1 | `full` run 2 |
| --- | --- | --- | --- | --- |
| Containers healthy | 102s | 95s | 146s | 168s |
| Hurl admin-API run | 161s | 230s | 513s | 502s |
| Deploy subtotal | 263s | 325s | 659s | 670s |
| **`--full` wall clock** | **443s** | **488s** | **825s** | **918s** |

**Full is unchanged within noise (~872s average against the earlier ~918s).
Lite is ~100s slower than its earlier ~370s, and none of that is the
deploy.** Lite's own phases are the same as the earlier measurement
(119s/113s → 102s/95s containers, 283s/223s → 161s/230s Hurl). The
difference is above the deploy: `hurl/run-linkup.sh` runs
`scripts/verify.sh --fast` inside itself, and that tier has gone from ~8–16s
to **~49s** (286 tests) since the earlier figures were taken, plus 2.7's new
`2.7.unjoin(...)`/`2.7.unjoin.topology` checks in `acceptance.sh`.

That coupling is worth naming, because the tier table does not admit it:
**every test added to `--fast` is also added to the reproducibility proof.**
At ~49s it is 10% of a lite `--full` and 6% of a full one — not yet a
problem, but the trend is monotone and nothing measures it.

**Recommendation (feeds Task 2's snapshot decision):** lite's ~370s full
cycle is close enough to the snapshot mechanism's own ~315s restore time
(below) that the snapshot's already-thin 3x speedup over a full-profile
redeploy shrinks further once lite is the point of comparison instead of
full — see the snapshot section below for the actual decision.

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

Everything above about own-server joins was inferred from a cold deploy's own
certificate sequences or from `docs/xroad-770-notes.md` §7's hand-driven
retirement. This section is the first time the pack has actually driven one
through `apps/join-api` — join, and un-join — and it corrects three things
and confirms two.

**What was run**, all on `profile: lite` from a cold `scripts/verify.sh
--full`, 2026-08-03:

| | Cycle | Join | Un-join |
| --- | --- | --- | --- |
| PVTB (own server, `ss-pvtb`) | driven by `curl` | approve → `BLOCKED` 31s; `scripts/join-agent.sh pvtb` 100s to healthy; resume → `ACTIVE` 163s | `DELETE /members/pvtb` → `RETIRED` in **2.32s**, 5 reversals, nothing retried |
| PVTB (own server) again | driven through the console's join-tab endpoints (see caveat) | approve → `BLOCKED` ~25s; agent 76s; resume → `ACTIVE` 141s | `RETIRED` in **1.33s**, 5 reversals, nothing retried |
| PHTB (hosted on `ss-plr`) | driven by `curl` | approve → `ACTIVE, verified: true` in **64s** | `RETIRED` in **1.44s**, 6 reversals, nothing retried |

Both own-server cycles ended with `hurl/topology.json` **byte-identical** to
`tests/golden/lite/topology.json`, every regenerated scenario file identical,
`git status` clean, and `scripts/acceptance.sh` green — the join-c plan's
Global Constraint, now proven on the own-server half rather than only the
hosted one.

**Caveat on the second cycle, so nobody reads more into it than it proves.**
It was driven through the console's own `/api/join/*` proxy endpoints — the
exact server-side path the join tab's Approve and Resume buttons call, with
the operator token never leaving the container — and **not through a
browser**: no Chrome instance was available in the environment that ran
this. What was verified is therefore the data the tab renders from (the
`BLOCKED` record carrying `blocked.server` and the `scripts/join-agent.sh
pvtb` command, the 16-step sequence with its run of `actor: member` steps,
the `uncommitted: true` flag appearing at `ACTIVE`, the `RETIRING`/`RETIRED`
cards with their reversal list and Docker instruction) plus the rendering
code that consumes it (`apps/console/static/app.js`'s `renderJoinRequest` /
`renderJoinSteps`, and its 3s `setInterval` poll, which is what picks the job
up after the agent runs with no manual refresh). **Rendered pixels were not
checked.** Somebody should open the tab once and look at it before
demonstrating this to an audience; nothing here substitutes for that.

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
| `GET /security-servers` | includes `PROGRESSA:GOV:PVTB:SS-PVTB` | **gone** — only the three canonical servers |
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

### An own-server join could not reach `verified: true` — fixed, and now re-verified live (Wave 3 Task 6, 2026-08-07)

**Update, Wave 3 Task 6:** re-verified live. A real own-server join (PTSB)
reached `ACTIVE, verified: true` 131s after `resume`, with 7 of 12 shared
retries left when `join.r1_verify` started and nowhere near exhausting its
own 54. See "Wave 3 Task 6: proved live from cold" below for the full
account. The record below is left as originally written — it is the
diagnosis that led to the fix, not superseded by the confirmation.

**Reproduced on both own-server cycles measured 2026-08-03, and not a
federation fault.** The join reached `ACTIVE` with `verified: false` and
`Server.ClientProxy.UnknownMember`, and stayed there — while the *hosted*
join on the same stack, minutes apart, reached `ACTIVE, verified: true` with
6 of its 12 retries unspent.

The cause was `apps/join-api/job.py`'s one-budget-per-run rule (design spec
S5.5): `RETRY_BUDGET = 12` at `RETRY_INTERVAL_SECONDS = 10.0` is **120s for
the whole run**, and in an own-server join `ss.client_register`'s own
global-configuration propagation wait ate 95–107s of it before
`join.r1_verify` was reached at all. The reachability call then got the ~20s
that were left. Measured directly afterwards: the same call became
fault-free **46s** after `ACTIVE` in one cycle and only after **~8 minutes**
in the other — either way, more than the budget had left.

**There was no way back to `verified: true` from there** either:

- `POST /requests/{id}/resume` refuses with `409` — `apps/join-api/app.py`
  only resumes `FAILED` or `BLOCKED`, and this record is `ACTIVE`;
- even if it did not, `run()` would skip the step: `join.r1_verify` is
  already `last_completed_step`, and it neither provides a session token
  (`JobStep.must_rerun`) nor has a probe, so a resume walks past it.

Nothing was actually wrong with the member — `scripts/acceptance.sh` ran
green against it, `2.7.r1(PVTB.awards-api)` and `2.7.deny(PVTB.awards-api)`
included, which is the same fact `verified` was meant to record. But a
demonstration that ends on a red "verified: false — the reachability check
has not passed yet" badge in the console's join tab reads as a failed join,
and the operator had no button that fixed it.

**Fixed since:** `apps/join-api/job.py:92` now gives `join.r1_verify` its
own budget, `R1_RETRY_BUDGET = 54`, separate from the run's shared
`RETRY_BUDGET` — the first of the two candidate shapes below, not the
second. The step no longer draws on whatever the run's other steps left
behind; it gets its own 54 retries at 10s each regardless of how much
`ss.client_register`'s propagation wait consumed. **This has not been
re-verified live for the own-server case** — no `--full` acceptance run has
exercised an own-server join since the fix landed, so `2.7.md`'s clause
stays marked as not (yet) met rather than closed. What would confirm it: a
future full acceptance run reaching `ACTIVE, verified: true` for an
own-server join, which Wave 3's proof work is expected to produce
incidentally.

The shape chosen was giving `join.r1_verify` a budget of its own rather than
the run's leftovers. The other candidate considered — making re-verifying an
`ACTIVE` record its own idempotent endpoint — was not pursued: the budget
fix addresses the timing directly and needs no new endpoint, so the
`resume`-refuses-`ACTIVE` gap above is no longer load-bearing for this
defect.

### Sizing: spec §12's numbers hold; its closing sentence does not (Step 4)

Design spec §12 was checked by measuring rather than trusting it. **Its
table is right — measurably so — and only its prose conclusion overstates
what the table says.** Read the row carefully before comparing anything to
it: §12's `~13 GB` line is **`lite + backend + own Security Server`**, and
that budget *includes* a third-party backend (§12 budgets a Joget DX
instance plus its database at 1.5–2.5 GB). It is not a prediction for
`lite + own Security Server` alone, which is the topology measured here.

Measured with exactly that topology up — lite plus one own-server member,
**no** third-party backend (`docker stats --no-stream`, colima VM with
15.62 GiB):

| | |
| --- | --- |
| `ss-pvtb` (the joined member's own server) | 2.22 GiB |
| `ss-pdga` / `ss-plr` / `ss-pnea` | 2.27 / 2.26 / 2.24 GiB |
| `cs` | 1.88 GiB |
| `ca` | 87 MiB |
| four mock providers | ~33 MiB each |
| `join-api` | 41 MiB |
| **Total** | **11.13 GiB** |

**11.13 GiB, and §12's own components predict 11.1 GB for this exact
topology** — lite at ~8.9 GB plus one Security Server at ~2.0–2.3 GB. The
spec's per-component figures are confirmed to within noise, including the
~2.0–2.3 GB per Security Server it took from the member-parameterisation
measurements above: a joined member's own server really is the same resource
class as a canonical one (2.222 GiB against 2.239–2.269 GiB for the three
canonical servers in the same reading).

Add §12's own backend budget (1.5–2.5 GB for a Joget DX instance plus its
database) and the total lands at **~12.6–13.6 GiB — which is §12's `~13 GB`
row, arrived at from a live measurement instead of an estimate.** On this
15.62 GiB host that leaves roughly 2–3.5 GiB spare, which is exactly what
that row already says: **"fits, tight"**.

**What is wrong is §12's closing sentence, not its arithmetic.** The prose
after the table says own-server joins and a real backend "cannot both be
shown on a 16 GB host" — which contradicts the table's own `fits, tight`
verdict two lines above it. The measurement settles it in the table's
favour: `lite + backend + own Security Server` fits, tightly, and the
sentence should read as a *recommendation* against it (the same
`hosted_on`-by-default recommendation §6.2 and this document already make on
three other grounds) rather than as a statement of impossibility. The rows
that genuinely do not fit are unchanged and unchallenged: `full + backend`
(~15 GB) and `full + backend + own Security Server` (~17 GB), plus any
second own-server member (another ~2.2 GiB) on top of the lite combination.

### Two real bugs this proof found

- **A joined member's own Security Server ignored `network.bind`.**
  `hurl/generate.py` emitted `hurl/compose.members.yml` with a bare
  `ports: ["7100:4000", "7180:8080"]`, so `ss-pvtb` published its admin UI
  *and* its unauthenticated X-Road proxy port on `0.0.0.0` — walking around
  both `deployment.yaml`'s `network.bind` and `scripts/lib-stack.sh`'s
  two-statement refusal. `scripts/check-exposure.sh` caught it on the next
  acceptance run, which also means **acceptance could not be green while any
  own-server member was joined**. Fixed by prefixing both mappings with
  `${XROAD_BIND:-127.0.0.1}`, the same way every line in the hand-written
  `docker-compose.yml` already did.
- **`scripts/acceptance.sh`'s `fetch_retry` accepted a non-JSON body.** The
  "Lite profile's full cycle, measured" section below already recorded this
  on 2 of 3 fresh deploys and left it as a follow-up; it failed this task's
  first cold `--full` at 2.6.2 with a `JSONDecodeError` traceback. Fixed at
  the root — the retry loop's success test is now "the body parses as JSON",
  not "curl exited 0".

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

## The Task 6 gate: does lite plus an own-server join cover `profile: full`? (join-c plan Task 5)

**Historical: `profile: lite`/`profile: full` were retired 2026-08-06 (Wave
3 Task 4, design decision 5), by a different route than the one join-c
Task 6 (referenced below) considered — this section records why join-c's
own Task 6 was gated off at the time, not a live choice today.**

The join-c plan's Task 6 acts on this sentence, so it is stated flatly first:

> **No. An own-server join on top of `profile: lite` closes lite's
> best-known gap and makes lite a better day-to-day default than it was, but
> it does not replace the one `--full` under `profile: full` before a plan is
> closed out, and `deployment.yaml`'s shipped default should not change on
> the strength of this task alone.**

**What lite plus an own-server join now genuinely covers, and lite alone
never did.** `README.md` names lite's one gap as "PNIA's and MoEYS's own
certificate sequences". An own-server join runs exactly that sequence —
`ss.bringup_init`, `ss.auth_key_csr`, `ss.sign_key_csr`,
`ss.bringup_register` and its Central-Server approval, `ss.activate`,
`ss.tsa_post`, `ss.client_add` — from the same `hurl/steps.py` registry
entries and the same templates cold deploy renders, against a real Security
Server, and proves the result with a real cross-server `r1` call to a
provider **on its own server** (`ss-pnea` → `ss-pvtb`, not a hosted client).
It also proves the reverse: that server leaving the Central Server cleanly.
That is more than lite had, and it is not a simulation of it.

**What it does not cover, in the order that matters.**

1. **The cold-deploy assembly, which is where those sequences actually
   live.** Under lite, `hurl/scenarios/20-ss-pnia.hurl` is a **6-line stub**
   whose only content is a comment saying the bring-up below is not run;
   the full-profile file is **273 lines**. `hurl/run-linkup.sh` concatenates
   every scenario into one `hurl/.build/setup.hurl` and runs it in a single
   Hurl process with a single variable scope. `apps/join-api/job.py` runs one
   Hurl process per step with a shared cookie jar and a context it
   re-establishes deliberately. The *templates* are shared; the *assembly* is
   not, and only a full-profile deploy executes the assembly. `--fast`'s
   golden corpus checks those 273 lines' bytes; nothing but `profile: full`
   runs them.
2. **The five-Security-Server resource envelope.** Measured for this task:
   lite plus one own-server member is **11.1 GiB**. Full alone is ~15–17 GiB
   (the member-parameterisation section above). Host CPU contention across
   five concurrent X-Road JVMs is a documented, reproducible failure class in
   this pack's own history, and no join on a lite base reaches it.
3. **PNIA and MoEYS as independent providers.** Under lite they are hosted
   clients on `ss-plr` in every configuration this pack ships. An own-server
   join proves the *shape* with a throwaway member's identity; it does not
   run those two members' own configs as servers.
4. **It is not automated, and automating it would change what `--full`
   means.** An own-server join stops at `BLOCKED` until someone runs
   `scripts/join-agent.sh` out of band. Nothing in `scripts/verify.sh --full`
   does that, and wiring it in would make the reproducibility proof depend on
   the join API being up, an applicant payload being committed, and two
   Docker cleanup commands running afterwards — three new ways for the proof
   itself to fail for reasons unrelated to reproducibility.
5. **It cannot currently reach `ACTIVE, verified: true`** (see the section
   above), so a gate built on it would assert something strictly weaker than
   the hosted join already asserts.

Points 1 and 2 are the load-bearing ones: both are properties of the
cold-deploy path and of the host, and no join can reach either. Points 3–5
are fixable; points 1 and 2 are not, by this mechanism.

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
**102s** (within the documented 76–100s range); `POST .../resume` ran the
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
| Own-server bring-up (`join-agent.sh`) | 76–100s | **102s** — within range |
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
