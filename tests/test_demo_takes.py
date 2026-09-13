"""The film profile's two contracts: beats.yaml (what capture.py and
scripts/demo-capture.sh run) and takes.json (what the deck builder reads).
No browser, no federation -- the shapes only; a real capture run is
scripts/demo-capture.sh."""
from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
BEATS = yaml.safe_load((PACK / "apps/console/capture/beats.yaml").read_text())
TAKES = PACK / "out/demo-takes/takes.json"
ACTIONS = {"goto", "tab", "click", "wait", "wait_js", "step", "scroll", "pin", "expect", "receipts",
           "record_until", "record_for", "still"}


def check_takes(takes: dict) -> None:
    """The schema: every key the deck builder's provenance line and slides read."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", takes["xroad_version"])
    assert re.fullmatch(r"[0-9a-f]{40}", takes["pack_commit"])
    assert isinstance(takes["pack_dirty"], bool)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", takes["captured_at"])
    assert re.fullmatch(r"\d{11}", takes["nin"])
    assert takes["beats"], "no beats"
    for beat_id, b in takes["beats"].items():
        assert b["kind"] in {"frame", "clip", "text"}, beat_id
        assert b["file"].startswith(beat_id + "."), beat_id
        assert b["caption"].strip(), beat_id
        assert re.fullmatch(r"[0-9a-f]{64}", b["sha256"]), beat_id
        if b["kind"] == "clip":
            assert b["still"] == beat_id + ".png", beat_id


def test_beats_are_well_formed():
    ids = [b["id"] for b in BEATS["beats"]]
    assert len(ids) == len(set(ids)), "duplicate beat id"
    for b in BEATS["beats"]:
        assert b["kind"] in {"frame", "clip", "text"}, b["id"]
        assert b["caption"].strip() and b["assert"], b["id"]
        if b.get("host"):
            assert b["kind"] == "text" and "actions" not in b, f"{b['id']}: a host beat is text, run by the shell"
            continue
        for action in b["actions"]:
            (verb,) = action
            assert verb in ACTIONS, f"{b['id']}: unknown action {verb}"
        if b["kind"] == "clip":
            verbs = [next(iter(a)) for a in b["actions"]]
            assert "record_until" in verbs and "still" in verbs, f"{b['id']}: a clip records and names its still"


def test_captions_are_distinct():
    """draft_cues.py places a slide by the words only it carries."""
    captions = [b["caption"] for b in BEATS["beats"]]
    assert len(captions) == len(set(captions))


def test_schema_accepts_a_well_formed_takes_file():
    check_takes({
        "video": "5.6", "nin": "02831663233", "xroad_version": "7.7.0", "pack_commit": "a" * 40,
        "pack_dirty": False, "captured_at": "2026-09-13T16:00:00Z",
        "beats": {"C6-break-restore": {"file": "C6-break-restore.mp4", "kind": "clip", "caption": "x",
                                       "sha256": "b" * 64, "still": "C6-break-restore.png"}},
    })


@pytest.mark.skipif(not TAKES.is_file(), reason="no capture run in out/demo-takes")
def test_the_last_capture_run_matches_the_schema_and_its_files():
    takes = json.loads(TAKES.read_text())
    check_takes(takes)
    assert set(takes["beats"]) == {b["id"] for b in BEATS["beats"]}
    for b in takes["beats"].values():
        assert (TAKES.parent / b["file"]).is_file(), b["file"]
