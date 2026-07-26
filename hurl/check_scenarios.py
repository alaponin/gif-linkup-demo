#!/usr/bin/env python3
"""Static check of the generated Hurl scenario set -- no running federation needed.

Catches the five mistakes that are otherwise only visible 20 minutes into a
deploy, when the run has already burned through its retries:

  1. a {{variable}} that is neither in vars.env nor captured earlier in the run;
  2. a capture used before the request that produces it (ordering bugs across
     the concatenated scenario files);
  3. an identifier in the scenarios that does not match manifest.yaml;
  4. a credential in vars.env that disagrees with what Compose injects into the
     containers from .env -- the run would then initialise a software token with
     one PIN while the container expects another, and fail at the first key
     generation looking like a certificate fault;
  5. a scenario no module claims, or a module claiming a scenario that does not
     exist -- the config -> prompt -> scenario -> acceptance chain must close.

Usage:  python3 hurl/check_scenarios.py        (exit 1 on any failure)
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("check_scenarios.py needs PyYAML: pip install pyyaml")

PACK = pathlib.Path(__file__).resolve().parent.parent
SCEN = PACK / "hurl" / "scenarios"

VAR_USE = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")
CAPTURE_LINE = re.compile(r"^([A-Za-z0-9_.-]+):\s+(jsonpath|cookie|header|body|xpath|regex)")
REQUEST_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+")

failures: list[str] = []


def note(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL {msg}")


def main() -> None:
    # vars.env must be a clean name=value list: a '#' comment would be parsed
    # into the value by Hurl and silently corrupt a hostname or a PIN.
    defined: set[str] = set()
    for lineno, raw in enumerate((PACK / "hurl/vars.env").read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or "#" in line:
            note(f"vars.env:{lineno} contains '#' -- Hurl reads it as part of the value")
            continue
        if "=" not in line:
            note(f"vars.env:{lineno} is not name=value: {line!r}")
            continue
        defined.add(line.split("=", 1)[0].strip())

    # Credentials must agree with what Compose injects from .env.
    env_path = PACK / ".env" if (PACK / ".env").exists() else PACK / ".env.example"
    env = {}
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = re.split(r"\s+#", v, maxsplit=1)[0].strip()
    vars_now = dict(
        line.split("=", 1)
        for line in (l.strip() for l in (PACK / "hurl/vars.env").read_text().splitlines())
        if line and "=" in line
    )
    for var, env_key in (
        ("token_pin", "XROAD_TOKEN_PIN"),
        ("ss_admin_user", "XROAD_ADMIN_USER"),
        ("ss_admin_password", "XROAD_ADMIN_PASSWORD"),
    ):
        if vars_now.get(var) != env.get(env_key):
            note(
                f"vars.env {var}={vars_now.get(var)!r} disagrees with "
                f"{env_path.name} {env_key}={env.get(env_key)!r} -- the scenarios "
                "would authenticate with a value the containers do not have"
            )

    files = sorted(SCEN.glob("*.hurl"))
    print(f"checking {len(files)} scenarios")

    # Walk the concatenation in order, tracking what is available at each point.
    available = set(defined)
    for path in files:
        in_captures = False
        for lineno, raw in enumerate(path.read_text().splitlines(), 1):
            line = raw.strip()
            if line.startswith("#"):
                continue
            if line == "[Captures]":
                in_captures = True
                continue
            if line.startswith("[") or REQUEST_LINE.match(line):
                in_captures = False
            for var in VAR_USE.findall(raw):
                if var not in available:
                    note(f"{path.name}:{lineno} uses {{{{{var}}}}} before it exists")
            if in_captures:
                m = CAPTURE_LINE.match(line)
                if m:
                    available.add(m.group(1))

    # Identifiers must match the frozen manifest -- these are the cross-pack
    # join keys KP3 and KP4 build on.
    manifest = yaml.safe_load((PACK / "manifest.yaml").read_text())
    ids = manifest["identifiers"]
    body = "\n".join(p.read_text() for p in files)

    # Every scenario is claimed by a module, and every claim resolves.
    claimed: set[str] = set()
    for mod in manifest["modules"]:
        for rel in (s.strip() for s in str(mod.get("scenarios", "")).split(",")):
            if not rel:
                continue
            claimed.add(rel)
            if not (PACK / rel).exists():
                note(f"module {mod['id']} claims {rel}, which does not exist")
    for path in files:
        rel = f"hurl/scenarios/{path.name}"
        if rel not in claimed:
            note(f"{path.name} is not claimed by any module in manifest.yaml")
    for member in ids["members"]:
        instance, cls, code, subsystem = re.split(r"[:/]", member.replace(":", "/"))
        if f'"member_code": "{code}"' not in body:
            note(f"manifest member {member} never registered in the scenarios")
        if f'"subsystem_code": "{subsystem}"' not in body:
            note(f"manifest subsystem {subsystem} never added as a client")
    for service in ids["services"]:
        code = service.rsplit("/", 1)[1]
        if f'"rest_service_code": "{code}"' not in body:
            note(f"manifest service {service} never published")
    # identity: and identifiers: are the one place duplication remains inside
    # manifest.yaml itself (by design -- identifiers: is the untouched
    # cross-pack contract, identity: is what generate.py actually reads).
    # Nothing else watches them from here on; check they agree.
    identity = manifest["identity"]
    if identity["instance"] != ids["instance"]:
        note(f"identity.instance ({identity['instance']!r}) disagrees with identifiers.instance ({ids['instance']!r})")
    if identity["member_class"] != ids["member_class"]:
        note(f"identity.member_class ({identity['member_class']!r}) disagrees with identifiers.member_class ({ids['member_class']!r})")
    owner_expected = f"{ids['instance']}/{ids['member_class']}/{identity['owner']['code']}"
    if owner_expected != ids["owner"]:
        note(f"identity.owner.code ({identity['owner']['code']!r}) disagrees with identifiers.owner ({ids['owner']!r})")
    for member_str in ids["members"]:
        _, _, code, subsystem = re.split(r"[:/]", member_str.replace(":", "/"))
        if not any(v["code"] == code and v["subsystem"] == subsystem for v in identity["members"].values()):
            note(f"identifiers.members entry {member_str} has no matching identity.members entry (code+subsystem)")

    # hurl/topology.json (apps/console's only source of topology) must exist,
    # match the deployed profile, and describe exactly the members frozen in
    # manifest.yaml -- the same class of agreement check as identity:/
    # identifiers: above.
    topo_path = PACK / "hurl" / "topology.json"
    if not topo_path.exists():
        note("hurl/topology.json does not exist -- run hurl/generate.py")
    else:
        topo = json.loads(topo_path.read_text())
        deployment = yaml.safe_load((PACK / "deployment.yaml").read_text())
        expected_profile = deployment.get("profile", "full")
        if topo.get("profile") != expected_profile:
            note(f"topology.json profile ({topo.get('profile')!r}) disagrees with deployment.yaml ({expected_profile!r})")
        topo_ids = {s["id"] for s in topo.get("subsystems", [])}
        manifest_ids = set()
        for member_str in ids["members"]:
            instance, cls, code, subsystem = re.split(r"[:/]", member_str.replace(":", "/"))
            manifest_ids.add(f"{instance}:{cls}:{code}:{subsystem}")
        if topo_ids != manifest_ids:
            note(f"topology.json subsystems {sorted(topo_ids)} disagree with manifest identifiers.members {sorted(manifest_ids)}")

    if failures:
        print(f"\n{len(failures)} problem(s)")
        sys.exit(1)
    print(f"\nOK -- {len(available - defined)} captures, {len(defined)} variables, "
          f"identifiers match manifest.yaml")


if __name__ == "__main__":
    main()
