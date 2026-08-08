# Acceptance check — any member (generic, not tied to a module number)

**Proves:** the once-only-exchange pattern this pack demonstrates is a property
of the bus's *configuration*, not of the education-sector agencies it happens
to ship with — the same registration, ACL-exactness and purpose-limitation
checks 2.2–2.5 make specifically for PNEA/PLR/PNIA hold for *any* member
`hurl/generate.py` discovers under `configs/member-*/`, canonical or joined.
**Run by:** `scripts/acceptance.sh` (SS admin REST API + `hurl/topology.json`)

- **Given** the pack is deployed (`scripts/deploy.sh`) and seeded
  (`scripts/seed.sh`) — with whatever member set is currently configured, not
  necessarily the canonical four;
- **When** the check reads `hurl/topology.json` (the one topology
  `hurl/generate.py` derives from `configs/` + `manifest.yaml` — nothing
  here is re-derived by hand) and, for **every** subsystem it describes:
  - queries `GET /clients` on the Security Server `hosted_on` names, for that
    subsystem's registration status;
  - for **every** service that subsystem publishes, queries
    `GET /clients/{id}/service-clients` and, for each subject found,
    `GET .../service-clients/{subject}/access-rights`;
- **Then**
  - the subsystem is `REGISTERED` on the server `hosted_on` names — its own
    server if unhosted, the server it names if hosted (member-parameterisation
    Task 2's `hosted_on` resolution, exercised here exactly as configured);
  - the service's granted-subject list equals its config's `access:` list
    **exactly** — no more, no fewer;
  - each granted subject's service-code list equals **exactly** the one
    service being checked — not some other service leaking in via a wider
    grant;
  - a service whose `access:` list is **empty** has **no** subjects at all —
    the same exactness rule applied to the empty case, which is easy to leave
    unchecked because there is nothing to assert *except* absence (`pemis-api`
    was this pack's own example of that, until MoEYS was retired in Wave 3
    Task 1 — this check still proves the empty case rather than assuming it,
    for whichever service happens to grant nobody today).

This is not a fifth acceptance module alongside 2.1–2.6: it is the check 2.2–
2.5 already make, expressed once, generically, over whatever
`hurl/topology.json` says is actually deployed — so a joined member added
through `prompts/member.md` (member-parameterisation Task 8) has a real,
documented acceptance from the moment it exists, not an implicit one nobody
wrote down. 2.6 stays the framework's headline, education-specific check
(`acceptance/2.6.md`) and is not generalised — the once-only exchange it
proves is Progressa's story to tell, not every member's.

**Monitoring add-ons (Wave 5, G-06).** The operational- and environmental-
monitoring add-ons are server-level, not client-level, so this check does not
extend the per-subsystem loop above for them — it checks every Security
Server `hurl/topology.sh`'s `SS_ORDER` names (canonical or a joined member's
own server) via `docker exec <host> supervisorctl status`, asserting both
`xroad-monitor` and `xroad-opmonitor` are `RUNNING`. A joined member that
skips the add-on is exactly the retrofit case the onboarding path warns
against, and because the check is keyed to *server*, not *member*, a hosted
member's host being covered already covers the hosted member — there is no
per-member gap for a hosted join to fall through.

Status: VERIFIED — this check already runs as part of `scripts/acceptance.sh`
(member-parameterisation Task 7) and passes on the live stack, covering the
canonical three today (Wave 3 Task 1 retired MoEYS; Wave 3 Task 4 retired the
`full`/`lite` profile split it used to be verified under both of) and any
joined member added later without a further code change.
