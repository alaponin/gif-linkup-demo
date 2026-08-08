# Generating prompt — a new member joins the bus

**Building block(s):** member-`<key>` (a new one — `<key>` is the agency's
code, lowercased; nothing hardcodes the set of keys this can be, see
`hurl/generate.py`'s `discover_members`)
**Produces:** `configs/member-<key>/<key>.yaml`, plus a new
`identity.members.<key>` entry under `manifest.yaml`
**Public spec:** NIIS X-Road subsystem registration; OpenAPI 3 service
descriptions; access rights (ACL)

## Problem

The canonical four (PDGA, PNEA, PLR, PNIA) are Progressa's curriculum —
modules 2.1–2.6, frozen for KP3/KP4 (MoEYS was a fifth, retired in Wave 3
Task 1; see `docs/production-delta.md`). Everything past those four is this pack's
other claim: that joining the bus is a property of configuration, not of
`generate.py`'s source code. This prompt is that join. Unlike the module-
specific prompts (2.2–2.5), which only ever produce a config file — the
identity of a canonical member already lives in `manifest.yaml` — this one
must produce **both** the config and the `identity.members` entry it points
at, because neither exists yet for a member that isn't there.

## Prompt (copy-paste ready)

```
Below is the NIIS X-Road reference for subsystem registration, OpenAPI3 service
descriptions and access rights, and the Progressa service brief for the agency
joining the bus [paste both].

Generate two YAML documents, separated by a line containing exactly `---`:

DOCUMENT 1 — the identity.members.<key> entry for manifest.yaml (<key> is the
agency's code, lowercased):
(1) code, name — the agency's X-Road member code and full name;
(2) subsystem, subsystem_description — the subsystem code this brief
    registers and what it does;
(3) origin: joined — never canonical. Only a canonical member may appear in
    manifest.yaml's identifiers.members block (the frozen KP3/KP4 cross-pack
    contract); a joined member never enters it, and this prompt never writes
    that block.

DOCUMENT 2 — configs/member-<key>/<key>.yaml:
(1) module: "<key>", building_block: member-<key>;
(2) security_server — code and DNS name (ss-<key>); AND EITHER a services
    block (below) OR hosted_on naming an existing member's Security Server
    DNS name, if this agency will not run its own — never both are absent,
    and hosted_on is the only field that says so; a member with neither
    published services nor a reason to exist should not be joining;
(3) services — for each service this agency publishes: service code, OpenAPI3
    spec URL only (not the forwarding URL, enabled state or TLS-verify — the
    Security Server reads those from the spec's own servers.url and always
    enables what it publishes, so a config copy here would drift), and
    access: the exact consumer subsystems the brief names, in
    PROGRESSA/GOV/<CODE>/<SUBSYSTEM> form. Omit entirely if this agency only
    consumes;
(4) semantic — only if this agency publishes a service another already-
    registered member's config lists in ITS OWN access grants for a
    provenance-tracked exchange (mirrors pnia.yaml's identity example): entity,
    key and field list, copied from the brief's semantic map. The field list
    is a legal decision — only what the stated purpose needs;
(5) member_requirements — Module 5.2's six-item checklist, answered by the
    joining agency, not assumed: has_security_server, has_registered_identity,
    standards_portfolio_adopted, data_conformant (booleans), technical_contact
    (a name), and lawful_basis — only if this agency is consumer-only; a
    provider states its lawful basis per service instead (see (3) above),
    and this field is then omitted here;
(6) for each service in (3): sla — Module 5.3's five terms
    (availability, response_time, support_hours, incident_response,
    change_notice) plus a signatory, reusing the same template for every
    service on the bus. Omit entirely for a consumer-only agency (no
    services to attach one to).

Rules: every identifier is [confirm: verify against the live X-Road registry].
Do not add fields the purpose does not need; do not widen the ACL beyond what
the brief names. Output only the two YAML documents.
```

## Inputs / outputs

- **Inputs:** the NIIS X-Road specs + the joining agency's service brief
  (name, code, subsystem, whether it runs its own server, what it publishes
  and to whom).
- **Output:** append Document 1 under `manifest.yaml`'s `identity.members:`;
  write Document 2 to `configs/member-<key>/<key>.yaml`. Then run
  `python3 hurl/generate.py` — the new member is discovered from the
  directory, not registered anywhere else. `scripts/member.sh list` shows it
  once generated; `scripts/deploy.sh` brings it up.

  This is the by-hand path. The same Document-2 payload (reshaped to the
  join API's `JoinPayload` schema, `apps/join-api/schema.py`) can instead be
  submitted directly to the join API, `POST /requests` (`apps/join-api/
  app.py` — the API has no `/api/join` path prefix despite the design
  spec's §7 calling that its base path; every route hangs directly off the
  app root, confirmed by reading `app.py`, not assumed from the spec). This
  enforces the same two safeguards below (`origin: joined` never optional;
  `hosted_on` naming a real, unhosted host) mechanically rather than by
  reviewer discipline, **plus** three checks a human authoring this file by
  hand has no equivalent of: reachability of the declared backend, no
  operation outside the join policy's `allowed_methods`, and a declared
  backend-auth mechanism (spec §8 checks 9–11). Module 2.7 is this path,
  first-class.

## Safeguard

Two mistakes are specific to a join, not a registration this pack has done
before. First, `origin: joined` is not optional decoration — leaving it off
defaults to `canonical` (per `hurl/check_scenarios.py`'s gate) and a
demonstration join would silently start looking like part of the frozen
KP3/KP4 contract. Second, `hosted_on` is only ever a DNS name another member
already owns; a value nothing owns, or a chain (hosting a member that is
itself hosted), is a hard failure in `generate.py` by design — check the brief
names a real, unhosted host before running this.

There is no `scripts/member.sh add`: running this prompt and committing what
it produces **is** how a member joins. `scripts/member.sh` only reports on
the joined set and retires a member that turns out to be a throwaway (see
`scripts/member.sh remove`) — it never writes member config by hand.
