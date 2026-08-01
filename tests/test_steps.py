"""hurl/steps.py's registry contract -- join-a-step-registry plan Task 1/4.

Grows in Task 4 into the checker that proves requires/provides matches the
templates; for now it only proves the registry's ids are well-formed.
"""
from __future__ import annotations

import pathlib
import re
import sys

PACK = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK / "hurl"))

import steps as steps_module  # noqa: E402

ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_]+)+$")


def test_step_ids_are_unique_and_well_formed():
    ids = [step.id for step in steps_module.REGISTRY]
    assert len(ids) == len(set(ids)), f"duplicate step id(s): {sorted(set(x for x in ids if ids.count(x) > 1))}"
    malformed = [i for i in ids if not ID_RE.match(i)]
    assert not malformed, f"step id(s) not matching {ID_RE.pattern}: {malformed}"
