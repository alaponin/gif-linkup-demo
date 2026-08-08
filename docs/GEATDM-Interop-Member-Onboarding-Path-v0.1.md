# Member onboarding — the standard repeatable path

> **SUPERSEDED by `GEATDM-Interop-Member-Onboarding-Path-v0.2.md` (4 Aug 2026).** v0.2 revises this draft against NIIS reference material and the Estonian and Finnish national instantiations. The principal correction is structural: the real repeatable path has **two tracks** (an ungated development track and the gated production track below), which this version does not have. Retained for the change record only.

**Status:** v0.1 draft, elicited from the 08-Interoperability module
**Sources:** `GEATDM-Interop-Method-v1.0` §10.2, §10.3; `GEATDM-Interop-Toolkit-v1.0` TK-IO-07, TK-IO-08, TK-IO-09, TK-IO-10, TK-IO-12; `GEATDM-Interop-Reference-Model-v1.0` §5.2, §5.3, §5.4, §6
**Corroborated against:** the verified X-Road 7.7.0 admin-API sequence in `10-Knowledge-Products/KP2-GIF/KP2-build-pack/` (`hurl/steps.py`, `apps/join-api/job.py`, `configs/x-road-bus/2.7.yaml`)

---

## What the material already fixes

The module is not silent on onboarding — it fixes four things, and they are consistent with each other:

| Fixed by the material | Where |
|---|---|
| A six-phase workflow, 12–24 weeks per member, reducing to 6–10 weeks at maturity | Method §10.2; TK-IO-10 |
| The obligation set a member accepts | Ref Model §5.4; operationalised by TK-IO-08 |
| Who decides admission: **Steering Committee accountable, Operating Authority responsible**, member merely informed | Ref Model §5.3 RACI |
| Per-service commitments, separate from membership | TK-IO-09 |

What the material does **not** fix — and what this document adds — is the join between that organisational workflow and the technical sequence X-Road actually imposes. TK-IO-10 Phase 3 says "member deploys Security Server software" and Phase 5 says "service registered in framework catalogue"; neither names a single admin-API call, an actor per call, or a reversal. The result is a workflow that is repeatable on paper and improvised in practice.

---

## The path — seven gates

Each gate has one accountable body, an entry condition, a fixed artefact set, and an exit test. **A gate that cannot be failed is not a gate**, so each exit test below is stated as something that can come back negative.

| Gate | Name | Accountable | Responsible | Duration | Exit artefact |
|---|---|---|---|---|---|
| **G0** | Eligibility and intent | Operating Authority | OA onboarding team | 1–2 wk | Signed application + obligation acceptance |
| **G1** | Admission decision | **Steering Committee** | Operating Authority | 2–4 wk | Minuted admission; member identity allocated |
| **G2** | Hosting and topology decision | Operating Authority | OA architecture | 1 wk | Topology record (own server / hosted) |
| **G3** | Identity and trust | Operating Authority | OA PKI team | 1–2 wk | Member-grade certificates issued and verified |
| **G4** | Platform conformance | Operating Authority | Member + OA conformance | 4–10 wk | Passed platform conformance report |
| **G5** | Service conformance and registration | Operating Authority | Member + OA | 2–4 wk | Signed SLA + registered service + exact ACL |
| **G6** | Production go-live and handover | Operating Authority | Member | 1–2 wk | Monitored first production transactions |
| **GX** | Retirement | Steering Committee | Operating Authority | 2–4 wk | De-registration record |

Total 12–25 weeks at early maturity — consistent with Method §10.2's own estimate. G2 is new; G4/G5 are Method §10.2's steps 4 and 5 re-cut (see §"Seven gaps", item 3). GX is new.

### G0 — Eligibility and intent

**Entry:** an organisation asks to join, or the Implementation Plan schedules it.
**Do:** eligibility test against TK-IO-08 §1; the member's senior representative signs acceptance of the obligation set (Ref Model §5.4); the member names a Technical Focal Point and, where personal data will flow, a Data Protection Officer (TK-IO-08 §3).
**Exit test:** does the applicant hold a legal mandate for data it proposes to expose as authoritative? An organisation that wants to *consume* only still passes; an organisation claiming authority over data another body already owns **fails here, not at G5**. Authoritative-source collision is the cheapest failure to catch at the application desk and the most expensive to catch after registration.

