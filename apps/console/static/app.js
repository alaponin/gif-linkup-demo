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
      await loadAcl();
    } else {
      alert("Reset could not verify the restored ACL -- see server logs.\n" + JSON.stringify(resp));
    }
  });
  refreshJournalBanner();
  setInterval(refreshJournalBanner, HEARTBEAT_INTERVAL_MS);
}

// --------------------------------------------------------------- topology ----

async function loadTopologyBadge() {
  const topo = await api("/api/topology");
  $("#profile-badge").textContent = `profile: ${topo.profile}`;
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
  renderInspector(data);
  await renderCounterForm(nin, data, runToken);
}

const GROUP_TITLES = { identity: "Identity", enrolment: "Enrolment" };
const BEFORE_HOLD_MS = 800;

function sourceClassFor(info) {
  return info.source === "citizen" ? "citizen"
    : info.source.startsWith("PNIA") ? "PNIA"
    : info.source.startsWith("PLR") ? "PLR" : "citizen";
}

function revealField(row, info) {
  const valueEl = row.querySelector(".field-value");
  valueEl.textContent = info.value ?? "not available";
  valueEl.classList.toggle("empty", info.value == null);
  row.querySelector(".source-badge").style.visibility = "visible";
  row.classList.add("shown");
}

// Three beats, not one reveal: the empty form ("this is ten questions"),
// then the one question (NIN) landing, then the nine pre-filled fields --
// so the audience sees the *before* the once-only exchange is saving them
// from, not just the after (UX plan Task 2, Step 2).
async function renderCounterForm(nin, data, runToken) {
  $("#counter-form-card").style.display = "block";
  $("#counter-nin-line").textContent = `NIN ${nin}`;
  $("#counter-learner-name").textContent = "";

  const fieldsEl = $("#counter-fields");
  fieldsEl.innerHTML = "";

  const entries = Object.entries(data.credential_application);
  const askedCount = entries.filter(([, info]) => info.source === "citizen").length;
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

  const rows = []; // [[fieldName, info, rowEl]]
  groupOrder.forEach(group => {
    const section = document.createElement("div");
    section.className = "form-group";
    if (group !== "citizen") {
      const heading = document.createElement("h3");
      heading.className = "form-group-heading";
      heading.textContent = GROUP_TITLES[group] || group;
      section.appendChild(heading);
    }
    groups[group].forEach(([name, info]) => {
      const row = document.createElement("div");
      row.className = "form-field shown"; // visible immediately, blank
      const badgeText = info.source === "citizen" ? "you told us" : info.source;
      row.innerHTML = `
        <span class="field-name">${esc(info.label)}</span>
        <span class="field-value empty">&mdash;</span>
        <span class="source-badge ${sourceClassFor(info)}" style="visibility:hidden">${esc(badgeText)}</span>
      `;
      section.appendChild(row);
      rows.push([name, info, row]);
    });
    fieldsEl.appendChild(section);
  });

  await sleep(BEFORE_HOLD_MS);
  if (runToken !== counterFormRun) return; // superseded during the hold

  // ask the one question
  for (const [, info, row] of rows) {
    if (info.source === "citizen") revealField(row, info);
  }
  $("#progress-line").textContent = `asked ${askedCount} · pre-filled 0 / ${prefillTotal}`;
  await sleep(STAGGER_MS * 2);
  if (runToken !== counterFormRun) return;

  // then let the bus answer the rest, one at a time
  let filled = 0;
  for (const [name, info, row] of rows) {
    if (info.source === "citizen") continue;
    await sleep(STAGGER_MS);
    if (runToken !== counterFormRun) return; // a newer run took over mid-fill
    revealField(row, info);
    filled += 1;
    $("#progress-line").textContent = `asked ${askedCount} · pre-filled ${filled} / ${prefillTotal}`;
    if (name === "family_name") {
      const given = data.credential_application.given_name?.value;
      const family = info.value;
      if (given && family) $("#counter-learner-name").textContent = ` — ${given} ${family}`;
    }
  }

  bumpSessionTally(prefillTotal);
}

// -------------------------------------------------------------- inspector ----

