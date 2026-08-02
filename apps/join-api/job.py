"""apps/join-api/job.py -- the join job engine (join-b Task 4, design spec
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

Hosted joins only (spec S8 check 6 rejects anything else), so every step's
actor is "operator" regardless of hurl/steps.py's own `actor` default -- the
override join-a plan Task 3 Step 4 documented at build_hosted_client()'s call
sites, applied here rather than trusted from the registry.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
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
# (found in review, in the built image, 2026-08-02). An absolute path is one
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


def scrub(text: str, secrets: dict[str, str]) -> str:
    """Belt and braces for spec S5.4: no credential in a persisted error
    message. Hurl's own error output quotes the template source (`{{token_pin}}`,
    unexpanded -- verified), so this should never have anything to do; it
    costs one pass over a short string and removes the need to trust that."""
    for value in secrets.values():
        if value:
            text = text.replace(value, "***")
    return text


# -- the sequence -------------------------------------------------------------


def _host(pack_dir: pathlib.Path, payload: JoinPayload) -> dict:
    """The existing member whose Security Server hosts this join. Resolved
    from disk the same way validate.py's hosting check (S8 check 6) does, so
    an approved request cannot resolve to a different host than the one that
    was validated."""
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
    core = yaml.safe_load((pack_dir / "configs" / "x-road-bus" / "2.1.yaml").read_text())
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
    # shared name (found in review, 2026-08-02). SPECVAR is a token chosen in
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
    """The steps a hosted_on join runs, in order, with every @TOKEN@ resolved.

    Steps 1-5 are the job's own re-establishment of what cold deploy captures
    once and keeps in Hurl scope (see the module docstring); 6-8 are
    build_hosted_client()'s sequence verbatim -- client-add, then the SIGN key,
    then registration, the order hurl/steps.py's comment says is load-bearing;
    9-10 are build_service_file()'s.
    """
    generate, steps = _hurl_modules(pack_dir)
    host = _host(pack_dir, payload)
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
                # Not step.actor: under hosted_on every step is the operator's
                # (join-a plan Task 3 Step 4) -- there is no member-side
                # infrastructure in this path at all.
                actor="operator",
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
    # The HOST's own identity, not the joining member's: this step's
    # /initialization body sets owner_member_code/security_server_code, and
    # the host is already initialised with its own. Re-run only to obtain a
    # session on it (the anchor upload and initialization 409 or no-op).
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
    add(
        "ss.client_add",
        dict(
            SS=host["dns_name"],
            MEMBER_CODE=code,
            SUBSYSTEM=payload.subsystem,
            CONNECTION_TYPE="HTTP",
            HOSTVAR=host_var,
            SESS_P=sess_p,
            CAP_P=cap_p,
        ),
    )
    # SS_CODE is the HOST's, not this member's nominal one -- the cert lives
    # on the host's token and naming a server that was never brought up would
    # be a lie in the cert (build_hosted_client()'s docstring).
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
                    # Cold deploy names MoEYS here to explain acceptance/2.6's
                    # negative check. A join has no such counterpart, and
                    # naming a canonical member in a joined member's rendered
                    # comment would be misleading.
                    NEGATIVE="(none -- see acceptance/2.7.md)",
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
        return None
    host = next((s for s in topology["security_servers"] if s["host"] == entry["hosted_on"]), None)
    proxy_port = (host or {}).get("proxy_port", 8080)
    # ponytail: the service ROOT path, not an operation from the joining
    # member's OpenAPI document. What this call has to prove is S2.4's
    # "registry-perfect but dead" case -- that a request actually traverses
    # the consumer's proxy, the provider's proxy and reaches the backend --
    # and any non-X-Road response proves that, including a backend 404. An
    # operation-specific path needs the spec re-fetched here and its path
    # parameters invented; add that when acceptance/2.7.md needs a specific
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


def _default_run_hurl(label: str, body: str, variables: dict[str, str]) -> dict:
    """Run one rendered step and return its Hurl JSON report element.

    --report-json APPENDS a new array element to <dir>/report.json on every
    invocation that names the same directory (verified against the committed
    out/hurl-report/report.json, which carries one element per historical
    deploy), so every call gets a fresh directory that is deleted again here.
    --insecure mirrors run-linkup.sh: the Test CA's certificates are
    self-signed. Hurl's own --retry is not used -- the retry budget is the
    run's, not the step's (spec S5.5), and lives in run() below.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"kp2-join-{label.replace('/', '_').replace(':', '_')}-"))
    try:
        step_file = tmp / "step.hurl"
        step_file.write_text(body)
        report_dir = tmp / "report"
        args = [HURL_BIN, "--insecure"]
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
    imported from apps/console: this container does not mount that app (Task
    1 copied its request-boundary guard for the same reason).

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
# state and the executor decides. Only two of the eight probed steps are
# reachable from a hosted join, so only two interpreters exist -- a probed
# step with no interpreter here simply re-runs, which is what the 409-safety
# default already covers.


