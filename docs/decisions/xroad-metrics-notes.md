# `xroad-metrics` spike (Task 5, E.3) — no-go, with evidence

Half-day-equivalent spike, run against **this stack's own running
containers**, not researched from docs alone (though the docs are cited
too, for the parts a live probe can't observe). The question:
`docs/production-delta.md`'s G4.8 / the onboarding path's G4 third exit test
("is its monitoring data arriving centrally?") is unmet on purpose today —
does wiring up NIIS's `xroad-metrics` collector stack close it, at a cost
this demo pack can carry?

**Verdict: no-go.** The data source is not the blocker — it works, live,
today, with zero extra provisioning (§1). The official collector *stack* is
the blocker: no container images (§2), a second database engine the
existing datastore plan never priced in (§3), and a footprint NIIS's own
docs size at multiple hosts even for a "simplified" test setup (§4). This
returns to the backlog with the evidence below, per the plan's own
sign-off that this is an acceptable spike outcome.

## 1. The data source itself: live-tested, works today, no new provisioning

`getSecurityServerOperationalData` is a **standard X-Road service, called
over the same client-proxy path any regular service call already uses** —
not a separate monitoring port, not a separate credential. Confirmed live,
twice, from inside the `join-api` container (already on the `linkup`
network):

- **Wrong routing fails loudly and specifically.** Addressing the query
  directly at a foreign security server's client port (`ss-pdga:8080`)
  using a client that server doesn't host (`PROGRESSA/GOV/PNEA/EXAMS`,
  which lives on `ss-pnea`) faults immediately:
  `Server.ClientProxy.UnknownMember: Client 'SUBSYSTEM:PROGRESSA/GOV/PNEA/EXAMS' not found`.
  X-Road expects a client to always call out through its *own* hosting
  server, which then routes federation-wide — the same pattern
  `apps/join-api/job.py`'s own `join.r1_verify` step and
  `apps/console/xroad.py`'s `exchange()` already use for ordinary service
  calls; nothing special for op-monitoring.
- **Routed correctly, it just works.** `PROGRESSA/GOV/PNEA/EXAMS` addressed
  through its own server (`ss-pnea:8080`), querying `PDGA`'s data, returns
  `HTTP 200` with a real `getSecurityServerOperationalDataResponse`
  (`recordsCount: 0` — PNEA has made no calls *to* PDGA in this run) and a
  gzip-attached JSON body per the MTOM/multipart shape the protocol
  documents (`cid:operational-monitoring-data.json.gz`).
- **Querying its own server, it returns real records.** The same client
  addressed at its *own* member code (self-query) returned
  `recordsCount: 15` — real, already-buffered operational data from this
  session's own traffic (identity-api/enrolment-api calls PNEA made during
  earlier acceptance/live-verification runs). Decoded, each record is
  exactly the shape `X-Road-Metrics`' collector module documents it
  ingests: `clientMemberCode`/`serviceMemberCode`/`serviceCode`,
  `requestInTs`/`requestOutTs`/`responseInTs`/`responseOutTs`,
  `succeeded`, `statusCode`, `xRoadVersion`, `messageId`, `restMethod`,
  `clientSecurityServerAddress`/`serviceSecurityServerAddress`. This is a
  direct read of the exact wire format the collector is built to consume —
  the add-ons `tests/test_addons.py` already proves *running*
  (`supervisorctl status`: `xroad-opmonitor RUNNING` on every `ss-*`) are
  demonstrably also **producing usable data**, not just present.
- **No new registration, no new certificate, no new ACL.** Every identity
  used above is a canonical member this pack already has (`PROGRESSA/GOV/
  PNEA:EXAMS`), calling through infrastructure that already exists. This is
  the one place the plan's framing undersold the demo: the "biggest
  unknown" is not whether the add-ons' *data* is reachable — it
  demonstrably is — it's what wraps around it (§2-§4).

Reproduction: a hand-built SOAP envelope (`getSecurityServerOperationalData`,
protocol 4.0, `om:searchCriteria` with a wide `recordsFrom`/`recordsTo`
window) `POST`ed with `Content-Type: text/xml` to the calling member's own
`ss-*:8080` — no Hurl template exists for this in `hurl/steps.py` (op-
monitoring was never a join-time or acceptance-time call before this
spike), and none is added here — a one-off probe, not a wired mechanism.

## 2. No official container image — package-only, one host per module by default

