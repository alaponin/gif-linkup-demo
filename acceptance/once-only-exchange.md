# Acceptance check — module 2.6 (the once-only exchange) — THE PACK'S HEADLINE CHECK

**Proves:** a real cross-server once-only exchange runs on the federation: PNEA
pre-fills identity from PNIA and enrolment from PLR; the learner is asked once;
the unauthorised caller is denied. All four EIF layers in one call (Module 5.6).
**Run by:** `scripts/acceptance.sh` (X-Road test calls per the r1 REST protocol)

- **Given** the full pack is deployed (`deploy.sh`) and seeded (`seed.sh`); a
  seeded learner NIN is picked from `apps/data/persons.csv` that also exists in
  `enrolments.csv`;
- **When** the check makes the two consumer-side calls through ss-pnea:

  ```
  curl -H 'X-Road-Client: PROGRESSA/GOV/PNEA/EXAMS' \
    http://localhost:2080/r1/PROGRESSA/GOV/PNIA/IDENTITY/identity-api/persons/{nin}
  curl -H 'X-Road-Client: PROGRESSA/GOV/PNEA/EXAMS' \
    http://localhost:2080/r1/PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api/enrolments/{nin}
  ```

- **Then** — four assertions, each mapped to its layer:
  1. **Happy path** (technical) — both calls return HTTP 200 **cross-server**
     (ss-pnea → ss-pnia / ss-plr), not via any direct app connection;
  2. **Right learner** (semantic) — every returned field equals the seeded record
     for that NIN (name, date of birth, region; school, level, year, status) —
     not merely "data returned";
  3. **Asked once** (organisational + legal) — the assembled credential
     application contains the citizen-provided field (`nin`) plus only pre-filled
     bus fields; the two sets are disjoint and cover the form; no field either
     registry holds is re-entered;
  4. **Negative** (organisational) — the identity-api call repeated with
     `X-Road-Client: PROGRESSA/GOV/PLR/ENROLMENT`, routed through **PLR's own
     Security Server** (localhost:3080) — PLR is on the bus and is a provider
     in its own right, but holds no grant on PNIA's `identity-api` — is
     **denied with an X-Road access-denied error**: the denial must come from
     the provider-side ACL, not from a transport failure or from ss-pnea
     rejecting a client it does not host. On the bus does not mean granted
     this service.

Additional negative: a NIN seeded in PNIA but deliberately absent from PLR
returns identity + a clean 404 from enrolment — proving errors are observable,
not silent.

5. **Field conformance** (G5.9) — both responses carry exactly the fields
   their own OpenAPI contract declares: nothing the contract withholds is
   returned, and nothing the contract requires is missing. Distinct from
   assertion 2 ("right learner"), which compares returned values to the
   seeded row and cannot see a response that adds a field the CSV carries and
   the contract withholds — the serious case, since that field is exactly
   what purpose limitation exists to keep off the wire.

**Artefact:** on success the suite writes `out/application-{nin}.json` — the
assembled credential application with per-field provenance (the one citizen
field vs every bus-pre-filled field and its source). This is the tangible
asked-once object for the video demonstration, and the seam a KP4 Joget form
later replaces. Optional further evidence at P0: the exchange visible in the
provider Security Server's message log [confirm P0: message-log query].

When this check passes, KP2 stops being a framework explained and becomes a
framework that runs.

Status: VERIFIED on the live stack. Every assertion above — the four
layer-mapped ones, the additional 404 negative and field conformance — runs as
`scripts/acceptance.sh`'s `2.6.1`-`2.6.6`, green in `scripts/verify.sh --full`
from cold against the collapsed single topology. `docs/production-delta.md`
records the run, including the two that are only convincing live: `2.6.4`'s
denial arriving as the X-Road fault `Server.ServerProxy.AccessDenied` rather
than a transport error, and `2.6.6` observed failing on a deliberately
contract-violating response before passing clean again.
