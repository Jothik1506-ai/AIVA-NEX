// background.js
// MV3 service worker. Its only job is to relay the already-sanitized screen
// graph to the local server - it never touches the DOM or sees raw page
// content itself. Kept separate from content.js/popup.js so the network
// call isn't subject to any page's Content-Security-Policy.

const SERVER_URL = "http://127.0.0.1:8000";

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

  return false;
});