`nordic-institute/X-Road-Metrics` ships **no Docker image for any of its
seven modules** (collector, corrector, reports, opendata, anonymizer,
networking/visualizer, the experimental analysis module). Installation is
Ubuntu 22.04/24.04 `.deb` packages, systemd services and cron jobs — the
opposite of this pack's whole posture (`docker-compose.yml`, digest-pinned
images, `profiles:` flags). Every other "NIIS OSS" component this pack runs
(`niis/xroad-central-server`, `niis/xroad-security-server-sidecar`) *is*
published as a maintained image; `xroad-metrics` is not, and building four-
plus unofficial images from `.deb` packages ourselves is a materially
different (and materially larger) commitment than "compose services behind
a `profiles: ["metrics"]` flag" implied when this task was scoped.

## 3. Two databases, not one — MongoDB is the primary store, Postgres is a downstream projection

The datastore plan this task was told to reuse (`docs/plans/join-datastore-
postgres-digitalocean-plan.md`, `kp2_metrics` — "future: xroad-metrics —
named, not built") priced in **one** additional Postgres database. The real
architecture needs **two engines**:

- **MongoDB** is where the collector actually writes and the corrector
  actually reads/cleans — raw, potentially sensitive operational entries
  (IP addresses among them), one year on disk, one week in RAM, run as a
  replica set for availability. This is the module this pack's proposed
  `metrics-db` Postgres container does not cover at all.
- **PostgreSQL** holds only the *anonymized* output the `anonymizer`
  module produces for the `opendata` module to publish — a downstream
  projection of the Mongo data, not a replacement for it. `kp2_metrics`
  names the right *kind* of database for this one tier; it was never going
  to be the collector's primary store, because the collector doesn't have
  one that's Postgres.

NIIS's own security guidance treats this as a hard boundary, not an
implementation detail: it recommends separate VLANs for the
Mongo-holding "private" modules and the Postgres-holding "public" ones,
because the former can carry personal data (IP addresses) the latter must
never see. Standing up "the database" for this spike would mean standing
up both engines and the isolation between them, not extending the existing
`join-api`/`kp2_join` Postgres pattern to a second database name.

## 4. Footprint, even at NIIS's own "simplified" testing scale

NIIS's docs size *production* at nine dedicated hosts (one per module plus
the central db) and name a *testing-simplified* floor of **two**:

- `xroad-metrics-private` — MongoDB + collector + corrector + reports +
  anonymizer
- `xroad-metrics-opendata` — PostgreSQL + opendata + networking/visualizer

Two hosts (or, translated to this pack's shape, at minimum two custom-built
multi-process containers plus MongoDB plus a second Postgres instance) is a
different order of addition than "profiles: metrics compose services" reads
as in isolation — closer to a second demo stack bolted onto this one than a
sidecar.

Collector configuration compounds this: `settings.yaml` takes **one**
`security-server`/`security-server-client` pair per file (multi-instance
support is separate settings-file *profiles*, not a list of targets within
one file). Comprehensive coverage of this pack's four `ss-*` servers —
the actual point of a federation-wide collector — would mean either four
separate collector configurations/cron entries (each authenticating as a
different existing canonical member: PDGA/PNEA/PLR/PNIA, one per server it
owns) or accepting partial, single-server-scoped coverage. Both are
solvable — §1 already shows the underlying calls work with existing
identities — but "solvable with four times the collector configuration"
is scope this task's `~1.5 day` E.3 estimate did not carry.

## 5. What would have to be true to reopen this

- An officially maintained (or this-pack-maintained-and-committed-to)
  container image for at least collector + corrector + opendata +
  anonymizer, so this stays `docker compose`-shaped rather than becoming a
  second, differently-operated demo.
- A real answer for MongoDB in `docker-local` (a plain container is cheap;
  the point is that the datastore plan and this pack's "one Postgres
  pattern" story both need to acknowledge a second engine exists) and in
  the droplet target (does DO's managed Mongo, or a self-run replica set,
  fit the same trust-boundary posture `docs/deployment-targets.md` already
  asks of Postgres?).
- A decision on collector scope: one server (cheap, partial) vs. all four
  (real, four times the config) — and whether that decision belongs in
  `deployment.yaml` alongside `datastore.kind`.

None of this is disqualifying forever — §1's finding is that the hard part
(does the demo federation actually produce and serve the data the
collector wants?) is already proven yes. What's missing is packaging this
pack does not control (§2) and a footprint this task's own estimate did
not carry (§3-§4). `docs/production-delta.md`'s G4 text and
`docs/path-conformance.yaml`'s `G4.8` are updated to say so precisely,
rather than leaving the older, vaguer "adding X-Road Metrics later closes
it without a retrofit" note standing unexamined.
