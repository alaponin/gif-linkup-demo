# Service catalogue — design

**Status:** design, pre-implementation. Nothing described here is built yet.
**Driver:** `docs/GEATDM-Interop-Member-Onboarding-Path-v0.3-amendments.md` A9.
**Implementation plan:** `docs/decisions/superpowers/plans/2026-08-09-kp2-service-catalogue.md`.
**Closes, when built:** `docs/path-conformance.yaml` G5.6, S6.2, S6a.4, and the
larger half of S7.6.

> **Status claims live in `docs/path-conformance.yaml`, not here.** This
> document says what the artefact *is*; that file says whether it exists. No row
> in it changes status before the code that satisfies it exists — the rule the
> 2026-08-08 review produced, and the reason this design was written before any
> status was touched.

---

## 1. Why this is being built now

`docs/decisions/onboarding-alignment-design.md` §4.4 deferred `catalogue-entry.md`
(G-05b) with an explicit unlock condition: *"the catalogue metadata half waits for
a curriculum or framework driver."*

The curriculum door is closed and should stay closed. Topic 5 is a contracted
deliverable with a fixed subtopic count and runtime (RFQ-S-GIGA-2026-022); adding
a subtopic is a scope change to an ITU contract, and D3 decided that correctly.

That leaves the framework door, which is the one this opens. Amendment **A9**
makes the catalogue entry a G5 exit condition in the onboarding path itself. The
pack then builds it because the framework requires it — not because the pack
found a gap and grew to fill it, which is the failure mode P6 exists to prevent.

One correction of record travels with this. §5 of the alignment design lists
*"A generated `catalogue-entry.md` per service"* as what the pack builds
**instead of** a catalogue — a row written before D3 and withdrawn by §4.4 forty
pages later. The two have contradicted each other since. That document is frozen,
so the contradiction is annotated in place rather than rewritten, and this
document is where the resolution lives.

---

## 2. The boundary that keeps §5 intact: generated, not collected

§5's "not building" row is still right about the thing it names. Holding on to
that requires being precise about which of two different artefacts is being
built, because both are called a catalogue.

**A collector** — NIIS's X-Road Catalog is the reference implementation — walks
`listClients → listMethods → getOpenAPI/getWsdl` across the ecosystem on a timer,
stores the result, and serves it through a Lister and optionally a portal
(Finland's Liityntäkatalogi, Estonia's X-tee catalogue front-end). It is a **pull**
architecture over a bus it does not control. It sees everything actually
published, including services no register ever authorised, and it is stale
between runs.

**A register's own output** is derived from the data the registration act already
produced. It is a **push** artefact: the same validated `JoinPayload` that wrote
`configs/member-<key>/<key>.yaml` writes the catalogue record, in the same code
path, at the same moment. It cannot be stale relative to the register, because
regenerating it *is* the register.

This pack builds the second and not the first. The distinction is not a
convenience — it is the reason the §5 boundary survives:

| | Collector (§6 building block, **not built here**) | Register output (**this design**) |
|---|---|---|
| Direction | Pull, scrapes the bus | Push, derived from the join |
| Coverage | Everything published, including unregistered | Only what this register authorised |
| Staleness | Between collection runs | Structurally impossible |
| Can supply the SLA, lawful basis, pattern | **No — none are on the wire** | Yes, they are join payload fields |
| Needs a portal, search, currency policy | Yes | No |

The last row but one is the whole argument for doing this at registration. A
collector can rebuild every endpoint of a service from `getOpenAPI` whenever it
likes. It can never rebuild the signed SLA, the lawful basis, or the tier-1
pattern classification, because those are not transmitted by anything. **Metadata
not captured at registration is not recoverable later by any amount of catalogue
engineering.**

Stated the other way, so this is not oversold: the artefact designed here is
*complete* for members that joined through this register and *blind* to
everything else on the bus. A production ecosystem needs both halves. §7 below
names what production must still add.

---

## 3. The unit: one entry per published service

```
onboarding/<key>/
  00-gates.md
  01-admission.md        (real joins only)
  02-requirements.md
  03-sla/<code>.md
  04-catalogue/<code>.md   ← new, one per published service
  05-registration.md
  99-retirement.md        (at exit)
```

**Why `04-`, and not the path's `05-services/<code>/`.** Path §7 asks for one
folder per service holding contract, semantic map, SLA, ACL and catalogue entry
together. This tree already numbers registration at `05-`, so adopting that shape
means renumbering the tree, relocating every SLA file, regenerating both goldens
and editing the acceptance clauses that assert against them — a re-baselining
event bought for a directory name. `04-catalogue/` sits where the entry belongs
in gate order (after the SLA is signed at 5.3, at or before registration at 5.4)
and costs none of it. A6 already permits a layout other than the path's; A9
extends that permission to the per-service shape and states the invariant the
layout is *for* — which this satisfies. **S7.6 therefore stays a named absence,
with a smaller absence to name.**

