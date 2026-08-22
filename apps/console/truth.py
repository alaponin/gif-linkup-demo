"""apps/console/truth.py -- the single reader of pack truth.

Loads hurl/topology.json (hosts, hosting, services, configured ACLs) and
configs/x-road-bus/once-only-exchange.yaml (the exchange: calls, r1 paths,
prefills, the four layer_* strings, asked_once, negative_check). The console
never re-derives topology or exchange semantics -- it renders what this
module loads, and this module never invents a value the files don't already
state.

Two things are load-bearing here:
  - the four layer_* strings are split two-and-two across
    once-only-exchange.yaml's two calls (identity-api: technical+legal;
    enrolment-api: organisational+semantic) -- neither call carries all
    four, so layers() aggregates across both;
  - configs/x-road-bus/once-only-exchange.yaml's negative_check.entrypoint
    is a static string (today "http://ss-plr:8080") that this module never
    trusts directly -- entrypoints are always resolved from topology.json's
    hosted_on instead, the same mechanism the consumer entrypoint above
    uses. This mattered under the old lite profile, where the negative
    check's unauthorised caller could be hosted rather than self-standing
    (MoEYS, before it was retired) -- a literal
    "http://ss-moeys:8080" would have been wrong there. With one topology,
    every canonical member is
    self-hosted, but resolving from topology.json rather than
    special-casing per member is still what a future hosted (joined)
    member's entrypoint depends on, so the mechanism stays.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib

import yaml


FIELD_LABELS = {
    "nin": "NIN",
    "given_name": "Given name",
    "family_name": "Family name",
    "date_of_birth": "Date of birth",
    "sex": "Sex",
    "region": "Region",
    "school": "School",
    "level": "Level",
    "enrolment_year": "Enrolment year",
    "status": "Enrolment status",
}


def _label(name: str) -> str:
    return FIELD_LABELS.get(name, name.replace("_", " ").capitalize())


@dataclasses.dataclass(frozen=True)
class FormField:
    name: str
    label: str
    source: str  # "citizen" or a member_code, e.g. "PNIA"
    group: str   # "citizen", or the lowercased subsystem_code of the call
                 # that prefills it (e.g. "identity", "enrolment") -- derived
                 # from topology.json, never hardcoded per member


@dataclasses.dataclass(frozen=True)
class Truth:
    pack_dir: pathlib.Path
    topology: dict
    exchange: dict
    form_fields: list[FormField]
    expected_acl: dict[str, list[str]]
    layers: dict[str, str]
    consumer_entrypoint: str
    negative_check_entrypoint: str
    identity_mock_base_url: str  # PNIA's mock backend, off the bus entirely --
    # for the legal pane's "held" query only


def load_catalogue(pack_dir: str | pathlib.Path) -> dict | None:
    """onboarding/catalogue.yaml, or None if it has never been rendered.

    Read per call rather than cached in Truth: a join or un-join regenerates
    the file, and this matches join-api's GET /catalogue, which re-derives on
    every call too.
    """
    path = pathlib.Path(pack_dir) / "onboarding" / "catalogue.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


def _member_code(xroad_id: str) -> str:
    """Any X-Road identifier ('A/B/CODE/...' or 'A:B:CODE:...') -> CODE.

    Works for both 4-part member/subsystem ids ('A/B/PNEA/EXAMS') and 5-part
    service ids ('A/B/PNIA/IDENTITY/identity-api') -- the member code is
    always the third component either way.
    """
    return xroad_id.replace(":", "/").split("/")[2]


def _to_colon_id(value: str) -> str:
    """Normalise an X-Road identifier to colon form ('A/B/C/D' -> 'A:B:C:D'),
    matching what the live admin API returns for subject/client ids."""
    return value.replace("/", ":")


def _entrypoint_for_member_code(topology: dict, member_code: str) -> str:
    """Where this member's own information system talks to its Security
    Server -- the plain client proxy (:8080) or the TLS one (:8443),
    decided by the subsystem's own connection_type, exactly the way
    scripts/lib-stack.sh's rest_base() decides it for the shell callers
    (docs/production-delta.md row 19).

    The host name is the Security Server's DNS name either way, and for the
    TLS case that is load-bearing rather than cosmetic: its internal TLS
    certificate is issued for that name, so a URL naming anything else --
    an address, a published-port alias -- fails hostname verification. This
    console runs on the `linkup` network, where the name resolves directly.
    """
    for subsystem in topology["subsystems"]:
        if subsystem["member_code"] == member_code:
            host = subsystem["hosted_on"]
            if subsystem.get("connection_type", "HTTP") != "HTTP":
                return f"https://{host}:8443"
            return f"http://{host}:8080"
    raise RuntimeError(
        f"truth.py: no subsystem in topology.json for member_code {member_code!r} "
        "-- topology.json is stale, re-run hurl/generate.py"
    )


def _subsystem_code_for_member(topology: dict, member_code: str) -> str:
    for subsystem in topology["subsystems"]:
        if subsystem["member_code"] == member_code:
            return subsystem["subsystem_code"]
    raise RuntimeError(f"truth.py: no subsystem in topology.json for member_code {member_code!r}")


def load_truth(pack_dir: str | pathlib.Path) -> Truth:
    pack_dir = pathlib.Path(pack_dir)

    topo_path = pack_dir / "hurl" / "topology.json"
    if not topo_path.exists():
        raise RuntimeError(f"{topo_path} does not exist -- run `python3 hurl/generate.py` first")
    topology = json.loads(topo_path.read_text())

    exchange_cfg = yaml.safe_load((pack_dir / "configs/x-road-bus/once-only-exchange.yaml").read_text())
    exchange = exchange_cfg["exchange"]

    # -- form model: every citizen-provided field, every bus-prefilled field
    # tagged with which member supplies it (from that call's own prefills).
    # Built in call order (citizen field(s) first, then each call's prefills
    # in the order once-only-exchange.yaml lists them) rather than sorted -- the form reads
    # as "who you are, then where you studied", not a database dump.
    citizen_fields = list(exchange["asked_once"]["citizen_provides"])
    prefilled_fields = set(exchange["asked_once"]["prefilled_from_bus"])

    field_source: dict[str, str] = {f: "citizen" for f in citizen_fields}
    field_group: dict[str, str] = {f: "citizen" for f in citizen_fields}
    field_order: list[str] = list(citizen_fields)
    union_of_prefills: set[str] = set()
    for call in exchange["calls"]:
        member_code = _member_code(call["service"])
        group = _subsystem_code_for_member(topology, member_code).lower()
        for field in call.get("prefills", []):
            union_of_prefills.add(field)
            if field in field_source:
                raise RuntimeError(
                    f"truth.py: field {field!r} is prefilled by {member_code} but "
                    "also citizen-provided -- configs/x-road-bus/once-only-exchange.yaml is inconsistent"
                )
            field_source[field] = member_code
            field_group[field] = group
            field_order.append(field)

    # Same invariant scripts/acceptance.sh check 2.6.3 asserts at runtime,
    # checked here at load time instead: coverage AND purpose limitation.
    if union_of_prefills != prefilled_fields:
        raise RuntimeError(
            "truth.py: the union of every call's prefills "
            f"({sorted(union_of_prefills)}) does not equal "
            f"asked_once.prefilled_from_bus ({sorted(prefilled_fields)}) in "
            "configs/x-road-bus/once-only-exchange.yaml"
        )

    form_fields = [
        FormField(name=f, label=_label(f), source=field_source[f], group=field_group[f])
        for f in field_order
    ]

    # -- layers: aggregated across all calls. No single call
    # carries all four -- identity-api has technical+legal, enrolment-api has
    # organisational+semantic.
    layers: dict[str, str] = {}
    for call in exchange["calls"]:
        for key in ("layer_technical", "layer_legal", "layer_organisational", "layer_semantic"):
            if key in call:
                layers[key[len("layer_"):]] = call[key]

    # -- expected ACL, normalised to colon form (matches the live admin API).
    expected_acl: dict[str, list[str]] = {}
    for subsystem in topology["subsystems"]:
        for svc in subsystem["services"]:
            expected_acl[svc["code"]] = [_to_colon_id(a) for a in svc["access"]]

    # -- entrypoints resolved from topology, never from once-only-exchange.yaml's static
    # (profile-unaware) entrypoint fields -- see module docstring.
    consumer_entrypoint = _entrypoint_for_member_code(topology, _member_code(exchange["consumer"]))
    negative_check_entrypoint = _entrypoint_for_member_code(
        topology, _member_code(exchange["negative_check"]["unauthorised_client"])
    )

    # PNIA's mock backend, off the bus -- the "held" query never goes near
    # X-Road (module docstring). Derived from configs/member-pnia/pnia.yaml's
    # spec_url the same way apps/mock-registry/app.py's own servers.url is:
    # {base}/spec.yaml -> {base}/v1.
    pnia_config = yaml.safe_load((pack_dir / "configs/member-pnia/pnia.yaml").read_text())
    identity_spec_url = next(
        svc["spec_url"] for svc in pnia_config["services"] if svc["code"] == "identity-api"
    )
    identity_mock_base_url = identity_spec_url.removesuffix("/spec.yaml") + "/v1"

    return Truth(
        pack_dir=pack_dir,
        topology=topology,
        exchange=exchange,
        form_fields=form_fields,
        expected_acl=expected_acl,
        layers=layers,
        consumer_entrypoint=consumer_entrypoint,
        negative_check_entrypoint=negative_check_entrypoint,
        identity_mock_base_url=identity_mock_base_url,
    )
