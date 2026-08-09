# Onboarding gates

Every row is a gate exit (`docs/decisions/onboarding-alignment-design.md`'s P2: a named
absence teaches as well as an implementation). A missing file means the gate
has not been passed, whatever the calendar says.

Statuses use `docs/path-conformance.md`'s vocabulary; that file is
authoritative for the pack as a whole -- this table is this member's own
record.

| Gate | Exit test | Accountable | Status |
| --- | --- | --- | --- |
| Member Requirements (5.2) | Checklist stated by the applicant | Operating Authority | **implemented** -- [`02-requirements.md`](02-requirements.md) |
| SLA (5.3) | Signed SLA per published service | Operating Authority | **out of scope** -- no services published, and a consumer-only member has none (TK-IO-09 is written for providers; the onboarding path's own §8 open question 5) |
| Registration (5.4) | Subsystem registered, ACL granted | Operating Authority | **implemented** -- [`05-registration.md`](05-registration.md) |
| Application (G0) | Application + signed membership agreement; Technical Focal Point and, where personal data flows, a DPO | Operating Authority | **named absence** -- not implemented in this demo, see `docs/production-delta.md` |
| Admission (G1) | Minuted admission decision | Steering Committee | **named absence** -- not implemented in this demo, see `docs/production-delta.md` |
| Hosting (G2) | Own Security Server vs hosted as a client; hosting compatible with the member's role | Operating Authority | **implemented** -- [`05-registration.md`](05-registration.md)'s hosting row |
| Certificates (G3) | CA/TSA issuance record, member-verified | Operating Authority | **simulated** -- the Test CA signs any CSR presented, with no identity vetting; the own-server/hosted key asymmetry is real. See `docs/production-delta.md` |
| Platform conformance (G4) | Add-ons installed; monitoring data arriving centrally | Operating Authority | **implemented** -- add-ons confirmed per server (`acceptance/member.md`); **named absence** for the central collector -- see `docs/production-delta.md` |
| Service conformance (G5) | Contract, SLA and ACL registered; a live response carries exactly the fields the contract declares | Operating Authority | **implemented** -- SLA (above, when published) and ACL ([`05-registration.md`](05-registration.md)) recorded here; contract and field conformance checked at join time but not copied into this record; catalogue entry per published service: nothing published, so nothing to catalogue; **named absence** for the tier-1 BB pattern register -- see `docs/production-delta.md` |
| Go-live (G6) | Monitored first production transactions | Operating Authority | **named absence** -- not implemented in this demo, see `docs/production-delta.md` |
| Retirement (GX) | Absent everywhere; message-log records retained for the statutory period | Operating Authority | **simulated** -- written at exit by the API (`99-retirement.md`, once retired) and the absence half is asserted (`acceptance/join-member.md`); **named absence** for message-log retention, which demo teardown does not meet -- see `docs/production-delta.md` |

`01-admission.md` is written by the join API at approval; canonical members
predate it, so their records begin at `02`.
