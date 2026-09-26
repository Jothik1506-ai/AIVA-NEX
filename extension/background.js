// background.js
// MV3 service worker. Its only job is to relay the already-sanitized screen
// graph to the local server - it never touches the DOM or sees raw page
// content itself. Kept separate from content.js/popup.js so the network
// call isn't subject to any page's Content-Security-Policy.

const SERVER_URL = "http://127.0.0.1:8000";

// Feedback goes to the AIVA Work Manager's public feedback inbox - the same
// endpoint the AIVA Browser reports into. CORS is deliberately opened on
// this one route server-side (see the Work Manager's server.js,
// allowFeedbackCors), since it exists specifically to receive submissions
// from apps with no Work Manager login of their own.
const FEEDBACK_URL = "https://manager.aivafreelancia.in/api/feedback";

// [privacy-hardening] Every server call except /health carries the shared
// token (Settings panel -> chrome.storage.local.aivaServerToken) in the
// X-Aiva-Token header; the server answers 401 without it. Off-device
// feedback is sent only when the user ticked the opt-in in Settings.
function getSettings() {
  return new Promise((resolve) =>
    chrome.storage.local.get(["aivaServerToken", "aivaFeedbackOptIn"], (s) => resolve(s || {}))
  );
}

async function serverFetch(path, options = {}) {
  const { aivaServerToken } = await getSettings();
  const headers = { ...(options.headers || {}) };
  if (aivaServerToken) headers["X-Aiva-Token"] = aivaServerToken;
  const res = await fetch(SERVER_URL + path, { ...options, headers });
  if (res.status === 401) {
    const err = new Error("Server token missing or wrong - open Settings (gear icon) and paste the token printed by the server.");
    err.status = 401;
    throw err;
  }
  return res;
}

// The extension has no default_popup any more (see manifest.json) - it uses
// the Side Panel API instead, so the UI opens as a full-height panel docked
// to the browser window (like Claude for Chrome) rather than a small
// dropdown. The toolbar icon click has to open it explicitly.
chrome.action.onClicked.addListener((tab) => {
  if (tab.windowId != null) chrome.sidePanel.open({ windowId: tab.windowId });
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "SEND_TO_SERVER") {
    serverFetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(msg.graph),
    })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          sendResponse({ ok: false, error: data.detail || "Server rejected the request." });
        } else {
          sendResponse({ ok: true, action: data });
        }
      })
      .catch((err) => sendResponse({ ok: false, error: err.status === 401 ? err.message : "Could not reach server: " + err.message }));
    return true; // keep the message channel open for the async response
  }

  // --- Visual perception (SIH26171): relay the ALREADY-MASKED screenshot to
  // the local server only (see vision.js). ---
  if (msg.type === "PERCEIVE") {
    serverFetch("/perceive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(msg.payload),
    })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        sendResponse(res.ok ? { ok: true, data } : { ok: false, error: data.detail || "Perception failed." });
      })
      .catch((err) => sendResponse({ ok: false, error: err.status === 401 ? err.message : "Could not reach server: " + err.message }));
    return true;
  }
  // --- end visual perception ---

  if (msg.type === "PING_SERVER") {
    fetch(SERVER_URL + "/health")
      .then((res) => res.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg.type === "GET_MODELS") {
    serverFetch("/models")
      .then((res) => res.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg.type === "CHAT_WITH_SERVER") {
    serverFetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: msg.message,
        graph: msg.graph,
        model: msg.model,
        history: msg.history || [],
      }),
    })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          sendResponse({ ok: false, error: data.detail || "Server rejected chat request." });
        } else {
          sendResponse({ ok: true, data });
        }
      })
      .catch((err) => sendResponse({ ok: false, error: err.status === 401 ? err.message : "Could not reach server: " + err.message }));
    return true;
  }

  if (msg.type === "NAVIGATE_TAB") {
    if (msg.url) {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs && tabs[0]) {
          chrome.tabs.update(tabs[0].id, { url: msg.url }, (updatedTab) => {
            sendResponse({ ok: true, tabId: updatedTab.id });
          });
        } else {
          chrome.tabs.create({ url: msg.url }, (newTab) => {
            sendResponse({ ok: true, tabId: newTab.id });
          });
        }
      });
      return true;
    }
  }

  if (msg.type === "CHECK_AUTH") {
    serverFetch("/models")
      .then((res) => sendResponse(res.ok ? { ok: true } : { ok: false, error: "Server answered HTTP " + res.status }))
      .catch((err) => sendResponse({ ok: false, error: err.status === 401 ? err.message : "Could not reach server: " + err.message }));
    return true;
  }

  if (msg.type === "SEND_FEEDBACK") {
    getSettings().then(({ aivaFeedbackOptIn }) => {
      if (aivaFeedbackOptIn !== true) {
        sendResponse({ ok: false, error: "Off-device feedback is disabled. Enable it in Settings (gear icon) to send." });
        return;
      }
      sendFeedback(msg.payload, sendResponse);
    });
    return true;
  }

  return false;
});

// Off-device: only reached after the Settings opt-in check above.
function sendFeedback(payload, sendResponse) {
    fetch(FEEDBACK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          sendResponse({ ok: false, error: data.error || "Feedback was not accepted." });
        } else {
          sendResponse({ ok: true, id: data.id });
        }
      })
      .catch((err) => sendResponse({ ok: false, error: "Could not reach the feedback server: " + err.message }));
}
