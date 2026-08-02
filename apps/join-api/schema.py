"""apps/join-api/schema.py -- the join payload's shape (join-b Task 2, spec
S3/S9). Mirrors what prompts/member.md already produces by hand into
configs/member-<key>/<key>.yaml (see configs/member-pnia/2.5.yaml for the
committed shape this typed model is standing in for): member identity, a
Security Server descriptor, the services it publishes (omitted entirely for
a consume-only member), an optional semantic block, the backend-auth
declaration spec S2.5 requires, and an optional requested_access list for a
consumer (spec S2.7).

`origin` is deliberately not a field here. A joined member's
manifest.yaml identity.members.<key> entry always gets `origin: joined`,
forced at the point validate.py turns an approved request into that entry
(spec S8 check 4, S9) -- never read off the wire. Leaving the field out of
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
    """spec S2.5: how the joining member's own backend authenticates calls
    from the Security Server. The enum -- not a configs/x-road-bus/2.7.yaml
    policy key -- is deliberate (spec S8: "the permissible values of a field
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
    # own-server branch, spec S6). Deliberately an EXPLICIT opt-in rather
    # than inferred from an absent hosted_on -- configs/x-road-bus/2.7.yaml's
    # join.default_hosting: hosted_on says in as many words that "own_server
    # must be asked for", and a payload that simply forgot hosted_on would
    # otherwise become a silent own-server join that sits in BLOCKED waiting
    # for infrastructure nobody agreed to stand up. validate.py's hosting
    # check (S8 check 6) rejects a request that sets neither, and one that
    # sets both.
    own_server: bool = False


class Service(_Strict):
    code: str
    spec_url: str
    # Consumer subsystems this service's ACL grants, PROGRESSA/GOV/<CODE>/
    # <SUBSYSTEM> form -- configs/member-pnia/2.5.yaml's own access: shape.
    access: list[str] = Field(default_factory=list)


class Semantic(_Strict):
    entity: str
    key: str
    fields: list[str]


class Backend(_Strict):
    auth: BackendAuth


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
    # Recorded and surfaced to the operator, never acted on by this API
    # (spec S2.7) -- a provider granting access is that provider's own
    # config, which this API cannot touch.
    requested_access: list[str] = Field(default_factory=list)
