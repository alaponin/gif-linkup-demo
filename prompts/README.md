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

## Not here yet

Two generating prompts are named absences rather than omissions — the legal
and organisational layers of the framework have no prompt in this pack:

- `legal-decree.md` — the decree the exchanges rely on.
- `governance-pack.md` — the operating authority's own governance
  instruments.

When they arrive they take the same shape as the five above, each with a
brief under `examples/` and a document-tier check of its own.
