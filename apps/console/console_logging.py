"""apps/console/console_logging.py -- JSON-lines logging to stdout, stdlib
`logging` only. This is apps/join-api/join_logging.py's sibling, built for
consistency across both services (production-hardening-plan.md's E.1) --
console is its own container with its own build (apps/console/Dockerfile),
so it cannot import join-api's module across the image boundary; the design
is copied rather than shared, and kept intentionally small since console's
`_LOG` calls are already scoped to two lines (app.py's `_log_task_exception`/
`_watchdog`) rather than job.py's whole step-execution engine. Named
distinctly from its sibling (imported by app.py as
`import console_logging as logging_setup`) rather than both being
"logging_setup.py" -- see this file's `JsonFormatter` docstring for why.

`scrub()` is the same algorithm as apps/join-api/job.py's -- a plain
string-replace per secret value, "" and falsy values skipped -- not
imported from there for the same cross-image reason. app.py's own
docstring already states the invariant this filter reinforces: "Credentials
come from the environment ..., read here once, never returned in a response
or logged." Nothing in this app's two existing log call sites has been
found to carry ADMIN_PASSWORD or JOIN_OPERATOR_TOKEN (both log a task's
`Exception` via %r/%s, sourced from apps/console/xroad.py, which raises on
HTTP status/timeout, never on a value containing either secret) -- this
filter is belt-and-braces on that finding, not evidence it was ever wrong,
the identical stance apps/join-api/job.py's own scrub() docstring takes."""
from __future__ import annotations

import contextvars
import datetime
import json
import logging
import sys

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def scrub(text: str, secrets: dict[str, str]) -> str:
    for value in secrets.values():
        if value:
            text = text.replace(value, "***")
    return text


class ScrubFilter(logging.Filter):
    def __init__(self, secrets: dict[str, str]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage(), self._secrets)
        record.args = ()
        return True


_JSON_SCALARS = (type(None), bool, int, float)


def _scrub_payload(value, secrets: dict[str, str]):
    """Recurse into dicts/lists (and tuples, which json.dumps renders as
    arrays), scrub() every string leaf -- run BEFORE json.dumps so a
    secret is matched in its raw form, not a JSON-escaped copy (see
    JsonFormatter's docstring). Anything else -- an Exception, bytes, a
    set, any object json.dumps would otherwise only handle via its
    `default=str` fallback -- is stringified and scrubbed HERE instead:
    see apps/join-api/join_logging.py's identical function for why.
    int/float/bool/None pass through untouched."""
    if isinstance(value, str):
        return scrub(value, secrets)
    if isinstance(value, dict):
        return {k: _scrub_payload(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_payload(v, secrets) for v in value]
    if isinstance(value, _JSON_SCALARS):
        return value
    return scrub(str(value), secrets)


class JsonFormatter(logging.Formatter):
    """Scrubs `payload` recursively (message, extra_fields and exc_info
    together), not any one channel separately -- see
    apps/join-api/join_logging.py's identical class for why a per-channel
    scrub (an earlier version of both modules did this) is a hole:
    extra_fields arrives raw and was never covered by ScrubFilter's
    record.msg rewrite. Scrubbing happens BEFORE json.dumps, not on the
    serialized string (an earlier version of this class did that too) --
    a secret containing a `"` or `\\` would otherwise get JSON-escaped
    first, and scrub()'s literal str.replace(value, "***") no longer
    matches the escaped copy.

    `secrets` is bound on THIS INSTANCE (constructor param), never a module
    global -- see apps/join-api/join_logging.py's identical class
    docstring for why: apps/join-api/app.py and apps/console/app.py both
    did a bare `import logging_setup` at the time (both this file and
    apps/join-api/join_logging.py were "logging_setup.py"), and a
    module-level secrets global is shared by every JsonFormatter in the
    process regardless of which service's `sys.modules["logging_setup"]`
    entry it actually came from -- found live, via a real test failure the
    moment both services' test suites ran in one pytest session. The two
    files were later given distinct names to close that sys.modules
    collision outright; the per-instance binding here is separate,
    independent defense that stays regardless."""

    def __init__(self, secrets: dict[str, str]) -> None:
        super().__init__()
        self._secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(_scrub_payload(payload, self._secrets), default=str)


def configure(name: str, secrets: dict[str, str]) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers = []
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(secrets))
    handler.addFilter(ScrubFilter(secrets))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


if __name__ == "__main__":
    # ponytail: the one runnable check this module's non-trivial logic (the
    # scrub filter) needs -- see apps/join-api/join_logging.py's own
    # identical self-check.
    import io

    buf = io.StringIO()
    log = configure("kp2.selftest", {"admin_password": "s3cr3t-pw"})
    log.handlers[0].stream = buf
    log.info("login failed for %s", "s3cr3t-pw")
    line = buf.getvalue().strip()
    assert "s3cr3t-pw" not in line, f"secret leaked into a log line: {line!r}"
    assert json.loads(line)["message"] == "login failed for ***"

    # extra_fields, not the message channel -- the regression this fix closes.
    buf2 = io.StringIO()
    log.handlers[0].stream = buf2
    log.info("watchdog reset failed", extra={"extra_fields": {"detail": "password=s3cr3t-pw"}})
    line2 = buf2.getvalue().strip()
    assert "s3cr3t-pw" not in line2, f"secret leaked via extra_fields: {line2!r}"
    assert json.loads(line2)["detail"] == "password=***"

    # A secret containing a JSON-escaped character (here `"`) must still
    # match -- scrubbing the serialized string instead of the raw payload
    # lets json.dumps escape the secret first.
    buf3 = io.StringIO()
    log3 = configure("kp2.selftest.escaping", {"admin_password": 'pa"ss\\word'})
    log3.handlers[0].stream = buf3
    log3.info("watchdog reset failed", extra={"extra_fields": {"detail": 'password=pa"ss\\word'}})
    line3 = buf3.getvalue().strip()
    assert json.loads(line3)["detail"] == "password=***", f"secret leaked via JSON-escaped chars: {line3!r}"

    print("console_logging self-check OK:", line, "|", line2, "|", line3)
