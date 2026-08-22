"""apps/console/console_logging.py's scrub story previously had zero pytest
coverage -- only the module's own `__main__` self-check, which
scripts/verify.sh never runs (found in review: the exact ordering
regression this module was fixed for could be reintroduced and every
`pytest` invocation would stay green). This wires the same assertions
apps/join-api/tests/test_app_health.py already makes for its sibling
module into a real, always-run test.

Also: apps/join-api/app.py and this file's own apps/console/app.py used to
both do a bare `import logging_setup`, resolving the SAME module in
whichever test collection ran first when scripts/verify.sh's single
`pytest` invocation loaded both services' test suites -- so this file's
`app._LOG`/`app.logging_setup` could silently be exercising join-api's
module instead of console's, found live in review. The two files are now
named distinctly (join_logging.py / console_logging.py, each imported as
`logging_setup`), which closes that collision outright."""
import io
import json
import os
import pathlib
import sys

os.environ["PACK_DIR"] = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "pack")
os.environ["OUT_DIR"] = "/tmp"
os.environ["XROAD_ADMIN_USER"] = "xrd"
os.environ["XROAD_ADMIN_PASSWORD"] = "secret"
os.environ["KP2_JOIN_OPERATOR_TOKEN"] = "test-operator-token"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app  # noqa: E402


def _captured_log_lines(fn) -> list[dict]:
    """Runs `fn()` with app._LOG's real handler pointed at an in-memory
    buffer instead of stdout -- the same handler/formatter/filter the
    running process actually uses, not a stand-in."""
    handler = app._LOG.handlers[0]
    buf = io.StringIO()
    original_stream = handler.stream
    handler.stream = buf
    try:
        fn()
    finally:
        handler.stream = original_stream
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_log_records_are_scrubbed_of_a_real_secret_value():
    lines = _captured_log_lines(lambda: app._LOG.info("login failed for %s", app.ADMIN_PASSWORD))
    assert len(lines) == 1
    assert app.ADMIN_PASSWORD not in json.dumps(lines[0])
    assert lines[0]["message"] == "login failed for ***"


def test_log_records_are_scrubbed_even_when_the_secret_arrives_via_extra_fields():
    lines = _captured_log_lines(
        lambda: app._LOG.info(
            "watchdog reset failed", extra={"extra_fields": {"detail": f"password={app.ADMIN_PASSWORD}"}}
        )
    )
    assert app.ADMIN_PASSWORD not in json.dumps(lines[0])
    assert lines[0]["detail"] == "password=***"


def test_log_records_are_scrubbed_of_a_secret_containing_json_special_characters():
    """Regression: scrubbing the already-json.dumps-serialized string let a
    secret containing a `"` or a backslash survive, JSON-escaped, because
    the literal str.replace no longer matched it. Fixed by scrubbing
    payload values before serialization (_scrub_payload)."""
    secret = 'pa"ss\\word'
    logger = app.logging_setup.configure("kp2.json-escaping-test", {"admin_password": secret})
    buf = io.StringIO()
    logger.handlers[0].stream = buf
    logger.info("watchdog reset failed", extra={"extra_fields": {"detail": f"password={secret}"}})
    line = json.loads(buf.getvalue().strip())
    assert line["detail"] == "password=***"


def test_log_records_are_scrubbed_when_the_secret_arrives_as_a_non_string_value():
    """Regression: _scrub_payload's fallback for anything that isn't a
    str/dict/list passed the value through untouched, so an Exception
    object carried via extra_fields (json.dumps's `default=str` fallback
    renders it) was never scrubbed. app.py's `_log_task_exception`/
    `_watchdog` currently log `task.exception()` through the %r message
    channel, not extra_fields -- this proves the fallback itself, not
    today's exact call sites, since the module's own guarantee ("every
    value that ends up in the JSON... passes through the scrub") should
    not depend on which channel a future caller picks."""
    secret = app.ADMIN_PASSWORD
    lines = _captured_log_lines(
        lambda: app._LOG.error(
            "startup reset failed",
            extra={"extra_fields": {"exc": RuntimeError(f"boom {secret}")}},
        )
    )
    assert secret not in json.dumps(lines[0])
