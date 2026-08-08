"""hurl/steps.py's registry contract.

A declared requires/provides contract nobody verifies is documentation that
rots. This module makes it mechanically true by parsing each step's raw
.tmpl source -- never the rendered hurl/scenarios/ output, which only
exists for one already-chosen member set (generate.py lost the profile
concept; there is one topology now) and would hide the registry's per-id,
cross-member contract.

Regex is sufficient: these are generated files with a fixed shape. Two
regimes appear in the templates:

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
    not the requires/provides-vs-@name@-token conflation it warns
    about: whatever sub() leaves inside {{...}} or a [Captures] name
    is a Hurl runtime identifier regardless of whether it still contains an
    @token@ pending its own substitution. @DESCRIPTION@, @MEMBER_CODE@ and
    friends never appear inside {{...}} or a capture name -- they're baked
    into literal JSON string values instead -- so this parser excludes them
    by construction, with no special-casing needed.

Cross-registry ordering additionally needs one normalisation: @SESS_P@_xsrf_token and @CAP_P@_client_id and @P@_xsrf_token
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


def test_no_step_is_unsafe_to_repeat():
    """join-a plan: class (d) (not safe to repeat at all) is
    empty today. If a future step ever sets unsafe_to_repeat=True, this test
    starts failing loudly instead of Plan B silently resuming across it."""
    unsafe = [step.id for step in steps_module.REGISTRY if step.unsafe_to_repeat]
    assert not unsafe, f"class (d) step(s) found -- Plan B must not resume across these: {unsafe}"


def test_ambiguous_steps_have_a_probe():
    """A step this audit classified as 409-ambiguous is only actually
    covered if it carries a probe (join-a plan) -- the
    classification comment above each Step in hurl/steps.py is not itself
    checked by anything, so this at least proves every declared probe path
    exists on disk."""
    missing = []
    for step in steps_module.REGISTRY:
        if step.probe is None:
            continue
        if not (TEMPLATES / step.probe).exists():
            missing.append(f"{step.id}: probe {step.probe!r} does not exist")
    assert not missing, "\n".join(missing)


def test_requires_satisfied_by_an_earlier_step_or_a_global():
    """Walk the registry in order -- the sequence Plan B will eventually
    resume through one step at a time. A requires not yet available is
    exactly the ordering bug found live in build_hosted_client(): a client's
    SIGN-key generation and registration rendered before the client-add step
    that must precede both."""
    available = {_canon(g) for g in GLOBALS}
    violations = []
    for step in steps_module.REGISTRY:
        for req in step.requires:
            if _canon(req) not in available:
                violations.append(f"{step.id} requires {req!r} before any earlier step provides it")
        for prov in step.provides:
            available.add(_canon(prov))
    assert not violations, "\n".join(violations)


# -- join-c plan: Step.reverse --------------------------------------------
#
# The id sequence apps/join-api/job.py's build_sequence() actually renders
# for a hosted join (join-b plan's own add() calls), NOT the full
# cold-deploy REGISTRY above: a hosted join never touches
# cs.signing_keys/cs.trust_services/ss.auth_key_csr/ss.bringup_register/
# ss.mgmt_register/ss.activate/ss.tsa_capture/ss.tsa_post at all, so those
# steps' captures are not something a reversal can ever assume. Mirrored
# here as a plain id list, rather than importing apps/join-api (a separate
# package this test suite otherwise has no dependency on) -- this list is
# the thing to update if build_sequence()'s own id sequence ever changes.
HOSTED_JOIN_FORWARD_SEQUENCE: tuple[str, ...] = (
    "cs.init",
    "cs.members_member",
    "cs.anchor",
    "ss.bringup_init",
    "ss.ca_name_capture",
    "ss.client_add",
    "ss.sign_key_csr",
    "ss.client_register",
    "service.publish",
    "service.acl",
)


def test_every_reversal_has_a_probe():
    """join-c plan: reversal is the case probes exist for --
    every step with a `reverse` must carry a `probe` (job.py has no other
    way to tell, on resume, whether a reversal call already ran)."""
    missing = [step.id for step in steps_module.REGISTRY if step.reverse and not step.probe]
    assert not missing, f"step(s) with a reversal but no probe: {missing}"


def test_reversal_order_names_exactly_the_steps_that_declare_a_reverse():
    """REVERSAL_ORDER and the `reverse=` fields are two hand-maintained lists
    of the same set, and apps/join-api/job.py's unjoin() walks the FIRST one.
    A step that gains a `reverse` but no REVERSAL_ORDER entry is silently
    never walked -- the un-join reports fewer reversals and still reports
    RETIRED."""
    assert {s.id for s in steps_module.REGISTRY if s.reverse} == set(steps_module.REVERSAL_ORDER)


def test_reversal_templates_exist():
    """Same reasoning as test_ambiguous_steps_have_a_probe -- a declared
    `reverse` path that does not exist on disk is worse than none."""
    missing = []
    for step in steps_module.REGISTRY:
        if step.reverse is None:
            continue
        if not (TEMPLATES / step.reverse).exists():
            missing.append(f"{step.id}: reverse {step.reverse!r} does not exist")
    assert not missing, "\n".join(missing)


def test_reversal_requires_satisfiable_from_a_completed_forward_run():
    """join-c plan's headline check: a reversal template that
    reads a Hurl {{var}} name no forward step of a hosted join ever
    `provides` is the most likely defect in this task -- it would only
    surface at runtime, on the first live reversal attempt.

    "Available" here means job.py's persisted `context`, not the in-memory
    `session` dict, which is forbidden from writing to disk (job.py module
    docstring point 3): every HOSTED_JOIN_FORWARD_SEQUENCE step's
    non-secret `provides` (a capture named *_xsrf_token is a session
    capture, dropped -- job.py's `_is_secret()`). Session-shaped refs
    inside a reversal template (every one of the six needs an XSRF token to
    authenticate) are allowed regardless of whether some earlier step's
    capture happens to be walked in here: JobStep.must_rerun guarantees
    job.py re-establishes a fresh session token every run/resume, which is
    a different, already-covered guarantee than "was this ever captured at
    all" -- the thing this test exists to catch, illustrated by the concrete
    instance: @CAP_P@_@SC@_description_id (service.publish) and
    @CAP_P@_sign_key_id (ss.sign_key_csr) are both forward [Captures], so a
    reversal reading them back is legitimate -- they are not orphaned
    references to something no forward step ever ran.
    """
    available = {_canon(g) for g in GLOBALS}
    for step_id in HOSTED_JOIN_FORWARD_SEQUENCE:
        for prov in steps_module.BY_ID[step_id].provides:
            if prov.endswith("_xsrf_token"):
                continue  # session, not context -- never persisted
            available.add(_canon(prov))

    violations = []
    for step in steps_module.REGISTRY:
        if step.reverse is None:
            continue
        refs, _ = _extract(step.reverse)
        for ref in refs:
            if ref.endswith("_xsrf_token"):
                continue  # re-established every run (must_rerun), not a context lookup
            if _canon(ref) not in available:
                violations.append(
                    f"{step.id} reverse {step.reverse!r} requires {ref!r}, not satisfiable "
                    "from a completed hosted-join forward run's context"
                )
    assert not violations, "\n".join(violations)
