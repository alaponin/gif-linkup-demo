"""Wave 5 (monitoring add-ons) regression guard.

The NIIS Sidecar image ships two tags per version: a "-slim" tag (bare
packages only) and a plain tag ("full") that additionally bundles message
logging, operational monitoring (xroad-opmonitor) and environmental
monitoring (xroad-monitor), pre-installed and supervisord-managed with no
separate admin-API call or environment variable involved
(nordic-institute/X-Road-Security-Server-sidecar's
security_server_sidecar_user_guide.md, sections 1.1 and 2.2 -- "Full image
uses the slim as the base and adds message logging, and environmental and
operational monitoring"). docker-compose.yml's `x-sidecar` anchor has never
carried a "-slim" suffix, so every Security Server this pack brings up --
including ss-pdga -- already runs both add-ons. This test is the guard
against a future change silently switching that off: it renders the real
Compose config (deployment.yaml's actual xroad.ss_digest/version, the same
values scripts/check-exposure.sh resolves) and asserts every Security
Server's resolved image is not a "-slim" tag.

No Docker daemon reachability is required beyond `docker compose ... config`,
same requirement --fast already has (verify.sh's own comment on why the
Docker CLI is needed there).
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent


def _rendered_services() -> dict:
    deploy_spec = yaml.safe_load((PACK / "deployment.yaml").read_text())
    xroad = deploy_spec["xroad"]
    network = deploy_spec.get("network") or {}
    env = {
        "XROAD_VERSION": str(xroad["version"]),
        "XROAD_CS_TAG": str(xroad["cs_tag"]),
        "TESTCA_TAG": str(xroad["testca_tag"]),
        "XROAD_CS_DIGEST": str(xroad.get("cs_digest") or ""),
        "XROAD_SS_DIGEST": str(xroad.get("ss_digest") or ""),
        "XROAD_BIND": str(network.get("bind") or "127.0.0.1"),
        # docker-compose.yml requires these two to be set (":?set in .env");
        # their value is irrelevant to image resolution, which is all this
        # test reads.
        "XROAD_TOKEN_PIN": "0000",
        "XROAD_ADMIN_PASSWORD": "placeholder",
    }
    import os

    full_env = {**os.environ, **env}
    out = subprocess.run(
        ["docker", "compose", "-f", str(PACK / "docker-compose.yml"), "config", "--format", "json"],
        cwd=PACK, env=full_env, capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)["services"]


def test_every_security_server_uses_the_full_non_slim_sidecar_image():
    services = _rendered_services()
    ss_services = {name: svc for name, svc in services.items() if name.startswith("ss-")}
    assert ss_services, "no ss-* services found in the rendered Compose config"
    slim = {name: svc["image"] for name, svc in ss_services.items() if "slim" in svc["image"]}
    assert not slim, (
        "the following Security Servers resolve to a -slim Sidecar image, which "
        "does not ship operational/environmental monitoring "
        f"(Wave 5, G-06): {slim}"
    )
