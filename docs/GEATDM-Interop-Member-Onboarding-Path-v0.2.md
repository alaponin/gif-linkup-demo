# Member onboarding — the standard repeatable path

**Status:** v0.2 draft. Supersedes v0.1 (3 Aug 2026).
**v0.2 change:** revised against NIIS reference material and the two mature national instantiations (Estonia X-tee, Finland Suomi.fi Data Exchange Layer). The main structural change is the **two-track shape** — an ungated development track and a gated production track — which v0.1 did not have.
**v0.2 rev. 4 Aug:** added §6a — how GovStack building blocks apply to the semantic layer, and the two-tier semantic classification at G5.

**Internal sources:** `GEATDM-Interop-Method-v1.0` §10.2–10.3; `GEATDM-Interop-Toolkit-v1.0` TK-IO-07/08/09/10/12; `GEATDM-Interop-Reference-Model-v1.0` §5.2–5.4, §6
**External sources:** X-Road Organisational Model; NIIS, *Additional Building Blocks of an X-Road Ecosystem* (2022); X-Road v7.3.0 and v6.21.0 release notes; UC-MEMBER; Estonia X-tee joining guidance (RIA); Finland Suomi.fi Data Exchange Layer deployment guidance (DVV)
**Corroborated against:** the verified X-Road 7.7.0 admin-API sequence in `10-Knowledge-Products/KP2-GIF/KP2-build-pack/`

> **Why this document exists at all.** NIIS publishes no operator onboarding playbook, and does so deliberately: the Organisational Model assigns onboarding to the Operator, UC-MEMBER specifies only what the *software* does, and the *Additional Building Blocks* post makes the point explicitly — member management and onboarding are an operator responsibility outside the core, with no off-the-shelf implementation. Estonia and Finland are the two reference instantiations. This document is that operator layer for a GEATDM-method framework.

---

## 0. What must exist before any member can be onboarded

v0.1 began at the first member. That was wrong: five things are ecosystem-level decisions, made once, and a member onboarding that has to improvise any of them is not repeatable.

| # | Prerequisite | Decision owner | Note |
|---|---|---|---|
| 1 | Central Server operating | Operating Authority | X-Road core |
| 2 | **Certification Authority (CA)** | OA + Steering Committee | Commercial trust service provider, or operator-run. Both are permitted by the Organisational Model. |
| 3 | **Time-Stamping Authority (TSA)** | OA + Steering Committee | Same choice. Both trust services are *required* for a functioning ecosystem — neither is optional, and neither is a per-member artefact. |
| 4 | **Member classes defined** | Steering Committee | The admissible classes (e.g. GOV, COM, NGO) are an eligibility policy, not a technical setting. Adding a class later is a governance act. |
| 5 | **Identifier and naming conventions published** | Operating Authority | See §1a. |
| 6 | **Building-block pattern register** | OA architecture + TWG | Which GovStack BB patterns the ecosystem recognises, and the standard contract shape of each. See §6a. |

Two of the four NIIS "additional building blocks" are also onboarding prerequisites in practice; see §6.

---

## 1. The two-track shape

**This is the correction v0.2 makes.** Both mature instantiations run two distinct paths, and the distinction is deliberate:

| | **Development track** | **Production track** |
|---|---|---|
| Who may join | Anyone — in Finland explicitly including private individuals | Admitted member organisations only |
| Gate | A form, emailed | Full G0–G6 |
| Membership application | **Not required** (Estonia: no RIHA application for dev; Finland: FI-DEV is open) | Required |
| Purpose | Try the platform, validate an integration, test a new X-Road version | Carry real data under legal basis |
| Duration | Days | 12–25 weeks (6–10 at maturity) |
| Data | Synthetic only | Real, under the decree |

**Why it matters for the path.** An agency that must clear a Steering Committee gate before it can see whether X-Road solves its problem will not start. The development track exists so that technical feasibility is established *before* the organisational machinery engages — which means G0 arrives with an applicant who already knows what they want to build. Finland's stated 1–3 month connection time assumes this; a programme with a single gated path should not expect it.

