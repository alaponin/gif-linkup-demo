# KP2 build pack — member join API (design)

**Status:** design drafted 2026-08-01 from a decision session with the user,
extended the same day after testing it against a concrete third-party-backend
scenario (§2), then cut back by an over-engineering pass (§16). **Not yet
approved**, and still larger than any prior plan in this pack — read §15 before
approving; it argues the build should be split into three sequenced plans
rather than attempted as one.

**Goal:** turn "a member joins the Linkup federation" from a human authoring
act — run `prompts/member.md` against an agency brief, commit what it
produces, regenerate, redeploy — into a service call: an authenticated,
schema-validated join request that an operator approves and that converges a
live federation onto the approved configuration, resumably, and can be
reversed.

**What this is not.** It is not a replacement for `prompts/member.md`. The
prompt stays the thing that turns an agency's prose service brief into a
member definition; this API takes the *result* of that work as a structured
payload and does everything after it. Generation stays a design-time act by a
human with a model; the join path stays deterministic (§3, decision 3).

**Explicitly out of scope:**

- Multi-federation or multi-instance joins. One Central Server, instance
  `PROGRESSA`, member class `GOV`.
- Real CA integration. Joins are signed by the Test CA at `http://ca:8888`,
  exactly as the existing scenarios are — the API inherits every demo-only
  caveat in `docs/production-delta.md` and adds several of its own (§13).
- Changing an existing member (rename, re-scope, add a service to a joined
  member). That is the parked "full rename/reuse support" spec in PLAN.md §9,
  and it is a genuinely different problem: joins create, this does not update.
  **See §2.4** — this exclusion has a sharper consequence for a third-party
  backend than it does for this pack's own mocks, and the design carries a
  detection mechanism even though the remedy stays out of scope.
- Joining a **canonical** member. The five (PDGA, MOEYS, PNEA, PLR, PNIA) are
  the frozen KP3/KP4 contract and are deployed by `hurl/run-linkup.sh`, not by
  this API, ever.

---

## 1. What already exists (and why this is smaller than it looks)

A surprising amount of the machinery a join API needs was built by the
member-parameterisation work (2026-07-27) and is live today. Naming it first
keeps the plan honest about what is genuinely new.

| Capability | Where it already lives | Status |
| --- | --- | --- |
| Members discovered from disk, not hardcoded | `hurl/generate.py` `discover_members()` — `configs/member-*/` + `manifest.yaml` `identity.members`, with four loud failure modes for disagreement | Live |
| `origin: joined` vs `canonical`, and the gate that keeps a joined member out of the frozen `identifiers.members` block | `hurl/check_scenarios.py` (line ~177) | Live |
| Hosted members (`hosted_on`), including rejection of unknown hosts and hosting chains | `generate.py` `resolve_hosted_on_map()` | Live |
| Deterministic scenario-number and host-port allocation for a member nothing pins | `_allocate_numbers()`, `allocate_ports()`, `FRESH_SS_SCENARIO_START=40`, `FRESH_SERVICE_SCENARIO_START=50`, `FRESH_PORT_START=7000`, `FORBIDDEN_PORT_RANGE` | Live |
| Compose service + volumes + healthcheck for a joined member that owns its own Security Server | `generate.py` writes `hurl/compose.members.yml`; `scripts/lib-stack.sh` adds it to `COMPOSE` when present (lines 135–137) | Live |
| A generic per-member acceptance check | `acceptance/member.md`, run by `scripts/acceptance.sh` over `hurl/topology.json` — registration status, exact ACL equality, empty-ACL exactness | Live, VERIFIED under both profiles |
| Retiring a joined member's config | `scripts/member.sh remove` — refuses canonical, deletes the directory and the `identity.members` entry, regenerates | Live |
| A hosted member's correct admin-API ordering | `build_hosted_client()` — client-add before SIGN-key generation before registration, two ordering bugs found live 2026-07-26 | Live |
| Server-side-only admin credentials behind an HTTP service, with a request-boundary guard | `apps/console/app.py` (`ADMIN_USER`/`ADMIN_PASSWORD` from env, never in a response or log; `X-KP2-Console` header + Origin check) | Live |
| Reversible live mutation with a persisted journal and a watchdog | `apps/console/journal.py` — ACL grants/revokes, reset on demand, on start, and on 120s no-heartbeat | Live |

**What is genuinely new**, and is what this spec is about:

1. A request/approval lifecycle with durable state (§4).
2. A **resumable, idempotent step engine** over the admin-API sequence, where
   today there is one all-or-nothing Hurl run (§5).
3. Bringing a Security Server up **in place**, on a federation that is already
   running, rather than only at cold deploy (§6, §7).
4. Live **de-registration** — which the pack has never done; today the only
   way to remove a member from a running federation is `teardown.sh --purge`
   (`member.sh remove` says so explicitly) (§10).
5. Admitting a backend **this pack did not author** (§2) — the requirement
   that shapes validation, the config schema, and the acceptance check.

---

## 2. The joining member's backend is not ours to author

This section exists because the design was tested against a concrete scenario
before being approved, and the scenario changed several requirements. It is
the part of the spec most worth reading closely, because it is where the join
API stops resembling anything the pack already does.

### 2.1 The scenario

**Progressa Tertiary Scholarship Board (PTSB)** wants to join. It is not one
of the canonical five and is not part of the KP3/KP4 contract. It already runs
a **Joget DX** app — `scholarship`, built by a systems integrator, backed by
Joget's own database — exposing an API designed in Joget's **API Builder**,
which produces an OAS3-compliant description. PTSB wants to:

1. **consume** `PROGRESSA/GOV/PNIA/IDENTITY/identity-api` and
   `PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api`, to pre-fill scholarship
   applications rather than asking the learner again — the same once-only
   argument Module 2.6 makes;
2. **publish** `awards-api` so PNEA can verify that a candidate holds a
   scholarship award before issuing a certificate.

This is exactly the join the API is for: a real new member, arriving with a
real backend, wanting both directions.

### 2.2 Why KP2's existing Joget seam does not cover it

The pack's stated seam is that "the member systems are mocks behind stable
OpenAPI contracts — the seam where KP4's Joget DX apps plug in later without
touching the X-Road configuration" (`README.md`). That works because **KP2
authors the contract**: `apps/specs/pnia-identity.openapi.yaml` is written by
this pack, pins `servers.url` to `http://app-pnia:8000/v1`, exposes exactly one
`GET`, and documents purpose limitation in its own description field. Joget
swapping in behind that contract changes nothing.

A *joining* agency inverts this. PTSB's Joget app brings **its own** contract,
generated by a tool, describing whatever operations an app designer happened to
build, with a `servers.url` Joget derives from its own system settings.
Everything in §2.3–§2.6 follows from that inversion, and none of it arises for
the canonical five.

### 2.3 Service-level access rights over a tool-generated spec

X-Road parses endpoints automatically from an OpenAPI 3 service description,
and those parsed endpoints **cannot be manually updated or deleted**. Access
rights can be set at two levels: **service level**, which applies to *all* the
endpoints of the REST service, and **endpoint level**, which grants specific
endpoints only.

The pack's config schema has only the service level. `configs/member-*/`'s
`services[].access[]` is a list of consumer subsystems per service *code*, and
`hurl/generate.py` turns it into
`POST /clients/{id}/service-clients/{subject}/access-rights` — one grant,
covering the whole service.

For the canonical mocks this is harmless, because they are authored to expose
one read operation each. A Joget API Builder API backing a form is not shaped
like that: a form-backed API typically exposes list, read, create, update and
delete. If PTSB's `awards-api` spec carries a `DELETE /awards/{id}`, then
publishing it and granting PNEA service-level access **grants PNEA the ability
to delete scholarship awards** — from a config file whose `access:` list looks
exactly like PNIA's, and which a reviewer would read as "PNEA can check
awards".

This survives the pack's own safety net. `acceptance/member.md` asserts, for
every service, that the granted-subject list equals the config's `access:` list
exactly, and that each subject's service-code list is exactly the one service.
Both are true here. **Nothing in the check looks at endpoints**, because until
now no service had more than one.

Compounding it: the documentation is explicit that endpoint-level access rights
**support only service-based access rights management**, not the
service-client-based management the pack's checks read via
`GET /clients/{id}/service-clients/{subject}/access-rights`. So this is not a
field to add to the YAML and a line to add to `generate.py` — it is a second
access-rights management model, with a different API shape and a different
acceptance assertion.

