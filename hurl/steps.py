"""The Linkup admin-API sequence as an ordered registry of named steps.

hurl/generate.py renders this registry in order to build hurl/scenarios/ --
same templates, same order, same bytes as before this module existed.
apps/join-api/job.py is the other consumer: it walks the registry one step
at a time, driving each step's template against the live federation and
threading its `provides` captures into the next step's `requires`.

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
    actor: str                  # "operator" | "member"
    requires: tuple[str, ...]   # Hurl {{var}} names this step reads
    provides: tuple[str, ...]   # Hurl [Captures] names this step writes
    # "has this already happened?" -- filename under hurl/templates/, or None.
    # Set only for the 409-ambiguous class identified by 's
    # audit (see the classification comment above each step
    # below). Read-only and 409-safe steps need no probe: 409-as-success is
    # the default, proven live for service.acl
    # (apps/console/xroad.py's 409 handling).
    probe: str | None = None
    # Class (d) from the same audit: True means Plan B's runner must refuse
    # to resume across this step (no probe can save it -- neither read-only
    # nor 409-safe nor resolvable by reading state back). Defaults False;
    # tests/test_steps.py asserts the registry has none today.
    unsafe_to_repeat: bool = False
    # "how do I undo this?" -- filename under hurl/templates/, or None.
    # Default None: most steps still do not have one, and that is correct --
    # only the six steps live-verified a working reversal
    # for (docs/decisions/xroad-770-notes.md #11) carry one. Like `template`, this is a
    # template filename, not a second Step: the reversal's own `requires` are
    # read straight off its .tmpl file (tests/test_steps.py's `_extract()`,
    # same as for `template`), never declared a second time here. Reversal
    # EXECUTION ORDER is not derivable from this field or from reversing
    # REGISTRY -- see REVERSAL_ORDER below.
    reverse: str | None = None


# Ordered registry: generate.py renders these in order. Order here IS the
# executable sequence -- the hosted-client ordering below relies on this
# order and must not reintroduce the bug it describes.
#
# -- 409-safety classification --------------
# Every step below is tagged with one of:
#   (a) read-only        -- no mutation, always safe to re-run.
#   (b) 409-safe mutation -- repeat either conflicts cleanly (409, proven
#       live for service.acl -- apps/console/xroad.py)
#       or is a state-setting call that's naturally idempotent (e.g. a
#       PATCH to the same value).
#   (c) ambiguous -- carries a `probe`. Two distinct failure modes
#       land here, both worth a probe even though only one was
#       anticipated: some of these create a NEW resource
#       with no natural uniqueness constraint (a repeat silently doubles
#       key/CA material rather than 409ing at all -- not "ambiguous 409",
#       genuinely ABSENT 409, arguably the harder case); others bundle a
#       submit-then-approve pair whose completion can diverge if the
#       process died in between (a repeat's submit half may cleanly 409
#       while the approval half is still outstanding).
#   (d) unsafe to repeat at all -- none found; tests/test_steps.py asserts
#       this class stays empty.
# Audited count: 3 (a), 10 (b), 8 (c), 0 (d) of 21 steps -- roughly a third
# need a probe, more than the "rare" framing anticipated but not
# "most" of them.
REGISTRY: tuple[Step, ...] = (
    # (b) POST /login is a re-authenticate (idempotent); POST /initialization
    # is a bootstrap-once call X-Road is expected to 409 on repeat --
    # UNVERIFIED for this specific endpoint until confirmed against a live
    # deploy.
    Step(
        id="cs.init",
        template="fragments/CS_INIT.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_admin_user", "cs_admin_password", "token_pin", "xroad_instance"),
        provides=("cs_xsrf_token",),
    ),
    # (b) POST /member-classes has a natural unique key (code) -- repeat
    # conflicts.
    Step(
        id="cs.member_class",
        template="fragments/CS_MEMBER_CLASS.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "member_class"),
        provides=(),
    ),
    # (b) PUT /tokens/0/login on an already-logged-in token is naturally
    # idempotent.
    Step(
        id="cs.token_login",
        template="fragments/CS_TOKEN_LOGIN.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "token_pin"),
        provides=(),
    ),
    # (c) POST .../signing-keys creates a NEW key every call -- no natural
    # uniqueness, so a repeat silently doubles the CS's signing keys rather
    # than 409ing. Cold-deploy-only (Plan B's join flow never reaches this
    # step), so lower urgency than the ss.*/service.* probes below, but
    # audited and probed for registry completeness.
    Step(
        id="cs.signing_keys",
        template="fragments/CS_SIGNING_KEYS.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token"),
        provides=(),
        probe="fragments/PROBE_CS_SIGNING_KEYS.hurl.tmpl",
    ),
    # (c) Same reasoning as cs.signing_keys: POST /certification-services
    # (and the OCSP-responder/timestamping-service POSTs bundled in the same
    # step) have no confirmed uniqueness constraint. Cold-deploy-only.
    Step(
        id="cs.trust_services",
        template="01-cs-trust-services.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "ca_host"),
        provides=("ca_id",),
        probe="fragments/PROBE_CS_TRUST_SERVICES.hurl.tmpl",
    ),
    # (b) POST /members and /subsystems have a natural unique key
    # (member_id/subsystem_id); the PATCH to management-services-configuration
    # is idempotent (same value every time for the owner).
    Step(
        id="cs.members_owner",
        template="02-cs-members-owner.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "member_class", "xroad_instance"),
        provides=(),
    ),
    # (b) Same reasoning as cs.members_owner -- and the one cs.* step Plan B's
    # join flow actually reaches (a new member's own registration on the CS).
    # Rendered once per member, in a loop, in generate.py -- the registry
    # holds this step once -- the same rule applies here.
    # Reversal live-verified (docs/decisions/xroad-770-notes.md
    # #11, table row 6) -- sixth and LAST step in the reversal order
    # (REVERSAL_ORDER below): the member's identity leaves the Central
    # Server only after every SS-side call has undone the member's bus
    # presence.
    Step(
        id="cs.members_member",
        template="02-cs-members-member.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "member_class"),
        provides=(),
        reverse="fragments/CS_MEMBER_DELETE.hurl.tmpl",
        probe="fragments/PROBE_CS_MEMBER_DELETE.hurl.tmpl",
    ),
    # (a) read-only: downloads the current anchor, nothing to conflict on.
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
    # conflation it warns about: whatever sub() leaves inside
    # {{...}} or a [Captures] name is a Hurl runtime identifier regardless of
    # whether it still contains an @token@ pending its own substitution.
    #
    # `actor` below is declared for the join lifecycle a joining member goes
    # through (build_ss_file's own-server path): "the anchor-upload through
    # cert-import run is member; CS approval is operator".
    # Two call sites are declared exceptions to these defaults,
    # documented at the call site rather than as a second field, because Plan
    # A has no executor to read either value yet:
    #   - main()'s 10-ss-pdga block reuses ss.bringup_init/ss.auth_key_csr/
    #     ss.sign_key_csr/ss.activate/ss.tsa_post to bring up the *operator's*
    #     own management Security Server, never a joining member's -- read
    #     "operator" there regardless of the default below.
    #   - build_hosted_client()'s ss.client_add/ss.sign_key_csr/service.publish/
    #     service.acl steps run against the HOST's Security Server on behalf of
    #     a member with none of its own -- "under hosted_on, every step is
    #     operator" regardless of the defaults below.
    # (b) bundles anchor upload (replace-with-same-content is a no-op),
    # login (idempotent) and initialization (bootstrap-once, same reasoning
    # as cs.init) and token-login (idempotent).
    Step(
        id="ss.bringup_init",
        template="fragments/SS_BRINGUP_INIT.hurl.tmpl",
        actor="member",
        requires=("@HOSTVAR@", "ss_admin_user", "ss_admin_password", "gconf_anchor", "member_class", "token_pin"),
        provides=("@P@_xsrf_token",),
    ),
    # (a) read-only. PDGA-only today (main()'s 10-ss-pdga block always
    # renders this; a regular member's build_ss_file() never does -- doing
    # so for a member would require that member to also become its own
    # ca_name source). ca_name is the single most-depended-on
    # `provides` in the registry: every ss.auth_key_csr and ss.sign_key_csr
    # step, for every member, reads it back.
    Step(
        id="ss.ca_name_capture",
        template="fragments/CA_NAME_CAPTURE.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_xsrf_token"),
        provides=("ca_name",),
    ),
    # (c) POST .../keys-with-csrs creates a NEW AUTH key every call -- no
    # natural uniqueness. Join-relevant (every member's own bring-up runs
    # this).
    Step(
        id="ss.auth_key_csr",
        template="fragments/SS_AUTH_KEY_CSR.hurl.tmpl",
        actor="member",
        requires=("@HOSTVAR@", "@P@_xsrf_token", "ca_name", "csr_country", "xroad_instance", "member_class", "ca_host"),
        provides=("@P@_auth_key_id", "@P@_auth_key_csr_id", "@P@_auth_key_csr", "@P@_auth_key_cert", "@P@_auth_key_cert_hash"),
        probe="fragments/PROBE_SS_AUTH_KEY.hurl.tmpl",
    ),
    # (c) Same reasoning as ss.auth_key_csr, for the SIGN key. Join-relevant.
    # Reversal live-verified (docs/decisions/xroad-770-notes.md
    # #11, table row 5; "What happens to a hosted member's SIGN key") --
    # fifth step in the reversal order (REVERSAL_ORDER below), last of the
    # SS-side calls: the client goes before its key even backwards, so this
    # runs after ss.client_add's delete, not before it.
    #
    # probe is UNCHANGED, not the table's own literal GET
    # /token-certificates/{hash} (that endpoint IS confirmed live --
    # apps/join-api/tests/fixtures/xroad/unjoin.sign_key_delete.probe.json
    # -- but needs @CAP_P@_sign_key_cert_hash, itself only knowable from the
    # very key this step is trying to determine the fate of). Reusing
    # PROBE_SS_SIGN_KEY.hurl.tmpl's GET /tokens/0 answers both questions a
    # reversal executor actually has from the SAME one read: which key_id
    # to delete (correlate by keys[].certificates[].owner_id, never by the
    # shared "Sign key" label -- SS_SIGN_KEY_DELETE.hurl.tmpl's own
    # comment) and whether that key is already gone (absence: no SIGNING
    # key whose certificate's owner_id ends with this member).
    Step(
        id="ss.sign_key_csr",
        template="fragments/MEMBER_SIGN_KEY.hurl.tmpl",
        actor="member",
        requires=("@HOSTVAR@", "@SESS_P@_xsrf_token", "ca_name", "xroad_instance", "member_class", "csr_country", "ca_host"),
        provides=("@CAP_P@_sign_key_id", "@CAP_P@_sign_key_csr_id", "@CAP_P@_sign_key_csr", "@CAP_P@_sign_key_cert", "@CAP_P@_sign_key_cert_hash"),
        probe="fragments/PROBE_SS_SIGN_KEY.hurl.tmpl",
        reverse="fragments/SS_SIGN_KEY_DELETE.hurl.tmpl",
    ),
    # (c) bundles PUT .../register with a GET-pending-then-approve pair whose
    # completion can diverge on a process death in between. Join-relevant.
    Step(
        id="ss.bringup_register",
        template="fragments/SS_BRINGUP_REGISTER.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_auth_key_cert_hash", "@P@_xsrf_token", "cs_host", "cs_xsrf_token"),
        provides=("@P@_auth_cert_req_id",),
        probe="fragments/PROBE_SS_BRINGUP_REGISTER.hurl.tmpl",
    ),
    # (c) Same partial-completion risk as ss.bringup_register, across six
    # bundled sub-actions. PDGA-only: nominates the management Security
    # Server as the provider of the CS's own management services. No other
    # member's bring-up runs this -- lower priority for Plan B than the
    # join-relevant probes above, kept for registry completeness.
    Step(
        id="ss.mgmt_register",
        template="fragments/SS_MGMT_REGISTER.hurl.tmpl",
        actor="operator",
        requires=("cs_host", "cs_xsrf_token", "xroad_instance", "member_class", "@HOSTVAR@", "@P@_xsrf_token"),
        provides=("@P@_client_id", "cs_management_service_address", "cs_management_service_wsdl",
                   "@P@_management_description_id", "@P@_auth_cert_deletion_service_id"),
        probe="fragments/PROBE_SS_MGMT_REGISTER.hurl.tmpl",
    ),
    # (b) PUT .../activate on an already-active cert is a state-transition
    # X-Road is expected to 409 on repeat.
    Step(
        id="ss.activate",
        template="fragments/SS_ACTIVATE.hurl.tmpl",
        actor="member",
        requires=("@HOSTVAR@", "@P@_auth_key_cert_hash", "@P@_xsrf_token"),
        provides=(),
    ),
    # (a) read-only. PDGA-only: tsa_name/tsa_url are captured once here and
    # reused by every later ss.tsa_post -- same pattern as ca_name.
    Step(
        id="ss.tsa_capture",
        template="fragments/TSA_CAPTURE.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@P@_xsrf_token"),
        provides=("tsa_name", "tsa_url"),
    ),
    # (c) POST /system/timestamping-services has no confirmed uniqueness
    # constraint on name/url (a Security Server can have multiple approved
    # TSAs) -- same "new resource, no natural key" reasoning as
    # ss.auth_key_csr/ss.sign_key_csr. Join-relevant.
    Step(
        id="ss.tsa_post",
        template="fragments/SS_TSA_POST.hurl.tmpl",
        actor="member",
        requires=("@HOSTVAR@", "@P@_xsrf_token", "tsa_name", "tsa_url"),
        provides=(),
        probe="fragments/PROBE_SS_TSA_POST.hurl.tmpl",
    ),
    # ss.client_add -> [ss.sign_key_csr] -> ss.client_register is the order
    # every caller must render these in (build_ss_file, build_hosted_client):
    # client-add must precede its SIGN-key generation, which must precede its
    # registration -- the signer rejects a member_id it doesn't yet recognize
    # as a client with 400 client_not_found (found live for the lite profile).
    # Reordering this list reintroduces that bug.
    # (b) POST /clients has a natural unique key (member_class+member_code+
    # subsystem_code) -- repeat conflicts.
    # Reversal live-verified (docs/decisions/xroad-770-notes.md
    # #11, table row 4) -- fourth step in the reversal order (REVERSAL_ORDER
    # below), after ss.client_register's unregister and before
    # ss.sign_key_csr's key delete: the client goes before its key,
    # backwards just as forwards (steps.py's own comment above).
    Step(
        id="ss.client_add",
        template="fragments/MEMBER_CLIENT_ADD.hurl.tmpl",
        actor="member",
        requires=("@HOSTVAR@", "@SESS_P@_xsrf_token", "member_class"),
        provides=("@CAP_P@_client_id",),
        reverse="fragments/MEMBER_CLIENT_DELETE.hurl.tmpl",
        probe="fragments/PROBE_MEMBER_CLIENT_DELETE.hurl.tmpl",
    ),
    # (c) Same partial-completion risk as ss.bringup_register (PUT
    # .../register then GET-pending-then-approve). Join-relevant -- every
    # member's own bring-up AND every hosted client runs this.
    # Reversal live-verified (docs/decisions/xroad-770-notes.md
    # #11, table row 3) -- third step in the reversal order
    # (REVERSAL_ORDER below), and the one whose ordering is NOT the mirror
    # of the forward client_add -> sign_key_csr -> client_register sequence
    # (see REVERSAL_ORDER's own comment). probe is UNCHANGED: table row 3's
    # probe (GET /clients/{id}, reading .status) is the exact same request
    # PROBE_SS_CLIENT_REGISTER.hurl.tmpl already makes for the forward
    # direction -- reused as-is, not re-derived. The forward interpreter
    # (job.py's _probe_client_registered) reads REGISTERED; a reversal
    # interpreter reads DELETION_IN_PROGRESS from the identical capture.
    Step(
        id="ss.client_register",
        template="fragments/MEMBER_CLIENT_REGISTER.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@CAP_P@_client_id", "@SESS_P@_xsrf_token", "cs_host", "cs_xsrf_token"),
        provides=("@CAP_P@_client_req_id",),
        probe="fragments/PROBE_SS_CLIENT_REGISTER.hurl.tmpl",
        reverse="fragments/MEMBER_CLIENT_UNREGISTER.hurl.tmpl",
    ),
    # (b) POST .../service-descriptions has a natural unique key
    # (rest_service_code per client) -- repeat conflicts; the separate PUT
    # .../enable on an already-enabled description is a state-transition
    # X-Road is expected to 409 on repeat.
    # Reversal live-verified (docs/decisions/xroad-770-notes.md
    # #11, table row 2) -- second step in the reversal order (REVERSAL_ORDER
    # below), after service.acl's revoke and before ss.client_register's
    # unregister.
    Step(
        id="service.publish",
        template="fragments/SERVICE_PUBLISH.hurl.tmpl",
        actor="member",
        requires=("@HOSTVAR@", "@CAP_P@_client_id", "@SESS_P@_xsrf_token", "@SPECVAR@"),
        provides=("@CAP_P@_@SC@_description_id",),
        reverse="fragments/SERVICE_DELETE.hurl.tmpl",
        probe="fragments/PROBE_SERVICE_DELETE.hurl.tmpl",
    ),
    # (b) proven live: 409 on an already-granted access right is treated as
    # success (apps/console/xroad.py's 409 handling) --
    # the one step in this registry with confirmed, not inferred, evidence.
    # Reversal live-verified (docs/decisions/xroad-770-notes.md
    # #11, table row 1) -- first step in the reversal order (REVERSAL_ORDER
    # below): revoke the grant before the service description it grants
    # access to is deleted.
    Step(
        id="service.acl",
        template="fragments/SERVICE_ACL.hurl.tmpl",
        actor="operator",
        requires=("@HOSTVAR@", "@CAP_P@_client_id", "@SESS_P@_xsrf_token"),
        provides=(),
        reverse="fragments/SERVICE_ACL_REVOKE.hurl.tmpl",
        probe="fragments/PROBE_SERVICE_ACL_REVOKE.hurl.tmpl",
    ),
)

BY_ID: dict[str, Step] = {step.id: step for step in REGISTRY}

# The reversal order, for a hosted client's six
# reversal calls -- established LIVE (docs/decisions/xroad-770-notes.md #11 finding 5,
# apps/join-api/tests/fixtures/xroad/unjoin.*.json), NOT `reversed(REGISTRY)`
# and NOT simply build_hosted_client()'s own forward sequence
# (ss.client_add -> ss.sign_key_csr -> ss.client_register) mirrored end to
# end. A naive full mirror of that forward sequence would run
# ss.client_register -> ss.sign_key_csr -> ss.client_add: the SIGN key would
# be deleted before the client that owns it. What was established live
# instead is ss.client_register -> ss.client_add -> ss.sign_key_csr -- the
# client goes before its key, same relative order as forward, only
# ss.client_register moves from last to first. The strict mirror (key before
# client) was never tried live. The runner that performs a reversal walks
# this order; this registry only records it -- nothing in hurl/generate.py
# or Plan A's cold-deploy rendering reads this constant.
#
# What this walk does NOT revoke (withdrawn, per the wave1-corrections
# plan): `service.acl` above revokes ACL entries on the departing member's
# OWN service(s) -- it does not revoke any grant naming the departing member
# AS A SUBJECT on some *other* member's service. That gap is unreachable
# today: schema.py's `requested_access` field is recorded on the join
# request and surfaced to the operator (job.py composes a human-facing
# message telling the operator the target provider must grant access via
# their own config) but nothing in this codebase ever writes an ACL grant on
# another member's behalf from it -- so no reversal walk has ever had such a
# grant to revoke. It becomes reachable the moment a future KP (KP3/KP4)
# adds a joined member that actually consumes another member's service, at
# which point a real "member X as subject on member Y's service" ACL entry
# would exist and this walk would leave it behind on X's un-join.
REVERSAL_ORDER: tuple[str, ...] = (
    "service.acl",
    "service.publish",
    "ss.client_register",
    "ss.client_add",
    "ss.sign_key_csr",
    "cs.members_member",
)
