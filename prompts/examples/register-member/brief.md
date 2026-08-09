# Service briefs — the three canonical members

*The input document for `prompts/register-member.md`. That prompt is one
generation run three times, once per agency below. Expected output: the
committed `configs/member-pnea/pnea.yaml`, `configs/member-plr/plr.yaml` and
`configs/member-pnia/pnia.yaml`. Diff yours against them, one at a time.*

Identity for all three — member code, name, subsystem code and description —
is frozen in `manifest.yaml`'s `identity.members` block. It is an input to
this prompt, never something it generates or restates.

All three have passed the Module 5.2 checklist: each has a Security Server
(or a hosting arrangement), a registered government identity, has adopted the
standards portfolio, and holds data conformant with the semantic map. Each
names its Head of IT as technical contact. Service levels for the two
providers are identical because Progressa reuses one template for every
service on the bus: 99.5% monthly uptime, response within 4 business hours
(1 hour for a P1), support Mon–Fri 08:00–18:00 ICT, a P1 acknowledged within
1 hour with an 8-hour resolution target, and 5 business days' notice of a
planned change. Each provider's Head of IT signs.

---

## 1 · PNEA — Progressa National Examination Authority

**The consumer.** PNEA issues senior-secondary certificates. In this slice it
publishes nothing at all: it exists to *call*, not to serve. It runs its own
Security Server.

It will call two services: PNIA's identity API and PLR's enrolment API.

Its Security Server connection type is **HTTP for the demonstration** — the
default, HTTPS, needs a client TLS certificate uploaded before any call
succeeds, and that is not part of this demonstration. Flag it demo-only. It
must not survive into production.

Because PNEA publishes no service, it has no service to attach a lawful basis
to, and states one for the member as a whole. Progressa carries no real
decree to cite here — Module 2's decree is out of scope for this pack — so
state the category, **statutory function**, rather than invent an article
number. For the same reason PNEA gets no SLA block: the service-level
template is written for providers, and a consumer's own obligations have no
template yet.

## 2 · PLR — Progressa Learner Registry

**A provider.** PLR is the authoritative source of learner enrolment records.
It runs its own Security Server and publishes one service, the **enrolment
API**, described by an OpenAPI 3 document its own system serves at
`http://app-plr:8000/spec.yaml`.

Access is granted to **PNEA's examinations subsystem only**. Nobody else.

On the semantic map this is the **enrolment** entity, keyed on the national
identity number, returning school, level, enrolment year and status, in a
digital-registries lookup pattern.

Its lawful basis is the Learner Registry Act, under which PLR is the
authoritative source of enrolment records. (Illustrative wording — this pack
has no real signed statute to cite.)

## 3 · PNIA — Progressa National Identity Authority

**A provider, and the sensitive one.** PNIA is the authoritative person
identity register. It runs its own Security Server and publishes one service,
the **identity API**, described at `http://app-pnia:8000/spec.yaml`.

Access is granted to **PNEA's examinations subsystem only**. PLR's enrolment
subsystem is deliberately *not* granted access, even though PLR is a member
in good standing — that exclusion is what the federation's negative check
relies on.

On the semantic map this is the **person** entity, keyed on the national
identity number, in a digital-registries lookup pattern. The field list is a
legal decision, not a technical one: exactly what the credential purpose
needs — national identity number, given name, family name, date of birth, sex
and region — and never the full identity record.

Its lawful basis is the National Identity Decree, purpose-limited person
lookup for credential issuance. (Illustrative wording, as above.)
