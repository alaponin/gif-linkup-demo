"""The Linkup admin-API sequence as an ordered registry of named steps.

hurl/generate.py renders this registry in order to build hurl/scenarios/ --
same templates, same order, same bytes as before this module existed. Nothing
consumes the registry per-step yet (Plan B does); see
docs/superpowers/specs/2026-08-01-member-join-api-design.md #15 and
docs/superpowers/plans/2026-08-01-kp2-join-a-step-registry.md.

`requires`/`provides` are Hurl *runtime* identifiers: `{{var}}` names and
`[Captures]` names respectively. They are NOT generate.py's `sub()` `@name@`
tokens, which are substituted in Python before Hurl ever sees the file.
tests/test_steps.py checks the declarations below against the templates.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Step:
    id: str                    # "cs.init", "ss.auth_key_csr" -- dotted, stable, never renumbered
    template: str               # filename under hurl/templates/
    actor: str                  # "operator" | "member" -- see design decision 5
    requires: tuple[str, ...]   # Hurl {{var}} names this step reads
    provides: tuple[str, ...]   # Hurl [Captures] names this step writes


# Ordered registry: generate.py renders these in order. Order here IS the
# executable sequence -- Task 3 relies on this for the hosted-client
# ordering bug it must not reintroduce.
REGISTRY: tuple[Step, ...] = (
    Step(
        id="cs.trust_services",
        template="01-cs-trust-services.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "ca_host"),
        provides=("ca_id",),
    ),
)

BY_ID: dict[str, Step] = {step.id: step for step in REGISTRY}
