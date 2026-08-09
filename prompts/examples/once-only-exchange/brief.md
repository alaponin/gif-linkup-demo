# Scenario brief — the once-only credential application

*The input document for `prompts/once-only-exchange.md`. Expected output: the
committed `configs/x-road-bus/once-only-exchange.yaml`. Diff yours against it.*

## The scenario

A learner applies for a **senior-secondary certificate** at the Progressa
National Examination Authority (PNEA). Today they would fill in a form
restating what the state already knows: their name, date of birth, sex,
region, which school they attend, at what level, in which year, and whether
their enrolment is active.

Under the once-only principle they should provide **one field: their national
identity number**. Everything else PNEA needs is already held by another
member of the bus, and PNEA should fetch it rather than ask.

## Who calls what

The calling subsystem is **PNEA's examinations subsystem**, entering the bus
through its own Security Server's REST interface (`ss-pnea`, port 8080).

It makes two calls:

1. **PNIA's identity API** — a lookup of a person by national identity
   number. It pre-fills given name, family name, date of birth, sex and
   region. This call is where the *legal* layer shows: PNIA returns only the
   fields the credential purpose needs, per the decree, not the whole
   identity record. Technically it is a routed cross-server call with mutual
   TLS between the two Security Servers.

2. **PLR's enrolment API** — a lookup of an enrolment by the same number. It
   pre-fills school, level, enrolment year and status. This call is where the
   *organisational* layer shows — the exchange happens between members the
   governance admitted — and the *semantic* layer, because all three agencies
   share the same semantic map, which is why "enrolment" resolves to the same
   thing at both ends.

Every identifier in those paths is frozen in `manifest.yaml`. Build the r1
paths mechanically from them.

## The negative check

A passing happy path is half a proof. The exchange must also demonstrate that
being on the bus is not permission to call everything.

Use **PLR's enrolment subsystem** as the unauthorised caller against PNIA's
identity API. PLR is already a bus member and already a provider, and it holds
no grant on identity-api — so a denial makes the point cleanly. Route the
attempt through the Security Server that actually hosts PLR's subsystem
(`ss-plr`, port 8080), so the refusal comes from the provider-side access
control rather than from PNEA's server declining to serve a client it does
not host.

Expect an X-Road access-denied error: PNIA's access list grants PNEA's
examinations subsystem only.
