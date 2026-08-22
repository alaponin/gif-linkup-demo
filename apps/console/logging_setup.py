"""apps/console/logging_setup.py -- JSON-lines logging to stdout, stdlib
`logging` only. This is apps/join-api/logging_setup.py's sibling, built for
consistency across both services (production-hardening-plan.md's E.1) --
console is its own container with its own build (apps/console/Dockerfile),
so it cannot import join-api's module across the image boundary; the design
is copied rather than shared, and kept intentionally small since console's
`_LOG` calls are already scoped to two lines (app.py's `_log_task_exception`/
`_watchdog`) rather than job.py's whole step-execution engine.

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


class JsonFormatter(logging.Formatter):
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
            payload["exc_info"] = scrub(self.formatException(record.exc_info), _current_secrets)
        return json.dumps(payload, default=str)


_current_secrets: dict[str, str] = {}


def configure(name: str, secrets: dict[str, str]) -> logging.Logger:
    global _current_secrets
    _current_secrets = secrets
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers = []
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ScrubFilter(secrets))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


if __name__ == "__main__":
    # ponytail: the one runnable check this module's non-trivial logic (the
    # scrub filter) needs -- see apps/join-api/logging_setup.py's own
    # identical self-check.
    import io

    buf = io.StringIO()
    log = configure("kp2.selftest", {"admin_password": "s3cr3t-pw"})
    log.handlers[0].stream = buf
    log.info("login failed for %s", "s3cr3t-pw")
    line = buf.getvalue().strip()
    assert "s3cr3t-pw" not in line, f"secret leaked into a log line: {line!r}"
    assert json.loads(line)["message"] == "login failed for ***"
    print("logging_setup self-check OK:", line)
