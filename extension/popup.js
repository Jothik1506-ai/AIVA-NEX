// popup.js
// Interactive Chat Agent & On-Device Local PII Profile Manager

let activeTabId = null;
let lastGraph = null;
let lastAction = null;
let flowStep = "scan"; // "scan" -> "send" -> "done"
let chatHistory = [];

// Maps the legacy profile form's fields to memory categories
// (docs/MEMORY-ARCHITECTURE-PLAN.md §5.2). Replaces the old flat
// `localProfile` blob - every read/write now goes through AivaMemory
// (memory.js), which classifies sensitivity, tracks provenance, and never
// silently invents a value the user didn't provide.
const PROFILE_FIELD_CATEGORIES = {
  name: "identity.name",
  email: "contact.email",
  phone: "contact.phone",
  address: "contact.address",
  pincode: "contact.pincode",
};

/** Live-reads the address fact from memory - used wherever the old flat
 * `localProfile.address` was read (order-confirmation display, the
 * autofill-permission gate). Returns "" if nothing is saved. */
async function getSavedAddress() {
  const facts = await AivaMemory.getFactsByCategory(PROFILE_FIELD_CATEGORIES.address);
  return facts.length ? facts[0].value : "";
}

const el = {
  log: document.getElementById("log"),
  logEmpty: document.getElementById("logEmpty"),
  primaryActionBtn: document.getElementById("primaryActionBtn"),
  executeIconBtn: document.getElementById("executeIconBtn"),
  modelSelect: document.getElementById("modelSelect"),
  statusLine: document.getElementById("statusLine"),
  serverDot: document.getElementById("serverDot"),
  serverDotSmall: document.getElementById("serverDotSmall"),
  
  // Chat Elements
  chatInput: document.getElementById("chatInput"),
  sendChatBtn: document.getElementById("sendChatBtn"),
  promptChips: document.getElementById("promptChips"),

  // Profile Elements
  profileToggleBtn: document.getElementById("profileToggleBtn"),
  profileSection: document.getElementById("profileSection"),
  profileName: document.getElementById("profileName"),
  profileEmail: document.getElementById("profileEmail"),
  profilePhone: document.getElementById("profilePhone"),
  profileAddress: document.getElementById("profileAddress"),
  profilePincode: document.getElementById("profilePincode"),
  saveProfileBtn: document.getElementById("saveProfileBtn"),
  profileStatusLine: document.getElementById("profileStatusLine"),

  // Feedback Elements
  feedbackToggleBtn: document.getElementById("feedbackToggleBtn"),
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

function addCard(html, customClass) {
  el.logEmpty.classList.add("hidden");
  const card = document.createElement("div");
  card.className = customClass ? "card " + customClass : "card";
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
    if (tabs && tabs[0]) {
      activeTabId = tabs[0].id;
      callback(tabs[0]);
    }
  });
}

// A confirmation card (autofill/order) is shown for a specific tab, but the
// user can switch tabs before clicking Confirm - the side panel stays open
// across tab switches, unlike a popup. Without this, confirming on tab A
// while tab B is now active would silently execute on tab B instead.
// This doesn't do full document-identity/navigation tracking - just the
// minimum needed so a confirm click can't be redirected to a different tab.
function withVerifiedTab(boundTabId, callback) {
  if (boundTabId == null) {
    addCard("⚠️ Lost track of which tab this was for - please re-scan and try again.", "chat-msg-agent");
    return;
  }
  chrome.tabs.get(boundTabId, (tab) => {
    if (chrome.runtime.lastError || !tab) {
      addCard("⚠️ That tab is no longer open - please re-scan and try again.", "chat-msg-agent");
      return;
    }
    callback(boundTabId);
  });
}

// ---------------------------------------------------------------------
// Local PII Profile Storage - backed by AivaMemory (memory.js), a
// normalized per-fact store, not the old single chrome.storage.local blob.
// ---------------------------------------------------------------------
async function loadLocalProfile() {
  const values = {};
  for (const [field, category] of Object.entries(PROFILE_FIELD_CATEGORIES)) {
    const facts = await AivaMemory.getFactsByCategory(category);
    values[field] = facts.length ? facts[0].value : "";
  }
  populateProfileInputs(values);
}

