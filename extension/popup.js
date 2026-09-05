// popup.js
// UI glue only - all detection/redaction happens in content.js, all network
// calls happen in background.js. This file just wires the dock/log UI to
// messages and renders the results as cards in the conversation log.

let activeTabId = null;
let lastGraph = null;
let lastAction = null;
let flowStep = "scan"; // "scan" -> "send" -> "done"

const el = {
  log: document.getElementById("log"),
  logEmpty: document.getElementById("logEmpty"),
  primaryActionBtn: document.getElementById("primaryActionBtn"),
  executeIconBtn: document.getElementById("executeIconBtn"),
  modelSelect: document.getElementById("modelSelect"),
  statusLine: document.getElementById("statusLine"),
  serverDot: document.getElementById("serverDot"),
  serverDotSmall: document.getElementById("serverDotSmall"),
  feedbackToggleBtn: document.getElementById("feedbackToggleBtn"),
  feedbackPlusBtn: document.getElementById("feedbackPlusBtn"),
  feedbackSection: document.getElementById("feedbackSection"),
  feedbackCategory: document.getElementById("feedbackCategory"),
  feedbackMessage: document.getElementById("feedbackMessage"),
  ratingRow: document.getElementById("ratingRow"),
  clearRatingBtn: document.getElementById("clearRatingBtn"),
  submitFeedbackBtn: document.getElementById("submitFeedbackBtn"),
  feedbackStatusLine: document.getElementById("feedbackStatusLine"),
};

function setStatus(text, kind) {
  el.statusLine.textContent = text || "";
  el.statusLine.className = "status-line" + (kind ? " " + kind : "");
}

function addCard(html) {
  el.logEmpty.classList.add("hidden");
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = html;
  el.log.appendChild(card);
  el.log.scrollTop = el.log.scrollHeight;
  return card;
}

function checkServer() {
  chrome.runtime.sendMessage({ type: "PING_SERVER" }, (res) => {
    if (chrome.runtime.lastError) return;
    const cls = "dot " + (res && res.ok ? "online" : "offline");
    el.serverDot.className = cls;
    el.serverDotSmall.className = cls;
  });
}

function getActiveTab(callback) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs && tabs[0]) callback(tabs[0]);
  });
}

// ---------------------------------------------------------------------
// Model picker - previously the extension always used whatever single
// model was hardcoded server-side (LOCAL_LLM_MODEL). The server now
// exposes GET /models (whatever Ollama/LM Studio actually has installed),
// so the popup can offer a real choice and send it along with /analyze.
// ---------------------------------------------------------------------
function loadModels() {
  chrome.runtime.sendMessage({ type: "GET_MODELS" }, (res) => {
    el.modelSelect.innerHTML = "";
    const models = (res && res.ok && res.data && res.data.models) || [];
    const list = models.length ? models : ["rule-engine (no local model)"];
    list.forEach((id) => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id;
      el.modelSelect.appendChild(opt);
    });
    const preferred = res && res.ok && res.data && res.data.default;
    if (preferred && list.includes(preferred)) el.modelSelect.value = preferred;
  });
}

function updatePrimaryButton() {
  if (flowStep === "scan") {
    el.primaryActionBtn.textContent = "Scan Page";
    el.executeIconBtn.classList.add("hidden");
  } else if (flowStep === "send") {
    el.primaryActionBtn.textContent = "Send to Server (Sanitized Only)";
    el.executeIconBtn.classList.add("hidden");
  } else if (flowStep === "done") {
    el.primaryActionBtn.textContent = "Scan Again";
    el.executeIconBtn.classList.remove("hidden");
  }
}

el.primaryActionBtn.addEventListener("click", () => {
  if (flowStep === "scan" || flowStep === "done") {
    runScan();
  } else if (flowStep === "send") {
    runSend();
  }
});

el.executeIconBtn.addEventListener("click", () => {
  runExecute();
});

function runScan() {
  setStatus("Scanning page…");
  el.primaryActionBtn.disabled = true;
  getActiveTab((tab) => {
    activeTabId = tab.id;
    chrome.tabs.sendMessage(tab.id, { type: "SCAN_PAGE" }, (res) => {
      el.primaryActionBtn.disabled = false;
      if (chrome.runtime.lastError || !res || !res.ok) {
        setStatus(
          "Could not scan this page. Try a normal http(s) page and reload it once after installing the extension.",
          "error"
        );
        return;
      }
      lastGraph = res.graph;
      renderScanResults(lastGraph);
      flowStep = "send";
      updatePrimaryButton();
      setStatus("Scan complete. Sensitive fields are masked on the page.", "ok");
    });
  });
}

function renderScanResults(graph) {
  const types = graph.detectedTypes || {};
  const chips = Object.keys(types).length
    ? Object.keys(types)
        .map((type) => `<span class="chip">${type}: ${types[type]}</span>`)
        .join("")
    : `<span class="chip">none detected</span>`;

  const jsonId = "json-" + Date.now();
  addCard(`
    <div class="card-label">Scan result</div>
    <div class="summary-row"><span>Sensitive items detected</span><strong>${graph.sensitiveItemsCount}</strong></div>
    <div class="chip-row">${chips}</div>
    <button class="json-toggle" data-target="${jsonId}">View sanitized screen graph</button>
    <pre class="json-preview hidden" id="${jsonId}">${escapeHtml(JSON.stringify(graph, null, 2))}</pre>
  `);

  el.log.querySelector(`[data-target="${jsonId}"]`).addEventListener("click", (e) => {
    document.getElementById(jsonId).classList.toggle("hidden");
  });
}

