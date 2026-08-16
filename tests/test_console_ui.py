"""The minimum guard on apps/console/static/app.js -- see
docs/decisions/console-ui-test-coverage.md for why there is no suite.

Short version: app.js is a demonstration surface, unit-testing it costs a JS
toolchain this pack otherwise does not need, and every claim it renders is
already asserted server-side. That is a defensible trade, but it rested on a
false premise -- that `--full`'s console smoke covers app.js. It does not:
the smoke curls /api/health and /api/reset and never loads a page, so no
tier in this pack has ever executed a line of app.js. A syntax error in it
shipped green through --fast, --live and --full alike, to be discovered by
whoever was running the workshop.

These two tests close that specific hole and nothing more. They need no
browser, no jsdom and no node_modules -- just the `node` binary, and they
skip cleanly without it rather than making it a new hard dependency of the
fast tier.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

PACK = pathlib.Path(__file__).resolve().parent.parent
STATIC = PACK / "apps" / "console" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_parses():
    """`node --check` is parse-only: no execution, no network, no DOM. It
    catches the class of error that currently reaches a live workshop --
    a stray brace, a bad template literal, a trailing comma where one is not
    allowed -- for a few milliseconds and no dependencies."""
    result = subprocess.run(
        ["node", "--check", str(STATIC / "app.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"apps/console/static/app.js is not valid JavaScript:\n{result.stderr}"


def test_app_js_is_still_wired_into_the_page():
    """A script that nothing loads is dead code that still passes a parse
    check. app.py mounts static/ with html=True, so index.html is the page
    the console serves and its <script> tag is the whole wiring."""
    index = (STATIC / "index.html").read_text()
    assert 'src="app.js"' in index, (
        "index.html no longer loads app.js -- either the console UI is dead code, "
        "or the wiring moved and docs/decisions/console-ui-test-coverage.md is now wrong."
    )
