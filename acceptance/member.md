# Acceptance check — any member (generic, not tied to a module number)

**Proves:** the once-only-exchange pattern this pack demonstrates is a property
of the bus's *configuration*, not of the four education-sector agencies it
happens to ship with — the same registration, ACL-exactness and purpose-
limitation checks 2.2–2.5 make specifically for MoEYS/PNEA/PLR/PNIA hold for
*any* member `hurl/generate.py` discovers under `configs/member-*/`, canonical
or joined.
**Run by:** `scripts/acceptance.sh` (SS admin REST API + `hurl/topology.json`)

- **Given** the pack is deployed (`scripts/deploy.sh`) and seeded
  (`scripts/seed.sh`) — with whatever member set is currently configured, not
  necessarily the canonical four;
- **When** the check reads `hurl/topology.json` (the one topology
  `hurl/generate.py` derives from `configs/` + `manifest.yaml` +
  `deployment.yaml`'s profile — nothing here is re-derived by hand) and, for
  **every** subsystem it describes:
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
    unchecked because there is nothing to assert *except* absence
    (`pemis-api` is this pack's own example: it publishes nothing consumable
    and grants nobody, and this check is what actually proves that rather
    than assuming it).

This is not a fifth acceptance module alongside 2.1–2.6: it is the check 2.2–
2.5 already make, expressed once, generically, over whatever
`hurl/topology.json` says is actually deployed — so a joined member added
through `prompts/member.md` (member-parameterisation Task 8) has a real,
documented acceptance from the moment it exists, not an implicit one nobody
wrote down. 2.6 stays the framework's headline, education-specific check
(`acceptance/2.6.md`) and is not generalised — the once-only exchange it
proves is Progressa's story to tell, not every member's.

Status: VERIFIED — this check already runs as part of `scripts/acceptance.sh`
(member-parameterisation Task 7) and passes on the live stack under both the
`full` and `lite` profiles, covering the canonical four today and any joined
member added later without a further code change.
