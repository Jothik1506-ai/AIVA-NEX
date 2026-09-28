// server-url.js
// Validation for the configurable local-server URL (Settings -> Server URL,
// chrome.storage.local.aivaServerUrl). Pure: loaded by background.js
// (importScripts), popup.html (globalThis.AivaServerUrl) and the Node tests.

(function (root) {
  const DEFAULT_SERVER_URL = "http://127.0.0.1:8000";

  /** -> { ok: true, url } with a normalised origin (no trailing slash), or
   * { ok: false, error }. Only http/https, no credentials, query or hash. */
  function validateServerUrl(input) {
    const raw = String(input == null ? "" : input).trim();
    if (!raw) return { ok: true, url: DEFAULT_SERVER_URL };
    let u;
    try {
      u = new URL(raw);
    } catch (e) {
      return { ok: false, error: "Not a valid URL. Example: " + DEFAULT_SERVER_URL };
    }
    if (u.protocol !== "http:" && u.protocol !== "https:") return { ok: false, error: "Server URL must start with http:// or https://" };
    if (u.username || u.password) return { ok: false, error: "Server URL must not contain a username or password." };
    if (u.search || u.hash) return { ok: false, error: "Server URL must not contain ? or # parts." };
    const path = u.pathname.replace(/\/+$/, "");
    return { ok: true, url: u.origin + path };
  }

  function isLocalhost(url) {
    try {
      const h = new URL(url).hostname;
      return h === "127.0.0.1" || h === "localhost" || h === "[::1]";
    } catch (e) {
      return false;
    }
  }

  const api = { DEFAULT_SERVER_URL, validateServerUrl, isLocalhost };
  root.AivaServerUrl = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
