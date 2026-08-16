# apps/console/static/app.js — accepting no unit tests, 2026-08-16

**Status: DECIDED.** `apps/console/static/app.js` gets no unit-test suite.
It is the largest single untested file in the pack, and that is a choice
rather than an oversight — recorded here so the next reader does not have to
guess which it was.

## What is actually covered today

Less than "covered by `--full`'s console smoke" suggests, which is the part
worth being precise about:

- `apps/console/tests/` covers the server side — the API routes app.js calls,
  the CSRF guard, the journal, the truth/xroad adapters. That is where the
  behaviour with consequences lives, and it is well covered.
- `--full`'s console smoke brings the container up and curls `/api/health`
  and `/api/reset`. **It never loads a page and never executes app.js.**
  There is no browser in any tier.

So before this decision was written down, a plain syntax error in app.js
passed `--fast`, `--live` and `--full` green and only surfaced in front of a
workshop participant. That specific gap was not worth accepting, and it is
closed: `tests/test_console_ui.py` parses app.js on the fast tier and checks
it is still wired into `index.html`. Cheap, no browser, no framework.

## Why no real suite

app.js is a single-file, no-build, no-framework script that renders DOM from
API responses. Unit-testing it means a DOM implementation (jsdom), a module
system it does not currently use, and a mock for every endpoint — a build
step and a JS toolchain in a pack whose entire test story is otherwise
`python3` plus PyYAML, on operator machines and a workshop droplet. The
pack's cost model is "clone it and run `scripts/verify.sh --fast` in eight
seconds"; a `node_modules/` is a real tax on that.

Weighed against what it would catch: the console is a **demonstration
surface**. It shows what the federation did; it is not the mechanism, and
nothing depends on it being correct except the person watching the screen.
Every claim it makes about the exchange is already asserted server-side by
`scripts/acceptance.sh` and `apps/console/tests/`, against the bus itself
rather than against the rendering of it. A rendering bug is visible in the
first ten seconds of the demo it exists for; a bus bug is not, which is why
the testing weight sits there instead.

## When to revisit

- app.js grows a second consumer, or anything starts depending on its output
  (an export, a screenshot diff, a KP3/KP4 embed) — it stops being a
  demonstration surface at that moment.
- It gains a build step or a module system for any other reason. The tooling
  objection above is most of this argument; once the toolchain is paid for,
  reopen it.
- A rendering bug survives to a workshop. One is evidence; the cost model
  above is a bet, and that would be the bet losing.
