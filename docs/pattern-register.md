# The pattern register

Tier 1 of the semantic layer classifies every published service by the
**exchange pattern** its contract takes — the cross-sector shape, before any
education vocabulary enters. `apps/join-api/schema.py`'s `ExchangePattern`
enum is what *validates* a value; this page is what the values *mean*, and
what each one is anchored on.

This register is **hand-authored, not generated**. Five patterns, changing
only when the enum changes (a schema edit with KP3/KP4 sign-off), do not
need a data file, a generator and a checker behind them — the pack has
reversed exactly that apparatus once already (`docs/path-conformance.md`
P0.5). If the enum ever passes about eight values, or a second consumer needs
the register as data rather than as prose, that is the point to revisit.

Each row names the GovStack building block whose specification the pattern
anchors on, at a pinned version: a BB spec is a moving document, and a
pattern anchored on "Payments" without a version is anchored on nothing.

## The register

Ordered by the process each pattern serves — admit a party, publish a
capability, agree the terms, execute an exchange.

| Pattern (enum value) | Process it serves | Anchor spec (pin, chapter) | Contract shape it standardises | Data-model anchor | Realised in Linkup by |
|---|---|---|---|---|---|
| `registration` | Admit a party | Registration 1.0 §7.2 | eligibility determinants → eForm → roles → result credential | eForm schema + Result/credential entity | `apps/join-api` itself: the payload is the eForm, `validate.py`'s checks are the determinants, operator approval is the registrar role, the membership record is the issued credential. Not set on any published service today |
| `digital_registries_lookup` | Publish a capability, and execute a lookup against it | Digital Registries 2.0 §7–8 | schema-driven registry read; a version per schema change; an audit entry per change | the registry's own published schema — here `configs/semantic/semantic-map.yaml` | `identity-api` (PNIA), `enrolment-api` (PLR), `awards-api` (PTSB); the service catalogue is the same discipline applied to the register of services |
| `consent` | Agree the terms | Consent 1.3.0 §7 | DataAgreement (purpose, lawful basis, attributes with sensitivity) → ConsentRecord, hash-chained revisions | DataAgreement / ConsentRecord | The statutory half only: `lawful_basis` per service and the semantic map's release sets are a hand-rolled DataAgreement. Consent-bearing exchanges are a named absence — Linkup's education exchanges are statutory, and that asymmetry is itself the teaching point |
| `messaging` | Notify | Messaging 1.0 §6/§8 | recipient + type + content + transaction id; delivery states | none — the BB is content-agnostic by design | Not exercised. Listed so a member classifying a notification service has a value to use |
| `payments` | Disburse | Payments 3.0 §7.1–7.2 | account mapper (functional identity → financial address) + bulk credit instructions | beneficiary / creditInstruction field tables | Not exercised. The PTSB scholarship-disbursement scenario is where it would land, in KP4 |

**Candidates**, named so that a future demonstration does not invent a
sixth label ad hoc. Each *requires a schema change* to
`ExchangePattern` — and therefore KP3/KP4 sign-off under the frozen-contract
rule — before it can be used:

| Candidate | Anchor spec (pin) | Would cover |
|---|---|---|
| `credential_issuance` | Wallet 1.2.0 (W3C VC 2.0, OpenID4VCI) | Issuing the assembled certificate as a verifiable credential — the KP4 seam |
| `appointment` | Scheduler 1.1 | Exam and appointment booking; the BB's own model names "teacher" as a resource category |

## Cautions the anchors carry

- **The pins matter.** Digital Registries 2.0 has a 3.0.0-alpha in
  development; Identity 2.0, Consent 1.3.0 and Payments 3.0 all shipped
  together; Wallet moves fastest. Re-read the chapter before trusting a row
  against a newer spec.
- **Maturity is uneven across GovStack.** Nothing here anchors on a
  draft-grade specification (GIS 0.4, E-Marketplace 0.1, CMS 0.1, whose data
  chapter is still a template placeholder), and nothing tier-1 should.
- **Anchor on the standard, not the implementation.** Identity's management
  structures are MOSIP-shaped; the OIDC standard claims in §7.2 are the
  anchor, not the enrollment structures in §7.3.
- **Payments cites no ISO 20022 or 8583.** Its field tables are
  GovStack-local vocabulary, not an international standard.
- **A production delta rides on the lookup row.** Identity BB's `sub` is a
  pairwise pseudonymous token precisely so one identifier cannot be
  aggregated across services; Linkup routes the raw NIN through every
  exchange. Correct for a demonstration, wrong for production —
  `docs/production-delta.md`.

## What classification does not do

A pattern on a service says what shape its contract takes. It does not say
the service is permitted to anyone (`onboarding/catalogue.yaml` is
`listMethods`, never `allowedMethods`), and nothing checks that the value a
member chose is the *right* one — the enum constrains the vocabulary, review
constrains the judgement.
