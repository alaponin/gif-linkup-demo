"""apps/console/journal.py -- append-only log of ACL mutations, and how to
reverse them. The reset path's safety depends entirely on this: every
mutation is journalled with applied=False BEFORE the live call, and marked
applied=True only after it succeeds. A crash between those two writes still
leaves an entry on disk with everything reset() needs to reverse it -- the
entry's prior_state, not its own action, is what gets restored, which is
also why replaying an already-applied entry is safe (xroad.py's grant/revoke
both treat "already in that state" as success, not failure).
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time
from typing import Callable


@dataclasses.dataclass
class JournalEntry:
    ts: float
    action: str          # "grant" or "revoke" -- which mutation was requested
    ss: str               # which SS's admin API reverses this
    client_id: str
    subject: str
    service_code: str
    prior_state: str      # "granted" or "revoked" -- the state BEFORE this action;
                           # reversing restores THIS, regardless of "action"
    applied: bool = False


class Journal:
    def __init__(self, path: pathlib.Path):
        self.path = path

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        text = self.path.read_text().strip()
        if not text:
            return []
        return json.loads(text)

    def _write(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=2))

    def is_dirty(self) -> bool:
        return len(self._read()) > 0

    def entries(self) -> list[dict]:
        return self._read()

    def append_pending(self, entry: JournalEntry) -> int:
        """Write BEFORE the live call. Returns the index for mark_applied."""
        entries = self._read()
        entries.append(dataclasses.asdict(entry))
        self._write(entries)
        return len(entries) - 1

    def mark_applied(self, index: int) -> None:
        entries = self._read()
        entries[index]["applied"] = True
        self._write(entries)

    def clear(self) -> None:
        self._write([])


def reset(
    journal: Journal,
    admin_session_factory: Callable[[str], object],
    expected_acl: dict[str, list[str]],
    topology: dict,
) -> dict:
    """Reverse the journal newest-first, verify the resulting live ACL
    equals expected_acl EXACTLY, and only then empty the journal. Returns
    {"ok": True} on success or {"ok": False, "discrepancies": [...]} --
    never a silent "reset ok" when the live state doesn't actually match.
    """
    for entry in reversed(journal.entries()):
        session = admin_session_factory(entry["ss"])
        if entry["prior_state"] == "granted":
            session.grant(entry["client_id"], entry["subject"], entry["service_code"])
        else:
            session.revoke(entry["client_id"], entry["subject"], entry["service_code"])

    discrepancies = []
    for service_code, expected_subjects in expected_acl.items():
        subsystem = next(
            s for s in topology["subsystems"] if any(svc["code"] == service_code for svc in s["services"])
        )
        session = admin_session_factory(subsystem["hosted_on"])
        live = session.read_subjects(subsystem["id"])
        if set(live) != set(expected_subjects):
            discrepancies.append({
                "service_code": service_code,
                "expected": sorted(expected_subjects),
                "live": sorted(live),
            })

    if discrepancies:
        return {"ok": False, "discrepancies": discrepancies}
    journal.clear()
    return {"ok": True}
