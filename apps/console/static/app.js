// KP2 demonstration console. Vanilla JS, no build step, no framework --
// see the plan's Global Constraints (air-gapped demo machine).

const STAGGER_MS = 220;
const HEARTBEAT_INTERVAL_MS = 30_000;
const ACL_POLL_INTERVAL_MS = 1_000;
const ACL_POLL_MAX_ATTEMPTS = 10; // ~10s -- the proxy's own authorization
// cache lags the admin API's read (server-conf-cache-period); tuned to 5s
// for this demo stack (xroad-demo-local.ini, docs/xroad-770-notes.md §6,
// production default is 60s) and measured live at 4.5-5.6s, so 10s is
// comfortable headroom rather than the old ~40s budget sized for the
// untuned default.

let lastNin = null;
let defaultNin = null;
let counterFormRun = 0; // bumped on every runExchange -- lets an in-flight
// reveal loop notice a newer run started (e.g. the presenter clicked a
// different learner mid-animation) and stop touching shared DOM/tally.

function $(sel) { return document.querySelector(sel); }
function $all(sel) { return Array.from(document.querySelectorAll(sel)); }

// Every value below traces back to a federated exchange call (a provider's
// response body) or an X-Road fault body -- never assume it's HTML-safe just
// because today's mocks are well-behaved.
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(value) {
  return String(value).replace(/[&<>"']/g, c => HTML_ESCAPES[c]);
}

// Request-boundary plan (S13): every endpoint below requires this header --
// a cross-origin form or <img> can't set it (a custom header on a
// cross-origin fetch would need a CORS preflight this server never answers
// with permission). Sent on every call, GET included, so the read endpoints
// that trigger real bus calls (the two /api/exchange/* routes) are covered
// too, not just the three that write.
async function api(path, opts) {
  const merged = { ...opts, headers: { ...(opts && opts.headers), "X-KP2-Console": "1" } };
  const resp = await fetch(path, merged);
  return resp.json();
}

// ---------------------------------------------------------------- tabs ----

function switchToTab(name) {
  $all(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $all(".tab-panel").forEach(p => p.classList.toggle("active", p.id === `tab-${name}`));
  // Join tab only polls while it's the one on screen (below) -- no reason
  // to hit join-api every few seconds from every other tab.
  if (name === "join") startJoinPolling(); else stopJoinPolling();
}

function initTabs() {
  $all(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => switchToTab(btn.dataset.tab));
  });
  $all(".forward-link").forEach(btn => {
    btn.addEventListener("click", () => switchToTab(btn.dataset.tab));
  });
}

// ------------------------------------------------------------ heartbeat ----

function startHeartbeat() {
  const beat = () => api("/api/heartbeat", { method: "POST" });
  beat();
  setInterval(beat, HEARTBEAT_INTERVAL_MS);
}

// ------------------------------------------------------- journal banner ----

// One read of /api/acl.dirty drives both the top banner (only shown while
// dirty) and the context bar's always-visible "Permissions: ..." badge --
// they track the same fact, so one poll rather than two (UX plan Task 9).
async function refreshJournalBanner() {
  const acl = await api("/api/acl");
  $("#journal-banner").classList.toggle("dirty", acl.dirty);
  $("#context-permissions").textContent = `Permissions: ${acl.dirty ? "modified" : "unmodified"}`;
  $("#context-permissions").classList.toggle("denied-label", acl.dirty);
  return acl;
}

async function resetEverything() {
  const resp = await api("/api/reset", { method: "POST" });
  await refreshJournalBanner();
  await refreshPermissionsToggle();
  if (!resp.ok) alert("Reset could not verify the restored ACL -- see server logs.\n" + JSON.stringify(resp));
  return resp;
}

function initJournalBanner() {
  $("#journal-reset-btn").addEventListener("click", resetEverything);
  refreshJournalBanner();
  setInterval(refreshJournalBanner, HEARTBEAT_INTERVAL_MS);
}

// --------------------------------------------------------------- topology ----

let TOPOLOGY = null; // cached from the one /api/topology fetch on load --
// looked up by member/host code, never re-derived (Global Constraint: no
// fourth copy of the topology).

async function loadTopologyBadge() {
  TOPOLOGY = await api("/api/topology");
  const total = TOPOLOGY.security_servers.length;
  const up = TOPOLOGY.security_servers.filter(s => s.reachable).length;
  $("#context-health").textContent = `Federation: ${up}/${total} reachable`;
  $("#context-health").classList.toggle("denied-label", up < total);
}

// -- persistent context bar: current learner, federation health,
// journal state, reset -- all visible regardless of which tab is open
// (UX plan Task 9, Step 2). Permissions state comes from
// refreshJournalBanner()'s own poll of /api/acl.dirty, not a second one.
function updateContextLearner(nin, given, family) {
  $("#context-learner").textContent = (given && family)
    ? `Learner: NIN ${nin} — ${given} ${family}`
    : `Learner: NIN ${nin}`;
}

function initContextBar() {
  $("#context-reset-btn").addEventListener("click", resetEverything);
}

function subsystemFor(memberCode) {
  return TOPOLOGY?.subsystems.find(s => s.member_code === memberCode);
}

function hostProxyPortFor(host) {
  return TOPOLOGY?.security_servers.find(s => s.host === host)?.host_proxy_port;
}

// Rewrites an in-network call URL (http://ss-pnea:8080/r1/...) to the
// host-mapped equivalent (http://localhost:2080/r1/...) a presenter can
// run outside the linkup network -- same path, same query, real data.
function hostMappedUrl(internalUrl) {
  const m = internalUrl.match(/^https?:\/\/([^:/]+):\d+(\/.*)$/);
  if (!m) return internalUrl;
  const port = hostProxyPortFor(m[1]);
  return port ? `http://localhost:${port}${m[2]}` : internalUrl;
}

// ---------------------------------------------------------------- counter ----

async function loadLearners() {
  const { learners } = await api("/api/learners");
  const container = $("#learner-chips");
  container.innerHTML = "";
  learners.forEach((learner, i) => {
    const chip = document.createElement("button");
    chip.className = "chip" + (learner.case === "no enrolment record" ? " clean-404" : "");
    chip.textContent = `${learner.nin} · ${learner.case}`;
    chip.addEventListener("click", () => runExchange(learner.nin));
    container.appendChild(chip);
    if (i === 0) defaultNin = learner.nin;
  });
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

let sessionQuestionsAvoided = 0;
function bumpSessionTally(prefillTotal) {
  sessionQuestionsAvoided += prefillTotal;
  $("#tally-badge").textContent = `questions avoided this session: ${sessionQuestionsAvoided}`;
}

async function runExchange(nin) {
  lastNin = nin;
  const runToken = ++counterFormRun;
  const data = await api(`/api/exchange/${nin}`);
  if (runToken !== counterFormRun) return; // superseded while the fetch was in flight
  await renderInspector(data);
  await renderCounterForm(nin, data, runToken);
}

const BEFORE_HOLD_MS = 800;
const MIN_ASKING_MS = 400; // floor under the real elapsed_ms so "asking..."
// is always visible on camera, even when the real call was very fast

function sourceClassFor(info) {
  return info.member_code === "citizen" ? "citizen"
    : info.member_code === "PNIA" ? "PNIA"
    : info.member_code === "PLR" ? "PLR" : "citizen";
}

// Provenance is never colour-alone (UX plan Task 10, Step 1): each source
// also gets its own shape, so the distinction survives colour-blindness or
// a black-and-white printout of a slide.
const SOURCE_SHAPE = { citizen: "▲", PNIA: "●", PLR: "■" }; // ▲ ● ■
function sourceShapeFor(info) {
  return SOURCE_SHAPE[sourceClassFor(info)];
}

function revealField(row, info) {
  const valueEl = row.querySelector(".field-value");
  valueEl.textContent = info.value ?? "not available";
  valueEl.classList.toggle("empty", info.value == null);
  row.querySelector(".source-badge").style.visibility = "visible";
  row.classList.add("shown");
}

// Three beats, not one reveal: the empty form ("this is ten questions"),
// then the one question (NIN) landing, then each provider answering in
// turn -- "asking PNIA..." -> "PNIA answered in 227ms" -> its fields land
// -- so the audience sees two systems answer visibly in sequence, not one
// block of values that could have been hard-coded (UX plan Tasks 2 & 3).
async function renderCounterForm(nin, data, runToken) {
  $("#counter-form-card").style.display = "block";
  $("#counter-nin-line").textContent = `NIN ${nin}`;
  $("#counter-learner-name").textContent = "";
  $("#break-proof-controls").style.display = "flex";
  updateBreakProofButtons(data);
  updateContextLearner(nin, null, null);

  const fieldsEl = $("#counter-fields");
  fieldsEl.innerHTML = "";
  $("#receipts-panel").style.display = "none";
  $("#receipts-panel").innerHTML = "";
  $("#receipts-toggle-btn").style.display = "none";

  const entries = Object.entries(data.credential_application);
  const askedCount = entries.filter(([, info]) => info.member_code === "citizen").length;
  const prefillTotal = entries.length - askedCount;
  $("#progress-line").textContent = "Without the bus, this is ten questions.";

  // Group in first-seen order (citizen field(s) first, then each call's
  // fields in the order truth.py built them -- never alphabetical).
  const groupOrder = [];
  const groups = {};
  entries.forEach(([name, info]) => {
    if (!groups[info.group]) { groups[info.group] = []; groupOrder.push(info.group); }
    groups[info.group].push([name, info]);
  });

  // citizen section renders plain, with all its rows blank-until-asked
  const citizenRows = []; // [[name, info, rowEl]]
  const providerSections = []; // [[group, memberCode, statusEl, [[name, info, rowEl], ...]]]

  groupOrder.forEach(group => {
    const section = document.createElement("div");
    section.className = "form-group";
    const fields = groups[group];
    let statusEl = null;
    if (group !== "citizen") {
      const memberCode = fields[0][1].member_code;
      const header = document.createElement("div");
      header.className = "provider-header";
      statusEl = document.createElement("span");
      statusEl.className = "provider-status";
      header.appendChild(statusEl);
      section.appendChild(header);
    }
    const sectionRows = [];
    fields.forEach(([name, info]) => {
      const row = document.createElement("div");
      row.className = "form-field shown"; // visible immediately, blank
      const badgeText = info.source === "citizen" ? "you told us" : info.source;
      row.innerHTML = `
        <span class="field-name">${esc(info.label)}</span>
        <span class="field-value empty">&mdash;</span>
        <span class="source-badge ${sourceClassFor(info)}" style="visibility:hidden">${sourceShapeFor(info)} ${esc(badgeText)}</span>
      `;
      section.appendChild(row);
      sectionRows.push([name, info, row]);
    });
    fieldsEl.appendChild(section);
    if (group === "citizen") citizenRows.push(...sectionRows);
    else providerSections.push([group, fields[0][1].member_code, statusEl, sectionRows]);
  });

  await sleep(BEFORE_HOLD_MS);
  if (runToken !== counterFormRun) return; // superseded during the hold

  // ask the one question
  for (const [, info, row] of citizenRows) revealField(row, info);
  $("#progress-line").textContent = `asked ${askedCount} · pre-filled 0 / ${prefillTotal}`;
  await sleep(STAGGER_MS * 2);
  if (runToken !== counterFormRun) return;

  // then each provider answers in turn, visibly -- or is denied, visibly
  // (UX plan Task 4: withdrawing one provider's permission must render
  // correctly here regardless of how the denial came about, not just via
  // the break-proof buttons below).
  let filled = 0;
  let anyDenied = false;
  for (const [, memberCode, statusEl, sectionRows] of providerSections) {
    const call = data.calls.find(c => c.service.split("/")[2] === memberCode);
    const memberName = subsystemFor(memberCode)?.member_name || memberCode;
    const hostedOn = subsystemFor(memberCode)?.hosted_on || "";

    statusEl.textContent = `Asking ${memberName}…`;
    const askingDelay = call ? Math.max(call.elapsed_ms, MIN_ASKING_MS) : MIN_ASKING_MS;
    await sleep(askingDelay);
    if (runToken !== counterFormRun) return;

    if (call?.denied) {
      anyDenied = true;
      statusEl.textContent = `${memberName} denied: ${call.fault_type}`;
      statusEl.closest(".form-group").classList.add("denied-group");
      for (const [, , row] of sectionRows) {
        row.classList.add("denied-row");
        row.querySelector(".field-value").textContent = "denied";
      }
      continue; // never counted as filled -- it wasn't
    }

    statusEl.textContent = call
      ? `${memberName} answered in ${call.elapsed_ms.toFixed(0)}ms · served by ${hostedOn}`
      : `${memberName} did not answer`;

    for (const [name, info, row] of sectionRows) {
      await sleep(STAGGER_MS);
      if (runToken !== counterFormRun) return;
      revealField(row, info);
      filled += 1;
      $("#progress-line").textContent = `asked ${askedCount} · pre-filled ${filled} / ${prefillTotal}`;
      if (name === "family_name") {
        const given = data.credential_application.given_name?.value;
        const family = info.value;
        if (given && family) {
          $("#counter-learner-name").textContent = ` — ${given} ${family}`;
          updateContextLearner(nin, given, family);
        }
      }
    }
  }

  $("#break-proof-caption").textContent = anyDenied
    ? "The same form, one source withdrawn — nothing here was hard-coded."
    : "";
  bumpSessionTally(filled);
  renderReceipts(data);
  $("#receipts-toggle-btn").style.display = "inline-block";
  $("#counter-forward-btn").style.display = "inline-block";
}

// -- receipts: the raw provider responses, verbatim, plus a curl command
// an architect can run on the host to get the same answer outside the
// console entirely (UX plan Task 3, Steps 3-4).
function renderReceipts(data) {
  const panel = $("#receipts-panel");
  panel.innerHTML = "";
  data.calls.forEach(call => {
    const card = document.createElement("div");
    card.className = "receipt-card";
    const curlCmd = `curl -s -H "X-Road-Client: ${data.client_header}" "${hostMappedUrl(call.url)}"`;
    card.innerHTML = `
      <h4>${esc(call.service)}</h4>
      <pre class="receipt-body">${esc(JSON.stringify(call.body, null, 2))}</pre>
      <button class="copy-curl-btn">Copy as curl</button>
    `;
    card.querySelector(".copy-curl-btn").addEventListener("click", async (e) => {
      await navigator.clipboard.writeText(curlCmd);
      const btn = e.target;
      const original = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = original; }, 1500);
    });
    panel.appendChild(card);
  });
}

