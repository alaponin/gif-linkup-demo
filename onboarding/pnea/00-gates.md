# Onboarding gates

Every row is a gate exit (`docs/decisions/onboarding-alignment-design.md`'s P2: a named
absence teaches as well as an implementation). A missing file means the gate
has not been passed, whatever the calendar says.

| Gate | Exit test | Accountable | Status |
| --- | --- | --- | --- |
| Member Requirements (5.2) | Checklist stated by the applicant | Operating Authority | [`02-requirements.md`](02-requirements.md) |
| SLA (5.3) | Signed SLA per published service | Operating Authority | no services published -- a consumer-only member has none (TK-IO-09 is written for providers; the onboarding path's own §8 open question 5) |
| Registration (5.4) | Subsystem registered, ACL granted | Operating Authority | [`05-registration.md`](05-registration.md) |
| Application (G0) | Application + signed membership agreement; Technical Focal Point and, where personal data flows, a DPO | Operating Authority | not implemented in this demo -- see `docs/production-delta.md` |
| Admission (G1) | Minuted admission decision | Steering Committee | not implemented in this demo -- see `docs/production-delta.md` |
| Hosting (G2) | Own Security Server vs hosted as a client; hosting compatible with the member's role | Operating Authority | passed -- [`05-registration.md`](05-registration.md)'s hosting row |
| Certificates (G3) | CA/TSA issuance record, member-verified | Operating Authority | not implemented in this demo -- see `docs/production-delta.md` |
| Platform conformance (G4) | Add-ons installed; monitoring data arriving centrally | Operating Authority | add-ons confirmed per server (`acceptance/member.md`); no central collector -- see `docs/production-delta.md` |
| Go-live (G6) | Monitored first production transactions | Operating Authority | not implemented in this demo -- see `docs/production-delta.md` |
| Retirement (GX) | Absent everywhere; message-log records retained for the statutory period | Operating Authority | written at exit by the API (`99-retirement.md`, once retired) -- the absence half is asserted (`acceptance/join-member.md`), the message-log retention half is unmet by demo teardown, see `docs/production-delta.md` |
