"""apps/join-api/job.py -- the join job engine (design spec
S5). One approved request becomes an ordered list of steps; each step is one
Hurl invocation over ONE of Plan A's registry templates (hurl/steps.py), run
against the live federation, with its `provides` captures parsed back out of
Hurl's JSON report and threaded into the next step.

Three things make this different from what hurl/run-linkup.sh does with the
same templates, and all three follow from resumability (decision 5):

  1. **One invocation per step, not one per run.** run-linkup.sh concatenates
     every rendered step into a single setup.hurl, so a variable captured
     early (ca_name, a session's XSRF token) stays in Hurl's scope for every
     later template in that one file. Nothing is in scope between our steps
     except what this module persists and re-injects as --variable. A hosted
     join therefore re-establishes, in its own job context, what cold deploy
     got for free: cs.init (CS session), cs.anchor (the anchor),
     ss.bringup_init against the HOST (the host's session) and
     ss.ca_name_capture (ca_name).
  2. **No Docker.** Design decision 8 (spec S3/S6): this container never gets
     a Docker socket, so `docker compose run --rm hurl` is closed to it. The
     pinned Hurl image's own binary is copied into this image at build time
     (see Dockerfile) and shelled out to locally. join-api is on the linkup
     network, so https://cs:4000 / https://ss-plr:4000 resolve here exactly
     as they do for the hurl compose service.
  3. **Session captures are never persisted.** Spec S5.4: the job context
     must never contain the token PIN, the admin password, or a session
     token. Any capture named *_xsrf_token is dropped on the way to disk --
     and a step that provides one therefore re-runs on resume (see
     JobStep.must_rerun), which is safe because those steps are all class
     (a)/(b) in hurl/steps.py's 409-safety audit.

Two shapes of join (spec S6):

  - **hosted_on**: the joining member's subsystem becomes an extra client on
    an EXISTING member's Security Server. Every step is the operator's,
    regardless of hurl/steps.py's own `actor` default -- the override
    documented at build_hosted_client()'s call sites,
    applied here rather than trusted from the registry. There is no
    member-side infrastructure in this path at all, so it never blocks.
  - **own_server**: the joining member brings up its own Security Server.
    Here the registry's per-step `actor` is read as declared -- the
    anchor-upload/CSR/activation steps really are the member's -- so
    `actor: member` steps genuinely exist in the sequence, and BLOCKED (the
    state that waits for them) becomes reachable.

And one shape of un-join: unjoin() walks a completed
job's steps BACKWARDS, running each hurl/steps.py `reverse` template guarded
by its `probe`. It is a second engine beside run(), not a mode of it -- see
the section comment above unjoin() for the three reasons.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

import httpx
import yaml

import validate
from schema import JoinPayload

# hurl/run-linkup.sh's own proven values (spec S5.5: match them). The budget
# is for the WHOLE RUN, not per step -- a step that fails is retried at the
# job level, from the top of that step, and every retry comes out of the same
# pot. Retrying the whole step rather than the failed entry inside it is what
# 409-as-success (spec S5.3) makes safe.
RETRY_BUDGET = 12
RETRY_INTERVAL_SECONDS = 10.0

# ...with ONE exception: the r1 reachability step (join.r1_verify) gets its
# own budget rather than whatever the run has left. Found live on the first
# own-server join: ss.client_register's wait for the new client to propagate
# from the Central Server consumed 95-107s of the 120s run budget before the
# sequence even reached the r1 step, leaving it 13-25s -- against a
# reachability window measured live at 45s to 8min after ACTIVE. The record
# could never say verified: true, and there was no way back (resume refuses a
# non-FAILED/BLOCKED record). The propagation the member is waiting out does
# not care how many retries the earlier steps burned, so it gets its own full
# window every time the run reaches it. 54 x 10s = 9 minutes, past the slower
# of the two observed cycles. This budget is spent by, and reported for, the
# r1 step alone -- it never touches record["retry_budget_left"], which stays
# the shared run budget for every other step kind.
R1_RETRY_BUDGET = 54

# BLOCKED (spec S4, S6.1). Before an `actor: member` step -- one this API has
# no business performing on its own, against a Security Server it does not
# own -- the run polls that server's :4000. The poll IS the completion signal:
# spec S6.1 records why the work-order queue and the callback endpoint an
# earlier draft had were deleted, and this is what replaced them. Bounded, and
# a bound that expires is NOT a failure: the request goes BLOCKED and stays
# there, indefinitely, until someone runs scripts/join-agent.sh and resumes.
# The bound exists only so the job stops holding app.py's single _JOB_LOCK
# while it waits for a human -- 30s, matching scripts/join.sh's and
# scripts/console.sh's own `--wait-timeout 30`.
BLOCKED_POLL_ATTEMPTS = 15
BLOCKED_POLL_INTERVAL_SECONDS = 2.0

# Confirmed live (PLAN.md S8, docs/xroad-770-notes.md S9): a federation left
# idle overnight fails every cross-server call with this, which reads like a
# certificate fault and is not one.
OCSP_MARKER = "Server.ClientProxy.SslAuthenticationFailed"
OCSP_HINT = (
    "this is the Test CA's OCSP responses going stale (~10 hours idle), not a "
    "certificate or configuration fault in this join -- redeploy the federation "
    "fresh (scripts/teardown.sh --purge, then hurl/run-linkup.sh) and resume"
)

# The Hurl binary the Dockerfile copies out of the pinned Hurl image is an
# Alpine/musl build; its shared libraries land here rather than in /usr/lib,
# where they would sit alongside (and could shadow) the Debian base image's
# own. HURL_ENV is the subprocess's ENTIRE environment -- deliberately not
# os.environ, so no credential this process holds can reach the Hurl child
# other than through the --variable values we choose (same reasoning as
# writer._run_generate's "no env=").
#
# HURL_BIN is absolute for exactly that reason: with no PATH in env=,
# subprocess resolves a bare name against os.defpath (/bin:/usr/bin), which
# does not include /usr/local/bin, and every step died with FileNotFoundError
# in the built image. An absolute path is one
# fewer thing to get right than a PATH entry that has to agree with the
# Dockerfile's COPY target.
HURL_BIN = "/usr/local/bin/hurl"
HURL_ENV = {"LD_LIBRARY_PATH": "/opt/hurl-lib"}


class StepFailure(Exception):
    """A step that exhausted the run's retry budget. Carries the step id and
    the last thing observed, which is what spec S5.5 says a FAILED request
    must record."""

    def __init__(self, step_id: str, message: str):
        super().__init__(f"{step_id}: {message}")
        self.step_id = step_id
        self.message = message


# -- importing hurl/generate.py and hurl/steps.py -----------------------------
# Same sys.path insert tests/test_steps.py and tests/test_allocation.py
# already use. generate.py guards its main() behind __main__, so importing it
# runs nothing; render()/sub()/ss_prefix()/dn_escape() and the CS_USER/
# CS_PASS/CSR_COUNTRY constants are reused from it rather than copied, so the
# rendering this module does cannot drift from cold deploy's.


def _hurl_modules(pack_dir: pathlib.Path):
    hurl_dir = str(pathlib.Path(pack_dir).resolve() / "hurl")
    if hurl_dir not in sys.path:
        sys.path.insert(0, hurl_dir)
    import generate  # noqa: PLC0415
    import steps  # noqa: PLC0415

    return generate, steps


@dataclasses.dataclass(frozen=True)
class JobStep:
    """One executable step. `id` is hurl/steps.py's id, suffixed with the
    service code (and ACL subject) for the steps rendered once per service --
    last_completed_step has to name exactly one execution, and
    "service.publish" alone would not."""

    id: str
    kind: str  # "hurl" | "r1"
    actor: str
    template: str | None
    tokens: dict[str, str]
    requires: tuple[str, ...]
    provides: tuple[str, ...]
    probe: str | None = None
    unsafe_to_repeat: bool = False
    r1: dict | None = None  # kind == "r1" only: url + client header

    @property
    def must_rerun(self) -> bool:
        """This step provides a session token, which is never persisted (spec
        S5.4), so a resume has to re-run it to get one -- derived from the
        capture names rather than hand-listed, so a registry change cannot
        leave a hand-list stale."""
        return any(name.endswith("_xsrf_token") for name in self.provides)


def _is_secret(name: str) -> bool:
    return name.endswith("_xsrf_token")


# ss_admin_user ("xrd") is not a secret -- it's a short, well-known, documented
# test/dev admin username (present in this pack's own docs) -- so scrub() leaves
# it out. It stays in every `secrets` dict this module passes around (build_constants
# still needs the real value), just never treated as something to hide from output:
# redacting it bought nothing, and "xrd" is short enough to appear as a substring
# inside unrelated words, which could strip legitimate diagnostic text.
_NOT_SECRET = {"ss_admin_user"}


def scrub(text: str, secrets: dict[str, str]) -> str:
    """Belt and braces for spec S5.4: no credential in a persisted error
    message. Hurl's own error output quotes the template source (`{{token_pin}}`,
    unexpanded -- verified), so this should never have anything to do; it
    costs one pass over a short string and removes the need to trust that."""
    for name, value in secrets.items():
        if value and name not in _NOT_SECRET:
            text = text.replace(value, "***")
    return text


# -- the sequence -------------------------------------------------------------


def _own_server(payload: JoinPayload) -> bool:
    """Which of the two shapes this join is. Derived from hosted_on alone,
    never from the payload's own_server flag: validate.py's hosting check
    already refused a request that set neither or both, so the two can never
    disagree here -- and deriving it from one field means they cannot drift
    if that check ever changes."""
    return not payload.security_server.hosted_on


def _host(pack_dir: pathlib.Path, payload: JoinPayload) -> dict:
    """The Security Server this join's steps run against, and the identity
    its session belongs to. Under hosted_on that is the existing member whose
    server hosts the join, resolved from disk the same way validate.py's
    hosting check (S8 check 6) does, so an approved request cannot resolve to
    a different host than the one that was validated.

    For an own-server join the member IS its own host -- so every @HOSTVAR@,
    every session prefix and build_constants' `<prefix>_host` resolve to the
    joining member's own server without a second branch anywhere else."""
    if _own_server(payload):
        ss = payload.security_server
        return {
            "key": payload.code.lower(),
            "dns_name": ss.dns_name,
            "code": ss.code,
            "member_code": payload.code,
            "member_name": payload.name,
        }
    manifest = yaml.safe_load((pack_dir / "manifest.yaml").read_text())
    identity = manifest["identity"]
    servers = validate.load_existing_security_servers(pack_dir)
    dns = payload.security_server.hosted_on
    for key, server in servers.items():
        if server["dns_name"] == dns:
            return {
                "key": key,
                "dns_name": dns,
                "code": server["code"],
                "member_code": identity["members"][key]["code"],
                "member_name": identity["members"][key]["name"],
            }
    raise StepFailure("plan", f"security_server.hosted_on {dns!r} names no existing member's Security Server")


