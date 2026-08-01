"""Unit tests for apps/console/journal.py. A FakeSession stands in for
xroad.AdminSession -- no network, no Docker."""
import dataclasses
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


def test_truncated_file_raises_runtime_error_naming_the_path(tmp_path):
    """S15: a crash mid-write must refuse loudly -- never a silent [] that
    would convert 'the federation may be mid-mutation' into 'nothing to
    do'. Truncating to half the bytes simulates write_text's old
    truncate-then-write window."""
    path = tmp_path / "journal.json"
    journal = Journal(path)
    journal.append_pending(JournalEntry(
        ts=1.0, action="revoke", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="granted",
    ))
    full = path.read_text()
    path.write_text(full[: len(full) // 2])

    with pytest.raises(RuntimeError) as exc_info:
        journal.entries()
    assert str(path) in str(exc_info.value)
    assert not issubclass(exc_info.type, json.JSONDecodeError)


def test_missing_and_empty_file_both_read_as_empty_list(tmp_path):
    """Existing behaviour, now explicitly pinned -- neither case is the
    corruption Task 1 refuses."""
    missing = Journal(tmp_path / "does-not-exist.json")
    assert missing.entries() == []

    empty_path = tmp_path / "empty.json"
    empty_path.write_text("")
    assert Journal(empty_path).entries() == []


def test_no_tmp_file_remains_after_append_pending(tmp_path):
    path = tmp_path / "journal.json"
    journal = Journal(path)
    journal.append_pending(JournalEntry(
        ts=1.0, action="revoke", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="granted",
    ))
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"leftover temp file(s): {tmp_files}"


def test_pre_existing_journal_from_old_code_path_reads_identically(tmp_path):
    """Format compatibility: a journal written by plain write_text (the
    pre-Task-1 code path, no .tmp/rename involved) must still read the
    same as one written by the new atomic _write."""
    path = tmp_path / "journal.json"
    entry = JournalEntry(
        ts=1.0, action="revoke", ss="ss-plr", client_id=CLIENT_ID,
        subject=SUBJECT, service_code="identity-api", prior_state="granted",
    )
    path.write_text(json.dumps([dataclasses.asdict(entry)], indent=2))

    assert Journal(path).entries() == [dataclasses.asdict(entry)]


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