### G1 — Admission decision

**Entry:** complete G0 pack.
**Do:** Operating Authority prepares the recommendation; **Steering Committee decides** (Ref Model §5.3). On admission, the OA allocates the member's framework identity — member class, member code, subsystem code(s) — and records it in the member registry.
**Exit test:** is the allocated identity unique, and does it collide with no existing member or reserved code? Identity is the one decision that is expensive to reverse: it propagates into certificates, ACLs, service identifiers and every consumer's configuration.

> **Correct an inconsistency in the material here.** TK-IO-10 Phase 1 shows only "operating-authority review". Ref Model §5.3 makes the Steering Committee *accountable* for member admission. TK-IO-10 should be amended to show the Steering Committee gate; as written, the workflow lets the OA admit members under its own authority.

### G2 — Hosting and topology decision *(new)*

**Entry:** admitted member.
**Do:** decide whether the member operates **its own Security Server** or is **hosted as a client on another organisation's Security Server**.

This decision is absent from the material, which assumes throughout that every member deploys its own (TK-IO-10 Phase 3: "member procures Security Server hardware"). Both shapes are real and X-Road supports both. The choice determines the critical path:

| | Own Security Server | Hosted as a client |
|---|---|---|
| G4 duration | 4–10 weeks | near zero |
| Member cost | hardware/VM, operations staff, patching | none |
| Trust | member holds its own signing key | **the host's token holds the joined member's signing key** |
| Autonomy | member controls its own availability | member's availability is the host's |
| Suits | ministries, large agencies, authoritative-data providers | small consumer-only bodies, agencies under a parent ministry |

**Exit test:** is the hosting choice compatible with the member's role? A body that will publish authoritative personal data should not be hosted on a peer's server, because the host's token then holds its signing key — a delegation with no counterpart in the obligation set. Record the decision and the reason.

> **Note on provenance.** The KP2 build pack defaults joins to hosted (`configs/x-road-bus/2.7.yaml`, `default_hosting: hosted_on`), but its own `docs/production-delta.md` justifies that on demo resource grounds — containers and RAM — not governance. Do not read the demo default as a production recommendation.

### G3 — Identity and trust

**Entry:** topology decided.
**Do:** Method §10.2 step 2 — PKI issues member-grade certificates; delivery to the Technical Focal Point; member verifies. For a hosted member this is a signing certificate only; for an own-server member it is authentication *and* signing.
**Exit test:** does the certificate chain validate against the framework's trust anchor from the member's own environment, not from the PKI team's? Certificate delivery that is only verified at the issuing end is a recurring source of G4 failure.

### G4 — Platform conformance

**Entry:** certificates in hand.
**Do:** for an own-server member — deploy, register the server with the Central Server, obtain and activate certificates, configure timestamping. For a hosted member — nothing; the platform is already conformant. Then run the platform half of the Conformance Test Plan (TK-IO-12): the binding technical standards in TK-IO-07 that concern the Security Server and its connectivity.
**Exit test:** does the member's server appear in the global configuration as registered and active, and does the conformance suite pass without waivers? A waived conformance item at G4 becomes an incident at G6.

### G5 — Service conformance and registration

**Entry:** a conformant platform, and a first service identified (as provider or consumer).
**Do:** this is where four artefacts converge, and the material currently treats them as unrelated:

1. **The service contract** — OpenAPI/AsyncAPI, submitted to the OA (TK-IO-10 Phase 5).
2. **The semantic mapping** — the member's fields mapped onto the framework's published semantic map for the entities it touches (Ref Model §6.2). *This step is missing from TK-IO-10 entirely; see gap 4.*
3. **The SLA** — TK-IO-09, signed by the providing member and the OA. *TK-IO-10 has no step where the SLA is signed; see gap 5.*
4. **The access-control list** — exactly the consumer subsystems the access policy names, and no others.