def build_constants(pack_dir: pathlib.Path, payload: JoinPayload, secrets: dict[str, str]) -> dict[str, str]:
    """Everything hurl/generate.py's main() writes into hurl/vars.env that
    this job's steps read, minus the members it never touches. vars.env
    itself is not reused: it is written by generate.py at 0600 for the hurl
    compose service, and re-deriving the handful of values needed here is
    cheaper than depending on that file existing and being current."""
    generate, _ = _hurl_modules(pack_dir)
    identity = yaml.safe_load((pack_dir / "manifest.yaml").read_text())["identity"]
    core = yaml.safe_load((pack_dir / "configs" / "x-road-bus" / "federation-core.yaml").read_text())
    host = _host(pack_dir, payload)
    constants = {
        "xroad_instance": identity["instance"],
        "member_class": identity["member_class"],
        "cs_host": core["central_server"]["address"],
        "ca_host": "ca",
        f"{generate.ss_prefix(host['dns_name'])}_host": host["dns_name"],
        "cs_admin_user": generate.CS_USER,
        "cs_admin_password": generate.CS_PASS,
        "ss_admin_user": secrets["ss_admin_user"],
        "ss_admin_password": secrets["ss_admin_password"],
        "token_pin": secrets["token_pin"],
        "csr_country": generate.CSR_COUNTRY,
    }
    # One spec-url variable per SERVICE, not per member. generate.py's
    # vars.env writes <key>_spec_url once per member and gets away with it
    # because every canonical member publishes exactly one service; nothing
    # in schema.py or validate.py caps services[], so a two-service join
    # through this API would publish both against whichever spec_url won the
    # shared name. SPECVAR is a token chosen in
    # this file, so it is disambiguated here rather than inheriting a
    # collision cold deploy never has.
    for svc in payload.services:
        constants[_spec_var(payload.code, svc.code)] = svc.spec_url
    return constants


def _spec_var(member_code: str, service_code: str) -> str:
    """The Hurl variable name a service's spec_url is injected under. One
    definition, read by build_constants (which sets it) and build_sequence
    (which names it in service.publish's @SPECVAR@) -- the two cannot drift."""
    return f"{member_code.lower()}_{service_code.replace('-', '_')}_spec_url"


