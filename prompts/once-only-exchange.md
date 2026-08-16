# Generating prompt — module 2.6 (Run the once-only exchange)

**Building block(s):** x-road-bus
**Produces:** `configs/x-road-bus/once-only-exchange.yaml`
**Public spec:** X-Road Message Protocol for REST (r1); PAERA v1.0 §5.2 Principle #5 (Once-Only)
**Realises:** Module `once-only-exchange` (`video_ref` 5.6) — the
production-grade form of subtopic 5.6's AI usage tip.

## Problem

Everything before this produced a federation with registered members and published
services. This module wires the proving exchange itself: the two cross-server calls
PNEA makes, the fields each pre-fills, the once-only assertion, and the negative
check. Its config is what `acceptance/once-only-exchange.md` executes.

## Prompt (copy-paste ready)

```
Below is the X-Road Message Protocol for REST (the r1 URL format and X-Road-Client
header), the frozen Progressa identifiers from manifest.yaml, and the once-only
scenario: a learner applies for a senior-secondary certificate at PNEA, which
pre-fills identity from PNIA and enrolment from PLR [paste all three].

Generate the exchange configuration as a single YAML document:
(1) exchange.name and a three-sentence scenario;
(2) consumer — the calling subsystem — and entrypoint — its Security Server's
    REST interface;
(3) calls — for each of the two services: the full X-Road service identifier, the
    logical request, the exact r1 path, the fields it pre-fills, and the
    interoperability layer(s) that call exercises (technical / legal /
    organisational / semantic);
(4) asked_once — what the citizen provides (one field: the NIN) versus every field
    pre-filled from the bus;
(5) headers — the X-Road-Client value;
(6) negative_check — the unauthorised subsystem and the expected denial.

Rules: construct every r1 path mechanically from the frozen identifiers
(/r1/{instance}/{class}/{member}/{subsystem}/{service}/{endpoint}) — do not invent
any segment; each is [confirm: against the live registry]. The asked_once lists
must be disjoint and together cover the full application form. Output only the
YAML document.
```

## Inputs / outputs

- **Inputs:** the r1 protocol spec + the frozen identifiers + the Progressa
  once-only scenario.
- **Output:** `configs/x-road-bus/once-only-exchange.yaml` — executed by `scripts/acceptance.sh`.

## Safeguard

An acceptance that only proves the happy path is half a check. This config must
carry the negative check (unauthorised caller denied) and the right-learner
assertion (fields match the seeded record, not merely "data returned") — and a
passing run proves this exchange only, not exchanges never tested.

## Prove it

- **Static** (`--fast`): `python3 hurl/generate.py && scripts/verify.sh --fast`.
- **Live** (`--live`): `scripts/acceptance.sh` — `acceptance/once-only-exchange.md`
  is the headline check, and the one the framework's own claim rests on.
