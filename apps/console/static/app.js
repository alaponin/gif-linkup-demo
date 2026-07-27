// KP2 demonstration console. Vanilla JS, no build step, no framework --
// see the plan's Global Constraints (air-gapped demo machine).

const STAGGER_MS = 220;
const HEARTBEAT_INTERVAL_MS = 30_000;
const ACL_POLL_INTERVAL_MS = 5_000;
const ACL_POLL_MAX_ATTEMPTS = 8; // ~40s -- confirmed live the proxy's own
// authorization cache can lag the admin API by up to ~30s (2026-07-26).

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

async function api(path, opts) {
  const resp = await fetch(path, opts);
  return resp.json();
}

// ---------------------------------------------------------------- tabs ----

function initTabs() {
  $all(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      $all(".tab-btn").forEach(b => b.classList.remove("active"));
      $all(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
    });
  });
}

// ------------------------------------------------------------ heartbeat ----

function startHeartbeat() {
  const beat = () => api("/api/heartbeat", { method: "POST" });
  beat();
  setInterval(beat, HEARTBEAT_INTERVAL_MS);
}

// ------------------------------------------------------- journal banner ----

async function refreshJournalBanner() {
  const acl = await api("/api/acl");
  const banner = $("#journal-banner");
  if (acl.dirty) {
    banner.classList.add("dirty");
  } else {
    banner.classList.remove("dirty");
  }
  return acl;
}

function initJournalBanner() {
  $("#journal-reset-btn").addEventListener("click", async () => {
    const resp = await api("/api/reset", { method: "POST" });
    if (resp.ok) {
      await refreshJournalBanner();
      await refreshPermissionsToggle();
    } else {
      alert("Reset could not verify the restored ACL -- see server logs.\n" + JSON.stringify(resp));
    }
  });
  refreshJournalBanner();
  setInterval(refreshJournalBanner, HEARTBEAT_INTERVAL_MS);
}

// --------------------------------------------------------------- topology ----

let TOPOLOGY = null; // cached from the one /api/topology fetch on load --
// looked up by member/host code, never re-derived (Global Constraint: no
// fourth copy of the topology).

async function loadTopologyBadge() {
  TOPOLOGY = await api("/api/topology");
  $("#profile-badge").textContent = `profile: ${TOPOLOGY.profile}`;
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
        <span class="source-badge ${sourceClassFor(info)}" style="visibility:hidden">${esc(badgeText)}</span>
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
        if (given && family) $("#counter-learner-name").textContent = ` — ${given} ${family}`;
      }
    }
  }

  $("#break-proof-caption").textContent = anyDenied
    ? "The same form, one source withdrawn — nothing here was hard-coded."
    : "";
  bumpSessionTally(filled);
  renderReceipts(data);
  $("#receipts-toggle-btn").style.display = "inline-block";
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
// (UX plan Task 7). enrolment-api and pemis-api are deliberately absent
// from this tab: they would just be two more inert rows, and the reset
// path's own verification (journal.py) already checks the mutable/
// untouched-service asymmetry without an audience needing to see it.
// Design decision 4 (why only identity-api is writable here) lives in
// app.py's comment, not on this page.

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
  statusEl.textContent = allowed ? "admitted" : "not admitted";
  statusEl.classList.toggle("denied-label", !allowed);
}

async function askAsMoeys() {
  const nin = lastNin || defaultNin;
  if (!nin) return;
  const resultEl = $("#moeys-result");
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
  $("#ask-as-moeys-btn").addEventListener("click", askAsMoeys);
  $("#permissions-revoke-btn").addEventListener("click", e => togglePneaAccess("revoke", e.target));
  $("#permissions-restore-btn").addEventListener("click", e => togglePneaAccess("grant", e.target));
  $("#permissions-reset-btn").addEventListener("click", resetPermissions);
  refreshPermissionsToggle();
}

// ------------------------------------------------------------------ init ----

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initJournalBanner();
  initReceiptsToggle();
  initBreakProof();
  initPermissions();
  startHeartbeat();
  loadTopologyBadge();
  loadLearners();
});
