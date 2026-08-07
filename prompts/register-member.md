# Generating prompt — register-member (PNEA, PLR and PNIA)

**Building block(s):** member-pnea, member-plr, member-pnia
**Produces:** `configs/member-pnea/pnea.yaml`, `configs/member-plr/plr.yaml`,
`configs/member-pnia/pnia.yaml`
**Public spec:** NIIS X-Road member and subsystem registration; Security
Server user guide (consumer connection types); OpenAPI 3 service
descriptions; access rights (ACL)

## Problem

Registering a member on the bus is the same shape for every member —
`acceptance/2.2.md` made exactly this point (Topic 5 subtopic 5.4: "the
registration shape is identical for every member") before that module was
retired, and it is why the three modules this one replaces (2.3 PNEA, 2.4
PLR, 2.5 PNIA) were one generation prompt run three times over three
different briefs, not three different prompts. This prompt is that one
generation, parameterised over which of the two shapes below the agency
takes. Precondition for all three: the member has passed the Member
Requirements checklist (Module 5.2; Interop Toolkit); a provider's service
levels are additionally set in a signed SLA (Module 5.3).

## Prompt (copy-paste ready)

```
Below is the NIIS X-Road reference for member/subsystem registration
(including consumer connection types), OpenAPI3 service descriptions and
access rights, the Progressa service brief for <AGENCY>, and — if this
agency provides a service — the relevant entity from the semantic map
[paste all that apply].

Generate the registration configuration as a single YAML document:
(1) member/subsystem identity is frozen in manifest.yaml's identity.members
    block (code, name, subsystem code, subsystem description) — an input to
    this prompt, not something it generates. Do not restate it;
(2) security_server — code and DNS name; optionally hosted_on, if this
    agency will not run its own Security Server, naming the one that
    hosts it instead;
(3) IF this agency only consumes (no services in this slice, e.g. PNEA):
    client.connection_type — how it connects to its own Security Server
    (HTTP for the demonstration, marked demo-only; HTTPS with a client TLS
    certificate for production) — and consumes: the provider services it
    will call, as full X-Road service identifiers;
    IF this agency provides a service (e.g. PLR, PNIA): services — service
    code and OpenAPI3 spec URL only (the forwarding URL, enabled state and
    TLS-verify are not config fields: the Security Server reads the
    forwarding target from the spec's own servers.url and always enables a
    published service explicitly, so a value here would be a second,
    driftable copy); access — the ACL, exactly the consumer subsystems the
    access policy names; and semantic — the entity, key and field list this
    service returns, copied from the semantic map (what this data means on
    the bus — for identity data specifically, the field list is a legal
    decision: only the fields the credential purpose needs, per the
    decree's purpose limitation, never the full record).

Rules: every identifier is [confirm: verify against the live X-Road
registry] — a wrong code silently routes nowhere or to the wrong agency. The
consumes/access lists must use identifiers exactly as frozen in
manifest.yaml — do not restate them from memory. An ACL is a whitelist: list
only who the policy grants, never who might be convenient. Output only the
YAML document.
```

## Inputs / outputs

- **Inputs:** the registration spec(s) + this agency's service brief + (for
  a provider) the semantic map entity + the access policy + the frozen
  identifiers from `manifest.yaml`.
- **Output:** one of `configs/member-pnea/pnea.yaml` (consumer),
  `configs/member-plr/plr.yaml` or `configs/member-pnia/pnia.yaml` (provider)
  for this slice, applied by `scripts/deploy.sh`.

## Safeguard

Two failure modes, one per shape:

- **Consumer (PNEA):** the default connection type (HTTPS) fails the demo
  call unless a client TLS certificate is uploaded — set HTTP for the
  demonstration and flag it demo-only. Do not let this survive into a
  production configuration.
- **Provider (PLR, PNIA):** verify the ACL both ways after deploy — the
  authorised consumer (PNEA:EXAMS) can call, and an unauthorised subsystem
  is denied. An ACL that only proves the happy path is half a check. For
  PNIA specifically — the most sensitive service on the bus — the field
  list is the legal layer: it must match the decree's purpose limitation
  exactly, never the full identity record; a wrong member code here can
  route one citizen's data to a service that asked about another.
