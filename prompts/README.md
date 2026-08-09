# Prompts

Every configuration in this pack was generated, not hand-written. The rule is
the same for all of them: **write the prompt first, run it, commit its
output.** A config that appeared some other way is undocumented by
construction — nobody can re-derive it, and nobody can tell what it was
supposed to say.

Each prompt follows one shape: **Problem** → a copy-paste **Prompt** block →
**Inputs / outputs** → **Safeguard** → **Prove it**. The last section names
the check that already exists for that config and the verify tier it runs in;
it never invents a new one.

`examples/` carries a worked input for each prompt that takes a brief, and
the answer that brief should produce. See `examples/README.md` for the
reproduce-and-diff exercise.

## The five prompts

| Prompt | Module (`manifest.yaml`) | `video_ref` | Produces |
| --- | --- | --- | --- |
| `federation-core.md` | `federation-core` | 5.5 | `configs/x-road-bus/federation-core.yaml` |
| `register-member.md` | `register-member` | 5.4 | the three canonical member configs |
| `once-only-exchange.md` | `once-only-exchange` | 5.6 | `configs/x-road-bus/once-only-exchange.yaml` |
| `join-member.md` | `join-member` | `"?"` | `configs/x-road-bus/join-policy.yaml` |
| `member.md` | — none | — | `configs/member-<key>/<key>.yaml` + a `manifest.yaml` entry |

`member.md` is bound to no module on purpose: a member joining is not a
curriculum module. `join-member`'s `video_ref` is `"?"` because no Topic 5
subtopic covers it yet.

## Name migration

The prompts were numbered `2.N.md` after the module they served, until the
modules were renamed to stable ids and three of them collapsed into one
(`manifest.yaml`'s own comment records why). Module bundles and reviews
written before that rename still cite the old names; this table resolves
them.

| Old name | Current file | Note |
| --- | --- | --- |
| `prompts/2.1.md` | `federation-core.md` | |
| `prompts/2.2.md` | — | module retired: its claim ("the registration shape is identical for every member") is what collapsed 2.3–2.5 into one prompt |
| `prompts/2.3.md` | `register-member.md` | PNEA, the consumer shape |
| `prompts/2.4.md` | `register-member.md` | PLR, the provider shape |
| `prompts/2.5.md` | `register-member.md` | PNIA, the provider shape |
| `prompts/2.6.md` | `once-only-exchange.md` | |
| `prompts/2.7.md` | `join-member.md` | |
| — | `member.md` | new; no numbered predecessor |

## Not here yet

Two generating prompts are named absences rather than omissions — the legal
and organisational layers of the framework have no prompt in this pack:

- `legal-decree.md` — the decree the exchanges rely on. Planned, W1.
- `governance-pack.md` — the operating authority's own governance
  instruments. Planned, W1.

When they arrive they take the same shape as the five above, each with a
brief under `examples/` and a document-tier check of its own.
