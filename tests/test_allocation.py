"""Unit tests for hurl/generate.py's port and scenario-number allocators (T5.4).

allocate_ports() and FORBIDDEN_PORT_RANGE exist because of two live incidents,
both documented at length in docs/production-delta.md: port 5000 and port
7000, on both of which macOS's AirPlay Receiver hangs the TCP connection
mid-TLS-handshake rather than refusing it outright -- an afternoon each to
diagnose. tests/test_golden.py only proves the canonical five produce known
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
    PINNED_PORTS,
    PINNED_SCENARIO_NO,
    PINNED_SERVICE_SCENARIO_NO,
    _allocate_numbers,
    allocate_ports,
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
