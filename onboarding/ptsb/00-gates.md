# Onboarding gates

One row per gate: what this member has, and the file that proves it. An
absence named here is a gate that has not been passed, whatever the calendar
says -- naming it is the point, not an oversight.

Statuses are **implemented**, **simulated**, **named absence** and **out of
scope**, defined in `docs/path-conformance.md`. That file states the pack's
status as a whole; this one is this member's own record.
`docs/production-delta.md` describes what each absence and simulation below
would need in production.

| Gate | Exit test | Accountable | Status |
| --- | --- | --- | --- |
| Member Requirements (5.2) | Checklist stated by the applicant | Operating Authority | **implemented** -- [`02-requirements.md`](02-requirements.md) |
| SLA (5.3) | Signed SLA per published service | Operating Authority | **implemented** -- [`03-sla/`](03-sla/) |
| Registration (5.4) | Subsystem registered, ACL granted | Operating Authority | **implemented** -- [`05-registration.md`](05-registration.md) |
| Application (G0) | Application + signed membership agreement; Technical Focal Point and, where personal data flows, a DPO | Operating Authority | **named absence** -- not implemented in this demo |
| Admission (G1) | Minuted admission decision | Steering Committee | **implemented** -- decided outside this system; reference recorded in [`01-admission.md`](01-admission.md) |
| Hosting (G2) | Own Security Server vs hosted as a client; hosting compatible with the member's role | Operating Authority | **implemented** -- [`05-registration.md`](05-registration.md)'s hosting row |
| Certificates (G3) | CA/TSA issuance record, member-verified | Operating Authority | **simulated** -- the Test CA signs any CSR presented, with no identity vetting; the own-server/hosted key asymmetry is real |
| Platform conformance (G4) | Add-ons installed; monitoring data arriving centrally | Operating Authority | **implemented** -- add-ons confirmed per server (`acceptance/member.md`); **named absence** for central monitoring collection |
| Service conformance (G5) | Contract, SLA and ACL registered; a live response carries exactly the fields the contract declares | Operating Authority | **implemented** -- SLA (above) and ACL ([`05-registration.md`](05-registration.md)) recorded here; contract and field conformance checked at join time, not copied into this record; catalogue entry per published service: [`04-catalogue/`](04-catalogue/); **named absence** for the tier-1 BB pattern register |
| Go-live (G6) | Monitored first production transactions | Operating Authority | **named absence** -- not implemented in this demo |
| Retirement (GX) | Absent everywhere; message-log records retained for the statutory period | Operating Authority | **simulated** -- `99-retirement.md` written at exit, and the absence asserted (`acceptance/join-member.md`); **named absence** for statutory message-log retention, which demo teardown does not meet |

`01-admission.md` is written by the join API at approval; canonical members
predate it, so their records begin at `02`.
