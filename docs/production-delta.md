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
| Auto-approve management requests (`2.1.yaml`, deploy step 4) | Manual approval per the governance RACI |
| Single Docker host, containers (`docker-compose.yml`) | Separate sized hosts per component, HA/redundancy |
| Fixed CS admin creds `xrd/secret` (test image) | Hardened access, individual accounts, audit |
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
