"""scripts/render_onboarding.py -- renders onboarding/<key>/ for one
canonical member through the *same* writer.render_onboarding_tree() a real
join uses (Wave 4 Task 3, K-01: "generate them, do not hand-author them").
No consistency test guarding a duplication that should not exist -- there is
only one renderer, this script just feeds it identity off manifest.yaml and
security_server/services/semantic/member_requirements off
configs/member-<key>/<key>.yaml instead of an approved JoinPayload.

`backend` has no equivalent in a canonical member's config (client.
connection_type/consumes is a different shape from services/access, and
none of onboarding/<key>/'s four files render backend at all -- Wave 4 Task
1's schema.py addition is the only thing that reads it, and that is a join-
time check this script never runs). A fixed placeholder value is correct
here, not a guess: nothing downstream of render_onboarding_tree() looks at
it, so the value cannot leak into the rendered record.

Invoked by scripts/render-onboarding.sh, never directly -- see that script
for why this needs the dev .venv (pydantic is apps/join-api's own
dependency, not this pack's).
"""
from __future__ import annotations

import pathlib
import shutil
import sys

import yaml

PACK_DIR = pathlib.Path(sys.argv[1])
KEY = sys.argv[2]

sys.path.insert(0, str(PACK_DIR / "apps" / "join-api"))
from schema import Backend, JoinPayload, MemberRequirements, SecurityServer, Service, SLA  # noqa: E402
from writer import render_onboarding_tree  # noqa: E402

manifest = yaml.safe_load((PACK_DIR / "manifest.yaml").read_text())
identity = (manifest["identity"]["members"].get(KEY)) or {}
if not identity:
    sys.exit(f"render_onboarding.py: no manifest.yaml identity.members.{KEY}")

member_dir = PACK_DIR / "configs" / f"member-{KEY}"
yaml_files = sorted(member_dir.glob("*.yaml"))
if not yaml_files:
    sys.exit(f"render_onboarding.py: no configs/member-{KEY}/*.yaml")
cfg = yaml.safe_load(yaml_files[0].read_text()) or {}

ss_cfg = cfg.get("security_server") or {}
security_server = SecurityServer(
    code=ss_cfg["code"],
    dns_name=ss_cfg["dns_name"],
    hosted_on=ss_cfg.get("hosted_on"),
    # A canonical member's config carries hosted_on only when it does not
    # run its own server (mirrors SecurityServer.own_server's own docstring,
    # schema.py) -- own_server is the explicit opt-in a join payload states;
    # a canonical config states the same fact implicitly, by omission.
    own_server=not ss_cfg.get("hosted_on"),
)

services = [
    Service(
        code=svc["code"],
        spec_url=svc["spec_url"],
        access=svc.get("access") or [],
        lawful_basis=svc.get("lawful_basis"),
        sla=SLA(**svc["sla"]) if svc.get("sla") else None,
    )
    for svc in (cfg.get("services") or [])
]

requirements_cfg = cfg.get("member_requirements")
if not requirements_cfg:
    sys.exit(
        f"render_onboarding.py: configs/member-{KEY}/{yaml_files[0].name} has no "
        "member_requirements: block -- Task 3 Step 1 adds it by hand before this "
        "script can render 02-requirements.md"
    )
member_requirements = MemberRequirements(**requirements_cfg)

payload = JoinPayload(
    code=identity["code"],
    name=identity["name"],
    subsystem=identity["subsystem"],
    subsystem_description=identity["subsystem_description"],
    security_server=security_server,
    services=services,
    backend=Backend(auth="none"),  # unused by render_onboarding_tree -- see module docstring
    member_requirements=member_requirements,
    # A canonical config's own consumer shape (client.connection_type/
    # consumes, e.g. configs/member-pnea/pnea.yaml) is the same fact
    # JoinPayload.requested_access carries for a join.
    requested_access=cfg.get("consumes") or [],
)

# render_onboarding_tree()'s mkdir is deliberately not exist_ok (mirrors
# _write_member's own "not exist_ok" fail-loud-on-collision, writer.py) --
# right for a real join, where onboarding/<key>/ must not already exist, but
# this script re-renders a canonical member's record from its config on
# every run, so it clears its own prior output first rather than treating
# a second run as a collision.
shutil.rmtree(PACK_DIR / "onboarding" / KEY, ignore_errors=True)
render_onboarding_tree(PACK_DIR, KEY, payload, request_id=None)
print(f"rendered onboarding/{KEY}/")