def build_sequence(pack_dir: pathlib.Path, payload: JoinPayload) -> list[JobStep]:
    """The steps this join runs, in order, with every @TOKEN@ resolved.

    Both shapes share a prologue (the job's own re-establishment of what cold
    deploy captures once and keeps in Hurl scope -- see the module docstring)
    and an epilogue (build_service_file()'s publish/ACL steps, plus the r1
    reachability call). The middle differs:

      hosted_on   -- build_hosted_client()'s sequence verbatim: client-add,
                     then the SIGN key, then registration, the order
                     hurl/steps.py's comment says is load-bearing. Every step
                     runs against the HOST's server, with the HOST's session.
      own_server  -- build_ss_file()'s sequence verbatim (hurl/generate.py
                     ~line 461), the same cold-deploy bring-up every canonical
                     member got, against the joining member's OWN server:
                     bringup_init, the AUTH key, the SIGN key, CS
                     registration, activation, the timestamping service, then
                     the client. Two reads cold deploy gets for free from
                     PDGA's single Hurl scope -- ca_name and tsa_name/tsa_url
                     -- are re-established here from the member's own server
                     instead (both are GETs on the GLOBAL configuration, so
                     any initialised Security Server answers them; this is the
                     "unless it also becomes that member's own ca_name source"
                     case hurl/steps.py's ss.ca_name_capture comment names).
    """
    generate, steps = _hurl_modules(pack_dir)
    host = _host(pack_dir, payload)
    own = _own_server(payload)
    sess_p = generate.ss_prefix(host["dns_name"])
    cap_p = generate.ss_prefix(payload.security_server.dns_name)
    host_var = f"{sess_p}_host"
    code = payload.code
    sequence: list[JobStep] = []

    def add(step_id: str, tokens: dict, *, suffix: str = "") -> None:
        step = steps.BY_ID[step_id]
        sequence.append(
            JobStep(
                id=step_id + suffix,
                kind="hurl",
                # Under hosted_on, NOT step.actor: every step is the
                # operator's -- there is no
                # member-side infrastructure in that path at all. Under
                # own_server the registry's own per-step actor is the truth
                # and is read as declared, which is what puts `actor: member`
                # steps in the sequence and makes BLOCKED reachable.
                actor=step.actor if own else "operator",
                template=step.template,
                tokens=tokens,
                requires=tuple(generate.sub(name, **tokens) for name in step.requires),
                provides=tuple(generate.sub(name, **tokens) for name in step.provides),
                probe=step.probe,
                unsafe_to_repeat=step.unsafe_to_repeat,
            )
        )

    add("cs.init", {})
    add(
        "cs.members_member",
        dict(
            MEMBER_NAME=payload.name,
            MEMBER_CODE=code,
            SUBSYSTEM_CODE=payload.subsystem,
            SUBSYSTEM_DESCRIPTION=payload.subsystem_description,
        ),
    )
    add("cs.anchor", {})
    # Under hosted_on this is the HOST's own identity, not the joining
    # member's: the step's /initialization body sets owner_member_code/
    # security_server_code and the host is already initialised with its own,
    # so it is re-run only to obtain a session on it (the anchor upload and
    # initialization 409 or no-op). Under own_server it is the joining
    # member's own identity, and the initialization is real.
    add(
        "ss.bringup_init",
        dict(
            SS=host["dns_name"],
            SS_CODE=host["code"],
            MEMBER_CODE=host["member_code"],
            MEMBER_NAME=generate.dn_escape(host["member_name"]),
            HOSTVAR=host_var,
            P=sess_p,
        ),
    )
    add("ss.ca_name_capture", dict(HOSTVAR=host_var, P=sess_p))
    # Both shapes run this, in different places: build_ss_file() puts it after
    # the server is registered and active, build_hosted_client() puts it
    # first. Only its POSITION differs, so the tokens are named once.
    client_add = dict(
        SS=host["dns_name"],
        MEMBER_CODE=code,
        SUBSYSTEM=payload.subsystem,
        CONNECTION_TYPE="HTTP",
        HOSTVAR=host_var,
        SESS_P=sess_p,
        CAP_P=cap_p,
    )
    if own:
        # build_ss_file()'s order, verbatim. The AUTH key/cert is what makes
        # the server itself a member of the federation; the SIGN key is what
        # makes its owner able to sign messages. SS_CODE/MEMBER_NAME are the
        # joining member's own throughout -- unlike the hosted case below,
        # there is no other server to name.
        csr = dict(
            SS_CODE=host["code"],
            MEMBER_CODE=code,
            MEMBER_NAME=generate.dn_escape(payload.name),
            HOSTVAR=host_var,
        )
        add("ss.auth_key_csr", dict(csr, P=sess_p))
        add("ss.sign_key_csr", dict(csr, SESS_P=sess_p, CAP_P=cap_p))
        add("ss.bringup_register", dict(HOSTVAR=host_var, P=sess_p))
        add("ss.activate", dict(HOSTVAR=host_var, P=sess_p))
        # Cold deploy captures tsa_name/tsa_url once on PDGA's server and
        # every later ss.tsa_post reads them back out of the same Hurl scope.
        # Nothing is in scope between this job's steps, so the capture is part
        # of the sequence -- same re-establishment the module docstring
        # describes for cs.init/cs.anchor/ca_name.
        add("ss.tsa_capture", dict(HOSTVAR=host_var, P=sess_p))
        add("ss.tsa_post", dict(HOSTVAR=host_var, P=sess_p))
        add("ss.client_add", client_add)
    else:
        add("ss.client_add", client_add)
        # SS_CODE is the HOST's, not this member's nominal one -- the cert
        # lives on the host's token and naming a server that was never brought
        # up would be a lie in the cert (build_hosted_client()'s docstring).
        add(
            "ss.sign_key_csr",
            dict(
                SS_CODE=host["code"],
                MEMBER_CODE=code,
                MEMBER_NAME=generate.dn_escape(payload.name),
                HOSTVAR=host_var,
                SESS_P=sess_p,
                CAP_P=cap_p,
            ),
        )
    add("ss.client_register", dict(HOSTVAR=host_var, SESS_P=sess_p, CAP_P=cap_p))
    for svc in payload.services:
        add(
            "service.publish",
            dict(
                MEMBER_CODE=code,
                SUBSYSTEM=payload.subsystem,
                SERVICE_CODE=svc.code,
                SC=svc.code.replace("-", "_"),
                HOSTVAR=host_var,
                SESS_P=sess_p,
                CAP_P=cap_p,
                SPECVAR=_spec_var(code, svc.code),
            ),
            suffix=f":{svc.code}",
        )
        for subject in svc.access:
            add(
                "service.acl",
                dict(
                    SERVICE_CODE=svc.code,
                    HOSTVAR=host_var,
                    SESS_P=sess_p,
                    CAP_P=cap_p,
                    ACL_SUBJECT=subject.replace("/", ":"),
                    # Cold deploy names MoEYS here to explain
                    # acceptance/once-only-exchange.md's negative check. A join
                    # has no such counterpart, and naming a canonical member in
                    # a joined member's rendered comment would be misleading.
                    NEGATIVE="(none -- see acceptance/join-member.md)",
                ),
                suffix=f":{svc.code}:{subject}",
            )
    r1 = _r1_target(pack_dir, payload)
    if r1:
        sequence.append(
            JobStep(
                id="join.r1_verify",
                kind="r1",
                actor="operator",
                template=None,
                tokens={},
                requires=(),
                provides=(),
                r1=r1,
            )
        )
    return sequence


def _r1_target(pack_dir: pathlib.Path, payload: JoinPayload) -> dict | None:
    """Where to make the reachability call from, and to (spec decision 6,
    S2.6, S12's acceptance clause). Returns None for a consume-only join --
    it publishes nothing to be reachable, and its ACTIVE means "registered
    and able to reach the global configuration" (spec S4), not "callable".

    The consumer is the first subject the payload grants access to: check 7
    (ACL sanity) already proved it exists, and hurl/topology.json -- rewritten
    by the generate.py run inside writer.apply_real() -- says which Security
    Server hosts it, the same file apps/console/truth.py reads.
    """
    service = next((svc for svc in payload.services if svc.access), None)
    if service is None:
        return None
    subject = service.access[0]
    topology = json.loads((pack_dir / "hurl" / "topology.json").read_text())
    subject_id = subject.replace("/", ":")
    entry = next((s for s in topology["subsystems"] if s["id"] == subject_id), None)
    if entry is None:
        # Not the consume-only case above: check 7 (ACL sanity) already
        # proved this exact subject exists in manifest.yaml before the
        # request was ever approved, so its absence here means manifest.yaml
        # and hurl/topology.json have diverged since -- "registry-perfect but
        # dead" (design spec S12), the one case join.r1_verify exists to
        # catch. Silently omitting the step (the previous behaviour) would
        # reach ACTIVE with `verified` never set at all, which is a worse
        # silence than the one S12 calls out.
        raise StepFailure(
            "plan",
            f"ACL subject {subject_id!r} passed check 7 against manifest.yaml but is missing from "
            "hurl/topology.json -- manifest and topology have diverged; cannot plan join.r1_verify",
        )
    host = next((s for s in topology["security_servers"] if s["host"] == entry["hosted_on"]), None)
    proxy_port = (host or {}).get("proxy_port", 8080)
    # ponytail: the service ROOT path, not an operation from the joining
    # member's OpenAPI document. What this call has to prove is S2.4's
    # "registry-perfect but dead" case -- that a request actually traverses
    # the consumer's proxy, the provider's proxy and reaches the backend --
    # and any non-X-Road response proves that, including a backend 404. An
    # operation-specific path needs the spec re-fetched here and its path
    # parameters invented; add that when acceptance/join-member.md needs a specific
    # endpoint asserted rather than reachability.
    path = "/".join(
        [
            "r1",
            topology["instance"],
            topology["member_class"],
            payload.code,
            payload.subsystem,
            service.code,
        ]
    )
    return {
        "url": f"http://{entry['hosted_on']}:{proxy_port}/{path}/",
        "client_header": subject,
        "service": service.code,
    }


# -- execution ----------------------------------------------------------------