**A consumer-only member gets no `04-catalogue/` directory at all**, exactly as
it gets no `03-sla/`. There is nothing to publish, and an empty directory would
be a stub asserting a gate that does not apply.

---

## 4. Fields, and where every one of them comes from

The design rule is that the entry is **entirely derived**. There is no new
operator input, no new prompt, and — this is worth stating plainly — **no
`schema.py` change at all**. Every field below already exists on a validated
`JoinPayload`, in `manifest.yaml`, or in `configs/semantic/semantic-map.yaml`.
An artefact that needed a new field would be a new obligation on the member;
this one is a new *rendering* of obligations they already met.

| Field | Example | Source | When it is not there |
|---|---|---|---|
| Service code | `enrolment-api` | `Service.code` | — required |
| X-Road service id | `PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api` | constructed: instance + member code + `subsystem` + service code | — always constructible |
| Provider | Primary Learner Registry (PLR) | `manifest.yaml` `identity.members.<key>` | — |
| Contract | `http://app-plr:8000/spec.yaml` | `Service.spec_url` | — required |
| Declared fields | `nin, school, level, …` | `validate.contract_fields()` on the spec the reachability check already fetched | *contract not re-fetched at render time* |
| Semantic entity (tier 2) | `enrolment` (anchor: OneRoster) | `Semantic.entity` → `configs/semantic/semantic-map.yaml` | *not declared* |
| **BB pattern (tier 1)** | `digital_registries_lookup` | `Semantic.pattern` | ***unclassified — cannot be found by pattern*** |
| Lawful basis | Learner Registry Act… | `Service.lawful_basis` | *not stated* |
| SLA | → [`../03-sla/enrolment-api.md`](../03-sla/enrolment-api.md) | **a link, never a copy** | the gate was not passed |
| ACL subjects | `PROGRESSA/GOV/PNEA/EXAMS` | `Service.access` | *none granted* |
| Registered by | join request id, or by hand | `request_id`, as `05-registration.md` already does | — |

### The five rules this table encodes

**R1 — Link the SLA, never copy it.** The orphan-SLA problem is *directional*: an
SLA reachable from the member but not from the service. The fix is a link in the
missing direction, not a second copy that can drift from the first. `03-sla/` stays
the one place an SLA is written.

**R2 — Derived only.** Nothing in an entry is a value a human typed into the
entry. Everything traces to the join payload, the manifest, or the semantic map.

**R3 — An absence renders as an absence, not a blank.** A service with no
`Semantic.pattern` renders *"unclassified — this service declares no exchange
pattern, so it cannot be found by pattern"* on the face of the entry. This is the
pack's P2 rule applied at field level, and it has a specific payoff: **S6a.1's
named absence stops being a row in a conformance file and becomes visible per
service, to the reader who would be affected by it.** Note what the rendered
string does *not* say: a member reading a catalogue entry has no idea what S6a.1
is and no way to look it up, so the entry states the consequence rather than the
conformance id. The id belongs here, in the design, where a reader can follow it.

**R4 — Generated, never hand-maintained.** Same rule as the rest of
`onboarding/<key>/`: a missing entry means the gate has not been passed, whatever
the calendar says. Nothing backfills a plausible-looking stub.

**R5 — Publication is not permission.** Every entry states this on its face. A
catalogue entry is the `listMethods` half of X-Road's own distinction; the
`allowedMethods` half is the provider's ACL, and appearing in this catalogue
grants a reader exactly nothing. This sentence is fixed text on every entry
because the single most likely misreading of a service catalogue — by a
prospective member, in exactly the situation the catalogue exists to serve — is
that finding a service means being able to call it.

---

## 5. The aggregate: `onboarding/catalogue.yaml`

One file for the whole instance, derived from `manifest.yaml` plus every
`configs/member-*/`, regenerated wholesale by one function.

```yaml
# Generated -- do not hand-edit. Regenerate: scripts/render-onboarding.sh
instance: PROGRESSA
generated_from: manifest.yaml + configs/member-*/
services:
  - id: PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api
    provider: {key: plr, code: PLR, name: Primary Learner Registry}
    service_code: enrolment-api
    contract: http://app-plr:8000/spec.yaml
    semantic: {entity: enrolment, anchor: OneRoster}
    pattern: digital_registries_lookup
    lawful_basis: "Learner Registry Act, PLR as the authoritative source ..."
    sla: onboarding/plr/03-sla/enrolment-api.md
    access: [PROGRESSA/GOV/PNEA/EXAMS]
    entry: onboarding/plr/04-catalogue/enrolment-api.md
```

