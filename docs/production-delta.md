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