Then register: publish the service description and apply the ACL.
**Exit test, in three parts** — all three, or the gate fails:

- an authorised consumer's call reaches the member's backend end to end;
- an **unauthorised** subsystem's identical call is denied *by the provider-side access control*, not by a transport error or an unknown-client rejection at the caller's own server;
- the response carries exactly the fields the contract declares — no more.

The middle clause is the one usually skipped. A registration proven only on the happy path has not been proven at all: it demonstrates that a route exists, not that a fence does.

### G6 — Production go-live and handover

**Entry:** G5 passed in the non-production environment.
**Do:** Method §10.2 step 6 — production deployment, operations handover from the OA support team to the member, first production transactions monitored.
**Exit test:** does the member's own monitoring see the transactions the OA's monitoring sees? Handover that leaves the OA as the only observer is not handover.

### GX — Retirement *(new)*

The material has no reverse path. Method §10.4 anticipates "member non-compliance becoming systemic" and TK-IO-08 §7 provides sanctions, but nothing describes de-registration — so the only documented end state for a member is permanence. A repeatable path needs a repeatable exit, for four real cases: machinery-of-government change (agency merged or abolished), voluntary withdrawal, sanction, and demonstration/pilot cleanup.

**Do:** revoke ACLs that name the member as a subject; unregister its services; notify every consumer that held access, with notice per TK-IO-09 §7; unregister the subsystem; delete the client; revoke certificates; remove the member from the Central Server; archive the message log per the retention rule.
**Exit test:** is the member absent from the Central Server, absent from every host's client list, absent from every ACL, **and** are its message-log records still retrievable for the statutory retention period? Deletion that takes the audit trail with it converts a retirement into an evidence gap.

---

## The technical sequence inside G4–G5

This is the part the material omits. The sequence below is the doc-verified X-Road 7.7.0 order, taken from a working implementation, with the actor named per step — which is what makes it a procedure rather than a diagram.

**Prologue (Operating Authority, on the Central Server):** register the member; confirm the configuration anchor.

**G4 — own-server member** (skipped entirely for a hosted member):

| # | Step | Actor |
|---|---|---|
| 1 | Initialise the Security Server with its owner identity | Member |
| 2 | Generate the authentication key and CSR; obtain the certificate | Member → OA PKI |
| 3 | Generate the signing key and CSR; obtain the certificate | Member → OA PKI |
| 4 | Register the server with the Central Server | Member → **OA approves** |
| 5 | Activate the server | Member |
| 6 | Configure the timestamping service | Member |

Step 4 is the one genuine wait state: registration is **blocked pending operator approval**. Every automation of this path has to model that as a state, not as a retry — in production it is days, not seconds.

**G4/G5 — both shapes:**

| # | Step | Actor |
|---|---|---|
| 7 | Add the client (subsystem) to its Security Server | Member (own) / **OA** (hosted) |
| 8 | Generate the client's signing key | Member (own) / **OA on the host's token** (hosted) |
| 9 | Register the client | → **OA approves** |
| 10 | Publish each service description | Member / OA |
| 11 | Apply the access-control list, one entry per authorised subject | OA |
| 12 | Verify: authorised call reaches the backend; unauthorised call is denied by the ACL | OA conformance |

**Order is load-bearing.** For a hosted member, client-add must precede its signing-key generation, which must precede registration. Getting this wrong produces errors that read as certificate problems.

**Step 8 is the delegation G2 warns about**: for a hosted member, the joined member's signing key is generated on the *host's* token. Whoever operates the host can sign as the joined member. That belongs in the obligation set and currently is not there.

---

## Seven gaps in the material, and the fix

