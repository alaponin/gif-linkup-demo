# Conventions (§0.5, §1a)

The onboarding path this pack teaches lists "identifier and naming
conventions published" as the fifth of five ecosystem-level decisions made
once, before member #1, and gives the reason: *"the naming convention is not cosmetic: certificates, DNS, firewall
rules and monitoring all key off the host name, and a convention retrofitted
after fifty members is not retrofitted at all."* This pack enforces one of
§1a's four conventions (`apps/join-api/validate.py`'s identifier allowlist)
without ever stating it as a convention. This page is that statement — for
each of the four, what §1a says a production ecosystem should publish, and
what this pack actually enforces, in code, today.

Design principle: publish conventions as documentation, enforce only what
code reads. This page does not move anything into a config file. The
identifier charset stays a constant; this page is now its stated source,
cited from the comment above `_bad_identifier` in `validate.py`. One rule,
one place, no indirection.

## Identifier character set

**§1a:** "From X-Road 7.3.0, X-Road identifiers permit only
`a-zA-Z0-9'()+,-.=?` and strict checking is on by default for fresh
installations" (X-Road v7.3.0 release notes, XRDDEV-1960).

**What this pack enforces:** exactly that pattern, nothing looser and nothing
stricter. `apps/join-api/validate.py`'s `_check_identifier_characters` rejects any `code`, `subsystem`, or `services[].code` that does
not fully match `[a-zA-Z0-9'()+,\-.=?]+` — empty and whitespace-only values
fall out of that match on their own, so there is no separate empty-string
check. The match is positive by design: a denylist of separators guessed to
collide with X-Road's REST message protocol disagrees with X-Road's own
allowlist in both directions, accepting identifiers it rejects and rejecting
ones it accepts.

## Member code scheme

**§1a:** "Typically the national business/organisation registry code. Stable
across renaming and merger" (Practice). A production ecosystem's member code
is usually not invented for X-Road at all — it is whatever registry code the
organisation already holds, reused rather than assigned.

**What this pack enforces:** two layered constraints on `JoinPayload.code`,
stricter than §1a's practice note because this pack's `code` does double
duty. First, `code` is an X-Road identifier, so it must satisfy the
identifier character set above. Second, `code.lower()` becomes both the
`configs/member-<key>/` directory name and the `manifest.yaml
identity.members.<key>` map key, so it must additionally satisfy the
narrower `[a-z0-9]+` — no uppercase, no punctuation at all —
(`_check_key_derivation`, check S8-2). A code that is a perfectly valid
X-Road identifier can still fail this second, stricter check; that is
deliberate, not a bug, and the two checks stay separate because they enforce
different things (`validate.py`'s comment on the point: "not a contradiction,
a different thing being constrained").

## Subsystem code scheme

**§1a:** "One per system, not per service" (UC-MEMBER). A joining
organisation gets one subsystem code for each system it operates behind
X-Road, not one per individual service that system exposes — a system with
five services is still one subsystem publishing five service codes, never
five subsystems of one service each.

**What this pack enforces:** the rule is realised structurally rather than by
a separate validator check. `schema.JoinPayload` has exactly one `subsystem`
field per join request, with `services: list[Service]` nested under it — the
shape of the payload makes "one subsystem, many services" the only thing a
submitter can express. There is nothing to check at request time because
there is no field through which a one-subsystem-per-service payload could
even be written down.

## Security Server host naming

**§1a:** Finland's Suomi.fi Data Exchange Layer mandates
`<organisation><role><environment><nn>.<domain>` — e.g.
`organisaatiolpdev01.org.fi` (DVV). This is general guidance on what a
production naming convention typically encodes, not this pack's own scheme:
a real convention names the **owner** (which organisation), the **role**
(what the host does), the **environment** (dev/test/prod), and a **sequence
number** (which instance, when an organisation runs more than one), so that
a name alone answers "whose is this, what is it for, which environment, and
which one" without a lookup.

**What this pack actually does:** `ss-<key>` — the member key with an
`ss-` prefix, nothing more. `configs/member-plr/plr.yaml`'s
`security_server.dns_name: ss-plr` and `configs/member-pnia/pnia.yaml`'s
`security_server.dns_name: ss-pnia` are both this pattern; `hurl/generate.py`
derives every Security Server's DNS name and container name from the member
key the same way. There is deliberately no validator check that a derived
value matches the pattern it was derived from — a check that a derived value
matches its own derivation tests the code against itself, not the data — this
convention is real and consistently applied, just not independently checked
at request time the way the other three are.

## Scenario values and machinery

The four conventions above are about identifiers. This one is about where any
scenario value is allowed to live at all.

Progressa is a scenario. Its bindings — the instance and member class,
the member set, semantic entities and their field lists, code lists, lawful
bases, seed vocabulary, and the exchange definitions themselves — are
declared in artefacts: `manifest.yaml`, `configs/`, `apps/data/`. Scripts,
templates and checks **read** those artefacts; they never restate a value
from one. A federation retargeted at another sector should change only the
declarations.

The test for a suspected literal is the one `join-policy.yaml` applies to a
policy key: *can it be set to another value, and does something observably
change — and if so, from where?* If the answer names an artefact, the literal
is a copy and belongs gone. If it names nothing, the value is machinery, not
a scenario binding, and it stays. `scripts/acceptance.sh` reads the instance
and member class from `hurl/topology.json` and the asked-once form from
`configs/x-road-bus/once-only-exchange.yaml` for exactly this reason: the
check now proves the live federation matches the declaration, which a
restated copy cannot do.

**The one open exception**, stated rather than hidden: the education
vocabulary in `scripts/gen_seed_data.py` — school names, levels, statuses,
the name pools — is Python literals with no declared source. Retargeting the
pack to another sector means editing that script. It is the largest remaining
piece of this rule's unfinished work, and it is deliberately not closed here.

There is no checker for this. The rule is enforced in review, like the
comment discipline — a grep for "any value that could have been declared"
has no honest pattern, and a checker that only knows today's literals would
pass the moment someone wrote a new one.