**Regenerated wholesale, never appended to.** This is the single most important
property of the aggregate and it is worth the paragraph:

- **Retirement closes for free.** GX.3 asks the operator to *"remove the catalogue
  entry"*, and `path-conformance.yaml` records that there is no catalogue to
  remove one from. With a derived aggregate there is also no *removal*: un-join
  deletes `configs/member-<key>/`, the next regeneration simply does not find it,
  and the entry is gone. A delete path is a thing that can be forgotten; a
  derivation cannot be.
- **It is golden-testable.** Byte-identical output on regeneration from unchanged
  inputs, which is the discipline the rest of the pack's generated corpus already
  lives under.
- **It cannot drift from the register.** An accumulating catalogue is a second
  source of truth about who publishes what. A derived one is a view.

---

## 6. `GET /catalogue`

Read-only, on `apps/join-api`. Returns the aggregate as JSON. No write path, and
it never talks to X-Road — it reads the same derived data as the YAML.

**Auth: the applicant token, not the operator token.** This is a deliberate choice
and the reasoning matters more than the choice. The reader who needs a service
catalogue is a body that has just joined, or is deciding whether to, and is
trying to find out what is on the bus. Gating that behind the operator credential
reproduces the exact problem the catalogue exists to solve — discovery by asking
someone who already knows. The applicant token is the pack's existing "a member,
or someone acting for one" credential, which is the right audience. Anonymous
access is not proposed: the pack's own exposure rules bind here as everywhere.

**The `?subject=` filter is deferred, on purpose.** Filtering the aggregate to the
services a given subsystem's ACL already names would be the `allowedMethods`
analogue, and it is cheap — the ACL subjects are right there in the data. It is
deferred to an optional task for one reason: it returns *what the register
recorded*, which is not the same thing as *what the bus will let you call*, and
the gap between those two is where an operator gets a wrong answer at the worst
moment. If it is ever built, that sentence ships on the response, not in the
documentation.

---

## 7. Out of scope — and what production must still add

Unchanged from §5 of the alignment design, now with the boundary drawn precisely:

| Not built here | Why | What production needs |
|---|---|---|
| A collector | Pull-side; §6 operator building block | X-Road Catalog (NIIS) or equivalent, to see services this register never authorised |
| A portal, search, browse | Same | Liityntäkatalogi / X-tee catalogue front-end analogue |
| Federation-wide view | This register covers one instance | Cross-instance `listClients` per federated partner |
| Currency/staleness policy | A derived view cannot be stale | A collector can be, and needs an SLA on its own freshness |
| A semantic/legal registry | RIHA's job, not the bus's | The system-level record: owner, data composition, legal basis — the half a technical catalogue never holds |

The last row is the one worth flagging to anyone reading this as an
implementation reference. Estonia's discovery story is not the X-tee catalogue;
it is RIHA, and the catalogue is secondary to it. A catalogue of *services* still
leaves the question of which *system* is authoritative for a given dataset
unanswered, and no amount of OpenAPI answers it.

---

## 8. Conformance rows this moves, when the code exists

Recorded here so the plan has an exit condition and the yaml has a diff to expect.
**Every one of these stays where it is until the corresponding task is green.**

| Row | Clause | Today | After |
|---|---|---|---|
| G5.6 | Register — publish description, apply ACL, create catalogue entry | named-absence | implemented |
| S6.2 | Service catalogue | named-absence | implemented, noted as register-derived, not a collector |
| S6a.4 | The entry carries the tier-1 classification | named-absence | implemented |
| S7.6 | `05-services/<code>/` — contract, semantic map, SLA, ACL, catalogue entry | named-absence (two of five) | named-absence (four of five, relocated) |
| S6a.1 | Tier 1 — BB pattern | named-absence | named-absence, now visible per service (R3) |
| GX.3 | Revoke, notify, remove the catalogue entry | named-absence | named-absence — the catalogue half closes by derivation; revocation and notification do not |

A9 is a proposal against v0.3. `path-conformance.yaml`'s `meta.path_document` is
v0.2 and its sha256 is pinned, so **no clause row is added for G5's fourth exit
test until v0.3 is adopted.** That step is Task 7 of the plan, and it is
conditional on a decision this pack does not take.
