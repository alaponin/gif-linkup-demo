"""apps/join-api/schema.py -- the join payload's shape. Mirrors what
prompts/member.md already produces by hand into
configs/member-<key>/<key>.yaml (see configs/member-pnia/2.5.yaml for the
committed shape this typed model is standing in for): member identity, a
Security Server descriptor, the services it publishes (omitted entirely for
a consume-only member), an optional semantic block, the required backend-auth
declaration, and an optional requested_access list for a
consumer.

`origin` is deliberately not a field here. A joined member's
manifest.yaml identity.members.<key> entry always gets `origin: joined`,
forced at the point validate.py turns an approved request into that entry
-- never read off the wire. Leaving the field out of
the model entirely, rather than accepting and discarding it, is the whole
guarantee: there is nothing in JoinPayload a hand-crafted payload could set
to make a join look canonical.

extra="forbid" everywhere: a payload with an unrecognised key (a typo, or a
deliberate "origin": "canonical") is a schema-validation failure (check 1),
not a silently-ignored extra field.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackendAuth(str, Enum):
    """How the joining member's own backend authenticates calls
    from the Security Server. The enum -- not a configs/x-road-bus/2.7.yaml
    policy key -- is deliberate ("the permissible values of a field
    are a schema concern")."""

    none = "none"
    network_allowlist = "network_allowlist"
    proxy_injected = "proxy_injected"


class SecurityServer(_Strict):
    code: str
    dns_name: str
    # A DNS name an EXISTING member already owns: this member's subsystem
    # becomes an extra client on that server and owns no container at all.
    hosted_on: str | None = None
    # Plan C: this member brings up its OWN Security Server (job.py's
    # own-server branch). Deliberately an EXPLICIT opt-in rather
    # than inferred from an absent hosted_on -- configs/x-road-bus/2.7.yaml's
    # join.default_hosting: hosted_on says in as many words that "own_server
    # must be asked for", and a payload that simply forgot hosted_on would
    # otherwise become a silent own-server join that sits in BLOCKED waiting
    # for infrastructure nobody agreed to stand up. validate.py's hosting
    # check rejects a request that sets neither, and one that
    # sets both.
    own_server: bool = False


class SLA(_Strict):
    """Module 5.3's five terms, "reuse the same
    template for every service on the bus" -- hence one SLA per Service, not
    per member. Free text like `lawful_basis` above:
    this pack has no numeric target registry to check these against, and a
    demo's own targets need to stay editable prose ("99.5% monthly uptime"),
    not a schema-enforced number."""

    availability: str
    response_time: str
    support_hours: str
    incident_response: str
    change_notice: str
    signatory: str


class Service(_Strict):
    code: str
    spec_url: str
    # Consumer subsystems this service's ACL grants, PROGRESSA/GOV/<CODE>/
    # <SUBSYSTEM> form -- configs/member-pnia/2.5.yaml's own access: shape.
    access: list[str] = Field(default_factory=list)
    # The decree article this exchange relies on, or "consent" --
    # free text, "[confirm: cite the decree article]" where a demo has no
    # real one to cite. Recorded and surfaced, never resolved against
    # anything: Module 2's decree is not in this pack, so there is nothing
    # to check it against, and a resolution check against a file we also
    # wrote would prove nothing.
    lawful_basis: str | None = None
    # Optional at the schema level, enforced at validate.py instead (spec
    # S8-style: a missing SLA on a published service is a REJECTED request
    # naming the check, not a parse failure) -- required for a provider,
    # optional for a consumer-only member (who has no services to attach
    # one to in the first place).
    sla: SLA | None = None


class ExchangePattern(str, Enum):
    """The contract shape a semantic exchange takes. The enum -- not
    a configs/x-road-bus/2.7.yaml policy key -- is
    deliberate ("the permissible values of a field are a schema
    concern"), the same rule BackendAuth above already follows.
    Vocabulary: docs/pattern-register.md."""

    registration = "registration"
    digital_registries_lookup = "digital_registries_lookup"
    consent = "consent"
    messaging = "messaging"
    payments = "payments"


class Semantic(_Strict):
    entity: str
    key: str
    fields: list[str]
    # Optional: making it required would reject every existing config until
    # all are classified against ExchangePattern.
    pattern: ExchangePattern | None = None


class Backend(_Strict):
    auth: BackendAuth


class MemberRequirements(_Strict):
    """Module 5.2's six-item checklist -- "states, up
    front, exactly what an agency must have in place before it can join."
    Required on every JoinPayload, provider or consumer: 5.2 precedes
    registration for everyone, not only for a member that publishes a
    service. All six as stated fields, not a mix of asserted and
    API-derived ones -- the teaching
    value is that the applicant answers the checklist.

    `lawful_basis` is the one item that reuses a field rather than
    declaring a second copy of it: Service.lawful_basis
    already carries this for a provider's services, so a provider can leave
    this None and rely on those; a consumer-only member, which has no
    services to attach one to, states it here instead."""

    has_security_server: bool
    has_registered_identity: bool
    standards_portfolio_adopted: bool
    data_conformant: bool
    lawful_basis: str | None = None
    technical_contact: str


class JoinPayload(_Strict):
    code: str
    name: str
    subsystem: str
    subsystem_description: str
    security_server: SecurityServer
    # Omit entirely (empty list) if this agency only consumes -- prompts/
    # member.md's own wording, carried into the API.
    services: list[Service] = Field(default_factory=list)
    semantic: Semantic | None = None
    backend: Backend
    member_requirements: MemberRequirements
    # Recorded and surfaced to the operator, never acted on by this API
    # -- a provider granting access is that provider's own
    # config, which this API cannot touch.
    requested_access: list[str] = Field(default_factory=list)
