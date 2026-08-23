"""db-sync-remote.sh must survive being piped into `ssh ... bash -s`.

infra/ci/db-sync.sh ships this script over SSH stdin, so the remote bash is
reading its OWN SOURCE from a pipe and -- unlike a seekable file -- has not
read ahead. `docker compose run` defaults to --interactive (`-T` only drops
the TTY), so an un-redirected compose call drains the rest of the script into
the container: the read-back that proves the joinapi DSN, the CA path,
verify-full and the firewall rule all work never runs, and the script still
exits 0. Green workflow, verification silently skipped. Found in review; the
same stdin conflict scripts/member.sh:159 documents from a live run.

The one assertion that catches it is "did the LAST line run" -- true only if
every compose call in between kept its `</dev/null`. The .env assertions ride
along because the upsert is the other piece with edge cases (DSNs carry `&`,
`%`, `@`, and .env is both shell-sourced and read by Compose's dotenv parser).

Hermetic: docker and gen-secrets.sh are stubbed, nothing is provisioned.
"""
from __future__ import annotations

import base64
import os
import pathlib
import re
import stat
import subprocess

import pytest

PACK = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = PACK / "infra" / "ci" / "db-sync-remote.sh"

DSN = "postgresql://joinapi:aB9@x%+_.-@h:25060/kp2_join?sslmode=verify-full&sslrootcert=/pack-secrets/do-db-ca.crt"
DSN_RO = "postgresql://joinapi_ro:R&R@h:25060/kp2_join?sslmode=verify-full"
CA = "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n"

# Faithful to `compose run`'s default --interactive: it DRAINS stdin. A stub
# that ignored stdin would pass even against the bug this test exists for.
_DOCKER_STUB = """#!/usr/bin/env bash
cat >/dev/null
case "$*" in *dump-records*) printf '{"id":1}\\n{"id":2}\\n';; *) echo "init ok" >&2;; esac
"""

# .env as a fresh droplet would have it after gen-secrets.sh: unrelated keys
# the upsert must not touch, plus a stale DSN it must replace rather than
# duplicate.
_GEN_SECRETS_STUB = """#!/usr/bin/env bash
printf 'XROAD_ADMIN_PASSWORD=abc\\nKP2_JOIN_DB_URL=stale\\nKP2_JOIN_DB_URL_RO_EXTRA=keep\\n' > .env
chmod 600 .env
"""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


@pytest.fixture
def sandbox(tmp_path):
    """The script's two absolute paths repointed at tmp_path, plus stubs."""
    pack, binn = tmp_path / "pack", tmp_path / "bin"
    (pack / "scripts").mkdir(parents=True)
    binn.mkdir()
    for path, body in ((binn / "docker", _DOCKER_STUB),
                       (pack / "scripts" / "gen-secrets.sh", _GEN_SECRETS_STUB)):
        path.write_text(body)
        path.chmod(0o755)
    src = re.sub(r"^CA_PATH=.*", f'CA_PATH="{tmp_path / "ca.crt"}"',
                 re.sub(r"^PACK=.*", f'PACK="{pack}"', SCRIPT.read_text(), count=1, flags=re.M),
                 count=1, flags=re.M)
    return pack, binn, src


def _run(sandbox, dsn: str = DSN):
    """Feed the script to `bash -s` on stdin, exactly as db-sync.sh does."""
    pack, binn, src = sandbox
    payload = "".join(f"{k}={_b64(v)}\n" for k, v in (
        ("KP2_SYNC_CA_B64", CA), ("KP2_SYNC_DSN_B64", dsn),
        ("KP2_SYNC_RO_B64", DSN_RO), ("KP2_SYNC_ADMIN_B64", "postgresql://doadmin:p@h/db"),
    )) + src
    env = dict(os.environ, PATH=f"{binn}:{os.environ['PATH']}")
    return subprocess.run(["bash", "-s"], input=payload, capture_output=True,
                          text=True, env=env, cwd=pack)


def test_read_back_still_runs_when_piped_to_bash_s(sandbox):
    """The last line of the script must execute -- see the module docstring."""
    r = _run(sandbox)
    assert r.returncode == 0, r.stderr
    assert "store reachable" in r.stdout + r.stderr, (
        "the script's final line never ran: a `docker compose run` above it lost "
        "its `</dev/null` and ate the rest of the piped script.\n" + r.stdout + r.stderr
    )


def test_env_upsert_is_correct_and_idempotent(sandbox):
    pack, _, _ = sandbox
    _run(sandbox)
    _run(sandbox)  # every `up`/`deploy` re-runs this
    env_file = pack / ".env"

    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    lines = env_file.read_text().splitlines()
    values = dict(line.split("=", 1) for line in lines)

    for key in ("KP2_JOIN_DB_URL", "KP2_JOIN_DB_URL_RO", "KP2_DB_CA_CERT"):
        assert sum(l.startswith(f"{key}=") for l in lines) == 1, f"{key} duplicated"

    # Single-quoted: .env is shell-sourced AND read by Compose's dotenv parser,
    # and an unquoted `&` backgrounds the rest of the assignment.
    assert values["KP2_JOIN_DB_URL"] == f"'{DSN}'"
    assert values["KP2_JOIN_DB_URL_RO"] == f"'{DSN_RO}'"
    assert values["XROAD_ADMIN_PASSWORD"] == "abc", "unrelated key was rewritten"
    assert values["KP2_JOIN_DB_URL_RO_EXTRA"] == "keep", "prefix-matched the wrong key"


def test_single_quote_in_a_dsn_fails_loud(sandbox):
    """Escaping it would make Compose reject the WHOLE .env -- breaking every
    later `docker compose` on the droplet, not just that key. Unreachable with
    DO's alphanumeric passwords, so this is the tripwire, not a path."""
    r = _run(sandbox, dsn="postgresql://joinapi:has'quote@h/db")
    assert r.returncode != 0, "wrote a .env that docker compose cannot parse"
    assert "single quote" in r.stderr