function populateProfileInputs(values) {
  el.profileName.value = values.name || "";
  el.profileEmail.value = values.email || "";
  el.profilePhone.value = values.phone || "";
  el.profileAddress.value = values.address || "";
  el.profilePincode.value = values.pincode || "";
}

el.profileToggleBtn.addEventListener("click", () => {
  el.profileSection.classList.toggle("hidden");
  el.feedbackSection.classList.add("hidden");
});

el.saveProfileBtn.addEventListener("click", async () => {
  el.saveProfileBtn.disabled = true;
  const edits = {
    name: el.profileName.value.trim(),
    email: el.profileEmail.value.trim(),
    phone: el.profilePhone.value.trim(),
    address: el.profileAddress.value.trim(),
    pincode: el.profilePincode.value.trim(),
  };

  try {
    for (const [field, value] of Object.entries(edits)) {
      if (!value) continue; // don't create a fact for a field the user left blank
      const category = PROFILE_FIELD_CATEGORIES[field];
      const existing = await AivaMemory.getFactsByCategory(category);
      if (existing.length) {
        if (existing[0].value !== value) await AivaMemory.updateFact(existing[0].id, value);
      } else {
        await AivaMemory.createFact({ category, key: field, value, source: "user_explicit" });
      }
    }
    el.profileStatusLine.textContent = "Profile saved locally in browser storage!";
    el.profileStatusLine.className = "status-line ok";
    setTimeout(() => {
      el.profileStatusLine.textContent = "";
      el.profileSection.classList.add("hidden");
    }, 1500);
  } catch (err) {
    // classifySensitivity/createFact throw AivaMemory.MemoryRefusedError for
    // a never_store category - none of these 5 legacy fields should ever hit
    // that, but never silently swallow a refusal if something changes later.
    el.profileStatusLine.textContent = err.message || "Could not save profile.";
    el.profileStatusLine.className = "status-line error";
  } finally {
    el.saveProfileBtn.disabled = false;
  }
});

// ---------------------------------------------------------------------
// Model Picker & Server Models
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
    el.primaryActionBtn.textContent = "Send Context";
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

