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
| Console holds admin credentials server-side, localhost-bound as the only access control | Credentials never colocated with a public-facing demo tool; network-level isolation |
| Console's ACL write path exists purely to be theatrical for an audience | No tool mutates production ACLs for demonstration purposes, ever |
| Proxy's `server-conf-cache-period` tuned to 5s (`xroad-demo-local.ini`, default is 60s) so an ACL change is filmable | Leave at the documented default (or size deliberately) — a short cache period trades proxy CPU for faster-to-reflect ACL changes, a trade a real federation's traffic volume should make on purpose, not by copying a demo value |

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

**One transient, self-healing race observed, not chased further:** one of
the two runs' acceptance suite hit `MISMATCH ... empty response` on the
first post-deploy exchange call — `fetch_retry`'s success check is the
curl exit code (an HTTP success status), which does not catch an X-Road
REST response that returns 200 with a body that is not yet valid JSON in
the seconds right after a fresh deploy. Re-running `scripts/acceptance.sh`
against the same, unchanged federation moments later passed cleanly end to
end — consistent with the propagation-lag pattern this pack already
documents elsewhere, not a new failure mode. Recorded honestly; not
investigated further, since diagnosing `fetch_retry`'s success criterion
is outside what this measurement task asked.

**Recommendation (feeds Task 2's snapshot decision):** lite's ~370s full
cycle is close enough to the snapshot mechanism's own ~315s restore time
(below) that the snapshot's already-thin 3x speedup over a full-profile
redeploy shrinks further once lite is the point of comparison instead of
full — see the snapshot section below for the actual decision.
