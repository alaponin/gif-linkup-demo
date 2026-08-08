"""Three one-member modules (2.3 PNEA, 2.4 PLR, 2.5 PNIA) were collapsed
into one three-member module. apps/console/app.py's _CONFIG_BY_MEMBER
now zips a module's building_blocks: against its comma-separated
member_configs: list (falling back to the single config: path) instead of
assuming one config per module -- a length mismatch there would silently
point every member of a collapsed module at the same config file (and the
console's semantic pane at the wrong -- or another member's -- fields).
config: itself stays a single, real path (not a comma list) because the
sibling ITU-Giga-KP-Plugin ship gate's check_pack.py does a plain
os.path.exists(pack/config) per module with no notion of a joined list --
see manifest.yaml's comment on the collapsed module.

This test does not import apps/console/app.py (its module-level code
demands XROAD_ADMIN_USER etc. and is exercised, against fixtures, by
apps/console/tests/); it re-derives the same map from the real
manifest.yaml via the same rule, so a real drift in manifest.yaml's
member_configs:/building_blocks: pairing fails here without needing the
console's env.
"""
from __future__ import annotations

import pathlib

import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent


def _config_by_member(manifest: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for module in manifest["modules"]:
        member_bbs = [b for b in module.get("building_blocks", []) if b.startswith("member-")]
        if not member_bbs:
            continue
        raw_configs = module.get("member_configs", module["config"])
        configs = [c.strip() for c in raw_configs.split(",")]
        assert len(configs) == len(member_bbs), (
            f"module {module['id']!r}: {len(member_bbs)} member building_blocks "
            f"but {len(configs)} config path(s)"
        )
        for bb, cfg in zip(member_bbs, configs):
            out[bb.removeprefix("member-").upper()] = cfg
    return out


def test_collapsed_register_member_module_maps_each_member_to_its_own_config():
    manifest = yaml.safe_load((PACK / "manifest.yaml").read_text())
    by_member = _config_by_member(manifest)

    assert by_member["PNEA"] == "configs/member-pnea/pnea.yaml"
    assert by_member["PLR"] == "configs/member-plr/plr.yaml"
    assert by_member["PNIA"] == "configs/member-pnia/pnia.yaml"
    # The bug this guards against: all three silently resolving to the same file.
    assert len({by_member["PNEA"], by_member["PLR"], by_member["PNIA"]}) == 3

    for code, rel in by_member.items():
        assert (PACK / rel).exists(), f"{code} -> {rel} does not exist"

    plr_fields = yaml.safe_load((PACK / by_member["PLR"]).read_text())["semantic"]["fields"]
    pnia_fields = yaml.safe_load((PACK / by_member["PNIA"]).read_text())["semantic"]["fields"]
    assert plr_fields != pnia_fields