**Decision: a joined member may publish read-only services.** The join policy
(§8) carries `allowed_methods: [GET]`, enforced at submission by fetching the
candidate spec and rejecting any description declaring a non-`GET` operation.
This is defensible on its own terms — a federation that admits third-party
write endpoints on day one is not a federation anyone should teach — cheap, and
testable in `--fast` against a fixture. Adding `endpoints:` to the config
schema and accepting the second management model is the principled fix and is
**deferred**, noted here so a later reader knows it was considered rather than
missed. Silently over-granting and documenting it is not an option: an
over-grant that the acceptance suite certifies as correct is worse than either.

### 2.4 `servers.url` is Joget's to set, not the joiner's

The pack's config deliberately carries no forwarding URL. `2.5.yaml` says so in
a comment, and `prompts/member.md` repeats the reasoning: the Security Server
reads the target from the spec's own `servers.url`, so "a config copy here
would drift". Correct — for a spec this pack writes.

Joget generates its OAS3 document, including the servers URL, from its own
configuration. In practice that resolves to whatever base URL the Joget
instance is configured with — a browser-facing hostname
(`https://scholarships.progressa.gov/jw/api/...`), or, on a hastily set up
instance, a `localhost` default. The Security Server container can resolve
neither: the first is outside the `linkup` bridge network, and the second
resolves to the Security Server itself.

**The failure mode is the reason this is a design requirement and not a
runbook note.** The service description is fetched successfully at publish
time, so the publish step succeeds. The service is enabled. The ACL is granted
correctly. The subsystem is `REGISTERED`. The job reaches `ACTIVE`, every step
in the console is green, and `acceptance/member.md` passes every assertion it
makes — because all of them read registry state (§2.6). The first real call
then fails at forwarding, after the demonstration has been declared working.

**Decision:** at submission the API fetches the candidate spec, parses
`servers.url`, and *resolves and connects to it from inside the `linkup`
network*, rejecting the join with a message naming the URL it could not reach
(§8, check 9). One HTTP request, before approval, turning a post-hoc mystery
into a rejection an integrator can act on. The payload also carries an explicit
acknowledgement that the base URL the spec advertises is the one the Security
Server should forward to — because when this goes wrong the fix is in the
joining agency's own system settings, not in anything this pack controls, and
the error message should say so.

**Spec drift, and why detection is in scope even though updates are not.** For
a hand-authored mock, an OpenAPI description changes when a developer edits a
file and commits. For a Joget app it changes when someone edits a form in a
browser. X-Road does not follow: a service description is reloaded only when
explicitly **refreshed**, at which point the Security Server re-reads the file
and compares it against existing services, warning if the composition of
services changed; existing services' settings are preserved across the refresh.

So a joined third-party member drifts from its published description silently,
in two directions:

- **New endpoints appear on refresh and inherit the service-level ACL.** An app
  designer adding a delete operation widens what PNEA can do, with no join, no
  approval, and no config change. This is §2.3 arriving through the back door,
  and it is the more likely of the two paths.
- **Removed endpoints leave X-Road serving a stale description**, so the
  federation advertises a capability the backend no longer has.

Updating a joined member stays out of scope. **Detecting** the drift does not:
`scripts/member.sh drift <key>` re-fetches the spec and diffs its endpoint set
against what was published at join time — the job context (§5.4) already
records everything needed for the comparison. A script rather than an API
endpoint, for the reasons in §7: drift is a report about a member, `member.sh`
is where this pack keeps those, and a report that runs when the join API is
down is more useful than one that does not.

### 2.5 Backend authentication has to live somewhere

X-Road does not authenticate to the provider's information system on the
consumer's behalf. Headers travel from consumer to provider essentially
unmodified — the protocol specifies a small set the Security Server rewrites
and passes the rest through — and JWT tokens as an authentication method
*between* the Security Server and the information system are explicitly not
supported.

Joget's API Builder authenticates API calls with `api_id` and `api_key`
headers, managed through a Manage API Key menu, with optional domain and IP
whitelisting.

A joining Joget provider therefore has three options:

1. **The consumer sends the provider's `api_key`.** This works and is the first
   thing an integrator will try. It is also a disaster: PNEA would hold PTSB's
   API key, the key would appear in PNEA's own configuration, and the
   federation's whole point — that authorisation is X-Road's ACL, asserted
   between authenticated members — is replaced by a shared secret.
2. **The backend network-allowlists the Security Server** and requires no key
   from the caller, so the Security Server is the only thing that may reach the
   backend and X-Road's ACL is the authorisation decision. This is the correct
   answer, and API Builder supports the whitelisting needed to express it.
3. **A reverse proxy between the Security Server and the backend injects the
   credential.** Also correct, more moving parts, and the usual answer when the
   backend cannot be changed.

The pack has never needed to record which is in force, because its mocks have
no authentication at all — plain HTTP inside the network, already flagged in
`docs/production-delta.md`. A joining member must declare it: the config gains
a `backend: { auth: none | network_allowlist | proxy_injected }` block (§9),
enforced the way `generate.py`'s `check_policy()` already enforces the bus's
own policy block — "a block the generator silently ignores is worse than no
block at all: it reads as configuration and is decoration."

### 2.6 Registry state is not evidence that a member works

