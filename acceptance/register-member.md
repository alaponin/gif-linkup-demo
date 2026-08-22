# Acceptance check — register-member (PNEA, PLR and PNIA registered on the bus)

**Proves:** the config → deploy pipeline correctly registers this pack's
three canonical members — the consumer-only case (PNEA) and the two
provider cases (PLR, PNIA) — with the per-member specifics
`acceptance/member.md`'s generic check has no way to assert: a consumer's
connection type and live token/certificate state, a provider's own-app
health and seeded data, and — for identity data specifically — that only
the decree's purpose-limited field list is ever returned.
**Run by:** `scripts/acceptance.sh` (SS admin REST API)

This document deliberately does **not** restate `acceptance/member.md`.
`member.md` already asserts, generically for any member `hurl/topology.json`
describes, that the subsystem is `REGISTERED` on the server `hosted_on`
names and that a service's granted-subject list equals its config's
`access:` list exactly — PNEA's, PLR's and PNIA's registration and
ACL-exactness already have a real, running check without this document
repeating it. What follows is only what `member.md` cannot see: it has no
notion of a consumer's connection type or certificate liveness, and no
notion of what a provider's own app actually returns.

- **Given** module 2.1 and this module are deployed and `scripts/seed.sh`
  has run;
- **When** the check queries each member's own Security Server admin API,
  and — for PLR and PNIA — probes the member's own app directly;
- **Then**:
  1. **PNEA (consumer)** — client `PROGRESSA/GOV/PNEA/EXAMS` exists with
     status `REGISTERED`; its connection type is `HTTPS_NO_AUTH` (per
     `configs/member-pnea/pnea.yaml`, `docs/production-delta.md` row 19 —
     the consumer hop is TLS on `:8443` and X-Road refuses the plaintext
     `:8080` for this client; still short of production, which authenticates
     the information system to the server with a client TLS certificate);
     ss-pnea holds
     current global configuration (it can see the other members in the
     registry — precondition for routing any call in module 2.6); the soft
     token is logged in and the sign + auth certificates are active;
  2. **PLR (provider)** — client `PROGRESSA/GOV/PLR/ENROLMENT` is
     `REGISTERED`; service `enrolment-api` (OpenAPI3) is **enabled**,
     service URL `http://app-plr:8000/v1`; `app-plr` answers a direct health
     probe and reports the seeded enrolment count;
  3. **PNIA (provider)** — client `PROGRESSA/GOV/PNIA/IDENTITY` is
     `REGISTERED`; service `identity-api` (OpenAPI3) is **enabled**, service
     URL `http://app-pnia:8000/v1`; a direct probe of `app-pnia` returns
     only the purpose-limited fields (`nin, given_name, family_name,
     date_of_birth, sex, region`) — no field beyond the decree's list,
     matching `configs/member-pnia/pnia.yaml`'s `semantic.fields`.

  The ACL-exactness of both providers' services — that each service's
  granted-subject list is exactly `PROGRESSA/GOV/PNEA/EXAMS`, one entry, no
  more — is `member.md`'s check, not repeated here.

Status: UNVERIFIED until this passes on the live stack (kp-solution-verify).
