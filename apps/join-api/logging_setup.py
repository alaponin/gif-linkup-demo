"""apps/join-api/logging_setup.py -- JSON-lines logging to stdout, stdlib
`logging` only (no new dependency; the Global constraints row in
production-hardening-plan.md forbids one for this task).

Two pieces:

  - `request_id_ctx`, a contextvar app.py's HTTP middleware sets to a fresh
    id per request (also returned as `X-Request-Id` and stamped into
    `request_events.detail`, so a log line and its audit-table row join on
    the same value -- app.py's own request-id middleware and `_save()`
    wrapper). Every JSON record carries whatever this holds, `null` outside
    a request (job.py's background-thread steps correlate by the join's own
    `id` instead -- threaded through as the `join_id` field by app.py's
    `_job_log`).
  - `ScrubFilter`, a `logging.Filter` built on `job.scrub(..., JOB_SECRETS)`
    -- the exact function and secret set app.py already uses to scrub
    subprocess output before it is persisted (app.py's `JOB_SECRETS` global,
    `job.scrub` calls at its GenerateFailure/RollbackFailure/error-path
    handlers). Logs are the same class of sink as those persisted error
    messages: something formats a value that might, on some path, carry a
    credential -- and the fix is the same guard, applied to every record
    this process ever emits rather than trusted to each call site's own
    care. Runs on the record's *rendered* message (after %-interpolation),
    so a secret arriving as a positional arg is caught exactly like one
    arriving in the format string.

`configure()` attaches both to one named logger and turns propagation off,
so this is the only formatting `docker logs <service>` ever sees for it --
mirroring apps/console/app.py's pre-existing `_LOG = logging.getLogger(...)`
(that file relied on uvicorn's own root config; this module replaces that
default with JSON for both services, per the task brief)."""
from __future__ import annotations

import contextvars
import datetime
import json
import logging
import sys

import job

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class ScrubFilter(logging.Filter):
    """Rewrites the record's message through job.scrub(..., secrets) before
    formatting. Mutates record.msg to the already-scrubbed, already-%-
    interpolated string and clears record.args -- scrub() takes one string,
    not a format string plus args, so interpolation has to happen here
    first (record.getMessage() does exactly that)."""

    def __init__(self, secrets: dict[str, str]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = job.scrub(record.getMessage(), self._secrets)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. `extra_fields` (a dict passed via
    `logging.Logger.info(..., extra={"extra_fields": {...}})`) is merged in
    verbatim -- this is how app.py/job.py attach request_id/join_id/step/
    duration_s/etc. without a bespoke LogRecord subclass."""

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
            # self.formatException already ran through ScrubFilter's
            # exc_text rewrite when a filter is attached to the same
            # handler (logging.Formatter.format caches into record.exc_text
            # -- but that caching happens INSIDE this call, after the
            # filter already ran on record.msg only). Scrub it again here,
            # directly: a traceback is exactly where a subprocess's stderr
            # (which can carry a credential -- job.py's own scrub() calls
            # exist for this) ends up via logger.exception().
            payload["exc_info"] = job.scrub(self.formatException(record.exc_info), _current_secrets)
        return json.dumps(payload, default=str)


# Set by configure() -- module-level because JsonFormatter.format has no
# other way to reach the secrets dict (logging.Formatter's constructor
# signature is fixed by the stdlib and this project does not subclass
# LogRecord). ScrubFilter runs first regardless (handler.filters before
# handler.formatter, stdlib logging's own order) and already scrubs
# record.msg; this covers the one thing a filter cannot reach -- text
# formatException() renders lazily, inside format() itself.
_current_secrets: dict[str, str] = {}


def configure(name: str, secrets: dict[str, str]) -> logging.Logger:
    """Build (or reconfigure) the named logger: one StreamHandler on
    stdout, JsonFormatter, ScrubFilter -- propagate=False so uvicorn's own
    root handler never double-prints or reformats these records."""
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
    # ponytail: the one runnable check this module's non-trivial logic
    # (the scrub filter) needs -- not a pytest file, since
    # tests/test_app_health.py already exercises configure() through
    # app.py's own import; this is the fast standalone version.
    import io

    buf = io.StringIO()
    log = configure("kp2.selftest", {"token_pin": "s3cr3t-pin"})
    log.handlers[0].stream = buf
    log.info("auth failure for %s", "s3cr3t-pin", extra={"extra_fields": {"step": "demo"}})
    line = buf.getvalue().strip()
    record = json.loads(line)
    assert "s3cr3t-pin" not in line, f"secret leaked into a log line: {line!r}"
    assert record["message"] == "auth failure for ***"
    assert record["step"] == "demo"
    print("logging_setup self-check OK:", line)
