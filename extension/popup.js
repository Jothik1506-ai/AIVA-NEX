// popup.js
// UI glue only - all detection/redaction happens in content.js, all network
// calls happen in background.js. This file just wires buttons to messages
// and renders the results.

let activeTabId = null;
let lastGraph = null;
let lastAction = null;

const el = {
  scanBtn: document.getElementById("scanBtn"),
  sendBtn: document.getElementById("sendBtn"),
  executeBtn: document.getElementById("executeBtn"),
  summarySection: document.getElementById("summarySection"),
  sensitiveCount: document.getElementById("sensitiveCount"),
  detectedTypesList: document.getElementById("detectedTypesList"),
  jsonSection: document.getElementById("jsonSection"),
  jsonPreview: document.getElementById("jsonPreview"),
  actionSection: document.getElementById("actionSection"),
  actionBox: document.getElementById("actionBox"),
  statusLine: document.getElementById("statusLine"),
  serverDot: document.getElementById("serverDot"),
  serverStatusText: document.getElementById("serverStatusText"),
};

function setStatus(text, kind) {
  el.statusLine.textContent = text || "";
  el.statusLine.className = "status-line" + (kind ? " " + kind : "");
}

function checkServer() {
  chrome.runtime.sendMessage({ type: "PING_SERVER" }, (res) => {
    if (chrome.runtime.lastError) return;
    if (res && res.ok) {
      el.serverDot.className = "dot online";
      el.serverStatusText.textContent = "server online";
    } else {
      el.serverDot.className = "dot offline";
      el.serverStatusText.textContent = "server offline";
    }
  });
}

function getActiveTab(callback) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs && tabs[0]) callback(tabs[0]);
  });
}

el.scanBtn.addEventListener("click", () => {
  setStatus("Scanning page…");
  getActiveTab((tab) => {
    activeTabId = tab.id;
    chrome.tabs.sendMessage(tab.id, { type: "SCAN_PAGE" }, (res) => {
      if (chrome.runtime.lastError || !res || !res.ok) {
        setStatus(
          "Could not scan this page. Try a normal http(s) page and reload it once after installing the extension.",
          "error"
        );
        return;
      }
      lastGraph = res.graph;
      renderScanResults(lastGraph);
      setStatus("Scan complete. Sensitive fields are masked on the page.", "ok");
    });
  });
});

function renderScanResults(graph) {
  el.summarySection.classList.remove("hidden");
  el.jsonSection.classList.remove("hidden");
  el.sendBtn.classList.remove("hidden");

  el.sensitiveCount.textContent = graph.sensitiveItemsCount;
  el.detectedTypesList.innerHTML = "";
  const types = graph.detectedTypes || {};
  Object.keys(types).forEach((type) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${type}: ${types[type]}`;
    el.detectedTypesList.appendChild(chip);
  });
  if (Object.keys(types).length === 0) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = "none detected";
    el.detectedTypesList.appendChild(chip);
  }

  el.jsonPreview.textContent = JSON.stringify(graph, null, 2);
}

el.sendBtn.addEventListener("click", () => {
  if (!lastGraph) return;
  setStatus("Sending sanitized context to server…");
  chrome.runtime.sendMessage({ type: "SEND_TO_SERVER", graph: lastGraph }, (res) => {
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
    setStatus("Server responded with an action.", "ok");
  });
});

function renderAction(action) {
  el.actionSection.classList.remove("hidden");
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
  el.actionBox.innerHTML = lines.join("<br/>");
}

el.executeBtn.addEventListener("click", () => {
  if (!lastAction || activeTabId == null) return;
  chrome.tabs.sendMessage(activeTabId, { type: "EXECUTE_ACTION", action: lastAction }, (res) => {
    if (chrome.runtime.lastError || !res) {
      setStatus("Could not execute action on the page.", "error");
      return;
    }
    setStatus(res.ok ? res.message : res.error, res.ok ? "ok" : "error");
  });
});

checkServer();
