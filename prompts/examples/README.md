# Worked examples

Every prompt in `prompts/` opens with "below is the reference and the
Progressa service brief [paste both]" — and until now the pack shipped
neither brief. A prompt whose input nobody has is a prompt nobody can run.

This directory is those inputs, and the answers they should produce.

## The exercise

1. **Run the prompt with the brief.** Paste the public reference the prompt
   names, paste `brief.md`, run it.
2. **Diff against expected.** Either the folder carries the expected output
   (`member-ptsb/`) or its brief names the committed config as the expected
   output (the other three) — the config in `configs/` *is* the answer, and
   it got there this way.
3. **Resolve the `[confirm:]` markers.** A generated config arrives with
   every identifier marked unconfirmed. Check each against the live registry
   before it is deployed — that resolution is the part a model cannot do for
   you, and the reason the markers exist.

A diff that comes back non-empty is not automatically your error. Prose
wording will differ; comments will differ; ordering may differ. What must
match is the *content*: the same keys, the same identifiers, the same access
lists, the same field lists. If those differ, one of you is wrong, and the
brief is the arbiter.

| Folder | Prompt | Expected output |
| --- | --- | --- |
| `federation-core/` | `federation-core.md` | `configs/x-road-bus/federation-core.yaml` |
| `register-member/` | `register-member.md` | the three `configs/member-*/` configs |
| `once-only-exchange/` | `once-only-exchange.md` | `configs/x-road-bus/once-only-exchange.yaml` |
| `member-ptsb/` | `member.md` | `expected-manifest-entry.yaml` + `expected-ptsb.yaml`, here |

`join-member.md` has no brief, deliberately: its inputs are the NIIS
management-request reference and this pack's own note on access-rights
granularity, both cited in the prompt itself. There is no agency brief to
write, because a join policy is the federation's own decision about everyone,
not one agency's description of itself.

## About `member-ptsb/`

PTSB is the one worked example that ships its own expected output rather than
pointing at a committed config, because PTSB is not a member of this pack —
it joins, demonstrates, and leaves (`exercises.md`, exercises 2–4). The
backend it joins with is real and tracked (`apps/specs/ptsb-awards.openapi.yaml`,
`apps/data/awards.csv`, the `app-ptsb` container); only its member config is
transient. That is what makes it the right example: you can actually run the
join.

Its brief was reconstructed from what its outputs demand, and the expected
config is checked two ways — its `semantic.fields` equal the tracked spec's
declared response fields, and the whole document is identical to what
`apps/join-api`'s writer produces from the same facts. The by-hand path and
the API path agree, which is the claim the example exists to make.

## Boundary

**These briefs are inputs to a person running a prompt. Nothing reads them.**
`hurl/generate.py` discovers members from `configs/member-*/`, and that is
the only path from a document to a deployment. A brief here must never become
a second input to the deploy sequence — if a future change makes
`generate.py` read anything under `prompts/`, that change is the mistake, not
this boundary.
