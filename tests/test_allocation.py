"""Unit tests for hurl/generate.py's port and scenario-number allocators.

allocate_ports() and FORBIDDEN_PORT_RANGE exist because of two live incidents,
both documented at length in docs/production-delta.md: port 5000 and port
7000, on both of which macOS's AirPlay Receiver hangs the TCP connection
mid-TLS-handshake rather than refusing it outright -- an afternoon each to
diagnose. tests/test_golden.py only proves the canonical four produce known
output; nothing asserted that a *forbidden* port is actually refused, which
is the property the incidents bought.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "hurl"))
from generate import (  # noqa: E402
    FORBIDDEN_PORT_RANGE,
    FRESH_SERVICE_SCENARIO_START,
    FRESH_SS_SCENARIO_START,
    MEMBER_SIDECAR_ANCHOR,
    PINNED_PORTS,
    PINNED_SCENARIO_NO,
    PINNED_SERVICE_SCENARIO_NO,
    _allocate_numbers,
    allocate_ports,
    member_service_block,
)

CANONICAL = ["pdga", "pnea", "plr", "pnia", "moeys"]

# The two live incidents (docs/production-delta.md), asserted as literal
# values rather than by referencing FORBIDDEN_PORT_RANGE itself: a test that
# only checks membership against the module's own constant would still pass
# if that constant were ever emptied or narrowed by mistake -- it has to
# know independently what "forbidden" means.
_AIRPLAY_5000_RANGE = range(5000, 5100)
_AIRPLAY_7000 = 7000


def _is_airplay_port(port: int) -> bool:
    return port in _AIRPLAY_5000_RANGE or port == _AIRPLAY_7000


def test_canonical_five_keep_their_pinned_ports_exactly():
    ports = allocate_ports(CANONICAL)
    assert ports["pnia"] == (5100, 5180)  # the incident, encoded -- not 5000/5080
    for key, expected in PINNED_PORTS.items():
        assert ports[key] == expected


def test_fresh_member_never_receives_a_forbidden_port():
    ports = allocate_ports(CANONICAL + ["zzzz1"])
    ui, rest = ports["zzzz1"]
    assert not _is_airplay_port(ui)
    assert not _is_airplay_port(rest)


def test_allocation_is_deterministic():
    owners = CANONICAL + ["zzzz1", "zzzz2"]
    assert allocate_ports(list(owners)) == allocate_ports(list(owners))


def test_fresh_member_never_collides_with_a_pinned_port():
    ports = allocate_ports(CANONICAL + ["zzzz1"])
    pinned_uis = {ui for ui, _ in PINNED_PORTS.values()}
    pinned_rests = {rest for _, rest in PINNED_PORTS.values()}
    ui, rest = ports["zzzz1"]
    assert ui not in pinned_uis
    assert rest not in pinned_rests


def test_enough_fresh_members_walk_past_7000_and_still_avoid_it():
    # FRESH_PORT_START is 7000 -- a naive start+n*100 walk lands a member
    # exactly on it (the live incident) without the forbidden-range check.
    # Five fresh members is enough to cross the 5000-5099 and 7000 gaps.
    owners = CANONICAL + [f"fresh{i}" for i in range(5)]
    ports = allocate_ports(owners)
    for key in owners:
        ui, rest = ports[key]
        assert not _is_airplay_port(ui), f"{key}: ui={ui} is forbidden"
        assert not _is_airplay_port(rest), f"{key}: rest={rest} is forbidden"
    assert len({v for pair in ports.values() for v in pair}) == 2 * len(ports)


def test_forbidden_port_range_constant_actually_covers_both_incidents():
    # Not circular with the tests above (which use literal values
    # independently) -- this one instead pins FORBIDDEN_PORT_RANGE itself to
    # what it must contain, so a future edit narrowing it is caught here
    # even if allocate_ports() never happens to walk into the gap in a test.
    assert set(range(5000, 5100)) <= FORBIDDEN_PORT_RANGE
    assert 7000 in FORBIDDEN_PORT_RANGE


def test_pinned_scenario_numbers_kept_and_fresh_starts_at_40():
    keys = list(PINNED_SCENARIO_NO) + ["zzzz1"]
    result = _allocate_numbers(keys, PINNED_SCENARIO_NO, FRESH_SS_SCENARIO_START)
    for key, expected in PINNED_SCENARIO_NO.items():
        assert result[key] == expected
    assert result["zzzz1"] == str(FRESH_SS_SCENARIO_START)


def test_pinned_service_scenario_numbers_kept_and_fresh_starts_at_50():
    keys = list(PINNED_SERVICE_SCENARIO_NO) + ["zzzz1"]
    result = _allocate_numbers(keys, PINNED_SERVICE_SCENARIO_NO, FRESH_SERVICE_SCENARIO_START)
    for key, expected in PINNED_SERVICE_SCENARIO_NO.items():
        assert result[key] == expected
    assert result["zzzz1"] == str(FRESH_SERVICE_SCENARIO_START)


def test_allocate_numbers_no_collisions_with_several_fresh_keys():
    keys = list(PINNED_SCENARIO_NO) + ["aaa", "zzz", "mmm"]
    result = _allocate_numbers(keys, PINNED_SCENARIO_NO, FRESH_SS_SCENARIO_START)
    assert len(set(result.values())) == len(result)


def test_allocate_numbers_is_deterministic():
    keys = list(PINNED_SCENARIO_NO) + ["aaa", "zzz"]
    assert (
        _allocate_numbers(list(keys), PINNED_SCENARIO_NO, FRESH_SS_SCENARIO_START)
        == _allocate_numbers(list(keys), PINNED_SCENARIO_NO, FRESH_SS_SCENARIO_START)
    )


# -- the bind guard on a joined member's own Security Server (join-c) --
# The only own-server compose block the golden corpus contains is the empty
# `services: {}` variant (no canonical member owns a joined server), so
# tests/test_golden.py never renders this branch at all -- which is exactly
# how it shipped, publishing an unauthenticated X-Road proxy
# port on 0.0.0.0, caught only by scripts/check-exposure.sh during a
# hand-driven --full own-server cycle. This is the --fast-tier guard that
# would have caught it on the commit instead.
def test_member_service_block_binds_both_ports_to_the_configured_interface():
    block = member_service_block("pvtb", "ss-pvtb", 7100, 7180)
    ports = next(line for line in block.splitlines() if line.strip().startswith("ports:"))
    # The literal, not a reference to some constant in generate.py: a test
    # that checked the module's own value would still pass if that value were
    # changed to a bare mapping -- same reasoning as _AIRPLAY_5000_RANGE above.
    assert ports.strip() == (
        'ports: ["${XROAD_BIND:-127.0.0.1}:7100:4000", '
        '"${XROAD_BIND:-127.0.0.1}:7180:8080", '
        # The TLS client proxy, UI+443 -- docs/production-delta.md row 19.
        # Bound to the same interface as the other two, which is the whole
        # point of this test: a third published port is a third place the
        # bind policy can be forgotten.
        '"${XROAD_BIND:-127.0.0.1}:7543:8443"]'
    ), f"a joined member's own Security Server must honour deployment.yaml's network.bind, got: {ports.strip()}"


def test_member_service_block_matches_the_hand_written_compose_convention():
    """The generated block's ports: line must use the same
    ${XROAD_BIND:-127.0.0.1} form docker-compose.yml already uses for every
    canonical server -- one convention, not two that can drift apart."""
    compose = (pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
    hand_written = [l.strip() for l in compose.splitlines() if l.strip().startswith("ports:")]
    assert hand_written, "docker-compose.yml declares no ports: lines -- this test is checking nothing"
    assert all("${XROAD_BIND:-127.0.0.1}:" in l for l in hand_written), hand_written
    assert "${XROAD_BIND:-127.0.0.1}:" in member_service_block("x", "ss-x", 1, 2)


def _hand_written_sidecar_anchor() -> dict[str, str]:
    """docker-compose.yml's own x-sidecar block, as a flat key -> value map of
    its top-level (two-space-indented) scalar keys. Parsed rather than
    hard-coded so this test tracks the hand-written file: the property under
    test is that the two anchors AGREE, not that either holds one literal."""
    compose = (pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
    lines = compose.splitlines()
    start = lines.index("x-sidecar: &sidecar")
    out: dict[str, str] = {}
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break  # next top-level key -- the anchor is over
        if not line.startswith("  ") or line.startswith("   ") or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.strip().partition(":")
        if sep:
            out[key] = value.strip()
    return out


# The hardening pass that added restart/mem_limit/cpus to docker-compose.yml's
# x-sidecar never reached the anchor generate.py redeclares in
# compose.members.yml -- a YAML anchor does not cross Compose's -f boundaries,
# so a joined member's own Security Server inherited none of it and shipped
# with no restart policy and no resource ceiling. The golden corpus cannot
# catch this (it only ever holds the empty `services: {}` variant, see the
# comment above), so this is the guard.
def test_the_generated_sidecar_anchor_carries_the_same_hardening_as_the_hand_written_one():
    hand = _hand_written_sidecar_anchor()
    for field in ("restart", "mem_limit", "cpus"):
        assert field in hand, (
            f"docker-compose.yml's x-sidecar no longer declares {field}: -- this test's "
            "reference point is gone, not the generated block's problem"
        )
        assert f"  {field}: {hand[field]}\n" in MEMBER_SIDECAR_ANCHOR, (
            f"compose.members.yml's generated x-sidecar must declare {field}: {hand[field]}, "
            f"matching docker-compose.yml's. Got:\n{MEMBER_SIDECAR_ANCHOR}"
        )


def test_a_joined_members_own_server_gets_a_healthcheck_start_period():
    """Without it, every probe during the 215-234s a Sidecar measurably takes
    to boot reports "unhealthy" instead of "starting" -- the same false alarm
    docker-compose.yml's x-sidecar grew start_period to avoid, and the same
    240s budget."""
    block = member_service_block("pvtb", "ss-pvtb", 7100, 7180)
    assert "      start_period: 240s\n" in block, block
