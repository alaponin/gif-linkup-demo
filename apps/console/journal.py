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
import os
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
        # Journal integrity plan (S15): a corrupt file must refuse loudly,
        # never be silently treated as "nothing to do" -- that would convert
        # "the federation may be mid-mutation and I cannot tell" into a
        # silent reset-ok, exactly what this module exists to prevent.
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{self.path} is not valid JSON -- an interrupted write or a "
                "hand-edit, not something this reader should guess about. "
                "Either inspect and repair the file, or, only if you are "
                "certain no mutation is outstanding, delete it."
            ) from exc

    def _write(self, entries: list[dict]) -> None:
        """Atomic on POSIX: a temp file beside the target (same filesystem,
        so os.replace's atomicity guarantee actually holds -- OUT_DIR is a
        Docker bind mount, and rename(2) across filesystems is not atomic)
        renamed onto the real path. A reader never sees a partially-written
        file. If the process dies before os.replace runs, the .tmp file is
        a write that was never adopted -- the real journal at self.path is
        still whatever it was before, and the stray .tmp is safe to delete,
        not a sign of corruption in the live file beside it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(entries, indent=2))
        os.replace(tmp, self.path)

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
