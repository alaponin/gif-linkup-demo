"""apps/join-api/join_logging.py -- JSON-lines logging to stdout, stdlib
`logging` only (no new dependency; the Global constraints row in
production-hardening-plan.md forbids one for this task). Imported by app.py
as `import join_logging as logging_setup` -- named distinctly from
apps/console/console_logging.py (its sibling, same design) rather than both
being "logging_setup.py": a bare `import logging_setup` in both app.py
files would let whichever service's test suite collects first in one
pytest session (scripts/verify.sh runs both in a single `pytest` command)
win the sys.modules cache, leaving the OTHER service's tests silently
exercising the wrong file's scrub logic -- found live, the hard way.

Two pieces:

  - `request_id_ctx`, a contextvar app.py's HTTP middleware sets to a fresh
    id per request (also returned as `X-Request-Id` and stamped into
    `request_events.detail`, so a log line and its audit-table row join on
    the same value -- app.py's own request-id middleware and `_save()`
    wrapper). Every JSON record carries whatever this holds, `null` outside
    a request (job.py's background-thread steps correlate by the join's own
    `id` instead -- threaded through as the `join_id` field by app.py's
    `_job_log`).
  - The scrub, built on `job.scrub(..., secrets)` -- the exact function
    app.py already uses to scrub subprocess output before it is persisted
    (`job.scrub` calls at its GenerateFailure/RollbackFailure/error-path
    handlers), applied to the JSON PAYLOAD as a whole, recursively, BEFORE
    it is serialized (`_scrub_payload`, run from `JsonFormatter.format`),
    not to any one channel (message, `extra_fields`, `exc_info`)
    separately -- and not to the serialized string either, which an
    earlier version of this module tried and which a JSON-escaped secret
    (one containing a `"` or a backslash) then defeated. Logs are the same
    class of sink
    as those persisted error messages: something formats a value that
    might, on some path, carry a credential -- and the fix is the same
    guard, applied to every record this process ever emits rather than
    trusted to each call site's own care. `ScrubFilter` (a `logging.Filter`)
    additionally rewrites `record.msg` before formatting, for the message
    channel specifically -- belt-and-braces, not the load-bearing guarantee
    (that's the whole-payload scrub in `JsonFormatter.format`, which is what
    catches a secret arriving via `extra_fields` too -- found live: an
    earlier version of this module scrubbed the message channel only and
    left `extra_fields` to print a secret verbatim).

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


_JSON_SCALARS = (type(None), bool, int, float)


def _scrub_payload(value, secrets: dict[str, str]):
    """Recurse into dicts/lists (and tuples, which json.dumps renders as
    arrays), job.scrub() every string leaf -- run BEFORE json.dumps so a
    secret is matched in its raw form, not a JSON-escaped copy (see
    JsonFormatter's docstring). Anything else -- an Exception, bytes, a
    set, any object json.dumps would otherwise only handle via its
    `default=str` fallback -- is stringified and scrubbed HERE instead:
    found live, a value that reaches json.dumps as a bare non-JSON-native
    object bypasses a scrub that runs only on str/dict/list leaves, and
    `default=str` then renders it (unscrubbed) after the scrub already
    ran. int/float/bool/None pass through untouched -- json-native, and
    incapable of carrying a string secret."""
    if isinstance(value, str):
        return job.scrub(value, secrets)
    if isinstance(value, dict):
        return {k: _scrub_payload(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_payload(v, secrets) for v in value]
    if isinstance(value, _JSON_SCALARS):
        return value
    return job.scrub(str(value), secrets)


class JsonFormatter(logging.Formatter):
    """One JSON object per line. `extra_fields` (a dict passed via
    `logging.Logger.info(..., extra={"extra_fields": {...}})`) is merged in
    verbatim -- this is how app.py/job.py attach request_id/join_id/step/
    duration_s/etc. without a bespoke LogRecord subclass.

    The scrub walks `payload` recursively (`_scrub_payload`, below) BEFORE
    `json.dumps` runs, not just record.msg (ScrubFilter's job) -- extra_fields
    arrives raw and logging.Formatter.format's own %-interpolation happens
    outside ScrubFilter's reach too. A per-field scrub (message here,
    extra_fields there, exc_info somewhere else) is exactly the "trusted to
    each call site's own care" posture this module's docstring claims to have
    replaced -- one earlier version of this function did that and left
    extra_fields unscrubbed, found live: a log record built with
    extra={"extra_fields": {"error": "...s3cr3t-pin"}} printed the secret
    verbatim. Recursing over every string value in `payload` is the fix that
    can't have a field-shaped hole: every string that ends up in the JSON,
    however it arrived, passes through job.scrub() before this function
    returns -- and doing it BEFORE json.dumps (not on the serialized string,
    which an earlier version of this function also tried) means a secret
    containing a `"` or a backslash still matches: json.dumps would otherwise escape
    it first, and job.scrub's plain str.replace(value, "***") is a literal
    match that a JSON-escaped copy of the same value no longer satisfies.

    `secrets` is bound on THIS INSTANCE (constructor param), never a module
    global -- found live, the hard way: a module-level `_current_secrets`
    global (an earlier version of this class) is shared by every
    JsonFormatter that exists in the process, and apps/join-api/app.py and
    apps/console/app.py both did a bare `import logging_setup` at the time
    (both this file and apps/console/console_logging.py were "logging_setup.py"
    -- the two were later given distinct names specifically to close the
    sys.modules collision this caused when both services' test suites
    loaded in one pytest session; see this module's own top-of-file
    docstring). The per-instance binding stays regardless, as its own
    defense: a module global would let whichever service's `configure()`
    ran LAST silently overwrite the OTHER service's scrub secrets for
    every formatter sharing the module, regardless of which logger's
    handler is doing the formatting -- proven by a real test failure the
    moment join-api and console tests ran in one
    session. Binding `secrets` per instance removes the sharing entirely:
    each handler's own JsonFormatter carries its own secrets no matter how
    many services' modules end up aliased to the same name."""

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
    """Build (or reconfigure) the named logger: one StreamHandler on
    stdout, JsonFormatter, ScrubFilter -- propagate=False so uvicorn's own
    root handler never double-prints or reformats these records."""
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

    # The regression this fix closes: a secret arriving via extra_fields
    # (not the message channel) used to print verbatim.
    buf2 = io.StringIO()
    log.handlers[0].stream = buf2
    log.info("job.step.end", extra={"extra_fields": {"error": "--variable token_pin=s3cr3t-pin"}})
    line2 = buf2.getvalue().strip()
    assert "s3cr3t-pin" not in line2, f"secret leaked via extra_fields: {line2!r}"
    assert json.loads(line2)["error"] == "--variable token_pin=***"

    # The regression a scrub-after-json.dumps ordering closes over: a secret
    # containing a JSON-escaped character (here `"`) must still match --
    # scrubbing the serialized string instead of the raw payload lets
    # json.dumps escape the secret first, so a literal str.replace on the
    # serialized text never matches it.
    buf3 = io.StringIO()
    log3 = configure("kp2.selftest.escaping", {"token_pin": 'pa"ss\\word'})
    log3.handlers[0].stream = buf3
    log3.info("job.step.end", extra={"extra_fields": {"error": 'token_pin=pa"ss\\word'}})
    line3 = buf3.getvalue().strip()
    assert json.loads(line3)["error"] == "token_pin=***", f"secret leaked via JSON-escaped chars: {line3!r}"

    print("join_logging self-check OK:", line, "|", line2, "|", line3)