def _default_run_hurl(
    label: str, body: str, variables: dict[str, str], *, cookie_jar: pathlib.Path | None = None
) -> dict:
    """Run one rendered step and return its Hurl JSON report element.

    --report-json APPENDS a new array element to <dir>/report.json on every
    invocation that names the same directory (verified against the committed
    out/hurl-report/report.json, which carries one element per historical
    deploy), so every call gets a fresh directory that is deleted again here.
    --insecure mirrors run-linkup.sh: the Test CA's certificates are
    self-signed. Hurl's own --retry is not used -- the retry budget is the
    run's, not the step's (spec S5.5), and lives in run() below.

    cookie_jar (--cookie/--cookie-jar, same file for both): found live --
    cs.members_member 401ed even though it
    carried {{cs_xsrf_token}} in its X-XSRF-TOKEN header exactly as
    hurl/steps.py declares. run-linkup.sh concatenates every step into ONE
    hurl invocation, so Hurl's own cookie jar carries the login's JSESSIONID
    to every later request in that file for free; job.py runs one PROCESS
    per step, so nothing did. X-Road's admin API validates the XSRF header
    against the SESSION the JSESSIONID cookie names, not the header alone
    (confirmed live: the header without the cookie is a 401, not a 403 --
    unauthenticated, not merely a CSRF mismatch). The *_xsrf_token capture
    already threaded through context/variables was necessary but not
    sufficient. Hurl's -b/-c read the same Netscape-format file curl uses;
    passing one shared jar for the whole run works because cookies in that
    format are domain-scoped, so the CS's and the host SS's sessions coexist
    in it without colliding -- no template or registry change needed."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"kp2-join-{label.replace('/', '_').replace(':', '_')}-"))
    try:
        step_file = tmp / "step.hurl"
        step_file.write_text(body)
        report_dir = tmp / "report"
        args = [HURL_BIN, "--insecure"]
        if cookie_jar is not None:
            args += ["--cookie", str(cookie_jar), "--cookie-jar", str(cookie_jar)]
        for name, value in variables.items():
            args += ["--variable", f"{name}={value}"]
        args += ["--report-json", str(report_dir), str(step_file)]
        proc = subprocess.run(args, capture_output=True, text=True, env=HURL_ENV)
        report_path = report_dir / "report.json"
        if not report_path.exists():
            # Hurl never got as far as writing a report (bad arguments, a
            # template it could not parse): there is no element to parse, so
            # synthesise a failed one carrying what it did say.
            return {"success": False, "entries": [], "_stderr": proc.stderr or proc.stdout}
        element = json.loads(report_path.read_text())[-1]
        element["_stderr"] = proc.stderr
        # Response bodies live in files beside the report, referenced by
        # relative path. The OCSP marker (spec S5.5) arrives in a body, not
        # in Hurl's own error text, so read them here while the directory
        # still exists.
        element["_bodies"] = "\n".join(
            path.read_text(errors="replace") for path in sorted((report_dir / "store").glob("*")) if path.is_file()
        ) if (report_dir / "store").is_dir() else ""
        return element
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _default_r1_call(url: str, client_header: str) -> tuple[bool, str]:
    """The r1 reachability call, adapted from apps/console/xroad.py's
    exchange() -- a plain GET on the consumer's proxy (:8080) with an
    X-Road-Client header, verify=False for the same Test CA reason. Not
    imported from apps/console: this container does not mount that app
    (app.py copies its request-boundary guard for the same reason).

    Verified means the call traversed X-Road and reached a backend: any
    response that is not an X-Road fault. A denial
    (Server.ServerProxy.AccessDenied) is a fault -- it means the ACL step did
    not take -- and so is a proxy-level SSL failure, which is what OCSP
    staleness looks like from here.
    """
    try:
        resp = httpx.get(url, headers={"X-Road-Client": client_header}, verify=False, timeout=10.0)
    except httpx.HTTPError as exc:
        return False, f"{url}: {exc}"
    try:
        body = resp.json()
    except ValueError:
        return True, f"{url}: HTTP {resp.status_code}"
    fault = body.get("type") if isinstance(body, dict) else None
    if isinstance(fault, str) and (fault.startswith("Server.") or fault.startswith("Client.")):
        return False, f"{url}: HTTP {resp.status_code} {fault} {body.get('message', '')}".strip()
    return True, f"{url}: HTTP {resp.status_code}"


def _default_server_up(dns_name: str) -> bool:
    """Does this Security Server's admin API answer on :4000? The same
    question hurl/compose.members.yml's generated HEALTHCHECK asks
    (`curl -f -k https://localhost:4000`), asked from outside the container
    instead -- join-api is on the linkup network, so the service name
    resolves here exactly as it does for every other step.

    ANY HTTP response counts, including the redirect or 401 an
    unauthenticated GET gets: what this has to establish is that the TLS
    listener is up and serving, not that a particular page renders. The
    failure it exists to distinguish is the one found live -- a
    container that is "started" but whose Tomcat/TLS listener never comes up,
    so a caller hangs mid-handshake instead of being refused."""
    try:
        httpx.get(f"https://{dns_name}:4000", verify=False, timeout=5.0)
    except httpx.HTTPError:
        return False
    return True


def _wait_for_server(dns_name: str, server_up: Callable[[str], bool], interval: float) -> bool:
    for attempt in range(BLOCKED_POLL_ATTEMPTS):
        if server_up(dns_name):
            return True
        if attempt + 1 < BLOCKED_POLL_ATTEMPTS:
            time.sleep(interval)
    return False


def _captures(element: dict) -> dict[str, str]:
    """Every [Captures] value in the invocation, across all of the step's
    entries -- a single template renders more than one Hurl entry (cs.init is
    /login then /initialization), and spec open question 3 is resolved:
    entries[].captures[] carries {name, value} per entry."""
    return {c["name"]: c["value"] for entry in element.get("entries", []) for c in entry.get("captures", [])}


def _statuses(element: dict) -> list[int]:
    return [
        call["response"]["status"]
        for entry in element.get("entries", [])
        for call in entry.get("calls", [])
        if call.get("response")
    ]


def _succeeded(element: dict) -> bool:
    """409-as-success, the idempotence default (spec S5.3): the templates
    assert exact created-statuses (HTTP 201), so a step whose effect already
    exists fails its assert with a 409 on the wire. Proven live for
    service.acl (PLAN.md S11, apps/console/xroad.py's grant()); assumed, per
    that section, for the rest of class (b)."""
    if element.get("success"):
        return True
    return 409 in _statuses(element)


def _failure_text(element: dict) -> str:
    text = "\n".join(part for part in (element.get("_stderr", ""), element.get("_bodies", "")) if part).strip()
    if OCSP_MARKER in text:
        return f"{OCSP_MARKER} -- {OCSP_HINT}\n\n{text}"
    return text or "no output"


# Probes are per-step reads, not a generic mechanism: each one returns raw
# state and the executor decides. A probed step with no interpreter here
# simply re-runs, which is what the 409-safety default already covers -- so
# an interpreter is only written where re-running is NOT safe.
#
# A hosted join reaches two of the eight probed steps; an own-server join
# reaches three more, one of which needed one:
#   - ss.bringup_register: bundles register-then-approve, whose halves can
#     diverge on a process death in between, so 409-as-success would report a
#     half-done step as done. Skipping it costs nothing downstream: its one
#     capture (@P@_auth_cert_req_id) is required by no later step.
#   - ss.auth_key_csr is deliberately left WITHOUT one, even though it is the
#     clearest (c) in the registry (POST .../keys-with-csrs has no natural
#     uniqueness, so a resumed retry silently makes a SECOND AUTH key rather
#     than 409ing). A probe here can only return a VERDICT -- _probe()
#     discards the probe's own captures -- and skipping this step leaves
#     @P@_auth_key_cert_hash unset, which ss.bringup_register and ss.activate
#     both require: the resume would then fail outright. A duplicate AUTH key
#     on the joining member's own (throwaway, demo) Security Server is the
#     lesser harm than a resume that cannot complete. Threading a probe's
#     captures back into the job context would fix this properly and is a
#     change to the probe mechanism, not to this task.
#   - ss.tsa_post is deliberately left WITHOUT one. Its probe template
#     (PROBE_SS_TSA_POST.hurl.tmpl) reads GET /timestamping-services -- the
#     GLOBAL approved list from the configuration, the same read
#     TSA_CAPTURE.hurl.tmpl makes -- not GET /system/timestamping-services,
#     the list the step itself POSTs to. It therefore cannot answer its own
#     question: it is non-empty whether or not the step ran. Interpreting it
#     would be worse than re-running, which at most leaves a duplicate
#     (identical) approved timestamping entry on that one Security Server.
#     Fixing the template is a registry change, not this task's.


def _probe_client_registered(step: JobStep, captures: dict) -> bool:
    return captures.get(f"{step.tokens['CAP_P']}_client_status") == "REGISTERED"


def _token_keys(step: JobStep, captures: dict) -> list | None:
    """PROBE_SS_SIGN_KEY's captured token body as its keys[] list, or None if
    there is no readable body at all. The distinction matters in the reversal
    direction: "this token carries no key for this member" is proof of
    absence, "I could not read this token" is not, and collapsing the two
    would skip the SIGN-key delete and leave exactly the orphan
    docs/xroad-770-notes.md #11 found."""
    raw = captures.get(f"{step.tokens['CAP_P']}_token")
    if not raw:
        return None
    try:
        token = json.loads(raw)
    except ValueError:
        return None
    # A token body with no keys[] at all is unreadable, not a token with no
    # keys: only a real list is evidence about what is on it.
    keys = token.get("keys") if isinstance(token, dict) else None
    return keys if isinstance(keys, list) else None


def _sign_key_id(step: JobStep, captures: dict) -> str | None:
    """This member's SIGN key id on the token PROBE_SS_SIGN_KEY just read, or
    None if it has none. Correlated by keys[].certificates[].owner_id, NEVER
    by label: a shared host's token carries one identically-labelled "Sign
    key" per hosted member (security_server.hosted_on; four, live, on
    ss-plr under the since-retired lite profile at the time --
    PROBE_SS_SIGN_KEY.hurl.tmpl's own comment).

    Forwards this answers "does it already exist?" (the probe interpreter
    below). Backwards it answers "which key do I delete?" -- the same read,
    the same correlation, which is exactly why hurl/steps.py's ss.sign_key_csr
    reuses this probe for its `reverse` rather than the literal
    GET /token-certificates/{hash} the live spike used (that needs the very
    hash whose fate is in question). SS_SIGN_KEY_DELETE.hurl.tmpl's own
    comment: a label match, or a captured id that predates a re-issued key,
    deletes a DIFFERENT agency's key."""
    keys = _token_keys(step, captures)
    if keys is None:
        return None
    suffix = f":{step.tokens['MEMBER_CODE']}"
    for key in keys:
        if key.get("usage") != "SIGNING":
            continue
        for cert in key.get("certificates", []):
            if str(cert.get("owner_id", "")).endswith(suffix):
                return str(key.get("id")) if key.get("id") is not None else None
    return None


def _probe_sign_key_exists(step: JobStep, captures: dict) -> bool:
    return _sign_key_id(step, captures) is not None


def _probe_auth_cert_registered(step: JobStep, captures: dict) -> bool:
    """Only REGISTERED counts. REGISTRATION_IN_PROGRESS means the PUT landed
    but the Central Server approval did not -- exactly the half-done case
    409-as-success would misread as done (PROBE_SS_BRINGUP_REGISTER's own
    comment)."""
    return captures.get(f"{step.tokens['P']}_auth_cert_status") == "REGISTERED"


PROBE_INTERPRETERS: dict[str, Callable[[JobStep, dict], bool]] = {
    "ss.client_register": _probe_client_registered,
    "ss.sign_key_csr": _probe_sign_key_exists,
    "ss.bringup_register": _probe_auth_cert_registered,
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _shared_cookie_jar(run_hurl):
    """One Netscape cookie jar for a whole run/walk, or the caller's own
    run_hurl untouched. Extracted from run() when unjoin() needed the same
    thing for the same reason (see _default_run_hurl's docstring: one Hurl
    PROCESS per step means nothing carries cs.init's JSESSIONID forward, and
    the admin API validates X-XSRF-TOKEN against the session that cookie
    names). Returns (run_hurl, tempdir-to-remove-or-None).

    Only wired in for the real default: a caller that supplies its own
    run_hurl is replaying fixtures and has nothing for a cookie jar to do."""
    if run_hurl is not _default_run_hurl:
        return run_hurl, None
    jar_dir = tempfile.mkdtemp(prefix="kp2-join-cookies-")
    jar = pathlib.Path(jar_dir) / "jar.txt"

    def wrapped(label: str, body: str, variables: dict, _jar=jar, _inner=run_hurl) -> dict:
        return _inner(label, body, variables, cookie_jar=_jar)

    return wrapped, jar_dir


def run(
    record: dict,
    pack_dir: pathlib.Path,
    *,
    secrets: dict[str, str],
    save: Callable[[dict], None],
    run_hurl: Callable[[str, str, dict], dict] = _default_run_hurl,
    r1_call: Callable[[str, str], tuple[bool, str]] = _default_r1_call,
    server_up: Callable[[str], bool] = _default_server_up,
    retry_interval: float = RETRY_INTERVAL_SECONDS,
    blocked_poll_interval: float = BLOCKED_POLL_INTERVAL_SECONDS,
) -> dict:
    """Drive `record` (an out/join/<id>.json request) to ACTIVE, FAILED or
    BLOCKED, persisting after every step via `save`. Mutates and returns the
    record.

    Resume: `record["last_completed_step"]` names the last step known to have
    completed; execution starts after it, re-injecting every persisted
    capture. Steps before it re-run only if they provide a session token
    (JobStep.must_rerun) -- nothing else is re-run, which is the guarantee
    the resume test asserts.

    BLOCKED: an own-server join's `actor: member` steps run against the
    joining member's own Security Server, which this API cannot stand up. Any
    such step is preceded by a bounded poll of that server's :4000; if the
    bound expires the request goes BLOCKED (never FAILED -- there is nothing
    wrong, the member has simply not brought its server up yet) and leaves
    through the same POST /requests/{id}/resume a FAILED one does, which
    re-enters here and polls again. A hosted_on join has no `actor: member`
    step at all and can never reach this state.
    """
    payload = JoinPayload(**record["payload"])
    sequence = build_sequence(pack_dir, payload)
    constants = build_constants(pack_dir, payload, secrets)
    context = dict(record.get("context") or {})
    # The captures spec S5.4 forbids on disk (session tokens) live here for
    # the length of the run and nowhere else. This split is the whole reason
    # JobStep.must_rerun exists: a resume has no session dict to restore, so
    # the steps that provide one run again.
    session: dict[str, str] = {}

    # One shared cookie jar for the whole run (_default_run_hurl's own
    # docstring explains why this exists at all: found live, on the first
    # real approve -- cs.members_member 401ed because the JSESSIONID
    # cs.init's login set was never carried to the next step's separate Hurl
    # process). Only wired in for the real default -- a caller that supplies
    # its own run_hurl (every test in this module) is replaying fixtures or
    # asserting on the step engine itself, never making a real HTTP call, so
    # there is nothing for a cookie jar to do there. A fresh, empty jar every
    # run/resume, never persisted to record or disk beyond this process's
    # tempdir: spec S5.4's "session state is never persisted" applies to
    # cookies exactly as it already does to *_xsrf_token.
    run_hurl, cookie_jar_dir = _shared_cookie_jar(run_hurl)

    try:
        last = record.get("last_completed_step")
        ids = [step.id for step in sequence]
        if last and last not in ids:
            raise StepFailure(
                "resume",
                f"last_completed_step {last!r} is not a step of this request's sequence "
                f"-- the payload or the registry changed under it; start a new request",
            )
        resuming = bool(last)
        completed = set(ids[: ids.index(last) + 1]) if last else set()

        record["state"] = "RUNNING"
        record["queued"] = False
        record["started_at"] = _now()
        record["error"] = None
        record["blocked"] = None
        # One budget per RUN (spec S5.5), so a resume starts with a full one --
        # the operator resuming is a new run, and the previous run's exhausted
        # budget is not evidence about this one.
        record["retry_budget_left"] = RETRY_BUDGET
        save(record)

        for step in sequence:
            already = step.id in completed
            if already and not step.must_rerun:
                continue
            if resuming and step.unsafe_to_repeat:
                # Empty class today (tests/test_steps.py asserts it stays empty);
                # the refusal exists so that stops being silently load-bearing.
                record["state"] = "FAILED"
                record["error"] = {
                    "step": step.id,
                    "message": f"refusing to resume across {step.id}: it is flagged unsafe_to_repeat "
                    "in hurl/steps.py and no probe can establish whether it already ran",
                }
                save(record)
                return record
            if step.actor == "member" and not _wait_for_server(
                payload.security_server.dns_name, server_up, blocked_poll_interval
            ):
                # Not a failure, and deliberately not counted against the
                # retry budget: nothing has gone wrong. This step is the
                # joining member's own (hurl/steps.py declares the actor), and
                # its server is not there yet.
                record["state"] = "BLOCKED"
                record["blocked"] = {
                    "step": step.id,
                    "server": payload.security_server.dns_name,
                    "message": (
                        f"{payload.security_server.dns_name} is not answering on :4000. "
                        f"{step.id} is the joining member's own step, not the operator's -- "
                        f"this API cannot stand that server up. Run "
                        f"`scripts/join-agent.sh {payload.code.lower()}` on the Docker host, "
                        "then resume this request."
                    ),
                }
                save(record)
                return record

            variables = {**constants, **context, **session}
            try:
                if resuming and step.probe and step.id.split(":")[0] in PROBE_INTERPRETERS:
                    if _probe(step, variables, pack_dir, run_hurl):
                        record["last_completed_step"] = step.id
                        save(record)
                        continue
                _execute(step, variables, context, session, pack_dir, run_hurl, r1_call, record, retry_interval)
            except StepFailure as exc:
                record["state"] = "FAILED"
                record["error"] = {"step": exc.step_id, "message": scrub(exc.message, secrets)}
                save(record)
                return record
            record["context"] = context
            if not already:
                # Forward only. A session step re-run on resume (`already` and
                # must_rerun) has already been counted by the run that first
                # completed it -- moving the marker back to it would, for the
                # span of the next two invocations, describe less progress than
                # was actually made, and a kill in that window would make the
                # NEXT resume re-run steps this one deliberately skipped.
                record["last_completed_step"] = step.id
            save(record)

        record["state"] = "ACTIVE"
        record["finished_at"] = _now()
        if not payload.services:
            # spec S4: a consume-only member's ACTIVE means registered and able
            # to reach the global configuration -- there is nothing of its own to
            # call, and it cannot call anyone until the providers it named in
            # requested_access: grant it. Say so rather than report an
            # unqualified success.
            record["note"] = (
                "consume-only join: registered and able to reach the global configuration. "
                "It can call nothing until the providers named in requested_access: grant it "
                "(that is their own config, not this API's)."
            )
        save(record)
        return record
    finally:
        if cookie_jar_dir is not None:
            shutil.rmtree(cookie_jar_dir, ignore_errors=True)


# The retry budget is a single mutable cell for the whole run rather than a
# parameter threaded through: one budget, one place (spec S5.5).
def _execute(
    step: JobStep,
    variables: dict,
    context: dict,
    session: dict,
    pack_dir: pathlib.Path,
    run_hurl,
    r1_call,
    record: dict,
    retry_interval: float,
) -> None:
    # The r1 step's own budget, never the run's -- see R1_RETRY_BUDGET. Its
    # counter is local for the same reason: record["retry_budget_left"]
    # describes what the OTHER step kinds have left, and r1's spending is not
    # theirs to inherit (it is also the last step of every sequence
    # build_sequence() puts it in, so nothing reads that field after it).
    r1 = step.kind == "r1"
    budget = R1_RETRY_BUDGET if r1 else record.get("retry_budget_left", RETRY_BUDGET)
    while True:
        if r1:
            ok, detail = r1_call(step.r1["url"], step.r1["client_header"])
            if ok:
                record["verified"] = True
                record["verified_by"] = detail
                return
            failure = f"{OCSP_MARKER} -- {OCSP_HINT}\n\n{detail}" if OCSP_MARKER in detail else detail
        else:
            generate, _ = _hurl_modules(pack_dir)
            missing = [name for name in step.requires if name not in variables]
            if missing:
                raise StepFailure(step.id, f"job context is missing required Hurl variable(s): {sorted(missing)}")
            try:
                element = run_hurl(step.id, generate.render(step.template, **step.tokens), variables)
            except Exception as exc:  # noqa: BLE001
                # The runner itself broke (a missing binary, an unreadable
                # template) -- not something a retry fixes, and not something
                # that should escape past the FAILED-with-a-step-id contract
                # into app.py's blanket handler. A
                # FileNotFoundError for the Hurl binary did exactly that.
                raise StepFailure(step.id, f"could not run this step: {type(exc).__name__}: {exc}") from exc
            if _succeeded(element):
                for name, value in _captures(element).items():
                    (session if _is_secret(name) else context)[name] = value
                    variables[name] = value
                record["retry_budget_left"] = budget
                return
            failure = _failure_text(element)

        if budget <= 0:
            if r1:
                # Not a job failure: spec S4 says a member that registered and
                # published but whose reachability call has not passed is
                # ACTIVE with verified: false, one fact about the member
                # rather than a place in the lifecycle.
                record["verified"] = False
                record["verified_by"] = (
                    f"unreachable for {R1_RETRY_BUDGET} attempts "
                    f"({RETRY_INTERVAL_SECONDS:.0f}s apart). Last observed: {failure}"
                )
                return
            record["retry_budget_left"] = 0
            raise StepFailure(
                step.id,
                f"exhausted the run's retry budget ({RETRY_BUDGET} attempts, "
                f"{RETRY_INTERVAL_SECONDS:.0f}s apart). Last observed:\n{failure}",
            )
        budget -= 1
        if not r1:
            record["retry_budget_left"] = budget
        time.sleep(retry_interval)


def _probe(step: JobStep, variables: dict, pack_dir: pathlib.Path, run_hurl) -> bool:
    """Has this step already happened? Only asked on resume, and only for the
    steps classified as ambiguous (spec S5.3): a probe
    failure answers "no", never fails the job -- re-running is the safe
    default and the whole point of the classification."""
    generate, _ = _hurl_modules(pack_dir)
    try:
        element = run_hurl(f"{step.id}#probe", generate.render(step.probe, **step.tokens), variables)
    except Exception:  # noqa: BLE001 -- a probe that cannot run answers "no"
        return False
    if not element.get("success"):
        return False
    return PROBE_INTERPRETERS[step.id.split(":")[0]](step, _captures(element))


# -- the reversal walk --------------------------------------------------------
# Un-joining is not run() with the sequence reversed. Three things make it its
# own engine:
#
#   1. **The order is not `reversed(completed_steps)`.** hurl/steps.py's
#      REVERSAL_ORDER is what was established LIVE (docs/xroad-770-notes.md
#      #11 finding 5): ss.client_register -> ss.client_add -> ss.sign_key_csr,
#      i.e. the client goes before its key backwards just as forwards. The
#      strict mirror (key before client) was never tried live.
#   2. **The guard is a probe with a per-step reading, not a status code.**
#      Two of the six signal absence with `200` and an EMPTY COLLECTION rather
#      than a 404 (service.publish's descriptions list, cs.members_member's
#      /clients?q= result), and a third (ss.client_register) signals it with a
#      transitional `DELETION_IN_PROGRESS` before the client is gone at all.
#      "probe 404s => already gone" is wrong for half the walk; each probe
#      template's own comment says what its absence signal is, and
#      REVERSAL_ABSENT below encodes exactly those six readings.
#   3. **Nothing is captured.** A reversal call returns 204 with no body. The
#      one value a reversal has to LEARN -- the SIGN key's id -- comes from
#      its own probe, not from the job context, deliberately
#      (SS_SIGN_KEY_DELETE.hurl.tmpl).
#
# What it DOES share with run(): the same rendered-template-through-Hurl step
# shape, the same shared cookie jar, the same ONE-BUDGET-PER-RUN retry
# semantics (RETRY_BUDGET / RETRY_INTERVAL_SECONDS, record["retry_budget_left"]),
# and the same "session captures are never persisted" rule -- which is why the
# walk re-runs the job's session steps first (JobStep.must_rerun), exactly as a
# resume does.
#
# Resumability: there is no reversal marker to trust. The walk
# is re-entrant because every entry is probed first -- a DELETE re-issued after
# a kill re-walks from the top and skips whatever the probes now report absent.
# record["last_reversed_step"] is written for the operator's benefit, never
# read back as a skip.

_HURL_VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

# The reversal analogue of _succeeded()'s 409-as-success (spec S5.3). Repeating
# any of the six is safe and distinguishable (docs/xroad-770-notes.md #11):
# 1 -> 409 accessright_not_found, 2 -> 404 service_description_not_found,
# 4 -> 404 client_not_found, 5 -> 404, 6 -> 404 member_not_found. So a 404 or
# 409 on a reversal means "already gone" -- which is what makes a probe that
# under-reports absence harmless: the call is attempted and succeeds anyway.
#
# The ONE exception is the reason this is not a bare status check: `DELETE
# /clients/{id}` following the unregister can answer `409 action_not_possible`
# while the deletion propagates (#7 recorded a multi-minute window for an
# own-server member; the hosted spike saw none, and #11 finding 3 is explicit
# that the window's SIZE is not established). That 409 is retryable, not
# success -- distinguished by its error code, because its STATUS is
# indistinguishable from service.acl's already-revoked 409.
REVERSAL_RETRYABLE_CODE = "action_not_possible"


def _reversal_succeeded(element: dict) -> bool:
    if element.get("success"):
        return True
    if REVERSAL_RETRYABLE_CODE in _failure_text(element):
        return False
    return bool({404, 409} & set(_statuses(element)))


def _absent_by_probe_404(step: JobStep, element: dict, variables: dict) -> bool:
    """service.acl and ss.client_add. Their probe templates ASSERT `HTTP 404`,
    so Hurl's own success IS the absence signal -- a live grant / a live client
    fails the assert with its 200."""
    return bool(element.get("success"))


def _absent_service_description(step: JobStep, element: dict, variables: dict) -> bool:
    """service.publish. PROBE_SERVICE_DELETE's own comment: GET
    /clients/{id}/service-descriptions ALWAYS 200s, and absence is an empty
    list, not a 404 -- one of the two probes here that does not 404."""
    if not element.get("success"):
        return False
    wanted = variables.get(f"{step.tokens['CAP_P']}_{step.tokens['SC']}_description_id")
    try:
        descriptions = json.loads(_captures(element).get(f"{step.tokens['CAP_P']}_service_descriptions", ""))
    except ValueError:
        return False
    if not isinstance(descriptions, list) or not all(isinstance(d, dict) for d in descriptions):
        # A body that is not the list-of-descriptions this endpoint documents
        # is unreadable, not empty -- and "unreadable" must never be absence.
        # Without this, a JSON object or string
        # iterates to elements that match nothing, any() is False, and this
        # returns "already gone": the service description survives its own
        # member's departure.
        return False
    if wanted is None:
        # Nothing to correlate against -- the reversal template needs this
        # same variable, so let it fail loudly there rather than guess here.
        return False
    return not any(str(d.get("id")) == str(wanted) for d in descriptions)


def _absent_client_registration(step: JobStep, element: dict, variables: dict) -> bool:
    """ss.client_register. Two absence signals, in sequence: while the client
    still exists the probe 200s with `status: DELETION_IN_PROGRESS` (the
    unregister landed), and once the LATER ss.client_add reversal has removed
    the client entirely the same read 404s. Both mean "the unregister does not
    need re-issuing"; only a live REGISTERED client does."""
    if element.get("success"):
        return _captures(element).get(f"{step.tokens['CAP_P']}_client_status") == "DELETION_IN_PROGRESS"
    return 404 in _statuses(element)


def _absent_sign_key(step: JobStep, element: dict, variables: dict) -> bool:
    """ss.sign_key_csr. Absent BY CORRELATION, not by status code: the probe
    reads the whole token and 200s whether or not this member's key is on it
    (hurl/steps.py's ss.sign_key_csr comment on why this probe, not the
    literal GET /token-certificates/{hash}, is the reversal's guard).

    An UNREADABLE token is not absence (_token_keys' own docstring): it falls
    through to the reversal, which then refuses to guess an id."""
    if not element.get("success"):
        return False
    captures = _captures(element)
    return _token_keys(step, captures) is not None and _sign_key_id(step, captures) is None


def _absent_cs_member(step: JobStep, element: dict, variables: dict) -> bool:
    """cs.members_member. PROBE_CS_MEMBER_DELETE's own comment: GET
    /clients?q=<code> on the Central Server always 200s and absence is an
    empty clients list -- the second of the two non-404 probes. There is no
    GET /subsystems/{id} on the CS at all (405), so this is the only viable
    read.

    Deliberately not filtered down to this member's own entry: `q=` is a
    SUBSTRING search, so another member whose code contains this one would
    keep the list non-empty and this would answer "not gone". That is the
    harmless direction -- the delete is attempted and 404 member_not_found
    reads as success (_reversal_succeeded)."""
    if not element.get("success"):
        return False
    try:
        body = json.loads(_captures(element).get("cs_member_delete_probe_clients", ""))
    except ValueError:
        return False
    clients = body.get("clients") if isinstance(body, dict) else None
    # No clients[] at all is unreadable, not empty -- same rule as
    # _absent_service_description's.
    return isinstance(clients, list) and not clients


REVERSAL_ABSENT: dict[str, Callable[[JobStep, dict, dict], bool]] = {
    "service.acl": _absent_by_probe_404,
    "service.publish": _absent_service_description,
    "ss.client_register": _absent_client_registration,
    "ss.client_add": _absent_by_probe_404,
    "ss.sign_key_csr": _absent_sign_key,
    "cs.members_member": _absent_cs_member,
}


def retire_instruction(payload: JoinPayload) -> dict | None:
    """What the operator must do by hand for an own-server
    member, because this API never gets a Docker socket (design decision 8,
    the same split scripts/join-agent.sh makes for the bring-up direction).
    None for a hosted member -- it owns no container and no volumes, and its
    residue is the SIGN key the walk deletes instead (Step 4b).

    The three volume names are hurl/generate.py's own, written into
    hurl/compose.members.yml's generated `volumes:` block."""
    if not _own_server(payload):
        return None
    key = payload.code.lower()
    dns = payload.security_server.dns_name
    volumes = [f"kp2-{key}-db", f"kp2-{key}-conf", f"kp2-{key}-archive"]
    return {
        "container": dns,
        "volumes": volumes,
        "message": (
            f"{payload.code} owned its own Security Server. This API does not touch Docker -- "
            f"run this on the Docker host to finish the un-join:\n"
            f"  docker rm -f {dns}\n"
            f"  docker volume rm {' '.join(volumes)}\n"
            f"Left in place, {dns}'s database, /etc/xroad and archive volumes survive "
            f"teardown and a later member reusing this key inherits them."
        ),
    }


def unjoin(
    record: dict,
    pack_dir: pathlib.Path,
    *,
    secrets: dict[str, str],
    save: Callable[[dict], None],
    run_hurl: Callable[[str, str, dict], dict] = _default_run_hurl,
    retry_interval: float = RETRY_INTERVAL_SECONDS,
) -> dict:
    """Walk `record`'s completed steps backwards, undoing each. Mutates and
    returns the record, persisting after every entry via `save`.

    RETIRING on entry (set by the caller), RETIRED when the walk completes.
    A failure leaves the record in RETIRING with `error` set -- NOT FAILED,
    which on this record would send POST /requests/{id}/resume back down the
    forward path. Re-issuing the DELETE resumes the walk; the probes make that
    re-entrant.

    No BLOCKED here, deliberately: un-joining an own-server member runs
    against a Security Server that must still be UP (its container is stopped
    afterwards, retire_instruction()), so a server that is not answering is a
    real failure with a real message, not a state to wait in. The forward
    path's BLOCKED exists because the member had not stood its server up yet;
    backwards there is no such "not yet".
    """
    payload = JoinPayload(**record["payload"])
    _, steps = _hurl_modules(pack_dir)
    sequence = build_sequence(pack_dir, payload)
    constants = build_constants(pack_dir, payload, secrets)
    context = dict(record.get("context") or {})
    session: dict[str, str] = {}

    ids = [step.id for step in sequence]
    last = record.get("last_completed_step")
    # An ACTIVE record's marker names the last step of the sequence. A record
    # missing it (or naming a step this payload no longer has) is walked in
    # full: the probes decide what actually needs undoing, which is a safer
    # default than assuming nothing ran.
    completed = set(ids[: ids.index(last) + 1]) if last in ids else set(ids)

    own = _own_server(payload)
    walk: list[JobStep] = []
    for base in steps.REVERSAL_ORDER:
        if own and base == "ss.sign_key_csr":
            # Step 4b is the HOSTED counterpart of Step 4. An own-server
            # member's SIGN key lives on its own token, in its own container,
            # on volumes retire_instruction() tells the operator to remove --
            # it does not outlive the un-join. Only a hosted member leaves a
            # key behind on somebody else's still-running Security Server,
            # which is the orphan docs/xroad-770-notes.md #11 found.
            continue
        if not steps.BY_ID[base].reverse:
            continue
        # Reversed within a base id too: the last grant made is the first
        # revoked. Irrelevant to X-Road (the grants are independent), but it
        # keeps "backwards" meaning one thing throughout the walk.
        walk += [s for s in reversed(sequence) if s.id.split(":")[0] == base and s.id in completed]

    run_hurl, cookie_jar_dir = _shared_cookie_jar(run_hurl)
    try:
        record["retire_started_at"] = _now()
        record["error"] = None
        record["retry_budget_left"] = RETRY_BUDGET
        record["reversal"] = []
        save(record)

        try:
            # The sessions this walk authenticates with are exactly the ones
            # the forward path never persisted (spec S5.4), re-established the
            # same way a resume does: by re-running the steps that provide
            # them. cs.init for the Central Server, ss.bringup_init for the
            # Security Server the member's client lives on.
            for step in sequence:
                if step.must_rerun:
                    _execute(
                        step, {**constants, **context, **session}, context, session,
                        pack_dir, run_hurl, None, record, retry_interval,
                    )

            for step in walk:
                variables = {**constants, **context, **session}
                base = step.id.split(":")[0]
                element = _reversal_probe(step, variables, pack_dir, run_hurl)
                if REVERSAL_ABSENT[base](step, element, variables):
                    record["reversal"].append({"step": step.id, "outcome": "already absent"})
                else:
                    if base == "ss.sign_key_csr":
                        # Never the id the forward run captured: it may predate
                        # a re-issued key, and on a shared host the wrong id is
                        # another agency's signing key
                        # (SS_SIGN_KEY_DELETE.hurl.tmpl). Re-derived from the
                        # probe that just ran, or the walk stops here.
                        key_id = _sign_key_id(step, _captures(element))
                        if key_id is None:
                            raise StepFailure(
                                step.id,
                                "cannot identify this member's SIGN key on the hosting server's token "
                                "(the probe did not answer). Refusing to delete a key by an id from the "
                                "job context: on a shared host that is another agency's signing key.",
                            )
                        variables[f"{step.tokens['CAP_P']}_sign_key_id"] = key_id
                    _execute_reverse(
                        step, steps.BY_ID[base].reverse, variables, pack_dir, run_hurl, record, retry_interval
                    )
                    record["reversal"].append({"step": step.id, "outcome": "reversed"})
                record["last_reversed_step"] = step.id
                save(record)
        except StepFailure as exc:
            record["error"] = {"step": exc.step_id, "message": scrub(exc.message, secrets)}
            save(record)
            return record

        record["state"] = "RETIRED"
        record["retired_at"] = _now()
        record["retire_instruction"] = retire_instruction(payload)
        save(record)
        return record
    finally:
        if cookie_jar_dir is not None:
            shutil.rmtree(cookie_jar_dir, ignore_errors=True)


def _reversal_probe(step: JobStep, variables: dict, pack_dir: pathlib.Path, run_hurl) -> dict:
    """The raw report element for this step's probe. Unlike _probe(), no
    verdict: REVERSAL_ABSENT's per-step interpreters need the element itself
    (three of the six read a captured body, and one reads the STATUS of a
    probe that deliberately failed its 200 assert). A probe that cannot run
    at all yields an element that every interpreter reads as "not gone" --
    attempting a reversal that was already done is safe
    (_reversal_succeeded), skipping one that was not is not."""
    generate, _ = _hurl_modules(pack_dir)
    try:
        return run_hurl(f"{step.id}#reverse-probe", generate.render(step.probe, **step.tokens), variables)
    except Exception:  # noqa: BLE001
        return {"success": False, "entries": []}


def _execute_reverse(
    step: JobStep,
    template: str,
    variables: dict,
    pack_dir: pathlib.Path,
    run_hurl,
    record: dict,
    retry_interval: float,
) -> None:
    """One reversal call, on the same one-budget-per-run terms _execute uses.
    Kept separate rather than folded into _execute: the success predicate is
    different (404/409-as-already-gone, minus the one retryable 409), there
    are no captures to thread, and a reversal's `requires` are not declared in
    the registry -- hurl/steps.py's `reverse` field is a template filename and
    says so, so the check below reads the rendered file instead."""
    generate, _ = _hurl_modules(pack_dir)
    body = generate.render(template, **step.tokens)
    missing = sorted({name for name in _HURL_VAR_RE.findall(body) if name not in variables})
    if missing:
        raise StepFailure(step.id, f"job context is missing Hurl variable(s) this reversal needs: {missing}")

    budget = record.get("retry_budget_left", RETRY_BUDGET)
    while True:
        try:
            element = run_hurl(f"{step.id}#reverse", body, variables)
        except Exception as exc:  # noqa: BLE001 -- same reasoning as _execute's
            raise StepFailure(step.id, f"could not run this reversal: {type(exc).__name__}: {exc}") from exc
        if _reversal_succeeded(element):
            record["retry_budget_left"] = budget
            return
        failure = _failure_text(element)
        if budget <= 0:
            record["retry_budget_left"] = 0
            raise StepFailure(
                step.id,
                f"reversal exhausted the run's retry budget ({RETRY_BUDGET} attempts, "
                f"{RETRY_INTERVAL_SECONDS:.0f}s apart). Last observed:\n{failure}",
            )
        budget -= 1
        record["retry_budget_left"] = budget
        time.sleep(retry_interval)
