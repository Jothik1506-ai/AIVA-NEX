# Aiva Nex Agent — Idea Description

**Problem Statement ID:** SIH26171
**Problem Statement Title:** On-device Visual Perception for Light-weight Browser Agents
**Organization:** Indian Space Research Organisation (ISRO)
**Theme:** Smart Automation
**PS Category:** Software
**Team Name:** Aiva Nex

## Idea Title

Aiva Nex Agent — a Privacy-Preserving Browser AI Agent.

## Proposed Solution

Aiva Nex Agent is a Chrome extension (Manifest V3) paired with a local FastAPI server, acting as an AI agent inside the browser. It implements on-device screen perception via structural DOM analysis — a lightweight, fast alternative to pixel-level vision for structured web forms — detecting sensitive fields such as passwords, OTPs, Aadhaar/PAN-like IDs, card numbers, email, phone, name, address, and hidden tokens entirely on-device. Every sensitive value is tokenized locally (for example, `EMAIL_1`, `PERSON_1`, `PASSWORD_FIELD`) before anything leaves the browser, so no raw PII or screenshot ever leaves the device.

A sanitized "screen graph" — page title, domain only, form structure, field labels/types, tokenized values, and approximate positions — is sent to a local server. The server independently re-scans the payload for PII shapes as a defense-in-depth backstop and rejects anything that looks raw. It then decides one browser action (click / focus / scroll / summarize), using a real local LLM (Ollama, LM Studio, or any OpenAI-compatible API) when available, and falling back to a deterministic rule engine when not. The extension executes that action back on the real page — for example, focusing the OTP field, clicking "Upload Certificate," or summarizing the page.

### How It Addresses the Problem

Aiva Nex Agent solves the core tension in AI browser agents: usefulness requires understanding page content, but sending raw page data — especially forms containing PII — to any server is a privacy and compliance risk. It gets the benefit of page understanding and automated action without ever transmitting sensitive data.

### Innovation and Uniqueness

The solution is client-side-first: detection, classification, tokenization, and a visual redaction overlay all happen before data leaves the browser. It applies defense-in-depth, with the server independently re-verifying that no PII leaked through rather than simply trusting the client. Its hybrid decision engine uses a real local LLM when present but never breaks, with an automatic fallback to rules that keeps the agent reliable even with zero AI installed. The entire system is fully local and offline-capable, with no cloud dependency required for its core privacy guarantee.

## Technical Approach

On the frontend, the extension is built with Chrome Extension Manifest V3 in plain JavaScript, with no build step or framework. The backend runs on Python 3.11+, using FastAPI and Pydantic. Local AI is optional: any OpenAI-compatible local LLM server works, including Ollama (llama3.2:1b, qwen2.5:0.5b), LM Studio, or Jan, called via the Python standard library's `urllib` with no extra dependency. Detection is implemented as on-device screen perception via regex pattern matching combined with DOM label/placeholder heuristics — a lightweight, structural alternative to pixel-level vision models that needs no heavy ML libraries server-side.

**Methodology / flow:** The content script scans the DOM, detects sensitive fields, and tokenizes and redacts them locally. The sanitized "screen graph" JSON is then sent via a background worker to `POST /analyze`. The server re-validates the payload for raw PII, then decides one action — trying the local LLM first, with a rule-engine fallback. Finally, the extension executes the action back on the live page.

## Feasibility and Viability

A fully working prototype has already been built and tested end-to-end (scan → redact → send → decide → execute) on a real demo form. It needs no heavy infrastructure, running entirely on localhost with no cloud costs and no GPU required, and uses only mature, well-supported technology (Chrome Extensions API, FastAPI, optional local LLM runtimes).

**Potential challenges and risks:** Name/address detection is a label-based heuristic rather than true Named Entity Recognition, so it may miss unlabeled free-text PII. Small local LLMs (0.5B–1B parameters) can be slow or occasionally produce malformed output. Pattern-based PII checks for Aadhaar, PAN, and card numbers are shape-based rather than checksum-validated. The local server currently has no authentication, which is acceptable for a local prototype but would need hardening for production.

**Mitigation strategies:** A rule-based fallback guarantees the agent never breaks even if the LLM fails or is absent. Server-side re-validation, as a defense-in-depth measure, catches any client-side redaction gaps. The roadmap includes upgrading to a lightweight local vision model for pixel-level screen understanding, adding real NER, checksum validation, and authentication for non-local deployment.

## Impact and Benefits

**Target audience:** Any internet user filling out sensitive forms online — scholarship, job, or government portals, banking, or healthcare — as well as organizations that want AI browser automation without compliance risk.

**Impact:** Aiva Nex Agent enables AI-assisted browsing and form-filling without the privacy trade-off users currently accept with cloud-based AI agents, and is directly relevant to India's data protection goals under the DPDP Act, since no PII is transmitted or stored off-device.

**Benefits:**
- **Social:** Builds public trust in AI agents handling personal and government data such as Aadhaar, PAN, and scholarship forms.
- **Economic:** Reduces liability and compliance cost for organizations deploying browser AI agents.
- **Environmental/technical:** A lightweight, local-first design reduces reliance on constant cloud inference calls.
- **Security:** Reduces the attack surface, since there is no sensitive data in transit or in server logs to breach.

## Research and References

- Chrome Extensions Manifest V3 documentation — developer.chrome.com/docs/extensions
- FastAPI documentation — fastapi.tiangolo.com
- Ollama (local LLM runtime) — ollama.com
- OpenAI `/v1/chat/completions` API spec (compatibility target for local LLM integration)
- India's Digital Personal Data Protection (DPDP) Act, 2023 — motivation for the privacy-by-design approach
- Project repository: github.com/Jothik1506-ai/AIVA-NEX

## Tech Stack

- **Browser Extension:** Chrome Extension Manifest V3, plain JavaScript (no build step, no framework)
- **Backend Server:** Python 3.11+, FastAPI, Pydantic
- **Local AI / LLM:** Ollama (llama3.2:1b, qwen2.5:0.5b), LM Studio, or any OpenAI-compatible local server, accessed via Python's `urllib`
- **Cloud LLM Fallback (optional):** Google Gemini API (free tier), used only if no local model is reachable
- **Detection Engine:** Regex pattern matching + DOM label/placeholder heuristics (on-device structural screen perception)
- **Decision Engine:** Hybrid — local/cloud LLM first, deterministic rule-based engine as fallback
- **Data Format:** JSON (sanitized "screen graph" exchanged between extension and server)
- **Networking:** REST-style HTTP (`POST /analyze`, `POST /chat`) between the extension's background worker and the local FastAPI server
- **Version Control / Hosting:** Git, GitHub (github.com/Jothik1506-ai/AIVA-NEX)