function runScan(callback) {
  setStatus("Scanning page…");
  el.primaryActionBtn.disabled = true;
  getActiveTab((tab) => {
    chrome.tabs.sendMessage(tab.id, { type: "SCAN_PAGE" }, (res) => {
      el.primaryActionBtn.disabled = false;
      if (chrome.runtime.lastError || !res || !res.ok) {
        setStatus("Could not scan this page. Reload the page once.", "error");
        if (callback) callback(null);
        return;
      }
      lastGraph = res.graph;
      renderScanResults(lastGraph);
      flowStep = "send";
      updatePrimaryButton();
      setStatus("Scan complete. Sensitive fields masked locally.", "ok");
      if (callback) callback(lastGraph);
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

  el.log.querySelector(`[data-target="${jsonId}"]`).addEventListener("click", () => {
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
      setStatus("Could not reach server.", "error");
      return;
    }
    if (!res.ok) {
      setStatus(res.error || "Server rejected request.", "error");
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
  addCard(`<div class="card-label">Action returned by server</div>${lines.join("<br/>")}`);
}

function runExecute() {
  if (!lastAction || activeTabId == null) return;
  chrome.tabs.sendMessage(activeTabId, { type: "EXECUTE_ACTION", action: lastAction }, (res) => {
    if (chrome.runtime.lastError || !res) {
      setStatus("Could not execute action on page.", "error");
      return;
    }
    setStatus(res.ok ? res.message : res.error, res.ok ? "ok" : "error");
  });
}

// ---------------------------------------------------------------------
// Interactive Chat Agent Section
// ---------------------------------------------------------------------
function handleSendChat(queryText) {
  const query = (queryText || el.chatInput.value).trim();
  if (!query) return;

  el.chatInput.value = "";
  addCard(escapeHtml(query), "chat-msg-user");
  setStatus("Aiva Nex Agent thinking…");

  const sendQueryWithGraph = (graph, boundTabId) => {
    const model = el.modelSelect.value;
    chrome.runtime.sendMessage(
      {
        type: "CHAT_WITH_SERVER",
        message: query,
        graph: graph || {},
        model: model,
        history: chatHistory,
      },
      (res) => {
        if (chrome.runtime.lastError || !res || !res.ok) {
          setStatus("Error communicating with chat server.", "error");
          addCard("Sorry, I could not reach the server. Is main.py running on localhost:8000?", "chat-msg-agent");
          return;
        }

        const data = res.data || {};
        chatHistory.push({ role: "user", content: query });
        chatHistory.push({ role: "assistant", content: data.reply });

        renderChatAgentResponse(data, boundTabId);
        setStatus("Ready", "ok");
      }
    );
  };

  // Capture which tab this question was actually about right now - any
  // confirmation card the reply produces must execute against this same
  // tab, not whatever tab happens to be active when the user later clicks
  // Confirm.
  getActiveTab((tab) => {
    if (lastGraph) {
      sendQueryWithGraph(lastGraph, tab.id);
    } else {
      runScan((graph) => sendQueryWithGraph(graph, tab.id));
    }
  });
}

async function renderChatAgentResponse(data, boundTabId) {
  let cardHtml = `<div>${escapeHtml(data.reply)}</div>`;

  if (data.summary) {
    cardHtml += `<div style="margin-top:8px; padding:8px; background:#0f172a; border-radius:6px; font-size:12px; color:#93c5fd;"><strong>Summary:</strong> ${escapeHtml(data.summary)}</div>`;
  }

  // 1. Search & Browse / Navigation Action
  if (data.action === "search_summary" || data.action === "open_url") {
    if (data.url) {
      chrome.runtime.sendMessage({ type: "NAVIGATE_TAB", url: data.url });
    }
  }

  // 2. Request Autofill Permission Card
  if (data.action === "request_autofill_permission") {
    const autofillCardId = "autofill-btn-" + Date.now();
    cardHtml += `
      <div class="permission-card">
        <div style="font-size:11px; color:#60a5fa; font-weight:600;">🔒 PRIVACY PERMISSION REQUEST</div>
        <div style="margin-top:4px; font-size:12px;">Allow Aiva Nex Agent to autofill form details from your on-device local storage?</div>
        <div class="permission-actions">
          <button class="btn-action-confirm" id="${autofillCardId}-confirm">✅ Confirm Autofill</button>
          <button class="btn-action-cancel" id="${autofillCardId}-cancel">❌ Cancel</button>
        </div>
      </div>
    `;

    setTimeout(() => {
      const confirmBtn = document.getElementById(`${autofillCardId}-confirm`);
      const cancelBtn = document.getElementById(`${autofillCardId}-cancel`);

      if (confirmBtn) {
        confirmBtn.addEventListener("click", async () => {
          confirmBtn.disabled = true;

          const address = await getSavedAddress();
          if (!address) {
            askToRememberFact({
              category: PROFILE_FIELD_CATEGORIES.address,
              label: "delivery address",
              boundTabId,
              onResolved: ({ value, persisted }) => {
                if (persisted) {
                  executeAutofillWithProfile(boundTabId);
                } else {
                  // "Use once" never gets written to memory, so a fresh
                  // autofill pass would flag this same field as unresolved
                  // again - skip re-asking about it for the rest of this
                  // confirm-flow (it's already been filled once, on purpose).
                  const skip = new Set([PROFILE_FIELD_CATEGORIES.address]);
                  withVerifiedTab(boundTabId, (tabId) => {
                    chrome.tabs.sendMessage(
                      tabId,
                      { type: "EXECUTE_ACTION", action: { action: "fill_single_field", category: PROFILE_FIELD_CATEGORIES.address, value } },
                      () => executeAutofillWithProfile(boundTabId, skip)
                    );
                  });
                }
              },
            });
            return;
          }

          executeAutofillWithProfile(boundTabId);
        });
      }
      if (cancelBtn) {
        cancelBtn.addEventListener("click", () => {
          addCard("Autofill cancelled by user.", "chat-msg-agent");
        });
      }
    }, 50);
  }

  // 3. Request Order Confirmation Card
  if (data.action === "request_order_confirmation") {
    const orderCardId = "order-btn-" + Date.now();
    const summary = data.order_summary || {};
    const savedAddress = await getSavedAddress();
    cardHtml += `
      <div class="permission-card" style="border-color:#22c55e;">
        <div style="font-size:11px; color:#4ade80; font-weight:600;">🛍️ FINAL ORDER CONFIRMATION</div>
        <div class="order-summary-box">
          <div><strong>Item:</strong> ${escapeHtml(summary.item || "Apple iPhone 17 (128GB)")}</div>
          <div><strong>Price:</strong> ${escapeHtml(summary.price || "₹74,999")}</div>
          <div><strong>Address:</strong> ${escapeHtml(savedAddress || "(no address saved locally)")}</div>
          <div><strong>Delivery:</strong> ${escapeHtml(summary.delivery || "Express Delivery (2-3 days)")}</div>
        </div>
        <div class="permission-actions">
          <button class="btn-action-confirm" id="${orderCardId}-confirm" style="background:#16a34a;">🛒 Confirm & Place Order</button>
          <button class="btn-action-cancel" id="${orderCardId}-cancel">❌ Cancel</button>
        </div>
      </div>
    `;

    setTimeout(() => {
      const confirmBtn = document.getElementById(`${orderCardId}-confirm`);
      const cancelBtn = document.getElementById(`${orderCardId}-cancel`);

      if (confirmBtn) {
        confirmBtn.addEventListener("click", () => {
          confirmBtn.disabled = true;
          withVerifiedTab(boundTabId, (tabId) => {
            chrome.tabs.sendMessage(tabId, { type: "EXECUTE_ACTION", action: { action: "place_order" } }, (res) => {
              if (chrome.runtime.lastError || !res || !res.ok) {
                addCard(`⚠️ ${(res && res.error) || "Could not place the order."}`, "chat-msg-agent");
                confirmBtn.disabled = false;
                return;
              }
              addCard("🎉 Order placed successfully! Check your demo page for confirmation.", "chat-msg-agent");
            });
          });
        });
      }
      if (cancelBtn) {
        cancelBtn.addEventListener("click", () => {
          addCard("Order placement cancelled.", "chat-msg-agent");
        });
      }
    }, 50);
  }

  // 4. Scroll / Summarize Direct Execution
  if (data.action === "scroll") {
    withVerifiedTab(boundTabId, (tabId) => {
      chrome.tabs.sendMessage(tabId, { type: "EXECUTE_ACTION", action: { action: "scroll", direction: data.direction } });
    });
  }

  if (data.action === "summarize") {
    withVerifiedTab(boundTabId, (tabId) => {
      chrome.tabs.sendMessage(tabId, { type: "EXECUTE_ACTION", action: { action: "summarize", summary: data.summary } });
    });
  }

  // 5. Open URL Navigation Action
  if ((data.action === "open_url" || data.url) && data.url) {
    chrome.runtime.sendMessage({ type: "NAVIGATE_TAB", url: data.url });
  }

  // 6. Interactive Suggested Action Buttons
  if (data.suggested_actions && data.suggested_actions.length) {
    const sugGroupId = "sug-group-" + Date.now();
    let btns = `<div class="quick-chips" style="margin-top:8px;" id="${sugGroupId}">`;
    data.suggested_actions.forEach((act) => {
      btns += `<button type="button" class="chip-btn" data-query="${escapeHtml(act.query)}">${escapeHtml(act.label)}</button>`;
    });
    btns += `</div>`;
    cardHtml += btns;

    setTimeout(() => {
      const container = document.getElementById(sugGroupId);
      if (container) {
        container.querySelectorAll(".chip-btn").forEach((btn) => {
          btn.addEventListener("click", () => {
            handleSendChat(btn.dataset.query);
          });
        });
      }
    }, 50);
  }

  addCard(cardHtml, "chat-msg-agent");
}

// ---------------------------------------------------------------------
// Generalized "ask to remember" flow (docs/MEMORY-ARCHITECTURE-PLAN.md §8.1)
// - replaces the old promptMissingLocation(), which only ever handled the
// address field. Fires for any recognized field content.js's autofill pass
// couldn't resolve from memory. Always offers Use once / Remember / Cancel,
// never just a single "save and continue" button - a Sensitive-tier value
// (plan §6) gets a second, distinct confirmation before Remember actually
// persists it.
// ---------------------------------------------------------------------
function askToRememberFact(opts) {
  const { category, label, boundTabId, onResolved } = opts;
  const cardId = "ask-fact-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
  const html = `
    <div class="permission-card" style="border-color:#f59e0b;">
      <div style="font-size:11px; color:#f59e0b; font-weight:600;">📍 ${escapeHtml(label.toUpperCase())} NEEDED</div>
      <div style="margin-top:4px; font-size:12px;">This page is asking for your ${escapeHtml(label)}. I don't have that saved yet. Want to provide it?</div>
      <textarea id="${cardId}-input" style="width:100%; margin-top:6px; background:#000; border:1px solid #374151; color:#fff; padding:6px; border-radius:6px;" placeholder="${escapeHtml(label)}"></textarea>
      <div class="permission-actions">
        <button class="btn-action-confirm" id="${cardId}-once">Use once</button>
        <button class="btn-action-confirm" id="${cardId}-remember" style="background:#2563eb;">Remember</button>
        <button class="btn-action-cancel" id="${cardId}-cancel">❌ Cancel</button>
      </div>
    </div>
  `;
  addCard(html, "chat-msg-agent");

  setTimeout(() => {
    const inputEl = document.getElementById(`${cardId}-input`);
    const onceBtn = document.getElementById(`${cardId}-once`);
    const rememberBtn = document.getElementById(`${cardId}-remember`);
    const cancelBtn = document.getElementById(`${cardId}-cancel`);
    const disableAll = () => {
      if (onceBtn) onceBtn.disabled = true;
      if (rememberBtn) rememberBtn.disabled = true;
      if (cancelBtn) cancelBtn.disabled = true;
    };

    if (onceBtn) {
      onceBtn.addEventListener("click", () => {
        const value = inputEl.value.trim();
        if (!value) return;
        disableAll();
        addCard(`Using your ${escapeHtml(label)} for this action only - not saved.`, "chat-msg-agent");
        onResolved({ value, persisted: false });
      });
    }

    if (rememberBtn) {
      rememberBtn.addEventListener("click", async () => {
        const value = inputEl.value.trim();
        if (!value) return;
        disableAll();

        let classification;
        try {
          classification = AivaMemory.classifySensitivity(category, value);
        } catch (err) {
          // never_store - the agent refuses and explains why (plan §6),
          // never silently drops it or falls back to "use once" on its own.
          addCard(`⚠️ ${err.message}`, "chat-msg-agent");
          return;
        }

        const persist = async () => {
          try {
            await AivaMemory.createFact({ category, key: category.split(".").pop(), value, source: "user_explicit" });
            addCard(`Saved your ${escapeHtml(label)} locally. I'll reuse it next time.`, "chat-msg-agent");
            onResolved({ value, persisted: true });
          } catch (err) {
            addCard(`⚠️ ${err.message}`, "chat-msg-agent");
          }
        };

        if (classification.level !== "sensitive") {
          await persist();
          return;
        }

        // Sensitive tier: a single click must not be enough to persist this
        // (plan §6) - a distinct second confirmation, not the standard one.
        const strongId = cardId + "-strong";
        addCard(
          `
          <div class="permission-card" style="border-color:#b45309;">
            <div style="font-size:11px; color:#f59e0b; font-weight:600;">⚠️ THIS IS SENSITIVE INFORMATION</div>
            <div style="margin-top:4px; font-size:12px;">Storing your ${escapeHtml(
              label
            )} means it will be available to autofill on any site you use this agent on. Store it anyway?</div>
            <div class="permission-actions">
              <button class="btn-action-confirm" id="${strongId}-yes" style="background:#b45309;">Yes, store it</button>
              <button class="btn-action-cancel" id="${strongId}-no">Use once only</button>
            </div>
          </div>
        `,
          "chat-msg-agent"
        );
        setTimeout(() => {
          const yesBtn = document.getElementById(`${strongId}-yes`);
          const noBtn = document.getElementById(`${strongId}-no`);
          if (yesBtn) {
            yesBtn.addEventListener("click", () => {
              yesBtn.disabled = true;
              if (noBtn) noBtn.disabled = true;
              persist();
            });
          }
          if (noBtn) {
            noBtn.addEventListener("click", () => {
              if (yesBtn) yesBtn.disabled = true;
              noBtn.disabled = true;
              addCard(`Using your ${escapeHtml(label)} for this action only - not saved.`, "chat-msg-agent");
              onResolved({ value, persisted: false });
            });
          }
        }, 50);
      });
    }

    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => {
        disableAll();
        addCard("Cancelled.", "chat-msg-agent");
      });
    }
  }, 50);
}

/**
 * Offers askToRememberFact() for one unresolved field at a time - asking
 * about several fields simultaneously would be exactly the "overwhelm the
 * user" pattern the plan explicitly avoids (progressive disclosure, §3).
 * `skip` holds categories already handled ("use once") earlier in this same
 * confirm-flow, so a value that deliberately wasn't persisted isn't
 * re-requested every time autofill runs again in this chain.
 */
function offerToResolveMissingFields(unresolvedFields, boundTabId, skip) {
  const skipSet = skip || new Set();
  const remaining = (unresolvedFields || []).filter((f) => !skipSet.has(f.category));
  if (!remaining.length) return;

  const next = remaining[0];
  const rest = remaining.slice(1);
  askToRememberFact({
    category: next.category,
    label: next.label,
    boundTabId,
    onResolved: ({ value, persisted }) => {
      if (persisted) {
        // The next autofill pass will find the newly-saved fact itself, and
        // will correctly recompute which fields (if any) are still missing.
        executeAutofillWithProfile(boundTabId, skipSet);
      } else {
        const nextSkip = new Set(skipSet);
        nextSkip.add(next.category);
        withVerifiedTab(boundTabId, (tabId) => {
          chrome.tabs.sendMessage(
            tabId,
            { type: "EXECUTE_ACTION", action: { action: "fill_single_field", category: next.category, value } },
            (res) => {
              if (!res || !res.ok) addCard(`⚠️ ${(res && res.error) || "Could not fill that field."}`, "chat-msg-agent");
              offerToResolveMissingFields(rest, boundTabId, nextSkip);
            }
          );
        });
      }
    },
  });
}

function executeAutofillWithProfile(boundTabId, skip) {
  const skipSet = skip || new Set();
  withVerifiedTab(boundTabId, (tabId) => {
    chrome.tabs.sendMessage(
      tabId,
      { type: "EXECUTE_ACTION", action: { action: "autofill" } },
      (res) => {
        if (chrome.runtime.lastError || !res) {
          addCard("⚠️ Could not autofill this page.", "chat-msg-agent");
          return;
        }
        if (res.ok) {
          addCard(
            `✅ ${res.message}<br/><em style="font-size:11px; color:#86efac;">(All data remained strictly in your local browser storage)</em>`,
            "chat-msg-agent"
          );
        } else {
          addCard(`⚠️ ${res.error || "Could not autofill this page."}`, "chat-msg-agent");
        }
        offerToResolveMissingFields(res.unresolvedFields, boundTabId, skipSet);
      }
    );
  });
}

// ---------------------------------------------------------------------
// Event Listeners for Chat Input & Quick Prompt Chips
// ---------------------------------------------------------------------
el.sendChatBtn.addEventListener("click", () => handleSendChat());
el.chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") handleSendChat();
});

if (el.promptChips) {
  el.promptChips.querySelectorAll(".chip-btn").forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.dataset.query;
      if (q) handleSendChat(q);
    });
  });
}

