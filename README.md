# KP2 build pack — Government Interoperability Framework

The runnable companion to the KP2 video bundle. The videos teach the build; this
pack **is** the ready solution — the configuration the modules generate, the prompts
that generate it, the scripts that deploy it, and the acceptance checks that prove it.

- **Track:** interoperability
- **Depends on:** none (foundation)
- **Stand it up:** see `runbook.md`
- **Index:** `manifest.yaml` (module → BB → config → prompt → acceptance, with
  `video_ref` to the Topic 5 subtopic each module realises, and the frozen
  Progressa identifiers that are the KP3/KP4 join keys)
- **Plan / review:** `PLAN.md` (build plan, doc-verified X-Road sequence),
  `REVIEW.md` (self-review; open decisions)

What's here: `deployment.yaml` (the analyst-facing deployment spec — topology
profile, X-Road version pins; `.env` carries only secrets), `docker-compose.yml`
(X-Road 7.7.0: Central Server, Test CA, five Security Servers — `deployment.yaml`'s
`profile: lite` runs three, PNIA and MoEYS hosted as extra clients on ss-plr;
see `hurl/README.md` "Known limits"), `configs/` (declarative YAML per module),
`prompts/` (the bb-config-gen plays that generate the configs), `hurl/` (the
federation as config-as-code — Hurl scenarios driving the admin REST APIs,
generated from `configs/`, retargeted from X-Road 7.7.0's own `setup.hurl`),
`acceptance/` (given/when/then per module; 2.6 is the once-only exchange, the
framework's acceptance; `member.md` is the generic per-member check every
joined member gets automatically), `scripts/` (deploy / seed / acceptance /
teardown / `member.sh list|remove` — reports on and retires joined members),
`apps/` (mock REST registries behind
the Security Servers + OpenAPI contracts +
Gambia-grounded, Progressa-named seed data; `apps/console/` is the optional
one-page demonstration UI, `scripts/console.sh up` — a demo asset, not a
module, never in the acceptance path), `docs/` (production delta per Module
5.7; X-Road 8 note; what reading the 7.7.0 reference corrected).

The number and identity of members is a property of `configs/member-*/` plus
`manifest.yaml`'s `identity.members`, not of this pack's source code. A new
member joins by running `prompts/member.md` against an agency brief and
committing what it produces — there is no `scripts/member.sh add`, because
writing member config by hand is exactly what this pack is demonstrating you
don't need to do.

By design, KP2's slice is **Joget-free**: the member systems are mocks behind
stable OpenAPI contracts — the seam where KP4's Joget DX apps plug in later
without touching the X-Road configuration.

Built and proven with the `itu-giga-kp` kit: `bb-config-gen` fills the configs,
`kp-solution-verify` proves the pack runs. **Status: VERIFIED (2026-07-25,
re-verified 2026-07-27 including `apps/console/`)** — `check_pack.py --ready`
passes and the live acceptance suite is green, including the reproducibility
proof (`teardown.sh --purge` → cold redeploy → reseed → acceptance, unattended
— PLAN.md §7) and, most recently, the same cycle plus a full console
up/exercise/reset pass (PLAN.md §11). Scope: Education only, public anchors
only. Demo only — never production (`docs/production-delta.md`).
