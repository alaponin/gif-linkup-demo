# Generating prompt — module 2.7 (A new member joins the bus — the join API)

**Building block(s):** x-road-bus
**Produces:** `configs/x-road-bus/join-policy.yaml`
**Public spec:** NIIS X-Road management-request approval model (registration
requires explicit operator approval — the same model
`configs/x-road-bus/federation-core.yaml`'s `policy.management_request_approval: explicit`
already commits the federation to); this pack's own §2.3 (service-level vs
endpoint-level access rights over a tool-generated OpenAPI spec, deferred —
see `docs/production-delta.md` item 6)

## Problem

2.1–2.6 register the federation's own curriculum. 2.7 is the policy the join
API (`apps/join-api/`) enforces on everyone who joins *after* that curriculum
exists — four keys, read by `validate.py`'s thirteen per-request checks
(spec S8's eleven, plus `lawful_basis` and `sla_required`, additions beyond
the spec) and by `hurl/generate.py`'s
`check_join_policy()`. This prompt exists so the policy
is teachable the way every other module's config is: as something a federation
operator authors from a reference, not something that only happens to be
correct because it was written by hand once. **It was, in fact, written by
hand for this pack**, before this prompt existed — the block is four keys
and was judged not worth generating through a model round-trip. This prompt
is what a federation operator starting from scratch,
or a different federation choosing different values, would actually run.

## Prompt (copy-paste ready)

```
Below is the NIIS X-Road management-request approval reference and this pack's
own note on service-level vs endpoint-level access rights over a tool-generated
OpenAPI spec [paste both].

Generate the join policy as a single YAML document, exactly four keys under a
`join:` block:
(1) member_class — the member class this federation admits joiners under.
    Normally the same class every existing member already registers under
    (Progressa's federation is GOV-only; check manifest.yaml's
    identity.member_class before answering);
(2) approval — "explicit" or "automatic". A federation that already requires
    explicit approval for its own management requests (policy.
    management_request_approval in configs/x-road-bus/federation-core.yaml) has already
    answered this the same way; a federation admitting a genuinely open set of
    joiners with no human review is a different, riskier design and should
    say so if chosen;
(3) default_hosting — "hosted_on" or "own_server": what a join request gets
    when it does not name a hosting choice. Hosting on an existing member's
    Security Server is the lower-friction default for a federation still
    building its membership base; requiring every joiner to run its own
    server is a real, defensible choice for a federation with different
    capacity assumptions;
(4) allowed_methods — the HTTP methods a joining member's published services
    may declare, checked by fetching the candidate's OpenAPI spec and
    rejecting any operation outside this list. A federation that has not
    built a story for what a third-party write endpoint means for its ACL
    exactness checks should set this to read-only methods only.

Rules: do not add a fifth key. Every key here must be enforced somewhere
(validate.py's checks or generate.py's check_join_policy()) — a key nothing
reads is decoration, not policy, and worse than no key at all (this is
join-policy.yaml's own standing rule, kept here rather than restated as decoration).
Output only the YAML document.
```

## Inputs / outputs

- **Inputs:** the X-Road management-request approval reference + this pack's
  §2.3 note on service-level vs endpoint-level access rights.
- **Output:** `configs/x-road-bus/join-policy.yaml`'s `join:` block — read by
  `apps/join-api/validate.py` (checks 4/6/10) and `hurl/generate.py`'s
  `check_join_policy()`. Not applied by `scripts/deploy.sh` like the other
  x-road-bus modules — it has no live-federation effect of its own; it only
  constrains what `apps/join-api` will accept.

## Safeguard

Of the four keys, `member_class` and `approval` are close to fixed for this
federation — Progressa is GOV-only, and 2.1's own
`management_request_approval: explicit` already commits the federation to a
human reviewing registrations, so a join policy answering `approval:
automatic` would be inconsistent with the federation it is joining, not
merely a different style. `default_hosting` and `allowed_methods` are the
genuine policy choices: a different federation, with different capacity or a
more developed access-rights story (§2.3's deferred endpoint-level model),
could set either differently without being wrong. Do not treat all four keys
as equally open — the first two are a consistency check against the rest of
the federation's own config, not a free choice each time this prompt runs.
