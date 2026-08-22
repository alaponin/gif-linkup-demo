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
the blocker: no published, pinnable container image, and an upstream
compose setup its own README scopes to "limited local testing" (§2); a
second database engine the existing datastore plan never priced in (§3);
and a footprint NIIS's own docs size at eight hosts in production and two
even for a "simplified" test setup (§4). This returns to the backlog with
the evidence below, per the plan's own sign-off that this is an acceptable
spike outcome.

**On sources.** §1 is a live probe against this pack's own containers and
is reproducible from the note itself. §2–§4 are claims about upstream
that a live probe cannot observe, so each one carries the document it
comes from and the date it was read. Every upstream URL below was fetched
**2026-08-22** against `nordic-institute/X-Road-Metrics` `master`.
Re-reading §2–§4 against a later `master` is the right way to decide
whether this no-go still holds — that is what the dates are for. Where the
first pass of this note asserted something the sources do not support, the
correction is stated in place rather than quietly dropped (see §2).

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

## 2. No *published* container image — the supported install is `apt`, and upstream's own compose is scoped to "limited local testing"

**Correction to this note's first pass, stated in place rather than
silently dropped.** That version claimed `X-Road-Metrics` "ships **no Docker
image for any of its seven modules**" and that adopting it would mean
"building four-plus unofficial images from `.deb` packages ourselves". The
second half is wrong: the repository has a top-level `Docker/` directory
with a Dockerfile per module, a `prepare-containers.sh` that builds all
seven (collector, corrector, anonymizer, opendata, opendata-collector,
reports, networking) and a `docker-compose.yaml` wiring them to MongoDB and
PostgreSQL. We would not be inventing the packaging. The accurate version of
the blocker is narrower, and it is two things:

- **Nothing is published.** There is no `niis/xroad-metrics-*` on Docker
  Hub or ghcr — unlike `niis/xroad-central-server` and
  `niis/xroad-security-server-sidecar`, which this pack already pins **by
  digest** (`deployment.yaml`'s `cs_digest`/`ss_digest`, C13). Images have
  to be built locally from that directory, which means there is no digest
  to pin and no upstream rebuild to inherit — a direct conflict with the
  image-provenance dimension `docs/deployment-targets.md` already commits
  to. Searched 2026-08-22; no such image found on either registry.
- **Upstream scopes its own compose out of production.** `Docker/README.md`
  states plainly: *"This Docker setup is only intended for limited local
  testing."* Adopting it for a demo pack that a reader may point at a
  droplet means either ignoring that sentence or owning the gap ourselves.

The *supported* installation path is unchanged and is what the module docs
document: Ubuntu Server 22.04 (Jammy) / 24.04 (Noble), `sudo apt install
xroad-metrics-collector`, driven by a cron file at
`/etc/cron.d/xroad-metrics-collector-cron` (default: every three hours).
That is still the opposite of this pack's posture (`docker-compose.yml`,
digest-pinned images, `profiles:` flags) — but "unsupported-for-production
containers exist" is a weaker blocker than "no containers exist", and the
no-go rests on §3 and §4 more than it rests on this section.

Sources, all fetched **2026-08-22**:
- <https://github.com/nordic-institute/X-Road-Metrics/tree/master/Docker> —
  per-module Dockerfiles, `prepare-containers.sh`, `docker-compose.yaml`,
  `mongodb-init/`, `postgresql-init/`.
- <https://github.com/nordic-institute/X-Road-Metrics/blob/master/Docker/README.md>
  — *"This directory contains Dockerfiles and scripts for building the
  containers for each module"*; *"This Docker setup is only intended for
  limited local testing."*
- <https://github.com/nordic-institute/X-Road-Metrics/blob/master/docs/collector_module.md>
  — Ubuntu 22.04/24.04, `apt install`, the cron file and its three-hour
  default.
- <https://hub.docker.com/u/niis> — the published NIIS images; no
  `xroad-metrics` entry as of the fetch date.

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

Sources, fetched **2026-08-22**, both
<https://github.com/nordic-institute/X-Road-Metrics/blob/master/docs/system_architecture.md>
and
<https://github.com/nordic-institute/X-Road-Metrics/blob/master/docs/database_module.md>:
- *"MongoDb is used to store 'non-anonymized' operational monitoring data
  that should be accessible only by the X-Road administrators. Anonymized
  operational monitoring data that can be published for wider audience is
  stored in the PostgreSQL."*
- *"MongoDB shall retain 1 year data in disk memory"*; *"MongoDB shall
  retain 1 week data in RAM memory for efficient query"*; *"MongoDB shall
  run in a replication set for availability"*; *"PostgreSQL shall retain 1
  year of public available data."*
- *"the MongoDb contains data that might contain sensitive information like
  IP-addresses or personal data. That should be accessible only by the
  X-Road administrators"*, mitigated by *"separate virtual LANs for the
  public and private modules and setting a firewalled routing between the
  networks."*
- The four modules that touch MongoDB directly, per `database_module.md`:
  collector (read/write), corrector (read/write), reports (read),
  anonymizer (read).

## 4. Footprint, even at NIIS's own "simplified" testing scale

NIIS's docs size *production* at **eight** dedicated hosts — one per module
plus the central database: `xroad-metrics-centraldb`, `-collector`,
`-corrector`, `-reports`, `-analyzer`, `-anonymizer`, `-opendata`,
`-networking` — and name a *testing-simplified* floor of **two**:

- `xroad-metrics-private` — MongoDB and the private modules
- `xroad-metrics-opendata` — PostgreSQL and the public modules

(An earlier draft of this note said "nine". It is eight; corrected against
the source below. The same draft attributed a precise module-per-host split
to that document — it does not give one at the simplified scale, only the
private/public division above, so the split is not asserted here. The
argument does not turn on either number.)

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

Sources, fetched **2026-08-22**:
- <https://github.com/nordic-institute/X-Road-Metrics/blob/master/docs/system_architecture.md>
  — the eight production hosts by name, and the two-host
  `xroad-metrics-private` / `xroad-metrics-opendata` testing setup with
  firewalled routing between them.
- <https://github.com/nordic-institute/X-Road-Metrics/blob/master/docs/collector_module.md>
  — `settings.yaml`'s single `security-server:` mapping (a host, not a
  list), and multi-instance handling by separate settings *profiles*
  (`settings_DEV.yaml`, `settings_TEST.yaml`, …, selected with
  `xroad-metrics-collector --profile TEST collect`).

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
collector wants?) is already proven yes. What's missing is a publishable,
pinnable image upstream does not offer and whose own compose setup it
scopes out of production (§2), and a footprint this task's own estimate did
not carry (§3-§4). `docs/production-delta.md`'s G4 text and
`docs/path-conformance.yaml`'s `G4.8` are updated to say so precisely,
rather than leaving the older, vaguer "adding X-Road Metrics later closes
it without a retrofit" note standing unexamined.
