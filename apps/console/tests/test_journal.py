"""Unit tests for apps/console/journal.py. A FakeSession stands in for
xroad.AdminSession -- no network, no Docker."""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from journal import Journal, JournalEntry, reset  # noqa: E402

TOPOLOGY = {
    "subsystems": [
        {"id": "PROGRESSA:GOV:PNIA:IDENTITY", "member_code": "PNIA", "hosted_on": "ss-plr",
         "services": [{"code": "identity-api", "access": []}]},
    ],
}
EXPECTED_ACL = {"identity-api": ["PROGRESSA:GOV:PNEA:EXAMS"]}
CLIENT_ID = "PROGRESSA:GOV:PNIA:IDENTITY"
SUBJECT = "PROGRESSA:GOV:PNEA:EXAMS"


class FakeSession:
    """granted: {client_id: {subject, ...}} shared across all FakeSession
    instances the factory returns, so state persists like a real ACL would."""
    def __init__(self, granted: dict):
        self.granted = granted
        self.calls = []

    def grant(self, client_id, subject, service_code):
        self.calls.append(("grant", client_id, subject, service_code))
        self.granted.setdefault(client_id, set()).add(subject)

    def revoke(self, client_id, subject, service_code):
        self.calls.append(("revoke", client_id, subject, service_code))
        self.granted.setdefault(client_id, set()).discard(subject)

    def read_subjects(self, client_id):
        return list(self.granted.get(client_id, set()))


def _factory(granted):
    return lambda host: FakeSession(granted)


def test_reverse_order_restoration(tmp_path):
    """Revoke, then re-grant, then reset -- must restore the ORIGINAL state
    (granted), which only happens if reset reverses newest-first. Reversing
    oldest-first would leave it revoked instead."""
    journal = Journal(tmp_path / "journal.json")
    granted = {CLIENT_ID: {SUBJECT}}  # starts granted

    idx = journal.append_pending(JournalEntry(
        ts=1.0, action="revoke", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="granted",
    ))
    FakeSession(granted).revoke(CLIENT_ID, SUBJECT, "identity-api")
    journal.mark_applied(idx)
    assert granted[CLIENT_ID] == set()  # revoked

    idx = journal.append_pending(JournalEntry(
        ts=2.0, action="grant", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="revoked",
    ))
    FakeSession(granted).grant(CLIENT_ID, SUBJECT, "identity-api")
    journal.mark_applied(idx)
    assert granted[CLIENT_ID] == {SUBJECT}  # manually re-granted

    result = reset(journal, _factory(granted), EXPECTED_ACL, TOPOLOGY)
    assert result == {"ok": True}
    assert granted[CLIENT_ID] == {SUBJECT}  # back to the original state
    assert journal.entries() == []


def test_crash_mid_write_recovery(tmp_path):
    """An entry written before the live call, but never marked applied
    (simulating a crash between the two writes) -- reset must still restore
    prior_state; grant/revoke's own idempotency makes replaying safe whether
    or not the live call actually happened before the crash."""
    journal = Journal(tmp_path / "journal.json")
    granted = {CLIENT_ID: set()}  # currently revoked -- unclear if the crash
                                    # happened before or after the live call

    journal.append_pending(JournalEntry(
        ts=1.0, action="revoke", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="granted",
    ))
    # note: no mark_applied() -- this is the crash

    result = reset(journal, _factory(granted), EXPECTED_ACL, TOPOLOGY)
    assert result == {"ok": True}
    assert granted[CLIENT_ID] == {SUBJECT}  # prior_state restored regardless
    assert journal.entries() == []


def test_refusal_to_empty_journal_when_verification_fails(tmp_path):
    """If the live state doesn't match expected_acl after reversal, reset
    must report the discrepancy and MUST NOT clear the journal -- never a
    silent 'reset ok'."""
    journal = Journal(tmp_path / "journal.json")
    # Live state has a subject expected_acl doesn't name at all -- reversing
    # the journal's own entry won't fix this drift.
    granted = {CLIENT_ID: {SUBJECT, "PROGRESSA:GOV:SOME:OTHER"}}

    journal.append_pending(JournalEntry(
        ts=1.0, action="revoke", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="granted",
    ))

    result = reset(journal, _factory(granted), EXPECTED_ACL, TOPOLOGY)
    assert result["ok"] is False
    assert result["discrepancies"][0]["service_code"] == "identity-api"
    assert journal.entries() != []  # journal NOT cleared


def test_is_dirty():
    path_file = pathlib.Path("/tmp/kp2-console-journal-test-dirty.json")
    path_file.unlink(missing_ok=True)
    journal = Journal(path_file)
    assert journal.is_dirty() is False
    journal.append_pending(JournalEntry(
        ts=1.0, action="revoke", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="granted",
    ))
    assert journal.is_dirty() is True
    journal.clear()
    assert journal.is_dirty() is False
    path_file.unlink(missing_ok=True)