**Operator obligation:** the development environment must be genuinely separate — its own Central Server, its own CA/TSA, its own trust anchor — and must carry a standing prohibition on real personal data, enforced by the membership terms rather than by hope.

### 1a. Conventions to publish before G1

| Convention | Constraint | Source |
|---|---|---|
| **Identifier character set** | From X-Road 7.3.0, X-Road identifiers permit only `a-zA-Z0-9'()+,-.=?` and strict checking is **on by default** for fresh installations. | v7.3.0 release notes (XRDDEV-1960) |
| **Member code scheme** | Typically the national business/organisation registry code. Stable across renaming and merger. | Practice |
| **Subsystem code scheme** | One per system, not per service. | UC-MEMBER |
| **Security Server host naming** | Finland mandates `<organisation><role><environment><nn>.<domain>` — e.g. `organisaatiolpdev01.org.fi`. Encodes owner, role, environment and sequence. | DVV |

The naming convention is not cosmetic: certificates, DNS, firewall rules and monitoring all key off the host name, and a convention retrofitted after fifty members is not retrofitted at all.

---

## 2. The production track — seven gates

| Gate | Name | Accountable | Responsible | Duration | Exit artefact |
|---|---|---|---|---|---|
| **G0** | Eligibility and intent | Operating Authority | OA onboarding team | 1–2 wk | Application + signed **membership agreement** |
| **G1** | Admission decision | **Steering Committee** | Operating Authority | 2–4 wk | Minuted admission; identity allocated |
| **G2** | Hosting and topology decision | Operating Authority | OA architecture | 1 wk | Topology record |
| **G3** | Identity and trust | Operating Authority | Trust service provider(s) | 1–2 wk | Certificates issued and member-verified |
| **G4** | Platform conformance | Operating Authority | Member + OA conformance | 4–10 wk | Passed platform conformance report |
| **G5** | Service conformance and registration | Operating Authority | Member + OA | 2–4 wk | Signed SLA + registered service + catalogue entry |
| **G6** | Production go-live and handover | Operating Authority | Member | 1–2 wk | Monitored first production transactions |
| **GX** | Retirement | Steering Committee | Operating Authority | 2–4 wk | De-registration record |

### G0 — Eligibility and intent

**Do:** eligibility test against TK-IO-08 §1; the member's senior representative signs the **membership agreement** — the terms and conditions of the ecosystem, distinct from the technical obligation set; member names a Technical Focal Point and, where personal data flows, a Data Protection Officer.

> **v0.2 correction.** v0.1 had "obligation acceptance" only. The reference practice separates two instruments: a *membership agreement* (contractual, signed once, ecosystem-wide) and the *Member Requirements* (operational, versioned, reissued as standards change). Conflating them means every standards revision reopens a contract.

**Exit test:** does the applicant hold a legal mandate for data it proposes to expose as authoritative? Consume-only applicants pass; an applicant claiming authority over data another body owns fails **here**, not at G5.

### G1 — Admission decision

**Do:** OA prepares the recommendation; **Steering Committee decides** (Ref Model §5.3). On admission the OA allocates member class, member code and subsystem code(s).

**Exit test, two parts:** is the identity unique, **and is it legal** — does it use only the permitted character set? Since 7.3.0 an identifier outside `a-zA-Z0-9'()+,-.=?` is rejected by both servers. An identifier accepted in a legacy-mode instance and rejected at the next upgrade is a migration incident.

> **Amend the material here.** TK-IO-10 Phase 1 shows only "operating-authority review"; Ref Model §5.3 makes the Steering Committee *accountable* for member admission. As written, the workflow lets the OA admit members under its own authority.

### G2 — Hosting and topology decision

**Do:** decide whether the member operates **its own Security Server** or is **hosted as a client on another organisation's**.

| | Own Security Server | Hosted as a client |
|---|---|---|
| G4 duration | 4–10 weeks | near zero |
| Member cost | VM/hardware, operations, patching | none |
| Trust | member holds its own signing key | **the host's token holds the joined member's signing key** |
| Autonomy | controls its own availability | inherits the host's |
| Suits | ministries, authoritative-data providers | small consumer-only bodies, agencies under a parent ministry |