def _probe_client_registered(step: JobStep, captures: dict) -> bool:
    return captures.get(f"{step.tokens['CAP_P']}_client_status") == "REGISTERED"


def _probe_sign_key_exists(step: JobStep, captures: dict) -> bool:
    """PROBE_SS_SIGN_KEY captures the whole token body: a shared host's token
    carries one identically-labelled "Sign key" per hosted member, so this
    correlates by the certificate's owner_id, never by label (confirmed live
    on ss-plr, join-a plan Task 5 Step 4)."""
    raw = captures.get(f"{step.tokens['CAP_P']}_token")
    if not raw:
        return False
    try:
        token = json.loads(raw)
    except ValueError:
        return False
    suffix = f":{step.tokens['MEMBER_CODE']}"
    for key in token.get("keys", []):
        if key.get("usage") != "SIGNING":
            continue
        for cert in key.get("certificates", []):
            if str(cert.get("owner_id", "")).endswith(suffix):
                return True
    return False


PROBE_INTERPRETERS: dict[str, Callable[[JobStep, dict], bool]] = {
    "ss.client_register": _probe_client_registered,
    "ss.sign_key_csr": _probe_sign_key_exists,
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run(
    record: dict,
    pack_dir: pathlib.Path,
    *,
    secrets: dict[str, str],
    save: Callable[[dict], None],
    run_hurl: Callable[[str, str, dict], dict] = _default_run_hurl,
    r1_call: Callable[[str, str], tuple[bool, str]] = _default_r1_call,
    retry_interval: float = RETRY_INTERVAL_SECONDS,
) -> dict:
    """Drive `record` (an out/join/<id>.json request) to ACTIVE or FAILED,
    persisting after every step via `save`. Mutates and returns the record.

    Resume: `record["last_completed_step"]` names the last step known to have
    completed; execution starts after it, re-injecting every persisted
    capture. Steps before it re-run only if they provide a session token
    (JobStep.must_rerun) -- nothing else is re-run, which is the guarantee
    Task 4 Step 6's resume test asserts.
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
            # NEXT resume re-run steps this one deliberately skipped (found
            # in review, 2026-08-02).
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
    budget = record.get("retry_budget_left", RETRY_BUDGET)
    while True:
        if step.kind == "r1":
            ok, detail = r1_call(step.r1["url"], step.r1["client_header"])
            if ok:
                record["verified"] = True
                record["verified_by"] = detail
                record["retry_budget_left"] = budget
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
                # into app.py's blanket handler. Found in review: a
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
            record["retry_budget_left"] = 0
            if step.kind == "r1":
                # Not a job failure: spec S4 says a member that registered and
                # published but whose reachability call has not passed is
                # ACTIVE with verified: false, one fact about the member
                # rather than a place in the lifecycle.
                record["verified"] = False
                record["verified_by"] = failure
                return
            raise StepFailure(
                step.id,
                f"exhausted the run's retry budget ({RETRY_BUDGET} attempts, "
                f"{RETRY_INTERVAL_SECONDS:.0f}s apart). Last observed:\n{failure}",
            )
        budget -= 1
        record["retry_budget_left"] = budget
        time.sleep(retry_interval)


def _probe(step: JobStep, variables: dict, pack_dir: pathlib.Path, run_hurl) -> bool:
    """Has this step already happened? Only asked on resume, and only for the
    steps join-a plan Task 5 classified as ambiguous (spec S5.3): a probe
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