// ---------------------------------------------------------------------
// Initial Setup
// ---------------------------------------------------------------------
checkServer();
loadModels();
loadLocalProfile();
updatePrimaryButton();
getActiveTab(() => {});

// ---------------------------------------------------------------------
// Feedback Logic
// ---------------------------------------------------------------------
let selectedRating = null;

function toggleFeedback() {
  el.feedbackSection.classList.toggle("hidden");
  el.profileSection.classList.add("hidden");
}
el.feedbackToggleBtn.addEventListener("click", toggleFeedback);

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
    } catch (e) {}

    const payload = {
      message,
      category: el.feedbackCategory.value,
      rating: selectedRating,
      source: "aiva-nex-agent",
      pageUrl: pageDomain,
      appVersion: chrome.runtime.getManifest().version,
      platform: navigator.userAgent.slice(0, 80),
    };

    chrome.runtime.sendMessage({ type: "SEND_FEEDBACK", payload }, (res) => {
      el.submitFeedbackBtn.disabled = false;
      if (chrome.runtime.lastError || !res || !res.ok) {
        setFeedbackStatus((res && res.error) || "Could not send feedback.", "error");
        return;
      }
      setFeedbackStatus("Thanks! Feedback sent.", "ok");
      el.feedbackMessage.value = "";
      selectedRating = null;
      renderStars();
    });
  });
});