**Minimum sizing for the own-server case** — publish it, as Finland does, so procurement is not a negotiation. Finland's published light/test dimensioning: Ubuntu 22.04 LTS, 2 vCPU, 4 GB RAM, 10 GB OS + 20–40 GB `/var` for logs, 1 Gb/s NIC — sufficient for ~50 queries/min at message sizes ≤ 500 KB. Production sizing is a separate figure. This is what TK-IO-08's empty "Annex A — Security Server technical specification" should contain.

**Exit test:** is the hosting choice compatible with the member's role? A body publishing authoritative personal data should not be hosted on a peer's server, because the host's token then holds its signing key — a delegation with no counterpart in the obligation set.

### G3 — Identity and trust

**Do:** the CA issues member-grade certificates; delivery to the Technical Focal Point; member verifies. Hosted member: signing certificate only. Own-server member: authentication *and* signing.

**Note the role split.** The Organisational Model makes trust services a third party to the ecosystem, not an OA function. Where the CA/TSA is commercial, G3's duration is the provider's SLA, not the OA's — and that dependency belongs in the programme risk register (TK-IO-13 currently has no trust-service-provider risk row).

**Exit test:** does the chain validate from the member's own environment, not the issuer's?

### G4 — Platform conformance

**Do (own-server only):** install; register with the Central Server; obtain and activate certificates; configure timestamping. Then the platform half of the Conformance Test Plan.

Four items v0.1 omitted, each a routine real-world blocker:

1. **Network and firewall.** The member's Security Server needs inbound and outbound reachability on the ecosystem's message ports — Finland publishes TCP **5500** and **5577** and tells providers to expect port-opening requests naming the counterpart's IP. Publish the equivalent for your instance; firewall change requests inside a ministry take weeks and are almost never on anyone's critical path until they are.
2. **Host naming** per §1a.
3. **Monitoring add-ons.** The operational-monitoring and environmental-monitoring add-ons are installed **on the Security Server**, and without them the member is invisible to the ecosystem's metrics and health monitoring. Installing them at G4 is trivial; retrofitting them across an installed base is a campaign.
4. **Configuration tooling.** Use the NIIS **Security Server Toolkit** (Finland's step 5 is "register and configure the Security Server using the X-Road toolkit") and the Ansible playbooks in the X-Road repository rather than a bespoke script. Hand-configured Security Servers are the main source of conformance variance.

**Exit test:** does the member's server appear in the global configuration as registered and active; does the conformance suite pass without waivers; **and is its monitoring data arriving centrally?**

### G5 — Service conformance and registration

Four artefacts converge here, and the material treats them as unrelated:

1. **The service contract** — OpenAPI/AsyncAPI, submitted to the OA.
2. **The semantic mapping — in two tiers** (see §6a): (a) **pattern classification** — which GovStack building-block pattern this exchange instantiates (Registration, Digital Registries lookup, Consent, Payments, …); (b) **field mapping** — the member's fields mapped onto the sector vocabulary in the framework's published semantic map. Tier (a) is cross-sector and reusable; tier (b) is not. *Both absent from TK-IO-10 entirely.*
3. **The SLA** — TK-IO-09, signed. *No TK-IO-10 step signs it.*
4. **The access-control list** — exactly the subjects the access policy names.

Then register: publish the service description, apply the ACL, and **create the service catalogue entry**.

> **v0.2 addition — the service catalogue closes the SLA gap concretely.** X-Road's own service-discovery mechanism is wire-level and requires querying each Security Server separately; a usable catalogue is an operator building block. Critically, the catalogue is where the *business* metadata lives — dataset description, terms and conditions, contact, pricing, **and the SLA**. So the SLA is not an orphan document: it is catalogue metadata attached to the registered service, and the catalogue entry is G5's natural exit artefact. Service descriptions can be collected from Security Servers automatically (Finland's `xroad-catalog` collector); the metadata is maintained by the owning organisation's service administrator.

**Legacy backends:** where the member's system cannot expose a conformant interface directly, the standard pattern is an **adapter service** between the Security Server and the legacy system, rather than modifying the legacy system. Naming this in the path prevents "our system can't do REST" becoming a stall.

**Exit test, three parts — all three, or the gate fails:**

- an authorised consumer's call reaches the backend end to end;
- an **unauthorised** subsystem's identical call is denied *by the provider-side access control* — not by a transport error, not by the caller's own server rejecting an unknown client;
- the response carries exactly the fields the contract declares.

The middle clause is the one usually skipped. A registration proven only on the happy path demonstrates that a route exists, not that a fence does.

### G6 — Production go-live and handover

**Exit test:** does the member's own monitoring see what the OA's monitoring sees? With G4 item 3 done, this is now checkable rather than aspirational: operational monitoring gives the OA ecosystem-wide visibility and the member visibility of its own traffic, by design.

### GX — Retirement

The material has no reverse path: sanctions exist (TK-IO-08 §7), de-registration does not, so the only documented end state for a member is permanence. Four real cases: machinery-of-government change, voluntary withdrawal, sanction, and pilot cleanup.

**Do:** revoke ACLs naming the member as a subject; unregister services; notify every consumer that held access, with notice per TK-IO-09 §7; **remove the catalogue entry**; unregister the subsystem; delete the client; revoke certificates; remove the member from the Central Server; archive the message log per the retention rule.

**Exit test:** is the member absent from the Central Server, from every host's client list, from every ACL and from the catalogue — **and** are its message-log records still retrievable for the statutory retention period? Deletion that takes the audit trail with it converts a retirement into an evidence gap.

---

## 3. The technical sequence inside G4–G5

Doc-verified X-Road 7.7.0 order, actor named per step.

**Prologue (OA, Central Server):** register the member; confirm the configuration anchor.

**G4 — own-server member** (skipped for a hosted member):

| # | Step | Actor |
|---|---|---|
| 1 | Initialise the Security Server with its owner identity | Member |
| 2 | Generate authentication key + CSR; obtain certificate | Member → CA |
| 3 | Generate signing key + CSR; obtain certificate | Member → CA |
| 4 | Register the server with the Central Server | Member → **OA approves** |
| 5 | Activate the server | Member |
| 6 | Configure the timestamping service | Member → TSA |

**G4/G5 — both shapes:**

| # | Step | Actor |
|---|---|---|
| 7 | Add the client (subsystem) to its Security Server | Member (own) / **OA** (hosted) |
| 8 | Generate the client's signing key | Member (own) / **OA on the host's token** (hosted) |
| 9 | Register the client | → **OA approves** |
| 10 | Publish each service description | Member / OA |
| 11 | Apply the ACL, one entry per authorised subject | OA |
| 12 | Verify: authorised call reaches the backend; unauthorised call denied by the ACL | OA conformance |

**Order is load-bearing.** For a hosted member, client-add must precede its signing-key generation, which must precede registration. Getting this wrong produces errors that read as certificate problems.

**Step 8 is the delegation G2 warns about.** For a hosted member the joined member's signing key is generated on the *host's* token. Whoever operates the host can sign as the joined member. This belongs in the obligation set and currently is not there.

### Three version-dependent facts worth pinning

1. **Approval is configurable, not inherently manual.** Since v6.21.0 the operator chooses automatic or manual approval of registration requests. v0.1 called steps 4 and 9 "the one genuine wait state" — that is true only under manual approval, which is a *policy* choice. State the choice explicitly in the operator's procedures: automatic approval collapses days into seconds and moves the control to G0–G1, where arguably it belongs.
2. **No complementary requests since 7.3.0.** Authentication-certificate and client registration requests are sent *from the Security Server* and approved on the Central Server; the Central Server no longer needs a complementary request created. Procedures written against 6.x describe a step that no longer exists.
3. **The management API is the sanctioned integration point.** Since 7.0, and materially improved in 7.3, both servers expose management APIs with full OpenAPI 3 descriptions — the Central Server serves its own at `/api/v1/openapi.yaml`. Scripted onboarding should target these, not the UI. Management-request origin IPs are now carried into the Central Server audit log, which is what makes an automated join auditable.

---

## 4. What can be automated, and what cannot

| Gates | Time | Nature |
|---|---|---|
| G0–G3 | 4–8 weeks | Organisational: legal mandate, a committee decision, certificate issuance by a third party. Not automatable, and should not be. |
| G4 (own server) | 4–10 weeks | Procurement, deployment, firewall change. Compressible by hosting (G2) and by the development track having de-risked it, not by tooling. |
| G4–G5 (technical) | **minutes** | Fully automatable via the management APIs. |
| G6 | 1–2 weeks | Operational confidence-building. |

**Use the existing tooling rather than building it:**

| Need | Component | Provenance |
|---|---|---|
| Security Server configuration | X-Road Security Server Toolkit | NIIS, open source |
| Cluster deployment | Ansible playbooks (`ansible/ss_cluster`) | X-Road repository |
| Container deployment | Security Server Sidecar (incl. Kubernetes) | NIIS |
| Metrics collection | X-Road Metrics | NIIS, open source |
| Service catalogue | `api-catalog` + `xroad-catalog` collector | Finnish Digital Agency, open source |
| Technical monitoring collection | `xroad-monitor-collector` | Finnish Digital Agency, MIT |
| Service management portal | **none available** | Custom build required — the one building block with no open-source implementation |

The KP2 build pack demonstrates the G4–G5 technical sequence driven end to end from a submitted payload in ~90 seconds for a hosted member, including validation, approval, config generation and the reachability-and-denial proof. Treat it as evidence that the sequence is automatable, not as the component to adopt.

**The reduction from 12 weeks to 6 comes from G2 (hosting), a standing certificate procedure at G3, automating G4–G5, and the development track having removed the unknowns — not from doing the committee work faster.** A programme that hits six weeks by compressing G0–G3 is compressing the part that makes membership lawful.

---

## 5. Gaps in the GEATDM material, and the fix

| # | Gap | Fix | v0.2 status |
|---|---|---|---|
| 1 | No hosting decision; TK-IO-10 assumes every member deploys its own server | Add G2 to TK-IO-10; add hosting + trust consequence to TK-IO-08 §2 | unchanged |
| 2 | Admission authority contradicts the RACI | Amend TK-IO-10 Phase 1 to show the Steering Committee gate | unchanged |
| 3 | Conformance tested before there is anything to conform | Split platform (G4) / service (G5) conformance; amend TK-IO-12 §1.2 | unchanged |
| 4 | Semantic conformance absent from onboarding | Add semantic mapping as a G5 artefact; add to TK-IO-08 §5 with the map as evidence | unchanged |
| 5 | The SLA is orphaned | Sign at G5 **and carry it as service-catalogue metadata** | sharpened |
| 6 | No retirement path | Add GX to TK-IO-10; de-registration procedure in TK-IO-08 §7 with the log-retention rule | unchanged |
| 7 | No per-onboarding artefact record | Adopt the file in §7 | unchanged |
| **8** | **No development track.** TK-IO-10 has one path; both reference instantiations have two. | Add the development track to TK-IO-10 as a pre-track with its own (minimal) requirements | **new** |
| **9** | **Ecosystem prerequisites unstated.** CA, TSA, member classes and naming conventions are treated as given. | Add §0 as a precondition section to TK-IO-10; add a trust-service-provider dependency row to TK-IO-13 | **new** |
| **10** | **TK-IO-08 Annex A is empty.** "Security Server technical specification" is a placeholder. | Publish minimum sizing, host naming and the port/firewall requirement | **new** |
| **11** | **The operator's own building blocks are unscoped.** The framework assumes a portal, a catalogue and monitoring exist. | Add §6 to the Method as an operator-capability roadmap | **new** |
| **12** | **The BB layer of the semantic map is missing.** Ref Model §7 names the BBs the framework consumes and produces, but nothing carries that into the semantic map or into onboarding — so cross-sector reuse stays implicit. | Publish the BB pattern register (§0.6); classify at G0/G1; make G5's semantic mapping two-tier; carry tier 1 into the catalogue entry | **new** |

---

## 6. The operator's own building blocks

X-Road's core provides no member management, service discovery, metrics or technical monitoring — these are the operator's to build, and onboarding depends on all four. Sequencing them against framework maturity:

| Building block | Needed by | Minimum viable start | Mature form |
|---|---|---|---|
| **Member management / service management portal** | member #1 | A wiki page or spreadsheet plus an email inbox — explicitly the recommended way to start, explicitly not scalable | Self-service portal: application, agreement signing, certificate requests, documentation; separate member and operator views |
| **Service catalogue** | member #3–5, when discovery stops being word-of-mouth | A published list | Auto-collected service descriptions plus maintained business metadata incl. SLA |
| **Reporting and metrics** | before the first Steering Committee performance report | Operational-monitoring add-on installed at G4 | Central collection; dependency graph; per-member reports; open data |
| **Technical monitoring** | before the installed base outgrows manual checks (~10 servers) | Environmental-monitoring add-on installed at G4 | Central collection; certificate-expiry and version alerting |

Two consequences for the path. First, **the add-ons must be installed during G4** — the collection layer can come later, but the add-on cannot, without a retrofit campaign. Second, **the service management portal is the only one with no open-source implementation available**, so it is a genuine build decision, and starting with a spreadsheet is a legitimate, documented choice rather than a failure.

---

## 6a. Building blocks and the semantic layer

**Sources:** GovStack *Building Blocks* catalogue and *About Building Blocks*; *Architecture and Nonfunctional Requirements* §4.6 (Flexible); GEATDM Ref Model §7.

### What GovStack actually gives the semantic layer — and what it does not

The GovStack catalogue specifies fourteen building blocks: Cloud Infrastructure, Consent, Content Management System, **Digital Registries**, E-Marketplace, E-Signature, GIS, **Identity**, **Information Mediator**, **Messaging**, **Payments**, **Registration**, Scheduler and Wallet. Each is defined as a composable, interoperable module exposing REST APIs, and design principle §4.6 states the reuse intent directly: *"Building Blocks should be re-usable and configurable, such that they can support multiple use cases with minimal effort… Building Blocks can be reused in multiple contexts."*

So the intuition is right: **Registration is an abstract, sector-neutral concept**, and an education enrolment is one instance of it, exactly as a farmer subsidy application or a health-facility licence is another.

But the boundary matters, and getting it wrong wastes months:

| | GovStack BBs provide | GovStack BBs do **not** provide |
|---|---|---|
| **Process** | The abstract act — *register*, *consent*, *pay*, *notify*, *look up a registry record*, *issue a credential* | — |
| **Interface** | A standard REST contract shape per BB | — |
| **Vocabulary** | — | What a *learner* is; that an enrolment has `school`, `level`, `enrolment_year`, `status`; the code lists; the identifier |

BBs are **functional abstractions, not data-model abstractions.** The sector vocabulary still comes from sector standards — OneRoster and CEDS for education, and ISO/IEC 11179 for the discipline of describing each element. A programme that expects to download an education data model from GovStack will not find one.

### The useful consequence: a two-tier semantic layer

This gives the semantic layer a structure it otherwise lacks:

```
Tier 1 — BB pattern      Registration        (cross-sector, from GovStack)
                              │
Tier 2 — Sector entity   Enrolment           (education, from OneRoster / CEDS)
                              │
Tier 3 — Instance        PLR enrolment-api   (this member, this contract)
```

Tier 1 is what makes an exchange **comparable across sectors**: once the ecosystem knows that "apply for a school place", "apply for a farm subsidy" and "apply for a business licence" are all *Registration*, they can share a request/response shape, a status model, an audit pattern and a consent hook — even though their payloads share not one field. That is where the reuse actually lives, and it is invisible unless someone writes it down.

Tier 2 is where meaning is negotiated between data owners, and it is genuinely sector-specific. Tier 3 is the member's own contract, checked at G5.

### Anchoring in the existing GEATDM material

Ref Model §7 already establishes the connection and should be read as this section's parent: the framework **is** the Information Mediator BB, and it consumes Identity BB, Registration BB, Messaging BB and a qualified time-stamping service. §7.3 already lists education's consumption as learner-registry queries, digital-credential issuance and school-facility queries — which are, in tier-1 terms, *Digital Registries lookup*, *Wallet / E-Signature credential issuance*, and *Digital Registries lookup* respectively.

Two BBs deserve specific attention in onboarding:

- **Consent BB** — the natural home of G5's data-protection envelope where the exchange needs a consent basis rather than a statutory one. Classifying an exchange as consent-bearing at tier 1 makes the legal question explicit at registration instead of at audit.
- **Digital Registries BB** — the pattern behind every authoritative-source lookup, which is most once-only traffic. Its contract shape is the one worth standardising first.

### Where it lands in the path

| Gate | BB-related addition |
|---|---|
| **§0.6** | Publish the **BB pattern register**: which patterns this ecosystem recognises, and the standard contract shape of each. Without it, tier 1 is folklore. |
| **G0** | The applicant states which BB patterns its systems implement or consume. This is a better eligibility question than "what do you want to connect", because it is answerable by a non-architect and it predicts the conformance profile. |
| **G1** | Admission record carries the member's BB profile — provider of *Digital Registries*, consumer of *Registration*, and so on. |
| **G5** | Two-tier semantic mapping (above). Tier 1 determines which standard contract shape and which conformance profile applies; tier 2 is checked against the published semantic map. |
| **§6 catalogue** | The service catalogue entry carries the tier-1 classification, so services become discoverable *by pattern* across sectors, not only by owning agency. |

### One terminology caution

GovStack's Architecture specification has a section called **"Onboarding Products"**. That means onboarding a *software product* — testing a candidate implementation for BB compliance. It is not member onboarding. The two are unrelated processes that share a word, and conflating them in a tender document has predictable consequences.

---

## 7. The onboarding file — one folder per member

```
members/<member-code>/
├── 00-application.md              G0  application + signed membership agreement
├── 01-admission.md                G1  Steering Committee minute; allocated identity
├── 02-topology.md                 G2  own-server | hosted; reason; trust note; sizing
├── 03-certificates.md             G3  CA/TSA issuance record; member-side verification
├── 04-platform-conformance.md     G4  conformance result; monitoring add-ons confirmed;
│                                      host name; firewall/port record; waivers
├── 05-services/
│   └── <service-code>/
│       ├── contract.openapi.yaml  G5  the service contract
│       ├── semantic-map.md        G5  field mapping onto the published map
│       ├── sla.md                 G5  TK-IO-09, signed; mirrored to the catalogue
│       ├── acl.md                 G5  authorised subjects, and the deny proven
│       └── catalogue-entry.md     G5  catalogue metadata and its owner
├── 06-golive.md                   G6  handover; first-transaction monitoring
└── 99-retirement.md               GX  written only at exit
```

Every file corresponds to a gate exit. An onboarding whose folder is missing a file has not passed that gate, whatever the calendar says — the property that makes the path auditable at the quarterly compliance review (Method §10.3.3).

---

## 8. Open questions for the framework owner

1. **Is hosted membership permitted in production**, and what does the host owe the hosted member? The signing-key delegation needs a clause in the obligation set either way.
2. **Automatic or manual approval** of registration requests — an operator policy choice since 6.21.0, and the single biggest lever on technical onboarding time.
3. **Trust services: commercial or operator-run?** Determines G3's duration, its risk owner, and whether the OA needs PKI staff at all.
4. **What is the message-log retention period**, and who holds it after retirement? GX cannot be specified without it.
5. **Does a consumer-only member need an SLA?** TK-IO-09 is written for providers; a consumer's obligations (rate, purpose limitation, log cooperation) have no template.
6. **When is the service management portal built?** It is the only operator building block with no open-source starting point, and the spreadsheet-and-email alternative has a known ceiling.
