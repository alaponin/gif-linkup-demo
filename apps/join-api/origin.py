"""apps/join-api/origin.py -- the pack's SSRF containment rule, with
exactly one implementation reachable from both sides that need it:
apps/join-api/validate.py's own per-request checks, and
scripts/member.sh's two host-side Python heredocs (`drift`, `refresh`),
which fetch an already-joined member's spec_url with no join-api process
running at all.

Extracted from validate.py's `_origin_error` (security-review-remediation-
plan.md Phase D, M2) rather than duplicated into member.sh, per this pack's
own convention (docs/conventions.md: "one rule, one place, no
indirection"). validate.py keeps a thin wrapper of the same name that
unpacks its own `policy` dict and calls `origin_error` here; every existing
message string is preserved byte for byte -- test_validate.py passing
unmodified is the proof this extraction changed no behaviour.

Stdlib only. No pydantic, no httpx, no yaml -- member.sh's heredocs have
only `python3` + PyYAML available (no venv of their own), so this module
must import cleanly there too.
"""
from __future__ import annotations

import ipaddress
import urllib.error
import urllib.parse
import urllib.request

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def origin_error(label: str, url: str, allowed_hosts: list[str] | None) -> str | None:
    """None if `url` may be fetched from a container holding the
    federation's admin credentials; a rejection message otherwise. Pure
    string work -- no DNS, no connection, so it is safe to call before any
    I/O and cheap to call twice.

    `allowed_hosts` is the allowlist directly (join-policy.yaml's
    join.spec_url_hosts, already unwrapped by the caller), not a policy
    dict -- this module does not know or care where the list came from.
    """
    if not isinstance(allowed_hosts, list) or not allowed_hosts:
        return (
            "configs/x-road-bus/join-policy.yaml declares no join.spec_url_hosts "
            "-- this API refuses to fetch any applicant-supplied URL without an "
            "allowlist to judge it against (it runs in a container holding the "
            "federation's admin credentials). Add the key and redeploy"
        )
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return (
            f"{label} {url!r} uses scheme {parsed.scheme or '(none)'!r} -- only "
            f"{sorted(_ALLOWED_SCHEMES)} are fetched (a file:// or schemeless URL "
            "reads this container's own filesystem, not the member's backend)"
        )
    host = parsed.hostname
    if not host:
        return f"{label} {url!r} names no host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return (
            f"{label} {url!r} names an IP address rather than a host name. "
            "Addresses are refused outright -- loopback, link-local and the "
            "cloud metadata address 169.254.169.254 among them -- regardless of "
            "join.spec_url_hosts; name a host on that list instead"
        )
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return (
            f"{label} {url!r} names {host!r}, which resolves inside this "
            "container -- the join API's own process and its credentials, never "
            "the member's backend"
        )
    if host not in allowed_hosts:
        return (
            f"{label} {url!r} names host {host!r}, which is not in "
            f"join.spec_url_hosts {sorted(allowed_hosts)} (configs/x-road-bus/"
            "join-policy.yaml) -- this URL is fetched from a container that "
            "holds the federation's admin credentials and can reach every "
            "Security Server's admin API, so only declared hosts are contacted"
        )
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect rather than following it. The allowlist is
    checked once, before the fetch (origin_error, above) -- a 302 from an
    allowlisted host straight to an admin API would walk right past that
    check if followed, the same reason validate.py's own httpx calls pin
    follow_redirects=False. redirect_request raises rather than returning
    None: a silent None leaves ambiguous handler-chain behaviour (urllib
    may keep looking for another handler to satisfy the redirect instead of
    just not following it) -- raising is the only way to make "refuse"
    unambiguous."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802,ANN001
        raise urllib.error.HTTPError(
            newurl, code,
            f"redirect refused ({req.full_url} -> {newurl}): this fetcher does "
            f"not follow redirects, matching the pack's no-redirect fetch rule",
            headers, fp,
        )


def no_redirect_opener(context=None) -> urllib.request.OpenerDirector:
    """An opener that raises on any redirect instead of following it. Pass
    `context` (an ssl.SSLContext) for a caller that also needs TLS handling
    -- scripts/member.sh's `_spec_ssl_context()` -- since a plain
    `urllib.request.urlopen(url, context=...)` call has no way to refuse a
    redirect at all."""
    handlers = [_NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)
