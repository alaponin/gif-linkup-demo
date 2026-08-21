"""infra/nginx/kp2-console.conf publishes join-api's APPLICANT routes only.

CONSOLE-EXPOSURE.md section 7's central claim is that the operator surface --
approve, resume, reject, DELETE /members -- has no public path at all: nginx
answers it before join-api ever sees it, so a leaked operator token is
internet-inert. That claim lives in nginx `location` blocks, which nothing else
in this pack tests and which a later edit ("just proxy the whole app") could
quietly undo.

This is a string/regex check over one config file, deliberately: running nginx
in CI to probe the routes would be over-engineering for a four-location config.
"""
from __future__ import annotations

import pathlib
import re

CONF = pathlib.Path(__file__).resolve().parent.parent / "infra/nginx/kp2-console.conf"

# Every `location <modifier> <path>` in the file, as (modifier, path) pairs.
_LOCATION = re.compile(r"^\s*location\s+(?:(=|~\*?|\^~)\s+)?(\S+)\s*\{", re.M)

OPERATOR_MARKERS = ("approve", "resume", "reject", "members", "refreshes")


def _locations() -> list[tuple[str, str]]:
    return [(m.group(1) or "", m.group(2)) for m in _LOCATION.finditer(CONF.read_text())]


def test_no_location_reaches_an_operator_route():
    for modifier, path in _locations():
        for marker in OPERATOR_MARKERS:
            assert marker not in path, f"location {modifier} {path} exposes {marker}"


def test_join_locations_are_exactly_the_three_applicant_routes():
    join = {(mod, path) for mod, path in _locations() if "/join" in path}
    assert join == {
        ("=", "/join/requests"),                          # POST: submit
        ("~", r"^/join/requests/[A-Za-z0-9_-]+$"),        # GET: poll one's own outcome
        ("=", "/join/catalogue"),                         # GET: discovery
        ("", "/join/"),                                   # catch-all: return 404
    }, join


def test_the_regex_route_cannot_match_an_operator_subpath():
    """The trailing $ is the whole defence -- without it /{id}/approve matches."""
    pattern = next(p for mod, p in _locations() if mod == "~" and p.startswith("^/join"))
    rx = re.compile(pattern)
    assert rx.match("/join/requests/AbC-1_23")             # a real token_urlsafe(8) id
    for suffix in ("approve", "resume", "reject"):
        assert not rx.match(f"/join/requests/AbC-1_23/{suffix}")


def test_everything_else_under_join_is_refused_not_proxied():
    """The catch-all: /join/ must return, never proxy_pass."""
    body = re.search(r"location\s+/join/\s*\{(.*?)\}", CONF.read_text(), re.S).group(1)
    assert "return 404" in body
    assert "proxy_pass" not in body


def test_applicant_locations_disable_basic_auth():
    """auth_basic and join-api's bearer token collide in the Authorization header."""
    text = CONF.read_text()
    for block in re.findall(r"location[^\n]*/join[^\n]*\{(.*?)\n    \}", text, re.S):
        assert "auth_basic off" in block, block


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