function initReceiptsToggle() {
  $("#receipts-toggle-btn").addEventListener("click", () => {
    const panel = $("#receipts-panel");
    const showing = panel.style.display === "block";
    panel.style.display = showing ? "none" : "block";
    $("#receipts-toggle-btn").textContent = showing ? "Show the receipts" : "Hide the receipts";
  });
}

// -- break-one-source proof: the existing ACL write is the trust device --
// revoking identity-api's grant makes exactly the PNIA half of the form
// fail while PLR still fills, with no new write path (UX plan Task 4).

function updateBreakProofButtons(data) {
  const identityCall = data.calls.find(c => c.service.split("/")[2] === "PNIA");
  const denied = identityCall?.denied === true;
  $("#break-proof-btn").style.display = denied ? "none" : "inline-block";
  $("#restore-proof-btn").style.display = denied ? "inline-block" : "none";
}

async function pollForIdentityDenied(nin, wantDenied, onProgress) {
  for (let attempt = 1; attempt <= ACL_POLL_MAX_ATTEMPTS; attempt++) {
    const data = await api(`/api/exchange/${nin}`);
    const call = data.calls.find(c => c.service.split("/")[2] === "PNIA");
    if ((call?.denied === true) === wantDenied) return true;
    onProgress(attempt);
    await sleep(ACL_POLL_INTERVAL_MS);
  }
  return false;
}

