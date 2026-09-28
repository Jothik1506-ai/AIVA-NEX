// settings.js - [privacy-hardening]
// Side-panel Settings: the shared server token (sent by background.js as the
// X-Aiva-Token header) and the opt-in for off-device feedback. Both live in
// chrome.storage.local only.

(() => {
  const $ = (id) => document.getElementById(id);
  const section = $("settingsSection");
  const tokenInput = $("serverTokenInput");
  const urlInput = $("serverUrlInput");
  const optIn = $("feedbackOptIn");
  const status = $("settingsStatusLine");

  function setStatus(text, kind) {
    status.textContent = text || "";
    status.className = "status-line" + (kind ? " " + kind : "");
  }

  function load() {
    chrome.storage.local.get(["aivaServerToken", "aivaFeedbackOptIn", "aivaServerUrl"], (s) => {
      tokenInput.value = s.aivaServerToken || "";
      urlInput.value = s.aivaServerUrl || AivaServerUrl.DEFAULT_SERVER_URL;
      optIn.checked = s.aivaFeedbackOptIn === true;
      if (!s.aivaServerToken) setStatus("No server token set yet - paste it from the server console.", "error");
    });
  }

  $("settingsToggleBtn").addEventListener("click", () => {
    section.classList.toggle("hidden");
    $("profileSection").classList.add("hidden");
    $("feedbackSection").classList.add("hidden");
    if (!section.classList.contains("hidden")) load();
  });
  // Keep only one panel open when the other header buttons are used.
  ["profileToggleBtn", "feedbackToggleBtn"].forEach((id) =>
    $(id).addEventListener("click", () => section.classList.add("hidden"))
  );

  $("saveSettingsBtn").addEventListener("click", () => {
    const token = tokenInput.value.trim();
    const v = AivaServerUrl.validateServerUrl(urlInput.value);
    if (!v.ok) return setStatus(v.error, "error");
    urlInput.value = v.url;
    chrome.storage.local.set({ aivaServerToken: token, aivaFeedbackOptIn: optIn.checked, aivaServerUrl: v.url }, () => {
      setStatus(
        AivaServerUrl.isLocalhost(v.url)
          ? "Saved. Testing connection…"
          : "Saved. Testing connection… (non-local server: page context and the token leave this machine" +
              (v.url.startsWith("http:") ? ", unencrypted over http" : "") + ")"
      );
      chrome.runtime.sendMessage({ type: "CHECK_AUTH" }, (res) => {
        if (chrome.runtime.lastError || !res) return setStatus("Could not reach the background worker.", "error");
        if (res.ok) return setStatus("Connected - token accepted by the local server.", "ok");
        setStatus(res.error || "Connection failed.", "error");
      });
    });
  });
})();
