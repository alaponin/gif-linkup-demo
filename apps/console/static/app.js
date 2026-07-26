// KP2 demonstration console. Vanilla JS, no build step, no framework --
// see the plan's Global Constraints (air-gapped demo machine).

const STAGGER_MS = 220;
const HEARTBEAT_INTERVAL_MS = 30_000;
const ACL_POLL_INTERVAL_MS = 5_000;
const ACL_POLL_MAX_ATTEMPTS = 8; // ~40s -- confirmed live the proxy's own
// authorization cache can lag the admin API by up to ~30s (2026-07-26).

let lastNin = null;
let defaultNin = null;

function $(sel) { return document.querySelector(sel); }
function $all(sel) { return Array.from(document.querySelectorAll(sel)); }

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
    chip.className = "chip" + (learner.case.includes("404") ? " clean-404" : "");
    chip.textContent = `${learner.name} (${learner.nin}) -- ${learner.case}`;
    chip.addEventListener("click", () => runExchange(learner.nin));
    container.appendChild(chip);
    if (i === 0) defaultNin = learner.nin;
  });
}

async function runExchange(nin) {
  lastNin = nin;
  const data = await api(`/api/exchange/${nin}`);
  renderCounterForm(nin, data);
  renderInspector(data);
}

function renderCounterForm(nin, data) {
  $("#counter-form-card").style.display = "block";
  $("#counter-nin").textContent = nin;
  const fieldsEl = $("#counter-fields");
  fieldsEl.innerHTML = "";

  const entries = Object.entries(data.credential_application);
  const total = entries.length;
  let filled = 0;

  entries.forEach(([name, info], i) => {
    const row = document.createElement("div");
    row.className = "form-field";
    const sourceClass = info.source === "citizen" ? "citizen"
      : info.source.startsWith("PNIA") ? "PNIA"
      : info.source.startsWith("PLR") ? "PLR" : "citizen";
    const badgeText = info.source === "citizen" ? "you told us" : info.source;
    row.innerHTML = `
      <span class="field-name">${name}</span>
      <span class="field-value ${info.value == null ? "empty" : ""}">${info.value ?? "not available"}</span>
      <span class="source-badge ${sourceClass}">${badgeText}</span>
    `;
    fieldsEl.appendChild(row);
    setTimeout(() => {
      row.classList.add("shown");
      filled += 1;
      $("#progress-line").textContent = `fields asked: 1 / fields filled: ${filled}`;
    }, i * STAGGER_MS);
  });
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
    el.innerHTML = `<h3>${pane.title}</h3><p class="sentence">${sentence}</p>`;
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
        <strong>${code}</strong> on ${info.hosted_on}
        <div class="grants">grants: ${info.live.join(", ") || "(none)"}</div>
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
    resultEl.innerHTML = `<strong>Denied.</strong><div class="fault">${JSON.stringify(call.body)}</div>`;
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
    resultEl.innerHTML = `<strong>Denied, as expected.</strong> MOEYS:PEMIS is never on this ACL.<div class="fault">${JSON.stringify(call.body)}</div>`;
  } else {
    resultEl.className = "result-box";
    resultEl.innerHTML = `<strong>Unexpected: not denied.</strong> ${JSON.stringify(call.body)}`;
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