async function runBreakOneSourceProof() {
  const nin = lastNin;
  if (!nin) return;
  const btn = $("#break-proof-btn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Revoking…";
  await api("/api/acl/revoke", { method: "POST" });
  await refreshJournalBanner();
  const ok = await pollForIdentityDenied(nin, true, attempt => {
    btn.textContent = `Waiting for the denial to take effect (${attempt}/${ACL_POLL_MAX_ATTEMPTS})…`;
  });
  btn.disabled = false;
  btn.textContent = original;
  if (!ok) {
    $("#break-proof-caption").textContent = "Did not observe the denial within the poll window -- try again.";
    return;
  }
  await runExchange(nin);
}

async function runRestoreProof() {
  const nin = lastNin;
  if (!nin) return;
  const btn = $("#restore-proof-btn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Restoring…";
  await api("/api/acl/grant", { method: "POST" });
  await refreshJournalBanner();
  const ok = await pollForIdentityDenied(nin, false, attempt => {
    btn.textContent = `Waiting for the restore to take effect (${attempt}/${ACL_POLL_MAX_ATTEMPTS})…`;
  });
  btn.disabled = false;
  btn.textContent = original;
  if (!ok) {
    $("#break-proof-caption").textContent = "Did not observe the restore within the poll window -- try again.";
    return;
  }
  await runExchange(nin);
}

function initBreakProof() {
  $("#break-proof-btn").addEventListener("click", runBreakOneSourceProof);
  $("#restore-proof-btn").addEventListener("click", runRestoreProof);
}

// -------------------------------------------------------------- inspector ----

// Single column, ordered the way EIF is taught -- legal, organisational,
// semantic, technical -- never the 1,4,3,2 grid order the layer_* keys
// happen to sort into. Each pane keeps its 2.6.yaml sentence as a heading
// and gains live evidence beneath it (UX plan Task 6).
async function renderInspector(data) {
  $("#inspector-empty").style.display = "none";
  const contextEl = $("#inspector-context");
  contextEl.style.display = "block";
  contextEl.textContent = `Showing the exchange run for NIN ${lastNin} at ${new Date().toLocaleTimeString()}.`;
  $("#inspector-forward-btn").style.display = "inline-block";

  const grid = $("#inspector-layers");
  grid.style.display = "grid";
  grid.innerHTML = "";

  const acl = await api("/api/acl");

  const panes = [
    { key: "legal", title: "Legal" },
    { key: "organisational", title: "Organisational" },
    { key: "semantic", title: "Semantic" },
    { key: "technical", title: "Technical" },
  ];

  panes.forEach(pane => {
    const el = document.createElement("div");
    el.className = "layer-pane";
    const sentence = data.layers[pane.key] || "(not stated for this call)";
    el.innerHTML = `<h3>${esc(pane.title)}</h3><p class="sentence">${esc(sentence)}</p>`;

    if (pane.key === "legal") {
      // Purpose limitation, proved by absence (UX plan Task 5): PNIA's own
      // record carries more than the credential purpose needs -- these
      // names came straight from the mock, off the bus, never their values.
      const sentFields = Object.entries(data.credential_application)
        .filter(([, info]) => info.member_code === "PNIA")
        .map(([name]) => name);
      const held = data.identity_held_fields || [];
      const detail = document.createElement("div");
      detail.className = "call-detail";
      detail.textContent =
        `PNIA sends: ${sentFields.join(", ")}\n` +
        `PNIA holds but withholds: ${held.join(", ") || "(none)"}`;
      el.appendChild(detail);
    } else if (pane.key === "organisational") {
      const detail = document.createElement("div");
      detail.className = "call-detail";
      detail.textContent = Object.entries(acl.services)
        .filter(([code]) => code === "identity-api" || code === "enrolment-api")
        .map(([code, info]) =>
          `${code}: configured=[${info.configured.join(", ")}]  live=[${info.live.join(", ")}]`)
        .join("\n");
      el.appendChild(detail);
    } else if (pane.key === "semantic") {
      const detail = document.createElement("div");
      detail.className = "call-detail";
      detail.textContent = Object.entries(data.semantic_fields || {})
        .map(([member, fields]) => {
          const withValues = fields.map(f => `${f}=${data.credential_application[f]?.value ?? "not available"}`);
          return `${member} semantic.fields: ${withValues.join(", ")}`;
        })
        .join("\n");
      el.appendChild(detail);
    } else if (pane.key === "technical") {
      data.calls.forEach(call => {
        const memberCode = call.service.split("/")[2];
        const servedBy = subsystemFor(memberCode)?.hosted_on || "?";
        const detail = document.createElement("div");
        detail.className = "call-detail";
        detail.textContent =
          `${call.service}\n${call.status_code ?? "ERR"} in ${call.elapsed_ms.toFixed(0)}ms, served by ${servedBy}\n${call.url}`;
        el.appendChild(detail);
      });
    }

    grid.appendChild(el);
  });
}

// ------------------------------------------------------------ permissions ----
// Two callers, one service, opposite outcomes -- that is the entire lesson
// (UX plan Task 7). enrolment-api is deliberately absent from this tab: it
// would just be one more inert row, and the reset path's own verification
// (journal.py) already checks the mutable/untouched-service asymmetry
// without an audience needing to see it. Design decision 4 (why only
// identity-api is writable here) lives in app.py's comment, not on this
// page. (pemis-api no longer exists -- MoEYS was retired in Wave 3 Task 1.)

function renderPermResult(resultEl, call) {
  if (call.denied) {
    resultEl.className = "result-box denied";
    resultEl.innerHTML = `<strong>Denied.</strong><div class="fault">${esc(JSON.stringify(call.body))}</div>`;
    return false;
  }
  if (call.status_code === 200) {
    resultEl.className = "result-box allowed";
    resultEl.innerHTML = `<strong>Allowed.</strong> Resolved in ${call.elapsed_ms.toFixed(0)}ms.`;
    return true;
  }
  resultEl.className = "result-box";
  resultEl.textContent = call.error || `Unexpected status ${call.status_code}`;
  return false;
}

async function askAsPnea() {
  const nin = lastNin || defaultNin;
  if (!nin) return;
  const resultEl = $("#pnea-result");
  resultEl.className = "result-box";
  resultEl.textContent = "Asking…";
  const data = await api(`/api/exchange/${nin}`);
  const call = data.calls.find(c => c.service.includes("identity-api"));
  const allowed = renderPermResult(resultEl, call);
  const statusEl = $("#pnea-status");
  statusEl.textContent = allowed ? "✓ admitted" : "✕ not admitted"; // shape, not colour alone
  statusEl.classList.toggle("denied-label", !allowed);
}

async function askAsPlr() {
  const nin = lastNin || defaultNin;
  if (!nin) return;
  const resultEl = $("#plr-result");
  resultEl.className = "result-box";
  resultEl.textContent = "Asking…";
  const data = await api(`/api/exchange/${nin}/negative`);
  renderPermResult(resultEl, data.calls[0]);
}

async function refreshPermissionsToggle() {
  const acl = await api("/api/acl");
  const granted = acl.services["identity-api"].live.length > 0;
  $("#permissions-revoke-btn").style.display = granted ? "inline-block" : "none";
  $("#permissions-restore-btn").style.display = granted ? "none" : "inline-block";
}

async function togglePneaAccess(action, btn) {
  const original = btn.textContent;
  btn.disabled = true;
  await api(`/api/acl/${action}`, { method: "POST" });
  await refreshJournalBanner();
  await refreshPermissionsToggle(); // admin-API state -- correct immediately
  const nin = lastNin || defaultNin;
  const wantDenied = action === "revoke";
  const ok = nin && await pollForIdentityDenied(nin, wantDenied, attempt => {
    btn.textContent = `Waiting for the change to take effect (${attempt}/${ACL_POLL_MAX_ATTEMPTS})…`;
  });
  btn.disabled = false;
  btn.textContent = original;
  if (!ok) {
    $("#permissions-caption").textContent = "Did not observe the change within the poll window -- try again.";
    return;
  }
  $("#permissions-caption").textContent = "";
  await askAsPnea(); // re-run against the proxy now that it's confirmed
}

async function resetPermissions() {
  const resp = await api("/api/reset", { method: "POST" });
  await refreshJournalBanner();
  await refreshPermissionsToggle();
  $("#permissions-reset-caption").textContent = resp.ok
    ? "Reset complete -- ACLs match their configured state."
    : "Reset could not verify the restored ACL -- see server logs.";
}

function initPermissions() {
  $("#ask-as-pnea-btn").addEventListener("click", askAsPnea);
  $("#ask-as-plr-btn").addEventListener("click", askAsPlr);
  $("#permissions-revoke-btn").addEventListener("click", e => togglePneaAccess("revoke", e.target));
  $("#permissions-restore-btn").addEventListener("click", e => togglePneaAccess("grant", e.target));
  $("#permissions-reset-btn").addEventListener("click", resetPermissions);
  refreshPermissionsToggle();
}

// -------------------------------------------------------------------- join ----
// Fourth tab: the join-api pending queue -- config diff, approve/reject,
// live progress as a step list coloured by actor, failed jobs with resume,
// the live-but-uncommitted warning, and any requested_access: follow-ups a
// join left open (UX plan: design spec S2.7). Every field below traces back
// to a join payload, which is attacker-supplied by construction (spec's own
// framing) -- esc() at every call site, same discipline as the counter tab's
// federated field values.
const JOIN_POLL_INTERVAL_MS = 3_000;
let joinPollTimer = null;

function startJoinPolling() {
  if (joinPollTimer) return;
  refreshJoinQueue();
  joinPollTimer = setInterval(refreshJoinQueue, JOIN_POLL_INTERVAL_MS);
}

function stopJoinPolling() {
  if (joinPollTimer) clearInterval(joinPollTimer);
  joinPollTimer = null;
}

// Steps come from join-api's own job.py sequence (id + actor + kind), never
// hand-listed here -- a hosted join is uniformly "operator", and an
// own-server join (Plan C) has a run of actor: member steps in the middle,
// which this renders from the same field with no console change beyond the
// styling below.
function renderJoinSteps(record) {
  const steps = record.steps;
  if (!steps || !steps.length) return "";
  const lastIdx = steps.findIndex(s => s.id === record.last_completed_step);
  const failedStep = record.error && record.error.step;
  const rows = steps.map((step, i) => {
    let status = i <= lastIdx ? "done" : "pending";
    // BLOCKED marks the same step RUNNING would: the next one, which is by
    // construction the actor: member step the request is waiting on.
    if ((record.state === "RUNNING" || record.state === "BLOCKED") && i === lastIdx + 1) status = "current";
    if (failedStep && step.id === failedStep) status = "failed";
    return `<li class="join-step ${status} actor-${esc(step.actor)}">`
      + `<span class="join-step-actor">${esc(step.actor)}</span>`
      + `<span class="join-step-id">${esc(step.id)}</span>`
      + `</li>`;
  });
  return `<ol class="join-step-list">${rows.join("")}</ol>`;
}

function renderJoinRequest(record) {
  const payload = record.payload || {};
  const state = record.state || "?";
  const el = document.createElement("div");
  el.className = "join-request";

  let html = `<div class="join-request-header">`
    + `<strong>${esc(payload.code || "?")}</strong> ${esc(payload.name || "")} `
    + `<span class="join-state join-state-${esc(state.toLowerCase())}">${esc(state)}</span>`
    + `</div>`;
  html += `<div class="join-request-meta">submitted ${esc(record.submitted_at || "?")}`
    + `${record.queued ? " &middot; queued behind another job" : ""}</div>`;

  if (payload.requested_access && payload.requested_access.length) {
    html += `<div class="join-followup">also requests access to: `
      + `${payload.requested_access.map(esc).join(", ")} `
      + `&mdash; the named provider must grant this; this API never does.</div>`;
  }

  if (state === "SUBMITTED") {
    html += `<pre class="join-diff">${esc(record.diff || "")}</pre>`;
    html += `<div class="join-actions">`
      + `<input type="text" class="join-decision-reference" data-id="${esc(record.id)}" `
      + `placeholder="Decision reference (minute ID + date) [confirm: cite the Steering Committee minute reference and date]">`
      + `<button class="action join-approve-btn" data-id="${esc(record.id)}">Approve</button>`
      + `<button class="secondary-action join-reject-toggle-btn" data-id="${esc(record.id)}">Reject&hellip;</button>`
      + `</div>`
      + `<div class="join-reject-box" id="join-reject-${esc(record.id)}" style="display:none">`
      + `<textarea class="join-reject-reason" placeholder="Reason (shown to the applicant)"></textarea>`
      + `<button class="action revoke join-reject-confirm-btn" data-id="${esc(record.id)}">Confirm reject</button>`
      + `</div>`;
  } else if (state === "REJECTED") {
    const r = record.rejection || {};
    html += `<div class="join-rejection">rejected (${esc(r.check || "?")}): ${esc(r.message || "")}</div>`;
  } else if (state === "APPROVED" || state === "RUNNING") {
    html += renderJoinSteps(record);
  } else if (state === "BLOCKED") {
    // A state whose exit condition is "a human runs a script" must name the
    // script. The key is derived here rather than read off the record: it is
    // the same code.toLowerCase() apps/join-api/validate.py's key_derivation
    // check already constrains to [a-z0-9]+, and nothing else in the record
    // is a better source for it.
    const key = (payload.code || "").toLowerCase();
    const blocked = record.blocked || {};
    html += `<div class="join-blocked">Waiting on the joining member's own Security Server`
      + `${blocked.server ? ` <code>${esc(blocked.server)}</code>` : ""} &mdash; `
      + `this API cannot stand it up, and in a real federation could not. `
      + `Run this on the Docker host, then Resume:`
      + `<pre class="join-blocked-command">scripts/join-agent.sh ${esc(key)}</pre></div>`;
    html += renderJoinSteps(record);
    html += `<div class="join-actions">`
      + `<button class="action join-resume-btn" data-id="${esc(record.id)}">Resume</button>`
      + `</div>`;
  } else if (state === "FAILED") {
    const e = record.error || {};
    html += `<div class="join-error">failed at <code>${esc(e.step || "?")}</code>: `
      + `<span class="join-error-message">${esc(e.message || "")}</span></div>`;
    html += renderJoinSteps(record);
    if (e.step === "config.write") {
      // apps/join-api/app.py's approve_request: the config was WRITTEN but
      // hurl/generate.py then rejected it -- resuming would re-run the
      // X-Road admin-API sequence against a config generate.py already
      // rejected once, using a stale topology.json. Resume cannot help here
      // (review finding, 2026-08-02); the working tree needs a human.
      html += `<div class="join-note">generate.py rejected this config after it was written -- `
        + `Resume cannot fix this. Check the working tree (configs/ and manifest.yaml) directly.</div>`;
    } else {
      html += `<div class="join-actions">`
        + `<button class="action join-resume-btn" data-id="${esc(record.id)}">Resume</button>`
        + `</div>`;
    }
  } else if (state === "ACTIVE") {
    html += renderJoinSteps(record);
    // A consume-only join never runs join.r1_verify (job.py's own
    // build_sequence), so `verified` stays unset -- job.py writes an
    // explanatory `note` instead (~job.py line 704), and the generic
    // "reachability check has not passed yet" line would be actively
    // misleading there (nothing is pending; there is nothing to verify).
    if (record.note) {
      html += `<div class="join-note">${esc(record.note)}</div>`;
    } else {
      html += record.verified
        ? `<div class="join-verified ok">verified: true &mdash; a real r1 call reached the backend</div>`
        : `<div class="join-verified pending">verified: false &mdash; the reachability check has not passed yet</div>`;
    }
    // uncommitted is bool | null (apps/join-api/app.py's _live_uncommitted):
    // null means the git check itself failed -- fails toward SHOWING a
    // warning, not hiding one (review finding, 2026-08-02: `if
    // (record.uncommitted)` alone treats null the same as false and would
    // silently drop this exact box on a git failure, the one case it most
    // needs to be seen). === true and === null both render a box; only
    // === false renders nothing.
    if (record.uncommitted === true) {
      html += `<div class="join-uncommitted-warning">Live but uncommitted &mdash; `
        + `configs/member-${esc((payload.code || "").toLowerCase())}/ and manifest.yaml are `
        + `active on this federation but not yet committed to git.</div>`;
    } else if (record.uncommitted === null) {
      html += `<div class="join-uncommitted-warning">Could not check whether `
        + `configs/member-${esc((payload.code || "").toLowerCase())}/ and manifest.yaml are `
        + `committed &mdash; the git check itself failed. Treat as uncommitted until confirmed otherwise.</div>`;
    }
  } else if (state === "RETIRING" || state === "RETIRED") {
    // join-c plan Task 4 Step 6, option (a): the states render, there is no
    // button. Un-joining is a `curl` / scripts/member.sh operation the runbook
    // documents -- this console is read-mostly-plus-approve, and a delete
    // control is a different act for a different audience than "watch an
    // agency arrive". Task 4 Step 8 is the other half of that argument: an
    // own-server un-join ENDS in two Docker commands this console cannot run,
    // and a button whose outcome is "now go do this by hand" in a browser tab
    // is worse than no button. So the instruction renders here, where the
    // record carries it, and the operator issues the DELETE where they can
    // also run the Docker commands.
    const rev = record.reversal || [];
    if (rev.length) {
      html += `<ol class="join-step-list">`
        + rev.map(r => `<li class="join-step ${r.outcome === "reversed" ? "done" : "pending"}">`
          + `<span class="join-step-actor">undo</span>`
          + `<span class="join-step-id">${esc(r.step)}</span>`
          + `<span class="join-step-note">${esc(r.outcome)}</span>`
          + `</li>`).join("")
        + `</ol>`;
    }
    const e = record.error || {};
    if (e.step) {
      html += `<div class="join-error">un-join stopped at <code>${esc(e.step)}</code>: `
        + `<span class="join-error-message">${esc(e.message || "")}</span></div>`
      + `<div class="join-note">Re-issue the DELETE to resume &mdash; every reversal is probed `
        + `first, so what is already gone is skipped.</div>`;
    }
    const instruction = record.retire_instruction;
    if (instruction) {
      html += `<div class="join-retire">This member owned its own Security Server. `
        + `The join API never touches Docker &mdash; run this on the Docker host to finish:`
        + `<pre class="join-retire-command">${esc(instruction.message || "")}</pre></div>`;
    }
    if (state === "RETIRED" && !record.config_removed) {
      html += `<div class="join-note">The federation no longer holds this member, but `
        + `configs/member-${esc((payload.code || "").toLowerCase())}/ was not removed &mdash; `
        + `run <code>scripts/member.sh remove ${esc((payload.code || "").toLowerCase())}</code>.</div>`;
    }
  }

  el.innerHTML = html;
  return el;
}

async function refreshJoinQueue() {
  const data = await api("/api/join/requests");
  const empty = $("#join-empty");
  const list = $("#join-list");
  if (!data || !Array.isArray(data.requests)) {
    list.innerHTML = "";
    empty.style.display = "block";
    empty.textContent = data && data.error
      ? `No join API reachable: ${data.error}`
      : "No join API reachable.";
    return;
  }
  if (data.requests.length === 0) {
    list.innerHTML = "";
    empty.style.display = "block";
    empty.textContent = "No join requests yet.";
    return;
  }
  empty.style.display = "none";
  list.innerHTML = "";
  data.requests.forEach(record => list.appendChild(renderJoinRequest(record)));
}

async function onJoinListClick(e) {
  const approveBtn = e.target.closest(".join-approve-btn");
  const resumeBtn = e.target.closest(".join-resume-btn");
  const rejectToggleBtn = e.target.closest(".join-reject-toggle-btn");
  const rejectConfirmBtn = e.target.closest(".join-reject-confirm-btn");

  if (approveBtn) {
    const refInput = approveBtn.parentElement.querySelector(".join-decision-reference");
    const decisionReference = (refInput.value || "").trim();
    if (!decisionReference) {
      // Wave 2 Task 2: the gate is the field itself, not just the API's 400
      // -- a required field with no input must not silently round-trip.
      alert("Decision reference is required: admission is a Steering Committee decision (Ref Model §5.3); this approves and records which one.");
      refInput.focus();
      return;
    }
    approveBtn.disabled = true;
    await api(`/api/join/requests/${encodeURIComponent(approveBtn.dataset.id)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision_reference: decisionReference }),
    });
    await refreshJoinQueue();
  } else if (resumeBtn) {
    resumeBtn.disabled = true;
    await api(`/api/join/requests/${encodeURIComponent(resumeBtn.dataset.id)}/resume`, { method: "POST" });
    await refreshJoinQueue();
  } else if (rejectToggleBtn) {
    const box = $(`#join-reject-${CSS.escape(rejectToggleBtn.dataset.id)}`);
    box.style.display = box.style.display === "none" ? "block" : "none";
  } else if (rejectConfirmBtn) {
    rejectConfirmBtn.disabled = true;
    const box = $(`#join-reject-${CSS.escape(rejectConfirmBtn.dataset.id)}`);
    const reason = box.querySelector(".join-reject-reason").value;
    await api(`/api/join/requests/${encodeURIComponent(rejectConfirmBtn.dataset.id)}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
    await refreshJoinQueue();
  }
}

function initJoinTab() {
  $("#join-list").addEventListener("click", onJoinListClick);
}

// ------------------------------------------------------------ guided run ----
// Walks all three beats with deterministic pauses -- the mode used for
// filming, and for anyone handed the URL cold with nobody to explain the
// three tabs' order (UX plan Task 9, Step 3).
const GUIDED_BEAT_PAUSE_MS = 1_800;

async function runGuidedDemonstration() {
  const btn = $("#run-demo-btn");
  const original = btn.textContent;
  btn.disabled = true;

  const nin = defaultNin;
  if (!nin) { btn.disabled = false; return; }

  switchToTab("counter");
  btn.textContent = "Asking once…";
  await runExchange(nin); // resolves after the full before/ask/fill animation
  await sleep(GUIDED_BEAT_PAUSE_MS);

  btn.textContent = "Showing how it worked…";
  switchToTab("inspector");
  await sleep(GUIDED_BEAT_PAUSE_MS * 2);

  btn.textContent = "Showing who's allowed…";
  switchToTab("permissions");
  await askAsPnea();
  await sleep(STAGGER_MS * 2);
  await askAsPlr();

  btn.disabled = false;
  btn.textContent = original;
}

function initGuidedDemo() {
  $("#run-demo-btn").addEventListener("click", runGuidedDemonstration);
}

// ------------------------------------------------------------------ init ----

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initJournalBanner();
  initContextBar();
  initReceiptsToggle();
  initBreakProof();
  initPermissions();
  initJoinTab();
  initGuidedDemo();
  startHeartbeat();
  loadTopologyBadge();
  loadLearners();
});
