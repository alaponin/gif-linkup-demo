"""hurl/steps.py's registry contract -- join-a-step-registry plan Tasks 1/4.

A declared requires/provides contract nobody verifies is documentation that
rots (Task 4's own framing). This module makes it mechanically true by
parsing each step's raw .tmpl source -- never the rendered hurl/scenarios/
output, which only exists for one already-chosen member/profile combination
and would hide the registry's per-id, cross-member contract.

Regex is sufficient (Task 4 Step 1): these are generated files with a fixed
shape. Two regimes appear in the templates:

  - CS-only steps (cs.*) have no per-member parameterisation: their
    {{var}}/[Captures] names are already concrete Hurl runtime identifiers,
    e.g. "cs_xsrf_token".
  - Security-Server steps (ss.*/service.*) are rendered once per member via
    generate.py's sub(), so their templates still carry @HOSTVAR@/@P@/
    @SESS_P@/@CAP_P@/@SPECVAR@ -- Python-level placeholders for a per-member
    Hurl identifier the template doesn't know yet (e.g. "@P@_xsrf_token"
    becomes "pdga_xsrf_token" or "pnia_xsrf_token" depending only on which
    member is being rendered). hurl/steps.py's requires/provides use that
    same raw, unrendered form -- see its module docstring for why that is
    not the requires/provides-vs-@name@-token conflation design decision 2
    warns about: whatever sub() leaves inside {{...}} or a [Captures] name
    is a Hurl runtime identifier regardless of whether it still contains an
    @token@ pending its own substitution. @DESCRIPTION@, @MEMBER_CODE@ and
    friends never appear inside {{...}} or a capture name -- they're baked
    into literal JSON string values instead -- so this parser excludes them
    by construction, with no special-casing needed.

Task 4 Step 4 (cross-registry ordering) additionally needs one
normalisation: @SESS_P@_xsrf_token and @CAP_P@_client_id and @P@_xsrf_token
name the same *kind* of per-member identifier under three different
sub()-parameter names (which prefix is in scope depends on the call site --
a member's own session vs. a hosting member's session vs. where a capture
lands). `_canon()` collapses any leading @UPPER_CASE@ token to a single
placeholder so the ordering check reasons about shape ("was a
<member>_xsrf_token captured earlier") rather than exact placeholder
spelling. This is a structural check, not a full per-instantiation
verifier -- that per-instantiation correctness is what tests/test_golden.py's
byte-identical corpus proves instead.
"""
from __future__ import annotations

import pathlib
import re
import sys

PACK = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = PACK / "hurl" / "templates"
sys.path.insert(0, str(PACK / "hurl"))

import steps as steps_module  # noqa: E402

ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_]+)+$")

# Same shape as hurl/check_scenarios.py's patterns, extended to tolerate the
# '@TOKEN@' placeholders that are still present in an unrendered .tmpl file.
VAR_USE = re.compile(r"\{\{([A-Za-z0-9_.@-]+)\}\}")
CAPTURE_LINE = re.compile(r"^([A-Za-z0-9_.@-]+):\s+(jsonpath|cookie|header|body|xpath|regex)")
REQUEST_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+")
PREFIX_TOKEN = re.compile(r"^@[A-Z_]+@")

# vars.env's static globals (generate.py main()'s "-- vars.env --" block) --
# never captured by any step, always available. "<P>" is the canonicalised
# form of the per-member globals generate.py also writes there (each
# member's own @HOSTVAR@ host name, and @SPECVAR@'s <member>_spec_url) --
# see _canon() below.
GLOBALS = {
    "cs_host", "cs_admin_user", "cs_admin_password",
    "ss_admin_user", "ss_admin_password",
    "token_pin", "xroad_instance", "member_class", "ca_host", "csr_country",
    "<P>",
}


def _canon(token: str) -> str:
    return PREFIX_TOKEN.sub("<P>", token)


def _extract(template: str) -> tuple[set[str], set[str]]:
    """Every {{name}} reference and every [Captures] name a raw .tmpl file
    declares, in the form they're written before generate.py's sub() runs."""
    refs: set[str] = set()
    captures: set[str] = set()
    in_captures = False
    for raw in (TEMPLATES / template).read_text().splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line == "[Captures]":
            in_captures = True
            continue
        if line.startswith("[") or REQUEST_LINE.match(line):
            in_captures = False
        refs.update(VAR_USE.findall(raw))
        if in_captures:
            m = CAPTURE_LINE.match(line)
            if m:
                captures.add(m.group(1))
    return refs, captures


def test_step_ids_are_unique_and_well_formed():
    ids = [step.id for step in steps_module.REGISTRY]
    assert len(ids) == len(set(ids)), f"duplicate step id(s): {sorted(set(x for x in ids if ids.count(x) > 1))}"
    malformed = [i for i in ids if not ID_RE.match(i)]
    assert not malformed, f"step id(s) not matching {ID_RE.pattern}: {malformed}"


def test_provides_equals_template_captures_exactly():
    mismatches = []
    for step in steps_module.REGISTRY:
        _, captures = _extract(step.template)
        declared = set(step.provides)
        if declared != captures:
            missing = captures - declared
            extra = declared - captures
            mismatches.append(
                f"{step.id} ({step.template}): "
                f"{'missing from provides: ' + repr(sorted(missing)) + ' ' if missing else ''}"
                f"{'declared but not captured: ' + repr(sorted(extra)) if extra else ''}"
            )
    assert not mismatches, "provides does not equal the template's capture set:\n" + "\n".join(mismatches)


def test_every_template_reference_is_accounted_for():
    unaccounted = []
    for step in steps_module.REGISTRY:
        refs, _ = _extract(step.template)
        known = set(step.requires) | set(step.provides) | GLOBALS
        missing = sorted(r for r in refs if r not in known and _canon(r) not in GLOBALS)
        if missing:
            unaccounted.append(f"{step.id} ({step.template}): {missing}")
    assert not unaccounted, (
        "template references a {{name}} not in requires, provides, or the "
        "known vars.env globals:\n" + "\n".join(unaccounted)
    )


def test_requires_satisfied_by_an_earlier_step_or_a_global():
    """Walk the registry in order -- the sequence Plan B will eventually
    resume through one step at a time. A requires not yet available is
    exactly the ordering bug 2026-07-26-deployment-spec-and-lite-profile.md
    found live in build_hosted_client() (join-a plan Task 3 Step 2)."""
    available = {_canon(g) for g in GLOBALS}
    violations = []
    for step in steps_module.REGISTRY:
        for req in step.requires:
            if _canon(req) not in available:
                violations.append(f"{step.id} requires {req!r} before any earlier step provides it")
        for prov in step.provides:
            available.add(_canon(prov))
    assert not violations, "\n".join(violations)