| # | Gap | Fix |
|---|---|---|
| 1 | **No hosting decision.** TK-IO-10 assumes every member deploys its own Security Server. | Add G2 to TK-IO-10; add a hosting section to TK-IO-08 §2 with the trust consequence stated. |
| 2 | **Admission authority contradicts the RACI.** TK-IO-10 Phase 1 shows OA review only; Ref Model §5.3 makes the Steering Committee accountable. | Amend TK-IO-10 Phase 1 to show the Steering Committee gate. |
| 3 | **Conformance is tested before there is anything to conform.** Method §10.2 puts conformance (step 4) before first service registration (step 5). | Split into platform conformance (G4) and service conformance (G5), as above. Amend TK-IO-12 §1.2 to name the two scopes. |
| 4 | **Semantic conformance is absent from onboarding.** TK-IO-08 §5 asks for "data-quality commitments"; no workflow step maps the member's fields onto the framework's semantic map. | Add semantic mapping as a G5 artefact; add "conforms to the published semantic map for entities X, Y" as a TK-IO-08 §5 requirement with the map as its evidence. |
| 5 | **The SLA is orphaned.** TK-IO-09 exists; no TK-IO-10 step signs it. | Make a signed SLA an exit criterion of G5, per registered service. A service registered without one is a service with no committed availability. |
| 6 | **No retirement path.** Sanctions exist; de-registration does not. | Add GX to TK-IO-10 and a de-registration procedure to TK-IO-08 §7, including the message-log retention rule. |
| 7 | **No per-onboarding artefact record.** Each onboarding produces documents; nothing says which set constitutes the file. | Adopt the checklist below as the standard onboarding file. |

---

## The onboarding file — one folder per member

The path is repeatable only if it leaves the same evidence every time.

```
members/<member-code>/
├── 00-application.md              G0  signed application + obligation acceptance
├── 01-admission.md                G1  Steering Committee minute; allocated identity
├── 02-topology.md                 G2  own-server | hosted; the reason; the trust note
├── 03-certificates.md             G3  issuance record; member-side verification
├── 04-platform-conformance.md     G4  TK-IO-12 platform result; waivers (ideally none)
├── 05-services/
│   └── <service-code>/
│       ├── contract.openapi.yaml  G5  the service contract
│       ├── semantic-map.md        G5  field mapping onto the published map
│       ├── sla.md                 G5  TK-IO-09, signed
│       └── acl.md                 G5  authorised subjects, and the deny proven
├── 06-golive.md                   G6  handover record; first-transaction monitoring
└── 99-retirement.md               GX  written only at exit
```

Every file corresponds to a gate exit. An onboarding whose folder is missing a file has not passed that gate, whatever the calendar says — which is the property that makes the path auditable at the quarterly compliance review (Method §10.3.3).

---

## What can be automated, and what cannot

The KP2 build pack demonstrates that **G4 step 7 through G5 step 12 can be driven end to end from a submitted payload in about 90 seconds** for a hosted member, including validation, operator approval, configuration generation, the live admin-API sequence, and the reachability-and-denial proof. That is worth knowing, because it reframes where onboarding time actually goes.

| Gates | Time | Nature |
|---|---|---|
| G0–G3 | 4–8 weeks | Organisational. Legal mandate, a committee decision, key ceremonies. Not automatable, and should not be. |
| G4 (own server) | 4–10 weeks | Procurement and deployment. Compressible by hosting (G2), not by tooling. |
| G4–G5 (technical) | **minutes** | Fully automatable, and automation is what makes it *repeatable* rather than merely fast. |
| G6 | 1–2 weeks | Operational confidence-building. |

The material's "12 weeks, reducing to 6 at maturity" (TK-IO-14) is therefore achievable — but the reduction comes from G2 (hosting), from a standing PKI procedure at G3, and from automating G4–G5, **not** from doing the committee work faster. A programme that tries to hit six weeks by compressing G0–G3 is compressing the part that makes membership lawful.

---

## Open questions for the framework owner

1. **Is hosted membership permitted at all in production**, and if so, what does the host owe the hosted member? The signing-key delegation needs a clause in the obligation set either way.
2. **Who approves registration at steps 4 and 9** — the OA's operator, or a named role in the Steering Committee's delegation? The material's RACI covers admission but not registration approval.
3. **What is the message-log retention period**, and who holds it after retirement? GX cannot be specified without it.
4. **Does a consumer-only member need an SLA?** TK-IO-09 is written for providers; a consumer's obligations (rate, purpose limitation, log cooperation) have no template.
