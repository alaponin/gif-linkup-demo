"""run-linkup.sh's health wait must cover every Security Server there is.

The wait used to name cs/ca/ss-pdga/ss-pnea/ss-plr literally and so skipped
ss-pnia: the one bounded loop that exists to survive a slow restart did not
watch the server most likely to be slow, and a slow ss-pnia surfaced as the
Hurl run failing against a server that was still booting rather than as this
loop's clear, timed message. The list is derived from SS_ORDER now
(hurl/topology.sh, generated from configs/ + manifest.yaml), which also picks
up a joined member's own Security Server for free.

This test runs the derivation for real against a SS_ORDER the pack does not
have, so re-hardcoding the canonical names would fail it.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

RUN_LINKUP = pathlib.Path(__file__).resolve().parent.parent / "hurl" / "run-linkup.sh"


def _health_block() -> str:
    text = RUN_LINKUP.read_text()
    start = text.index("_HEALTH_TARGETS=(")
    return text[start:text.index("\ndone", start)]


def test_targets_are_derived_from_ss_order() -> None:
    assignment = _health_block().splitlines()[0]
    out = subprocess.run(
        ["bash", "-c", f'SS_ORDER=(ss-one ss-two ss-late); {assignment}; printf "%s\\n" "${{_HEALTH_TARGETS[@]}}"'],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out == ["cs", "ca", "ss-one", "ss-two", "ss-late"]


def test_wait_and_failure_message_name_no_server_literally() -> None:
    # Both the docker inspect call and the timeout message have to expand the
    # array -- a literal list in either one is the bug coming back.
    assert not re.search(r"\bss-[a-z]+", _health_block())
