# Service brief — Progressa Tertiary Scholarship Board

*The input document for `prompts/member.md`. A brief is what an agency sends
the Operating Authority; it is prose, not configuration. Everything the
prompt needs is stated here, and nothing that is the prompt's job to decide.*

## The agency

The Progressa Tertiary Scholarship Board (PTSB) administers state
scholarships for tertiary study. It is a government body and would register
under the same member class as every existing member. Its X-Road member code
is **PTSB**.

PTSB runs one system relevant to the bus: the scholarship award register,
which records who holds an award, under which programme, and for which year.
The subsystem it wants registered is **SCHOLARSHIP** — the scholarship award
register.

## Why it is joining

PNEA issues senior-secondary certificates. Some candidates hold a PTSB
scholarship, and PNEA currently asks each candidate to produce the award
letter on paper. PTSB wants to publish the award record on the bus so PNEA
can verify it directly, and stop asking.

PNEA's examinations subsystem is the only consumer. No other agency has a
stated need for award data, and PTSB does not want the service open to the
bus generally.

## Hosting

PTSB has no Security Server of its own and no capacity to run one this year.
It has agreed with the Progressa Learner Registry that its subsystem will be
hosted as a client on **PLR's** Security Server (`ss-plr`). PTSB understands
that this means PLR's token holds its signing key.

## What it publishes

One service, the **awards API**. Its OpenAPI 3 description is served by
PTSB's own system at `http://app-ptsb:8000/spec.yaml`, and that document is
the contract: a GET returning a candidate's award by national identity
number. PTSB publishes nothing else and requests access to nothing.

The award record carries the national identity number, the award identifier,
the programme, and the year. PTSB is clear that PNEA needs all four to verify
an award and that nothing else in its register is to leave it — the candidate's
bank details and disbursement history stay where they are.

Against the Module 4 semantic map this is the **award** entity, keyed on the
national identity number, in a digital-registries lookup pattern.

The legal basis for the exchange has not yet been settled — PTSB's counsel is
identifying the article of the scholarships decree that authorises disclosure
to an examinations authority. It must be cited before go-live.

PTSB's register accepts any call that reaches it: it sits on an internal
network and has never needed to authenticate its own callers. PTSB knows this
is not good enough for production and is treating it as a demonstration
posture.

## Member requirements (Module 5.2)

PTSB's answers to the checklist:

- **Security Server:** yes, by the hosting arrangement above.
- **Registered identity:** yes — PTSB is an established statutory body with
  an existing government identifier.
- **Standards portfolio adopted:** yes.
- **Data conformant:** yes; the register's fields map onto the semantic map's
  award entity without transformation.
- **Technical contact:** Head of IT, PTSB.

## Service levels (Module 5.3)

PTSB offers, and its Head of IT will sign:

- 99.5% monthly uptime
- 500 ms at the 95th percentile
- support 08:00–17:00 on working days
- incidents acknowledged within 4 working hours
- 30 days' notice of a breaking change
