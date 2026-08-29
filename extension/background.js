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

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "SEND_TO_SERVER") {
    fetch(SERVER_URL + "/analyze", {
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
      .catch((err) => sendResponse({ ok: false, error: "Could not reach server: " + err.message }));
    return true; // keep the message channel open for the async response
  }

  if (msg.type === "PING_SERVER") {
    fetch(SERVER_URL + "/health")
      .then((res) => res.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg.type === "SEND_FEEDBACK") {
    fetch(FEEDBACK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(msg.payload),
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
    return true;
  }

  return false;
});
