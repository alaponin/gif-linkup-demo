"""Shell-source-text assertions for join-store-export.sh's permission fix
(security-review-remediation-plan.md §0.2).

No shell-level test harness for join-store-export.sh -- it shells out to
`docker compose run` against a real Postgres-backed join-api, which is not
worth standing up here (the plan's own **Tests** note says so explicitly).
This stops the next regression the way test_compose_rw_mount_user.py stops
lib-stack.sh's export lines: parse the script's own source text for the
actual mechanism (the `umask 077` line, the ordering, the `--user` flag),
not a behavioural snapshot this suite cannot exercise.
"""
from __future__ import annotations

import pathlib

PACK = pathlib.Path(__file__).resolve().parent.parent
EXPORT_SH = (PACK / "scripts" / "join-store-export.sh").read_text()
IMPORT_SH = (PACK / "scripts" / "join-store-import.sh").read_text()
REMOTE_DEPLOY_SH = (PACK / "infra" / "ci" / "remote-deploy.sh").read_text()


def test_export_script_sets_owner_only_umask_before_creating_the_dest_dir():
    umask_at = EXPORT_SH.index("umask 077")
    mkdir_at = EXPORT_SH.index('mkdir -p "$DEST_DIR"')
    assert umask_at < mkdir_at, 'umask 077 must run before mkdir -p "$DEST_DIR"'


def test_export_script_defaults_kp2_export_dir_to_the_laptop_convention():
    # Unchanged docker-local behaviour: no override, same out/join-migrated/
    # path as before this fix.
    assert ': "${KP2_EXPORT_DIR:=$PACK_DIR/out/join-migrated}"' in EXPORT_SH
    assert 'DEST_DIR="$KP2_EXPORT_DIR/$TIMESTAMP"' in EXPORT_SH


def test_export_script_pg_restore_list_runs_as_the_dump_owner():
    # The dump is 0600 (umask 077 above) -- only the *verification* run
    # (pg_restore --list) needs --user; the pg_dump call that writes it via
    # host-side redirect does not touch the file's ownership at all.
    # rindex, not index: both phrases also appear in the script's own
    # header-comment prose, ahead of the real invocations.
    pg_dump_at = EXPORT_SH.rindex("pg_dump -Fc")
    user_at = EXPORT_SH.index('--user "$(id -u):$(id -g)"')
    restore_at = EXPORT_SH.rindex("pg_restore --list")
    assert pg_dump_at < user_at < restore_at
    assert EXPORT_SH.count('--user "$(id -u):$(id -g)"') == 1


def test_remote_deploy_points_exports_outside_every_container_mount():
    assert "export KP2_EXPORT_DIR=/opt/kp2/exports" in REMOTE_DEPLOY_SH


def test_import_script_documents_the_same_export_dir_convention():
    # join-store-import.sh always takes an explicit dump-file argument (it
    # never derives one from KP2_EXPORT_DIR), but it shares the same default
    # so its usage message can point an operator at where exports land.
    assert ': "${KP2_EXPORT_DIR:=$PACK_DIR/out/join-migrated}"' in IMPORT_SH
