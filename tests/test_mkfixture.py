"""Tests for scripts/mkfixture.py -- the raw-HTTP-dump parser.

mkfixture.py turns a `curl -ksi` dump into the fixture JSON that
apps/console/tests/fixtures/xroad/*.json is made of, and those fixtures are
what the console's error-path tests assert against instead of a live
federation. It hand-rolls HTTP parsing -- header/body splitting, CRLF, the
multi-block redirect case -- in about twenty lines with no tests, which is
exactly the shape of code that silently produces a plausible-but-wrong
fixture. A misparsed fixture does not fail here; it fails as a confusing
console test six months later, or worse, passes one.

It is a flat script with no functions (everything runs at import), so these
tests run it as a subprocess -- the real entry point, the same way
scripts/capture-xroad-fixtures.sh calls it. That needs no production change
and tests what actually ships.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import sys

PACK = pathlib.Path(__file__).resolve().parent.parent
MKFIXTURE = PACK / "scripts" / "mkfixture.py"

CONTEXT = "GET /whatever -- a captured negative case"


def _run(tmp_path: pathlib.Path, raw: str, context: str = CONTEXT) -> dict:
    """Write `raw` as a dump, run mkfixture.py over it, return the JSON.
    raw is written as bytes so CRLF survives -- the whole point of the
    curl-dump cases below."""
    raw_path = tmp_path / "dump.raw"
    out_path = tmp_path / "fixture.json"
    raw_path.write_bytes(raw.encode())
    result = subprocess.run(
        [sys.executable, str(MKFIXTURE), str(raw_path), str(out_path), context],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"mkfixture.py failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(out_path.read_text())


def test_a_plain_lf_dump_is_parsed(tmp_path):
    fixture = _run(
        tmp_path,
        "HTTP/1.1 404 Not Found\n"
        "Content-Type: application/json\n"
        "\n"
        '{"status": 404, "error": {"code": "not_found"}}\n',
    )
    assert fixture["status"] == 404
    assert fixture["headers"]["Content-Type"] == "application/json"
    assert fixture["body"] == {"status": 404, "error": {"code": "not_found"}}


def test_a_real_crlf_curl_dump_is_parsed(tmp_path):
    """What `curl -ksi` actually writes. There is no "\\n\\n" anywhere in a
    CRLF dump, so the first partition finds no header/body boundary and the
    whole response lands in `head` -- the CRLF fallback is the branch that
    does the real work in production, not the LF one above."""
    fixture = _run(
        tmp_path,
        "HTTP/1.1 409 Conflict\r\n"
        "Date: Wed, 29 Jul 2026 18:48:20 GMT\r\n"
        "Content-Type: application/json\r\n"
        "\r\n"
        '{"error": {"code": "duplicate"}}\r\n',
    )
    assert fixture["status"] == 409
    assert fixture["body"] == {"error": {"code": "duplicate"}}
    assert set(fixture["headers"]) == {"Date", "Content-Type"}


def test_a_header_value_containing_colons_is_kept_whole(tmp_path):
    """Date headers carry a clock. Splitting on every colon instead of the
    first would truncate it to "Wed, 29 Jul 2026 18"."""
    fixture = _run(
        tmp_path,
        "HTTP/1.1 200 OK\r\nDate: Wed, 29 Jul 2026 18:48:20 GMT\r\n\r\n{}\r\n",
    )
    assert fixture["headers"]["Date"] == "Wed, 29 Jul 2026 18:48:20 GMT"


def test_the_last_response_wins_when_curl_followed_a_redirect(tmp_path):
    """`curl -i` with a redirect or a retry prints every response it saw.
    The fixture must record the final one -- what a real client ends up
    with -- not the 302 that got it there."""
    fixture = _run(
        tmp_path,
        "HTTP/1.1 302 Found\r\n"
        "Location: https://elsewhere/api\r\n"
        "\r\n"
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        "\r\n"
        '{"final": true}\r\n',
    )
    assert fixture["status"] == 200
    assert fixture["body"] == {"final": True}
    assert "Location" not in fixture["headers"]


def test_a_non_json_body_is_kept_verbatim(tmp_path):
    """X-Road's proxy can answer with plain text or HTML. Keeping the raw
    string is right; crashing on it, or silently writing null, is not."""
    fixture = _run(
        tmp_path,
        "HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/html\r\n\r\n"
        "<html><body>Bad Gateway</body></html>\r\n",
    )
    assert fixture["status"] == 502
    assert fixture["body"] == "<html><body>Bad Gateway</body></html>"


def test_an_empty_body_becomes_null(tmp_path):
    """A 204, or any response curl recorded with no body at all."""
    fixture = _run(tmp_path, "HTTP/1.1 204 No Content\r\nDate: x\r\n\r\n")
    assert fixture["status"] == 204
    assert fixture["body"] is None


def test_an_http_2_status_line_is_parsed(tmp_path):
    """HTTP/2 drops the reason phrase: "HTTP/2 404", not "HTTP/2 404 Not
    Found". The status must still come out as 404."""
    fixture = _run(tmp_path, "HTTP/2 404\r\ncontent-type: application/json\r\n\r\n{}\r\n")
    assert fixture["status"] == 404


def test_the_context_argument_is_recorded_verbatim(tmp_path):
    """The context string is the only part of a fixture a human writes -- it
    is why the captured response is worth keeping. It must survive intact."""
    fixture = _run(tmp_path, "HTTP/1.1 200 OK\r\n\r\n{}\r\n", context="why this matters")
    assert fixture["context"] == "why this matters"


def test_captured_is_a_utc_timestamp(tmp_path):
    """`captured` is what tells a reader whether a fixture predates the
    X-Road version the pack now runs against, so it has to be a real,
    parseable UTC instant rather than a local-time string."""
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    fixture = _run(tmp_path, "HTTP/1.1 200 OK\r\n\r\n{}\r\n")
    stamp = datetime.datetime.strptime(fixture["captured"], "%Y-%m-%dT%H:%M:%SZ")
    stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    after = datetime.datetime.now(datetime.timezone.utc)
    assert before <= stamp <= after


def test_the_written_file_matches_the_committed_fixture_shape(tmp_path):
    """The committed fixtures under apps/console/tests/fixtures/xroad/ are
    what the console tests load. A change to mkfixture.py's output shape
    would leave those unreadable, so pin the key set here."""
    fixture = _run(tmp_path, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}\r\n")
    assert set(fixture) == {"status", "headers", "body", "captured", "context"}

    committed = PACK / "apps" / "console" / "tests" / "fixtures" / "xroad"
    for path in sorted(committed.glob("*.json")):
        assert set(json.loads(path.read_text())) == set(fixture), (
            f"{path.name} has a different shape than mkfixture.py now produces"
        )