function escapeHtml(str) {
  return str.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function runSend() {
  if (!lastGraph) return;
  setStatus("Sending sanitized context to server…");
  el.primaryActionBtn.disabled = true;
  const model = el.modelSelect.value;
  const graphWithModel = { ...lastGraph, model };
  chrome.runtime.sendMessage({ type: "SEND_TO_SERVER", graph: graphWithModel }, (res) => {
    el.primaryActionBtn.disabled = false;
    if (chrome.runtime.lastError || !res) {
      setStatus("Could not reach the server. Is it running on 127.0.0.1:8000?", "error");
      return;
    }
    if (!res.ok) {
      setStatus(res.error || "Server rejected the request.", "error");
      return;
    }
    lastAction = res.action;
    renderAction(lastAction);
    flowStep = "done";
    updatePrimaryButton();
    setStatus("Server responded with an action.", "ok");
  });
}

function renderAction(action) {
  const lines = [`<span class="action-type">${action.action}</span>`];
  if (action.targetRef) lines.push(`target: ${action.targetRef}`);
  if (action.direction) lines.push(`direction: ${action.direction}`);
  if (action.summary) lines.push(action.summary);
  if (action.reason) lines.push(`<em>${action.reason}</em>`);
  // decidedBy shows whether a real local model answered this, or the
  // rule-based fallback did (e.g. no model running) - worth surfacing so
  // it's obvious which one is actually driving a given demo run.
  if (action.decidedBy) {
    const label = action.decidedBy.startsWith("local-llm")
      ? `🧠 decided by local model (${action.decidedBy.split(":")[1] || "unknown"})`
      : "⚙️ decided by rule-based fallback (no local model reachable)";
    lines.push(`<span class="decided-by">${label}</span>`);
  }
  addCard(`<div class="card-label">Action returned by server</div>${lines.join("<br/>")}`);
}

function runExecute() {
  if (!lastAction || activeTabId == null) return;
  chrome.tabs.sendMessage(activeTabId, { type: "EXECUTE_ACTION", action: lastAction }, (res) => {
    if (chrome.runtime.lastError || !res) {
      setStatus("Could not execute action on the page.", "error");
      return;
    }
    setStatus(res.ok ? res.message : res.error, res.ok ? "ok" : "error");
  });
}

checkServer();
loadModels();
updatePrimaryButton();

// ---------------------------------------------------------------------
// Feedback - goes to the AIVA Work Manager feedback inbox, the same
// endpoint the AIVA Browser reports into (see background.js for the
// actual URL). Not scanned/redacted like page content - the user is
// typing this themselves, about the extension, not pulling it off a page.
// ---------------------------------------------------------------------

let selectedRating = null;

function toggleFeedback() {
  el.feedbackSection.classList.toggle("hidden");
}
el.feedbackToggleBtn.addEventListener("click", toggleFeedback);
el.feedbackPlusBtn.addEventListener("click", toggleFeedback);

el.ratingRow.querySelectorAll(".star-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    selectedRating = Number(btn.dataset.rating);
    renderStars();
  });
});

el.clearRatingBtn.addEventListener("click", () => {
  selectedRating = null;
  renderStars();
});

function renderStars() {
  el.ratingRow.querySelectorAll(".star-btn").forEach((btn) => {
    btn.classList.toggle("active", selectedRating !== null && Number(btn.dataset.rating) <= selectedRating);
  });
}

function setFeedbackStatus(text, kind) {
  el.feedbackStatusLine.textContent = text || "";
  el.feedbackStatusLine.className = "status-line" + (kind ? " " + kind : "");
}

el.submitFeedbackBtn.addEventListener("click", () => {
  const message = el.feedbackMessage.value.trim();
  if (!message) {
    setFeedbackStatus("Write a message before sending.", "error");
    return;
  }

  el.submitFeedbackBtn.disabled = true;
  setFeedbackStatus("Sending…");

  getActiveTab((tab) => {
    let pageDomain = null;
    try {
      pageDomain = tab && tab.url ? new URL(tab.url).hostname : null;
    } catch (e) {
      /* tab.url can be a non-http scheme (chrome://, about:blank, ...) */
    }

    const payload = {
      message,
      category: el.feedbackCategory.value,
      rating: selectedRating,
      source: "aiva-nex-agent",
      pageUrl: pageDomain, // domain only, consistent with the rest of the extension - never the full URL
      appVersion: chrome.runtime.getManifest().version,
      platform: navigator.userAgent.slice(0, 80),
    };

    chrome.runtime.sendMessage({ type: "SEND_FEEDBACK", payload }, (res) => {
      el.submitFeedbackBtn.disabled = false;
      if (chrome.runtime.lastError || !res || !res.ok) {
        setFeedbackStatus((res && res.error) || "Could not send feedback. Check your connection.", "error");
        return;
      }
      setFeedbackStatus("Thanks! Feedback sent.", "ok");
      el.feedbackMessage.value = "";
      selectedRating = null;
      renderStars();
    });
  });
});