function renderInspector(data) {
  $("#inspector-empty").style.display = "none";
  const grid = $("#inspector-layers");
  grid.style.display = "grid";
  grid.innerHTML = "";

  const byService = {};
  data.calls.forEach(call => { byService[call.service] = call; });

  const panes = [
    { key: "technical", title: "Technical (EIF Layer 1)" },
    { key: "legal", title: "Legal (EIF Layer 4)" },
    { key: "organisational", title: "Organisational (EIF Layer 3)" },
    { key: "semantic", title: "Semantic (EIF Layer 2)" },
  ];

  panes.forEach(pane => {
    const el = document.createElement("div");
    el.className = "layer-pane";
    const sentence = data.layers[pane.key] || "(not stated for this call)";
    el.innerHTML = `<h3>${esc(pane.title)}</h3><p class="sentence">${esc(sentence)}</p>`;
    if (pane.key === "technical") {
      const detail = document.createElement("div");
      detail.className = "call-detail";
      detail.textContent = data.calls.map(c =>
        `${c.status_code ?? "ERR"}  ${c.elapsed_ms.toFixed(0)}ms  ${c.url}`
      ).join("\n");
      el.appendChild(detail);
    }
    grid.appendChild(el);
  });
}

// ------------------------------------------------------------ permissions ----

async function loadAcl() {
  const acl = await api("/api/acl");
  const container = $("#acl-services");
  container.innerHTML = "";
  Object.entries(acl.services).forEach(([code, info]) => {
    const row = document.createElement("div");
    row.className = "service-row";
    const mutable = code === "identity-api";
    const granted = info.live.length > 0;
    row.innerHTML = `
      <div>
        <strong>${esc(code)}</strong> on ${esc(info.hosted_on)}
        <div class="grants">grants: ${esc(info.live.join(", ") || "(none)")}</div>
      </div>
      ${mutable
        ? `<button class="action ${granted ? "revoke" : ""}" id="acl-toggle-btn">${granted ? "Revoke" : "Grant"} PNEA:EXAMS</button>`
        : `<span class="grants">not mutable in this demo</span>`}
    `;
    container.appendChild(row);
    if (mutable) {
      $("#acl-toggle-btn").addEventListener("click", () => toggleAcl(granted ? "revoke" : "grant"));
    }
  });
}

async function toggleAcl(action) {
  await api(`/api/acl/${action}`, { method: "POST" });
  await loadAcl();
  await refreshJournalBanner();
}

async function pollExchangeUntil(nin, predicate, resultEl) {
  for (let attempt = 1; attempt <= ACL_POLL_MAX_ATTEMPTS; attempt++) {
    const data = await api(`/api/exchange/${nin}`);
    const call = data.calls.find(c => c.service.includes("identity-api"));
    if (predicate(call)) return { data, call };
    resultEl.textContent = `Waiting for the Security Server's authorization cache to catch up (attempt ${attempt}/${ACL_POLL_MAX_ATTEMPTS})...`;
    await new Promise(r => setTimeout(r, ACL_POLL_INTERVAL_MS));
  }
  return null;
}

async function runPermissionsExchange() {
  const nin = lastNin || defaultNin;
  const resultEl = $("#permissions-result");
  resultEl.className = "result-box";
  resultEl.textContent = "Running...";

  const acl = await api("/api/acl");
  const currentlyGranted = acl.services["identity-api"].live.length > 0;

  const outcome = await pollExchangeUntil(
    nin,
    call => currentlyGranted ? call.status_code === 200 : call.denied,
    resultEl,
  );
  if (!outcome) {
    resultEl.textContent = "Did not observe the expected state within the poll window -- check the ACL and try again.";
    return;
  }
  const { call } = outcome;
  if (call.denied) {
    resultEl.className = "result-box denied";
    resultEl.innerHTML = `<strong>Denied.</strong><div class="fault">${esc(JSON.stringify(call.body))}</div>`;
  } else {
    resultEl.className = "result-box allowed";
    resultEl.innerHTML = `<strong>Allowed.</strong> identity-api resolved in ${call.elapsed_ms.toFixed(0)}ms.`;
  }
}

async function runNegativeExchange() {
  const nin = lastNin || defaultNin;
  const resultEl = $("#permissions-result");
  resultEl.className = "result-box";
  const data = await api(`/api/exchange/${nin}/negative`);
  const call = data.calls[0];
  if (call.denied) {
    resultEl.className = "result-box denied";
    resultEl.innerHTML = `<strong>Denied, as expected.</strong> MOEYS:PEMIS is never on this ACL.<div class="fault">${esc(JSON.stringify(call.body))}</div>`;
  } else {
    resultEl.className = "result-box";
    resultEl.innerHTML = `<strong>Unexpected: not denied.</strong> ${esc(JSON.stringify(call.body))}`;
  }
}

// ------------------------------------------------------------------ init ----

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initJournalBanner();
  startHeartbeat();
  loadTopologyBadge();
  loadLearners();
  loadAcl();
  $("#permissions-run-btn").addEventListener("click", runPermissionsExchange);
  $("#permissions-run-negative-btn").addEventListener("click", runNegativeExchange);
});