Every "Then" clause in `acceptance/member.md` is a registry-state read: the
subsystem is `REGISTERED` on the server `hosted_on` names; the granted-subject
list equals the config's `access:` list exactly; each subject's service-code
list is exactly the one service; an empty `access:` list has no subjects. All of
it is read through the Security Server's admin API. **Nothing invokes the
service.** The only check in the pack that makes a real cross-server call is
2.6, which is deliberately education-specific and deliberately not generalised
("the once-only exchange it proves is Progressa's story to tell, not every
member's").

For a mock this pack authors, registry state is a fair proxy for "it works" —
the backend is three containers the pack also wrote. For a third-party backend
it is not a proxy for anything: §2.4 in particular produces a member that is
registry-perfect and functionally dead.

**Decision:** 2.7's acceptance includes one real `r1` call (§12). Not a
generalisation of 2.6 — one call, asserting reachability, not asserting an
exchange's semantics.

### 2.7 A consuming member joins to a closed door

Joget consumes X-Road services through its **API Connector Builder**, which
takes an OAS3 document, lets the designer pick endpoints, and configures
headers — including the `X-Road-Client` header the REST protocol requires as
its one mandatory header. So a Joget app as a *consumer* is feasible, and is
the more natural first step for PTSB: pre-filling an application form is a
smaller ask than publishing an awards service.

`prompts/member.md` already supports a consume-only member ("Omit entirely if
this agency only consumes"). But the useful direction for a consumer is the
other one: PTSB needs PNIA and PLR to *grant it access*, which means editing
`configs/member-pnia/2.5.yaml` and `configs/member-plr/2.4.yaml` — existing
members, which the join API explicitly cannot touch.

So a consume-only join succeeds, reaches `ACTIVE`, and produces a member that
is registered, can reach the global configuration, and is denied by every
provider it cares about until a human edits two other members' configs and
redeploys.

**This is correct and must stay correct.** A provider deciding who may call it
is the provider's decision; an API that let a joining member grant itself
access to PNIA's identity service would be a serious design error. But the
outcome has to be legible, or the headline demonstration ends with a member
that does nothing. **Decision:** the payload carries a `requested_access:`
block — recorded, validated for resolvability, surfaced to the operator at
approval and in the console as an explicit follow-up ("PTSB requests access to
`identity-api`; PNIA must grant it"), and **never acted on** by the API. This
keeps the authorisation model honest while making the join's outcome visible,
and sets up a good teaching moment about who owns an authorisation decision in
a federation.

---

## 3. Decisions taken

Twelve decisions, taken with the user 2026-08-01. Numbered for reference by
the implementation plans. The further decisions §2 introduces are recorded in
their own subsections and are not renumbered into this list.

1. **Both config and convergence.** The API writes member config and then
   converges the live federation onto it. Config stays the source of truth;
   the API closes the loop.
2. **Request → review → approve.** A join is a durable request in a `PENDING`
   state until a federation operator approves it. Provisioning starts on
   approval, never on submission. This mirrors X-Road's own management-request
   model, which the pack already approves explicitly rather than via
   `auto-approve-*` flags (`configs/x-road-bus/2.1.yaml`
   `policy.management_request_approval: explicit`, enforced by `generate.py`
   `check_policy()`).
3. **Structured JSON, schema-validated.** The request body is a typed payload,
   not prose. `prompts/member.md` becomes the way to *author* that payload;
   no model runs inside the join path.
4. **First-class module 2.7.** Config artefact, generating prompt, acceptance
   check, manifest entry — the join is part of the taught curriculum, not a
   demo aside like `apps/console`. See §12 for what that costs.
5. **Resumable idempotent steps.** A failed job records the last completed
   step and resumes there. No rollback-on-failure, no restart-from-zero.
6. **Own Security Servers are supported**, brought up in place on the running
   stack — but by a host-side agent, not by the API (decision 8).
7. **The API writes files; a human commits.** The API never touches git.
8. **Split privilege: API container + host executor.** Docker never sits
   behind an HTTP port. Reasoning in §6 — this is the decision the user asked
   to be made on realism grounds, and it changes how the whole thing reads.
9. **Symmetric DELETE.** A joined member can be un-joined through the API,
   live, including de-registration on the Central Server. Canonical members
   never.
10. **Two roles, bearer tokens** — applicant and operator, issued by
    `scripts/gen-secrets.sh` into `.env`.
11. **The console gets an approval-queue tab.** Operators see pending
    requests, the generated config diff, and live job progress.
12. **Per-step Hurl fragments, not a second implementation.** The admin-API
    sequence stays defined once, in `hurl/generate.py`'s templates. §5 is
    entirely about how to make that survive contact with decision 5.

---

## 4. The join lifecycle

**Seven states, and only four of them are provisioning.** An earlier draft had
nine provisioning states alone — one per milestone (`CONFIG_WRITTEN`,
`CS_REGISTERED`, `SS_PROVISIONED`, `CS_APPROVED`, …) — before counting the
terminal ones. That was a second representation of progress alongside the step
list the job context already persists (§5.4), and two representations of one
fact can disagree. They are collapsed: **the request has a state; progress
inside it is the id of the last completed step.**

| State | Meaning | Leaves when |
| --- | --- | --- |
| `SUBMITTED` | Payload accepted and persisted; validation (§8) runs synchronously | Validation passes → `APPROVED` is now possible; fails → `REJECTED` |
| `REJECTED` | Terminal. Carries the name of the check that failed | — (resubmission is a new request) |
| `APPROVED` | Operator has approved; the job may start | The first step runs → `RUNNING` |
| `RUNNING` | Steps executing; `last_completed_step` advances | All steps done → `ACTIVE`; a step fails → `FAILED`; a step needs the member's own server → `BLOCKED` |
| `BLOCKED` | Waiting on the joining member's side — its Security Server must exist and be healthy (§6). **Own-server joins only**; a `hosted_on` join never enters this state | The server answers its healthcheck → `RUNNING` |
| `ACTIVE` | Registered, published, ACLs granted, and the `r1` reachability call passed (§2.6, §12) | `DELETE` → `RETIRING` → `RETIRED` (§10) |
| `FAILED` | Carries the failing step id and the last error. Resumable from that step (§5) | Operator resumes → `RUNNING` |

`ACTIVE` carries a boolean `verified`. A member that registered and published
but whose reachability call has not yet passed — the propagation window is
real, up to ~30s for the proxy's authorisation decision — is
`ACTIVE, verified: false`, not a distinct state. It is one fact about a member,
not a place in the lifecycle.

**The split that matters is not technical — it is who is acting**, and that is
now a property of each *step* (`actor: operator | member`) rather than of the
request. This is the better home for it: it is true per step, and the console
can colour a step list by actor without the lifecycle having to encode it.
Every step of a `hosted_on` join has `actor: operator`; an own-server join has
a run of `actor: member` steps in the middle, which is exactly what `BLOCKED`
exists to wait for.

For a **consume-only** member, `ACTIVE` means registered and able to reach the
global configuration — not able to call anything, until the providers it names
in `requested_access:` grant it (§2.7). The console and the API response both
say so rather than reporting an unqualified success.

**Why `BLOCKED` is the honest state, not the embarrassing one.** In a
real X-Road federation the operator does not provision the member's Security
Server and could not if it wanted to — the member installs it on its own
infrastructure, uploads the anchor, generates its own keys, obtains certs from
an approved CA, and submits registration requests that arrive at the CS for
approval. This pack collapses that boundary only because a single host happens
to hold every Security Server's admin credentials. Modelling state 6 as a real
wait, satisfied by an actor outside the API, is therefore *more* faithful to
X-Road than a join API that provisions everything would be. The demo satisfies
it with a host agent; a production deployment satisfies it with an email and a
fortnight. PTSB (§2.1) is the clean illustration: it runs its own Joget app
*and* would run its own Security Server, and neither is the operator's to
touch.

---

## 5. The step engine, without a second implementation of the sequence

This is the hard part, and the constraint the user chose (per-step Hurl
fragments, decision 12) is what makes it tractable.

### 5.1 The problem

Today `hurl/generate.py` emits whole scenario files — `build_ss_file()`,
`build_hosted_client()`, `build_service_file()` each produce one monolithic
body, concatenated by `hurl/run-linkup.sh` into a single Hurl invocation
because **Hurl captures do not cross file boundaries**. That is fine for cold
deploy: it is one transaction, all or nothing, with `--retry 12
--retry-interval 10000` absorbing propagation delay.

It is useless for resume. A job that failed at cert import cannot re-run the
file from the top — it would re-initialise a token that is already
initialised. And reimplementing the sequence in Python would create a second
definition of it that drifts from the Hurl one, which is exactly the failure
mode `PLAN.md` §5 already refused once ("a second, weaker copy of the headline
check is worse than none").

### 5.2 The shape

Refactor `generate.py`'s templates into a **step registry**. Each step is:

```python
Step(
    id="ss.auth_key_csr",
    template="ss_auth_key_csr.hurl",   # existing template text, unchanged
    actor="operator",                           # or "member" — see §4
    requires=["session_xsrf", "ca_name"],       # variables injected in
    provides=["auth_key_id", "auth_csr_id"],    # captures read back out
    probe=None,                                 # only where 409 is ambiguous — §5.3
)
```

**No `reverse` field yet.** An earlier draft gave every step a reversal
template for DELETE's benefit. That forces whoever writes each of the ~30
forward steps in Plan A to also design its reversal, before anyone has written
or tested a single one — and X-Road's de-registration sequences are the least
understood part of this spec (§10). The field is added in Plan C, when the
reversal sequences are known and the step ids they attach to are stable.
Deferring it costs one mechanical pass over the registry later; adding it now
costs thirty speculative decisions.

Two consumers of the same registry:

- **`generate.py` (cold deploy, unchanged output).** Concatenates the steps'
  rendered bodies in order, exactly as today. **Hard constraint: the generated
  scenario set must be byte-identical before and after this refactor.**
  `tests/test_golden.py` is the golden corpus that proves it, and this pack has
  used precisely this constraint before
  (`2026-07-26-deployment-spec-and-lite-profile-design.md` §2: "a diff of the
  full-profile generated scenarios before/after this change must be empty").
  If the golden test goes red, the refactor is wrong.
- **The join API (per-step execution).** Renders one step to a temporary
  fragment, injects `requires` as Hurl `--variable` arguments from the job
  context, runs Hurl with a JSON report, parses the captures named in
  `provides` out of it, and persists them back into the job context.

The sequence therefore has exactly one definition. Hurl remains the executor
in both paths — the API is not a second X-Road client, it is a scheduler over
the same fragments.

### 5.3 Idempotence: 409 by default, probes by exception

An earlier draft gave **every** step a probe — a read that answers "has this
already happened?" — with the engine running `probe → (skip | act)`, and
justified it by three payoffs: resume, idempotence, and DELETE. Two of those
three do not need it.

- **Resume does not need probes.** The job context already persists
  `last_completed_step` (§5.4). That record is authoritative for the runs *this
  API performed*, which is the case resume actually serves. Probing to
  rediscover what we already wrote down is a second source of truth for a fact
  we own.
- **Idempotence mostly does not need probes either.** X-Road returns `409` for
  already-in-that-state operations, and this pack already treats a `409` on ACL
  grant or revoke as success rather than failure (`PLAN.md` §11) — proven
  behaviour, live, not a hope.
- **DELETE does need them**, because the reversal path is inspecting state that
  something else may have changed. That is Plan C.

So the default is **409-as-success**, and a step carries a probe only where
`409` is genuinely ambiguous — where the same status could mean "already done"
or "conflicts with something unrelated". Those steps are enumerated in the
registry with a one-line reason each, rather than every step paying for a
second admin-API round trip and a second template to maintain.

The one case resume genuinely cannot cover: a step whose request **succeeded on
X-Road but whose result was never persisted**, because the process died in
between. On resume that step re-runs — which is exactly what 409-as-success is
for. If a step is neither `409`-safe nor probe-covered, it must not be in the
registry; that is a rule the step-registry tests can enforce mechanically.

This still matters more for a joining member than for the canonical five.
Publishing a service description is a network operation against a backend the
federation does not control (§2); a step that can be safely re-run after the
joining agency's application was briefly unreachable is the difference between
resume and teardown.

**Measured, not predicted (join-a plan Task 5, 2026-08-01):** the audit this
section anticipated has now run against the full registry. Of 21 steps: 3
read-only, 10 `409`-safe (either a natural conflict on repeat, or genuinely
idempotent), **8 ambiguous**, 0 unsafe to repeat. 8 of 21 (38%) is more than
this section's "genuinely ambiguous" framing suggested as the exceptional
case — worth recording plainly rather than let the original "rare" framing
stand uncorrected. It is still short of "most of them" (the threshold this
plan's own risk note set for treating the finding as invalidating this
section's approach), so **the 409-as-success default stands**; it just pays
for a probe more often than expected. Two distinct failure modes drove the
count, only one of which this section anticipated:

- **A submit-then-approve pair whose completion can diverge** (`ss.bringup_register`,
  `ss.client_register`) — this section's anticipated case: a resumed retry's
  submit half may cleanly `409` while the approval half is still outstanding,
  so treating "any `409`" as "step done" is not safe here.
- **A step that creates a new resource with no natural uniqueness
  constraint** (`cs.signing_keys`, `cs.trust_services`, `ss.auth_key_csr`,
  `ss.sign_key_csr`, `ss.tsa_post`, and the compound `ss.mgmt_register`) — a
  repeat does not `409` **at all**; it silently creates a second key/CA/TSA
  entry. Arguably the harder case of the two, since there is no status code
  to even be ambiguous about — the absence of a signal is the problem, not a
  confusing signal. This section's "ambiguous 409" framing did not name this
  failure mode; `hurl/steps.py`'s classification comments do.

All 8 probes were written from the Central/Security Server OpenAPI specs and
then **run live** against a real deployed federation (the pinned `hurl`
image, `docker run` directly against a live Central Server and Security
Server) rather than left as spec-derived guesses: one endpoint assumption was
wrong (`cs.signing_keys`'s probe first assumed a `GET /configuration-sources`
list that does not exist on the Central Server; its own signing keys are on
its token instead, `GET /tokens`) and was corrected from the live response.
A second finding worth carrying into Plan B: under the lite profile's
`hosted_on` pattern, a shared host's token carries one identically-labelled
SIGN key **per hosted member** — `ss.sign_key_csr`'s probe cannot use the key
label alone to decide "has this member's key already been generated"; it
must correlate by the certificate's `owner_id`. See
`hurl/templates/fragments/PROBE_SS_SIGN_KEY.hurl.tmpl`'s comment.

Class (d) — unsafe to repeat at all — is empty; `tests/test_steps.py`
enforces this stays true.

### 5.4 The job context is the join's record

Everything threaded between steps is persisted, per request, under
`out/join/<request-id>.json` — the same `OUT_DIR` convention
`apps/console/journal.py` already uses for `out/console-acl-journal.json`.

Contents: the validated payload; the current state; every completed step with
its timestamp; every capture (`ca_name`, key ids, CSR ids, cert hashes,
management-request ids, client ids); the endpoint set parsed from the member's
service description at publish time (the baseline the drift check in §2.4
compares against); and the last error. This file is what makes resume, DELETE,
drift detection and the console's progress view possible, and it is
inspectable by a human when something goes wrong at 2am during a recording.

**It is also a secret-handling surface.** Captures include certificate hashes
and key identifiers but must never include the token PIN, the admin password,
or a session token. The existing console security pass (`PLAN.md` §11) found
zero credential leakage into responses, logs, or the ACL journal; the join
job context must be held to the same standard, and its test suite must assert
it explicitly rather than by inspection.

### 5.5 Propagation is a wait state, not a failure

Global-conf generation and distribution takes minutes; the proxy's actual
authorisation decision can lag the admin API's own read by ~30s (a
server-conf cache effect, `PLAN.md` §11); and a Test CA OCSP response goes
stale after roughly ten hours idle, failing every cross-server call through
the server with `Server.ClientProxy.SslAuthenticationFailed` (`PLAN.md` §8).

The step engine must distinguish **not yet** from **failed**. It does that with
**one retry budget for the whole run**, matching what `hurl/run-linkup.sh`
already does successfully (`--retry 12 --retry-interval 10000`) — not a
per-step budget. A per-step budget is a tuning surface with one knob per step,
every knob a guess, before anyone has measured a single step's real
distribution. Steps get an individual override only once a measurement
justifies one, and the override carries the measurement in a comment.

A step that exhausts the budget reports `FAILED` with its id and the last
response observed, which is what a human needs to decide between resume and
abandon. A join attempted against a
federation that has been idle overnight should fail at its first cross-server
step with a message that names the OCSP staleness explicitly, not with a
generic TLS error — this is the single most likely way a live demo of this
module breaks.

---

## 6. Privilege: why the API does not touch Docker

The most realistic arrangement is the one where **the API never provisions the
member's Security Server at all**, for the reason given in §4: in a real
federation, it doesn't.

That reasoning happens to also give the best security posture, which is how
you can tell it is the right answer rather than a convenient one. Mounting the
Docker socket into a service that listens on an HTTP port is root-equivalent
on the host; doing it in a pack whose own README says "demo only — never
production" would be defensible but would make the module teach the wrong
lesson.

### 6.1 Two components

**`apps/join-api/`** — a FastAPI service in the compose stack under
`profiles: ["demo"]`, alongside `console`. No Docker socket. Holds
`XROAD_ADMIN_USER`/`XROAD_ADMIN_PASSWORD` and `XROAD_TOKEN_PIN` from `.env`
server-side only, on the `linkup` network so it can reach `cs`, `ca` and every
`ss-*` on `:4000` — and so it can perform the `servers.url` reachability check
of §2.4 from the same network position the Security Server will forward from.
Owns: the request lifecycle, validation, config writing, the step engine, and
the job context. It can run every step in states 5, 7, 8 and 9 — because in
this demo it happens to hold every server's credentials.

**`scripts/join-agent.sh <key>`** — a host-side script, run by the operator
when the console says a join is `BLOCKED`, holding no credentials of its own.
It:

1. checks the allocated host ports are actually free (`generate.py` already
   refuses the AirPlay range 5000–5099 and 7000 by construction — this is the
   check for everything else on a particular developer's machine);
2. runs `docker compose -f docker-compose.yml -f hurl/compose.members.yml up
   -d --wait --wait-timeout <n> ss-<key>` — the compose fragment and its
   healthcheck are **already generated** by `generate.py` (§1), so this adds
   no new topology code.

That is the whole agent. **No work-order queue and no callback.** An earlier
draft gave the API a `GET /work-orders` / `POST /work-orders/{id}/complete`
pair and had the agent poll and report — a small job queue, with its own
authenticated callback path, to communicate a fact the API can observe
directly. The API already has to poll the new Security Server's `:4000`
healthcheck before it can drive any admin call against it. That poll *is* the
completion signal: when the server answers, the request leaves `BLOCKED` on its
own. A callback would be a second, less reliable way of learning the same
thing.

The agent is the demo's stand-in for the joining agency's own infrastructure
team. That framing goes in the production delta verbatim (§13). Note it also
now reads as what it is — a one-line `docker compose up` a human runs, which is
a more honest depiction of "the member stands up its own server" than a queue
worker would be.

### 6.2 The `hosted_on` fast path

A member joining with `hosted_on` set — which the README already recommends as
the default for a single-host demo, since it costs zero extra containers and
sidesteps every own-server finding in `docs/production-delta.md` — passes
through the lifecycle without ever entering `BLOCKED`, and never involves the
agent at all. This is the path the acceptance check should exercise (§12), the one a
demo should use unless it specifically needs to show a server being stood up,
and — per the sizing note in §12 — the only one that fits alongside a
third-party backend on a 16 GB host.

---

## 7. API surface

Base path `/api/join`. All requests require `Authorization: Bearer <token>`
and the same request-boundary guard the console already applies (a custom
header, plus an Origin check when one is present — `apps/console` request-
boundary work, S12/S13).

**Applicant token** (`KP2_JOIN_APPLICANT_TOKEN`):

| Method | Path | Effect |
| --- | --- | --- |
| `POST` | `/requests` | Submit a payload. Validation (§8) runs synchronously → `SUBMITTED` or `REJECTED`. Returns `201` with a request id. |
| `GET` | `/requests/{id}` | State, `last_completed_step`, last error. |

**Operator token** (`KP2_JOIN_OPERATOR_TOKEN`):

| Method | Path | Effect |
| --- | --- | --- |
| `GET` | `/requests` | The queue, filterable by state. Each entry carries the config diff the join would write, computed at submission. |
| `POST` | `/requests/{id}/approve` | → `APPROVED`; starts the job. `202`. |
| `POST` | `/requests/{id}/reject` | → `REJECTED`, with a reason. |
| `POST` | `/requests/{id}/resume` | Re-run from `last_completed_step`. Only from `FAILED`. |
| `DELETE` | `/members/{key}` | Un-join (§10). Joined members only. **Plan C.** |
| `GET` | `/health` | Liveness; no auth. |

Twelve endpoints became eight. What came out, and why:

- **`GET /requests/{id}/diff`** — a separate endpoint to render a diff of files
  the API itself just wrote. The diff is computed once at submission and
  carried on the request, where the operator is already looking at it.
- **`GET /work-orders`, `POST /work-orders/{id}/complete`** — the job queue and
  callback, replaced by the health poll the API already performs (§6.1).
- **`GET /members/{key}/drift`** — real value (§2.4), wrong shape. Drift
  detection is a *report about a member*, and this pack already has one place
  for those: `scripts/member.sh`. It becomes `scripts/member.sh drift <key>`,
  sitting next to `list` and `remove`, needing no auth, no HTTP surface and no
  token — and reachable when the join API is not running, which is most of the
  time.
- **Applicant request scoping** (`own request only`) — enforcing it means
  binding each request to the token that submitted it and checking ownership on
  read. In a demo where one person is both applicant and operator, that is
  machinery guarding a boundary nobody crosses. The two roles stay (decision
  10) because the *asymmetry* is the teaching point — an applicant cannot
  approve — but an applicant may read any request. Real per-agency scoping
  arrives with per-agency credentials, not before.

**Deliberately absent:** any endpoint that mutates a canonical member, any
endpoint that grants a joining member access to someone else's service (§2.7),
any endpoint that commits to git, any endpoint that deploys or tears down the
federation as a whole. The join API's blast radius is exactly one member.

**Token model, and its limit.** One applicant token shared by all applicants
is a demo simplification, and the production delta must say so: a real
federation issues one credential per agency, and this pack's own teaching
material (X-Road's whole identity model) makes that obvious enough that
glossing it would be conspicuous. Per-agency mTLS is the honest production
answer and was considered and deferred — recorded here so the plan does not
have to rediscover it.

---

## 8. Validation: the join policy is configuration

Module 2.7's config artefact is `configs/x-road-bus/2.7.yaml` — the join
policy. This keeps the module inside the pack's own claim that the config *is*
the deployment, rather than burying the rules in `apps/join-api/`'s source.

```yaml
module: "2.7"
building_block: x-road-bus
join:
  member_class: GOV          # Progressa's federation admits government bodies only
  approval: explicit         # a human operator approves; never automatic
  default_hosting: hosted_on # a join defaults to hosting; own_server must be asked for
  allowed_methods: [GET]     # a joined member publishes read services only (§2.3)
```

`generate.py`'s existing `check_policy()` refuses to generate from a config
that declares a policy the generator does not apply — "a block the generator
silently ignores is worse than no block at all: it reads as configuration and
is decoration." The join API must be held to the same rule: every key in the
`join:` block is enforced, or it does not appear in the file.

**An earlier draft of this block failed that rule three times**, which is worth
recording because the failure mode is seductive — each key reads as
policy-shaped:

- `max_services: 4` — an arbitrary cap solving no stated problem. Deleted. If
  a joining member publishing five services is a real concern, the concern
  should be written down first and the number derived from it.
- `require_semantic_for_provenance: true` — a boolean that is always `true`,
  gating a check (§8.8) that can only assert the block is present and
  non-empty. A key that cannot be set to `false` is not configuration; the
  rule is unconditional and belongs in the check, not the file.
- `backend_auth: [none, network_allowlist, proxy_injected]` — the *set of
  permissible values* for a field, which is a schema concern. Listing an enum
  in a policy file invites someone to shorten it and expect enforcement that
  does not exist. The enum lives in the payload schema; §8.11 checks membership.

What survives is four keys, each of which a federation operator could
plausibly want to set differently and each of which is genuinely enforced.

Checks at submission, in order:

1. **Schema** — the payload's shape and types.
2. **Key derivation** — `key == code.lower()`, because `discover_members()`
   already enforces exactly that and fails loudly otherwise.
3. **Collision** — no existing `configs/member-<key>/`, no existing
   `identity.members.<key>`, no Security Server DNS or code clash.
4. **Not canonical** — the key is not one of the frozen five; `origin` is
   forced to `joined` and is never taken from the payload.
5. **Member class** — matches the policy.
6. **Hosting** — either `hosted_on` names a Security Server an existing,
   *itself-unhosted* member owns (`resolve_hosted_on_map()`'s two hard
   failures, applied at request time rather than at generate time), or the
   payload explicitly asks for its own server.
7. **ACL sanity** — every `access:` subject, and every `requested_access:`
   target, resolves to a subsystem that exists in `manifest.yaml`, in
   `PROGRESSA/GOV/<CODE>/<SUBSYSTEM>` form.
8. **Purpose limitation** — if the member publishes a service another member's
   config lists in a provenance-tracked exchange, a `semantic:` block is
   required, and its field list is checked against the declared purpose. This
   is the one check that is a legal judgement rather than a syntactic one, and
   the API can only enforce that it is *present and non-empty*; the human
   approving is enforcing the rest. The approval UI should say so —
   and with a tool-generated specification (§2.2) the human's share of this
   check gets *larger*, not smaller.
9. **Backend reachability** — fetch the declared `spec_url`, parse
   `servers.url`, and resolve and connect to it from inside the `linkup`
   network. Reject naming the URL that could not be reached (§2.4).
10. **Allowed methods** — the fetched description declares no operation outside
    the policy's `allowed_methods` (§2.3).
11. **Backend auth declared** — `backend.auth` is present and is one of the
    values the payload schema permits: `none`, `network_allowlist`,
    `proxy_injected` (§2.5).
12. **Identifier character restrictions** — `code`, `subsystem` and every
    service code satisfy X-Road's identifier restrictions, checked here with
    the restriction cited in the rejection message rather than discovered deep
    inside certificate signing where it looks like a CA problem. This pack has
    already been bitten in the same neighbourhood: a comma in MoEYS's
    `member_name` broke X-Road's server-side DN construction, which is why
    `hurl/generate.py` has `dn_escape()`. A service code copied from a
    third-party tool's human-facing API name is a plausible source of spaces,
    dots or slashes.

A failure at any of these returns `REJECTED` with the specific check named.
Rejection is cheap and reversible — resubmission is a new request.

---

## 9. Config and git

On `APPROVED`, before any live mutation, the API writes:

- `configs/member-<key>/<key>.yaml` — the same shape `prompts/member.md`
  produces today; `security_server.code`/`dns_name`, optional `hosted_on`,
  `services[]` with `code`, `spec_url` and `access[]`, optional `semantic`,
  plus the two blocks §2 adds: `backend: { auth: … }` and, where the member
  consumes, `requested_access:`. Note what it must *not* write: `type`,
  forwarding URL, `enabled`, `tls_verify` — `generate.py` never reads them and
  a config copy would drift (`configs/member-pnia/2.5.yaml`'s own comment says
  so).
- `manifest.yaml` `identity.members.<key>` — with `origin: joined`. **Never**
  `identifiers.members`, which is the frozen KP3/KP4 cross-pack contract;
  `check_scenarios.py` already fails if a non-canonical member appears there,
  and the API must not be the thing that discovers this at generate time.
- then runs `python3 hurl/generate.py`, which re-derives the scenarios,
  `topology.json`, `topology.sh` and `compose.members.yml` from disk.

Then it stops touching the repository. The diff is returned by
`GET /requests/{id}/diff` and shown in the console; a human commits it.

**The known gap this leaves**, stated plainly because it will bite someone:
between the config-writing step and the operator's commit, the federation is live
with configuration that exists only in a working tree. If the machine is
reset, the member is registered on the Central Server and nothing on disk
says so. Two mitigations, both cheap, both required:

- The API refuses to start a job when `git status --porcelain configs/
  manifest.yaml` is already dirty, so a join never stacks on top of
  uncommitted work whose provenance is unclear.
- `GET /requests` marks any `ACTIVE` member whose config is uncommitted, and
  the console surfaces it as a warning. "Live but uncommitted" is a visible
  state, not a silent one.

---

## 10. DELETE — un-joining, live

`DELETE /api/join/members/{key}`, operator token, joined members only. This is
the part of the design with the least prior art in the pack: today
`member.sh remove` explicitly does *not* touch a running federation, and
`teardown.sh --purge` is the only way to clear live state.

Reversal walks the job context's completed steps **backwards**. Plan C adds the
two per-step fields this needs and that Plan A deliberately does not carry
(§5.2): a `reverse` template, and a `probe` on every step the reversal touches.
The probe matters far more here than in the forward direction — the thing being
reversed may have been changed by hand since, and a reversal that assumes the
forward path completed cleanly will fail in the most confusing possible way.
This is the case that justifies probes at all (§5.3), and it is why they are
introduced here rather than everywhere.

Order, mirroring §4 in reverse:

1. Revoke ACLs, then disable and delete service descriptions.
2. Unregister the subsystem. **Note this is itself a management request** that
   the Central Server must approve — X-Road's deletion path goes through the
   same approval gate as registration, so the un-join inherits the same
   asynchrony and the same explicit-approval policy. Pleasing symmetry;
   also more waiting.
3. Delete the client on the Security Server; delete its SIGN key and cert.
4. Remove the member and subsystem on the Central Server.
5. If the member owned a server: a work order for the host agent — `docker
   compose stop`/`rm` the container and remove its three named volumes
   (`kp2-<key>-db`, `kp2-<key>-conf`, `kp2-<key>-archive`, per
   `compose.members.yml`'s generated `volumes:` block).
6. Delegate the config removal to `scripts/member.sh remove <key>` rather than
   reimplementing it — it already deletes the directory, strips the
   `identity.members` entry, refuses canonical members, and regenerates. The
   API calls it; it does not duplicate it.

Steps 1–5 are new admin-API sequences with no existing coverage anywhere in
the pack, and every one of them needs a Hurl fragment, a probe and a live
test. **This is the single largest and least-derisked piece of work in the
spec**, which is why §15 sequences it last and alone.

---

## 11. Console: the approval queue

A fourth tab in `apps/console`, consistent with what is there: the console
talks only to a backend, never to X-Road directly (here, to the join API
rather than to its own `xroad.py`), and it stays outside `manifest.yaml` and
outside `scripts/acceptance.sh`.

Shows: the pending queue; a request's validated payload and the config diff it
would write; approve/reject, with the reason box; live job progress as the step
list with the current step named and each step coloured by its `actor`; failed
jobs with their error and a resume button; the "live but uncommitted" warning
from §9; and any `requested_access:` follow-ups a join left open (§2.7).

Drift status (§2.4) is deliberately *not* here: it is a report produced by
`scripts/member.sh drift`, not a live view, and putting it in the console would
mean the console re-fetching third-party specifications on a timer.

Two things it must do that the ACL tab did not have to:

- **The join API's tokens must not reach the browser.** The console holds them
  server-side, exactly as it holds `XROAD_ADMIN_PASSWORD` today, and exposes
  its own already-guarded endpoints to the page.
- **Escape everything.** The stored-XSS finding from the console security pass
  — provider field values interpolated unescaped into `innerHTML` — applies
  with more force here, because a join payload is attacker-supplied by
  construction (an agency name, a service code, a rejection reason). The
  existing escaping helper is applied at every call site; the join tab must
  use it from the first commit, not acquire it in a later security pass.

The console's ACL journal is untouched by any of this. A join is not a
console mutation and must never enter that journal or be reversed by its
watchdog — the journal's contract is "the console's own ACL changes", and
widening it silently would break the guarantee `scripts/acceptance.sh` relies
on when it refuses to run against a dirty journal.

---

## 12. Module 2.7 and how it gets tested

Manifest entry, following 2.6's precedent for a module with no scenario of its
own:

```yaml
  - id: "2.7"
    video_ref: "?"   # see below — no existing Topic 5 subtopic covers this
    title: "A new member joins the bus — the join API"
    building_blocks: [x-road-bus]
    config: configs/x-road-bus/2.7.yaml
    scenarios: ""   # the join API runs step fragments; no static scenario file
    prompt: prompts/2.7.md
    acceptance: acceptance/2.7.md
```

**`video_ref` is an open decision, not an oversight.** Every existing module
maps to a Topic 5 subtopic: 2.1 → 5.5, 2.2–2.5 → 5.4, 2.6 → 5.6. 5.7 is *not*
available — it is "From demonstration to production", which
`docs/production-delta.md` already realises. So a first-class 2.7 either
extends 5.4 (registering a member — which is what a join *is*, just
automated), or the video bundle needs a new subtopic. This belongs on the same
Tuesday call as the existing video-calibration item in `PLAN.md` §9 (the
"four Security Servers" vs. five discrepancy), and should be raised before the
module is built rather than after it is filmed around.

`scenarios: ""` needs checking against `check_scenarios.py`'s claim logic
before it is written — 2.6 sets it, so the empty case is supported, but 2.7
differs in that its steps *do* correspond to real fragments, just
dynamically-rendered ones. Resolve by reading the claim logic during planning,
not by assuming (this is the same trap the lite-profile design flagged and was
right to flag).

**Acceptance (`acceptance/2.7.md`)**, given/when/then like the rest:

- **Given** a deployed, seeded federation and a valid join payload for a
  member with `hosted_on` set;
- **When** it is submitted, approved, and the job runs to `ACTIVE`;
- **Then** the subsystem is `REGISTERED`; its services are published and
  enabled; its granted-subject list equals its `access:` list exactly; **a real
  `r1` call through the consumer Security Server against the joined member's
  service returns 2xx**, and the same call from a subsystem outside the ACL is
  denied; the config and manifest on disk describe the member that is actually
  live; and `DELETE` returns the federation and the working tree to their prior
  state — with `hurl/topology.json` byte-identical to before the join, which
  `_allocate_numbers()`'s determinism already guarantees and Task 9 of the
  member-parameterisation plan already proved for the config-only case.

The `r1` clause is not optional decoration and not a generalisation of 2.6: it
is the only assertion in the whole suite that would catch §2.4's
registry-perfect-but-dead member. Everything else 2.7 asserts is registry
state, and §2.6 explains why that is not evidence for a backend this pack did
not write.

Note this leans on `acceptance/member.md` rather than restating it: the
generic per-member check already asserts registration and exact-ACL
properties for *any* discovered member. 2.7 asserts the **join and un-join
transitions** plus reachability; `member.md` asserts the resulting registry
state. Do not duplicate.

**Tier placement** — the pack's `--fast` is ~16s and `--full` is ~918s full /
~370s lite, and 2.7 must not wreck either:

| Tier | What 2.7 contributes | Cost |
| --- | --- | --- |
| `--fast` | Step-registry unit tests; the byte-identical golden test (`tests/test_golden.py`) proving the refactor changed no generated output; validation/policy tests including the twelve checks of §8 against fixture specs (§2.3's method check and §2.4's URL parse are pure functions over a fetched document); job-context secret-leakage tests; step engine driven against **recorded fixtures** — `scripts/capture-xroad-fixtures.sh` and `scripts/mkfixture.py` already exist for exactly this, and `apps/console/tests/fixtures/xroad/` is the precedent | Seconds. No containers. |
| `--live` | One `hosted_on` join to `ACTIVE` including the `r1` call, then `DELETE`, against a running stack | Minutes — the propagation waits are real and cannot be mocked away |
| `--full` | One own-server join, exercising the host agent and `compose.members.yml` bring-up | Several minutes plus a container; **`--full` only**, and the plan must measure the new totals rather than estimate them |

The `--live` number is the one to watch. If a hosted join costs more than
about two minutes, `--live` stops being the "run it when a task is done" tier
it is documented as, and the honest response is to move 2.7's live check
behind a flag rather than to quietly let `--live` become expensive.

**Sizing, when the joining member brings a real backend.** Measured in this
pack, not estimated: the full topology is ~13 GB steady state on a 16 GB VM
with roughly 3 GB headroom, at ~2.0–2.3 GB per Security Server; lite is
~8.9 GB. A Joget DX instance is a Java servlet application plus a database —
budget 1.5–2.5 GB for the pair before any load.

| Configuration | Rough total |
| --- | --- |
| full + third-party backend | ~15 GB — no headroom on a 16 GB host |
| full + backend + joined member's own Security Server | ~17 GB — **does not fit** |
| lite + backend, member `hosted_on` `ss-plr` | ~11 GB — comfortable |
| lite + backend + own Security Server | ~13 GB — fits, tight |

So a demonstration with a real third-party backend is a **lite-profile,
`hosted_on`** scenario, and own-server joins (Plan C) and a real backend cannot
both be shown on a 16 GB host. The spec already recommends `hosted_on` on two
other grounds (§6.2); this is the third, and the one that will actually decide
what gets recorded. Better to own it now than to discover it the morning of a
recording.

---

## 13. Production delta additions

`docs/production-delta.md` gains, at minimum:

1. **The operator does not provision the member's server.** The host agent
   (§6.1) simulates the joining agency's own infrastructure team. In
   production, `BLOCKED` is satisfied by the member, on the member's
   hardware, with the member's own CA-issued certificates — and takes days,
   not seconds.
2. **Shared applicant token.** One credential for all applicants; production
   issues one per agency, and should prefer mTLS (§7).
3. **Test CA in the join path.** A joining member's AUTH and SIGN certs are
   signed by `http://ca:8888` with no identity vetting whatsoever. In
   production this step is the entire trust decision.
4. **Backend authentication is `none` in the demo, and this is not guidance.**
   The pack's mocks accept plain HTTP from anywhere on the `linkup` network. A
   real joining member must use `network_allowlist` or `proxy_injected`; the
   consumer must never hold the provider's API credential (§2.5).
5. **A joined member's service description is never automatically refreshed.**
   X-Road reloads a description only on explicit refresh, so a third-party
   backend drifts silently from what the federation publishes. The API detects
   drift (§2.4); nothing in this pack remedies it.
6. **Read-only services only.** The join policy admits `GET` operations
   (§2.3). A production federation that admits write endpoints needs
   endpoint-level access rights and a different acceptance assertion.
7. **Live-but-uncommitted window.** §9's gap, and the two mitigations, stated
   as a known limitation rather than left implicit.
8. **No rate limiting, no quota.** The join API will happily accept as many
   requests as it is given.
9. **Job context on local disk.** `out/join/` is not durable, not replicated,
   and not access-controlled beyond filesystem permissions.

---

## 14. Docs to update

- `README.md` — the paragraph asserting "there is no `scripts/member.sh add`,
  because writing member config by hand is exactly what this pack is
  demonstrating you don't need to do" stays *true* and gets sharper: there is
  still no `add`, and now there is an API. The recommendation to default a
  joining member to `hosted_on` gains two more reasons (§6.2, §12 sizing).
- `prompts/member.md` — its "Inputs / outputs" section currently ends at
  "commit what it produces". It should note that the payload it produces can
  also be submitted to the join API, and that the API enforces the same two
  safeguards the prompt warns about (`origin: joined`, `hosted_on` validity)
  plus the backend checks of §8.
- `runbook.md` — how to run the join API and the host agent; the
  OCSP-staleness trap as it manifests in a join (§5.5); how to recover a
  `FAILED` job; and one line noting that a third-party backend's path prefix
  (Joget serves under `/jw/`, plus app and version segments) combined with
  X-Road's `r1` form produces a long consumer-side URL — unwieldy, not
  misconfigured.
- `scripts/member.sh` — gains `drift <key>` (§2.4) alongside `list` and
  `remove`, and its usage text gains a sibling that is still not `add`.
- `hurl/README.md` — the step registry and what per-step fragments mean for
  the "captures don't cross file boundaries" limitation, which stops being a
  quirk and becomes a load-bearing part of the design.
- `PLAN.md` §9 — record 2.7 and its sequencing.

---

## 15. Sequencing: this should be three plans, not one

Every prior plan in this pack has been one focused change verified live. This
spec is materially larger, and the honest recommendation is to split it:

**Plan A — the step registry, no API.** Refactor `generate.py`'s templates
into steps with `requires`/`provides`/`probe`. Ship the byte-identical golden
test. No new service, no new endpoint, nothing user-visible. This is the
riskiest change to the VERIFIED path and the one that benefits most from
landing alone, where a red golden test means exactly one thing.

**Plan B — the join API, hosted members only.** The lifecycle, validation
including all twelve checks of §8, config writing, the step engine, the two
tokens, the console tab, `member.sh drift`, module 2.7 and its acceptance
including the `r1` call. No own-server joins, no DELETE, no `reverse` field, no
probes beyond the enumerated ambiguous cases. This is the plan that delivers
the claim — "a member joins by calling an API" — end to end, and it is enough
to demonstrate a real third-party backend joining (§2) on the lite profile.

Plan B is still the largest of the three, and §16's pass was aimed squarely at
it: eight endpoints instead of twelve, four provisioning states instead of
nine, no job queue, no per-step probes, no per-step wait budgets, four policy
keys instead of seven.

**Plan C — own servers and un-joining.** The host agent, in-place compose
bring-up, and the whole of §10. The least-derisked work, sequenced where a
failure does not block the headline capability.

Plan A is a prerequisite for B; C depends on both. If only one ships, B is the
one that makes the module true.

**Written up as three plans, 2026-08-01:**

- `docs/superpowers/plans/2026-08-01-kp2-join-a-step-registry.md` — 5 tasks
- `docs/superpowers/plans/2026-08-01-kp2-join-b-api.md` — 6 tasks
- `docs/superpowers/plans/2026-08-01-kp2-join-c-own-server-and-unjoin.md` — 5 tasks

Plan C opens with a **spike**, not an implementation: live de-registration has
no precedent in this pack or in upstream's `setup.hurl`, which only ever builds
a federation up. Its remaining tasks are written against what the spike is
expected to find and are to be revised against what it actually finds. Plan C
also carries an explicit stop condition — if clean live de-registration turns
out not to be achievable on 7.7.0, `DELETE` is dropped rather than half-shipped.

---

## 16. Simplification pass (2026-08-01)

The design above has been through an over-engineering review and **the body
already reflects its outcome** — this section records what was cut and why, so
a later reader can tell an omission from an oversight, and can overrule a call
that turns out to be wrong.

The recurring pattern was not gratuitous complexity. It was **building the
general case before the specific one existed**: a field for every step because
one step might need it, a state for every milestone because progress must be
representable, a queue because two components must communicate. Each was
locally reasonable and collectively expensive.

### 16.1 Removed

| What | Was | Now | Why |
| --- | --- | --- | --- |
| Lifecycle states | 9 provisioning states, plus terminals | 4 provisioning states (7 total), plus `last_completed_step` | Two representations of progress can disagree. The step list already carried it (§4) |
| `ACTIVE(unverified)` | A distinct state | `ACTIVE, verified: false` | A parenthetical state name is a boolean wearing a costume |
| `reverse` per step | On every step, from Plan A | Added in Plan C | Forces ~30 speculative reversal designs before one is written or tested (§5.2) |
| `probe` per step | On every step | Only where `409` is ambiguous | Resume is served by the persisted step record; idempotence by 409-as-success, which this pack has already proven live (§5.3) |
| Wait budgets | Per step | One per run, overridden only on measurement | One knob per step, every knob a guess (§5.5) |
| `GET /work-orders`, `POST /work-orders/{id}/complete` | A job queue with an authenticated callback | Deleted | The API must poll the new server's healthcheck anyway; that poll *is* the completion signal (§6.1) |
| `GET /requests/{id}/diff` | An endpoint | A field on the request | An endpoint to render a diff of files the API itself just wrote |
| `GET /members/{key}/drift` | An endpoint | `scripts/member.sh drift` | Drift is a report about a member; `member.sh` is where this pack keeps those, and it works when the API is down (§7) |
| Applicant request scoping | "Own request only" | Any request readable; approval still operator-only | Binding requests to tokens to guard a boundary that, in a demo, one person stands on both sides of |
| `max_services: 4` | Policy key | Deleted | An arbitrary cap solving no stated problem (§8) |
| `require_semantic_for_provenance: true` | Policy key | Deleted | Always `true`; a key that cannot be `false` is not configuration (§8) |
| `backend_auth: [...]` | Policy key listing an enum | Payload schema | The permissible values of a field are a schema concern (§8) |

Net: **twelve endpoints → eight; nine provisioning states → four; seven policy
keys → four**; two per-step fields deferred; one queue and one callback path
deleted.

### 16.2 The finding worth being embarrassed about

The join policy block invented three keys that `generate.py`'s own
`check_policy()` would have rejected — the function that exists precisely to
refuse "a block the generator silently ignores... it reads as configuration and
is decoration." The spec quoted that rule two paragraphs above the block that
broke it three times.

The lesson generalises past this document: **policy-shaped YAML is the easiest
over-engineering to write and the hardest to notice**, because every key looks
like governance. The test that catches it is mechanical — *can this key be set
to another value, and does something observably change?* — and it should be
applied to `configs/x-road-bus/2.7.yaml` in review, not after.

### 16.3 Kept, though a reviewer might flag them

Not everything elaborate is over-engineered. These were examined and survived:

- **The step registry itself.** It is the most machinery in the design, and it
  earns it: without it there are two definitions of the admin-API sequence, and
  `PLAN.md` §5 already recorded what that costs ("a second, weaker copy... the
  two drift and the weaker one passes").
- **The byte-identical golden test.** A constraint, not a feature, and the only
  thing standing between the Plan A refactor and a silent change to the
  VERIFIED path.
- **Two token roles.** The *asymmetry* — an applicant cannot approve — is the
  teaching point and costs almost nothing once scoping is dropped. What was
  removed was the expensive half.
- **Twelve validation checks (§8).** Long list, but each rejects a specific
  live failure, four of them found by testing the design against a real
  third-party backend (§2). Cheap, synchronous, `--fast`-testable.
- **The three-plan split (§15).** More process than a single plan, and correct:
  Plan A touches the VERIFIED path and deserves to fail alone.

### 16.4 Arguable — flagged for the approver

Two calls could reasonably go the other way, and both are cheap to reverse:

1. **Dropping applicant request scoping.** It weakens decision 10's story if
   the module is ever demonstrated with genuinely separate applicant and
   operator actors. Restoring it is a `submitted_by` field and one comparison —
   but doing it *later* means no request written in the meantime carries the
   field.
2. **Moving drift to `member.sh`.** If the console is the only surface an
   operator ever looks at, a report they have to run in a terminal is a report
   nobody runs. The counter-argument is that drift is not a live property and
   polling third-party specifications from a demo UI is worse.

---

## Open questions the plans must resolve

1. **`scenarios: ""` for a dynamic module.** Read `check_scenarios.py`'s claim
   logic; do not assume 2.6's precedent covers 2.7 (§12).
2. **Where step fragments are written at runtime.** `hurl/scenarios/` is
   regenerated wholesale and never committed; per-request fragments must not
   collide with it or with a concurrent `generate.py` run. Likely
   `out/join/<request-id>/steps/`, but confirm against how
   `run-linkup.sh` and `--out-dir` already partition this.
3. ~~**Hurl JSON capture extraction.**~~ **RESOLVED (join-a plan Task 5,
   2026-08-01), live.** Ran the exact pinned image
   (`ghcr.io/orange-opensource/hurl:latest@sha256:d7727dcc…`) with
   `--report-json` against a real deployed Central Server, a small
   login-then-probe `.hurl` file. Confirmed: `report.json`'s
   `entries[].captures` is an array of `{"name": ..., "value": ...}` pairs,
   one entry per `[Captures]` block, present per-request — exactly the
   granularity §5.2's design needs ("runs Hurl with a JSON report, parses
   the captures named in `provides` out of it"). Plan B's executor can parse
   this directly; no further spike needed before that plan starts.
4. **Concurrency.** Two joins approved at once touch `manifest.yaml`,
   `configs/`, and `generate.py`'s output simultaneously. The console's
   `_MUTATE_LOCK` is the precedent, with its own documented limit (one
   process, not distributed). Simplest defensible answer: the API serialises
   jobs — one active join at a time, others queued — and says so.
5. **`ACTIVE` requires the acceptance call to pass.** Resolved in favour of
   yes (§2.6, §12): the `r1` reachability call runs as the final step, and a
   member that registers but cannot yet be called is `ACTIVE, verified: false`
   rather than a failed job — a real and temporary state, and naming it is
   better than either lying or failing. What the plan must still settle is the
   **budget**: the proxy's authorisation decision can lag the admin API by
   ~30s, so too short a budget makes `verified: false` the normal outcome and
   the flag stops meaning anything.
6. **Own-server port allocation on a running host.** `generate.py` allocates
   deterministically and refuses the AirPlay range, but cannot know what else
   is listening on a particular machine. The agent probes before bring-up
   (§6.1) — decide whether a busy port is a job failure or a re-allocation,
   noting that re-allocation breaks the determinism that Task 9's
   byte-identical check depends on.
7. **Endpoint-level access rights.** §2.3 defers them and admits read-only
   services instead. If a later programme needs a joined member to publish a
   write service, this reopens as a genuine piece of design work — a second
   access-rights management model, service-based rather than
   service-client-based, with its own acceptance assertion.

---

## Sources

X-Road behaviour asserted in §2 comes from the published documentation, not
from this pack's own code — the point of that section is to test the design
against X-Road as documented rather than against the five mocks the pack
happens to author.

- [Security Server User Guide](https://docs.x-road.global/Manuals/ug-ss_x-road_6_security_server_user_guide.html) — OpenAPI 3 service descriptions, automatic endpoint parsing, service-level vs endpoint-level access rights, service description refresh behaviour
- [X-Road: Message Protocol for REST](https://docs.x-road.global/Protocols/pr-rest_x-road_message_protocol_for_rest.html) — `X-Road-Client` as the one mandatory header, header pass-through and rewriting, identifier character restrictions
- [X-Road: Service Metadata Protocol for REST](https://docs.x-road.global/Protocols/pr-mrest_x-road_service_metadata_protocol_for_rest.html)
- [Security Server Architecture](https://docs.x-road.global/Architecture/arc-ss_x-road_security_server_architecture.html)
- [X-Road v6.22.0 Release Notes](https://nordic-institute.atlassian.net/wiki/spaces/XRDKB/pages/4915602/X-Road+v6.22.0+Release+Notes) — introduction of OpenAPI 3 REST publication and fine-grained REST access rights
- [Joget API Builder](https://kb.joget.org/jw/community/display/marketplace/API+Builder) — OAS3-compliant API generation, API key management
- [Joget API Properties](https://kb.joget.org/jw/community/display/marketplace/API+Properties) — `api_id` / `api_key` headers, domain and IP whitelisting
- [Joget API Connector Builder](https://kb.joget.org/jw/community/display/marketplace/API+Connector+Builder) — consuming an OAS3 document, selecting endpoints, header configuration

---

## Self-review notes

- **Grounding:** every mechanism cited in §1 was read in the repository during
  drafting — `generate.py` (`discover_members`, `resolve_hosted_on_map`,
  `allocate_ports`, `_allocate_numbers`, `check_policy`, the
  `compose.members.yml` writer), `scripts/member.sh`, `scripts/lib-stack.sh`
  lines 131–145, `manifest.yaml`, `configs/member-pnia/2.5.yaml`,
  `apps/specs/pnia-identity.openapi.yaml`, `acceptance/member.md`,
  `apps/console/app.py`, `scripts/console.sh`, `scripts/gen-secrets.sh`,
  `hurl/check_scenarios.py`. Not from memory.
- **Two review passes are folded in, not appended.** §2 came from testing the
  design against a third-party backend; §16 from an over-engineering review.
  Both revised the body rather than sitting beside it as findings lists, which
  is the only form in which a review of an *unimplemented* design is worth
  keeping — there was no artefact to review, so there is no record to preserve,
  only a better design or a worse one.
- **§2 is design, not a review.** It began as a separate review document
  (`docs/notes/reviews/2026-08-01-join-api-joget-review.md`, now merged and deleted)
  written by testing this design against a third-party-backend scenario before
  approval. Nothing in it was implemented, so there was no artefact to review
  and no reason to keep two documents that must be read together. Its findings
  are requirements here — in §2's decisions, §8's checks 9–12, §9's config
  shape, §12's `r1` clause and sizing table, and §13's entries 4–6.
- **Unverified assumptions, flagged not hidden:**
  - Open question 3 — Hurl's JSON report capture output is the load-bearing
    mechanism of §5.2 and has not been tested at step granularity. If it does
    not hold, decision 12 collapses and the choice between a Python step engine
    and threaded captures reopens. Prove it in a spike before committing to
    Plan A.
  - §2's X-Road claims are documentation-derived and were not tested on a live
    stack. The two most consequential — that service-level access rights apply
    to every parsed endpoint, and that a description is reloaded only on
    explicit refresh — should be confirmed against the running 7.7.0 stack
    early in Plan B, since §2.3 and §2.4's decisions both rest on them.
  - The sizing figures in §12 for a third-party backend are budgeted, not
    measured. The Security Server and profile numbers are measured; the
    backend's are not.
- **Scope discipline:** updating an existing member, multi-instance joins, and
  real CA integration are excluded and named. The rename/reuse spec parked in
  PLAN.md §9 is adjacent but genuinely separate — joins create, renames
  update.
- **Where this design says "no":** no git commits from the API, no Docker
  behind an HTTP port, no model in the join path, no second implementation of
  the admin-API sequence, no widening of the console's ACL journal, no join
  that can touch a canonical member, no join that grants itself access to
  another member's service, no write endpoints from a joined member. Each is a
  decision that could have gone the other way and is recorded here so a later
  reader does not relitigate it by accident.
