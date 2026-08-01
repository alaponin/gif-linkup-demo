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
        id="cs.init",
        template="fragments/CS_INIT.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_admin_user", "cs_admin_password", "token_pin", "xroad_instance"),
        provides=("cs_xsrf_token",),
    ),
    Step(
        id="cs.member_class",
        template="fragments/CS_MEMBER_CLASS.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "member_class"),
        provides=(),
    ),
    Step(
        id="cs.token_login",
        template="fragments/CS_TOKEN_LOGIN.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "token_pin"),
        provides=(),
    ),
    Step(
        id="cs.signing_keys",
        template="fragments/CS_SIGNING_KEYS.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token"),
        provides=(),
    ),
    Step(
        id="cs.trust_services",
        template="01-cs-trust-services.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "ca_host"),
        provides=("ca_id",),
    ),
    Step(
        id="cs.members_owner",
        template="02-cs-members-owner.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "member_class", "xroad_instance"),
        provides=(),
    ),
    # Rendered once per member, in a loop, in generate.py -- the registry
    # holds this step once (design decision 4 of the templates plan; join-a
    # plan Task 2 Step 2 applies the same rule here).
    Step(
        id="cs.members_member",
        template="02-cs-members-member.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "member_class"),
        provides=(),
    ),
    Step(
        id="cs.anchor",
        template="03-cs-anchor.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token"),
        provides=("gconf_anchor",),
    ),
    # -- Security Server bring-up. @HOSTVAR@/@P@ (and, for the sign-key step,
    # @SESS_P@/@CAP_P@) are generate.py's sub() tokens, resolved to a
    # concrete per-member Hurl identifier (e.g. "pdga_host") before Hurl ever
    # sees the file -- same step, rendered once per Security Server owner.
    # See the module docstring for why that is not the requires/provides
    # conflation design decision 2 warns about: whatever sub() leaves inside
    # {{...}} or a [Captures] name is a Hurl runtime identifier regardless of
    # whether it still contains an @token@ pending its own substitution.
    Step(
        id="ss.bringup_init",
        template="fragments/SS_BRINGUP_INIT.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "ss_admin_user", "ss_admin_password", "gconf_anchor", "member_class", "token_pin"),
        provides=("@P@_xsrf_token",),
    ),
    # PDGA-only today (main()'s 10-ss-pdga block always renders this; a
    # regular member's build_ss_file() never does -- Task 3 must not start
    # rendering it for a member unless it also becomes that member's own
    # ca_name source). ca_name is the single most-depended-on `provides` in
    # the registry: every ss.auth_key_csr and ss.sign_key_csr step, for
    # every member, reads it back (Tasks 2 and 3).
    Step(
        id="ss.ca_name_capture",
        template="fragments/CA_NAME_CAPTURE.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_xsrf_token"),
        provides=("ca_name",),
    ),
    Step(
        id="ss.auth_key_csr",
        template="fragments/SS_AUTH_KEY_CSR.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_xsrf_token", "ca_name", "csr_country", "xroad_instance", "member_class", "ca_host"),
        provides=("@P@_auth_key_id", "@P@_auth_key_csr_id", "@P@_auth_key_csr", "@P@_auth_key_cert", "@P@_auth_key_cert_hash"),
    ),
    Step(
        id="ss.sign_key_csr",
        template="fragments/MEMBER_SIGN_KEY.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@SESS_P@_xsrf_token", "ca_name", "xroad_instance", "member_class", "csr_country", "ca_host"),
        provides=("@CAP_P@_sign_key_id", "@CAP_P@_sign_key_csr_id", "@CAP_P@_sign_key_csr", "@CAP_P@_sign_key_cert", "@CAP_P@_sign_key_cert_hash"),
    ),
    Step(
        id="ss.bringup_register",
        template="fragments/SS_BRINGUP_REGISTER.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_auth_key_cert_hash", "@P@_xsrf_token", "cs_host", "cs_xsrf_token"),
        provides=("@P@_auth_cert_req_id",),
    ),
    # PDGA-only: nominates the management Security Server as the provider of
    # the CS's own management services. No other member's bring-up runs this.
    Step(
        id="ss.mgmt_register",
        template="fragments/SS_MGMT_REGISTER.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "xroad_instance", "member_class", "@HOSTVAR@", "@P@_xsrf_token"),
        provides=("@P@_client_id", "cs_management_service_address", "cs_management_service_wsdl",
                   "@P@_management_description_id", "@P@_auth_cert_deletion_service_id"),
    ),
    Step(
        id="ss.activate",
        template="fragments/SS_ACTIVATE.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_auth_key_cert_hash", "@P@_xsrf_token"),
        provides=(),
    ),
    # PDGA-only: tsa_name/tsa_url are captured once here and reused by every
    # later ss.tsa_post -- same pattern as ca_name.
    Step(
        id="ss.tsa_capture",
        template="fragments/TSA_CAPTURE.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_xsrf_token"),
        provides=("tsa_name", "tsa_url"),
    ),
    Step(
        id="ss.tsa_post",
        template="fragments/SS_TSA_POST.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_xsrf_token", "tsa_name", "tsa_url"),
        provides=(),
    ),
)

BY_ID: dict[str, Step] = {step.id: step for step in REGISTRY}
