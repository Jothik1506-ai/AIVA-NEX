# AIVA-NEX vs browser agents (as of 2026-09-28)

**Legend:** Yes / No / Partial. *(?)* = uncertain or not publicly documented. The AIVA-NEX column is checked against the code on branch `dev`. The other columns come from the web sources listed below.

| Product | Runs locally / on-device | PII redaction before the model | Needs GPU / cloud | Open source | Indian ID checksum awareness | Cost |
|---|---|---|---|---|---|---|
| **AIVA-NEX** | **Yes**: extension + server on 127.0.0.1, local LLM (Ollama/LM Studio) or rule engine. Nothing leaves the device **with the default config** (cloud off, feedback opt-in off). | **Yes**: regex+checksum+NER tokenisation in the browser, black-box pixel masking, server re-check (400 on validated PII) | **No GPU. No cloud** by default. Gemini is optional via `AIVA_ALLOW_CLOUD=1` | **Yes, MIT** (LICENSE in repo) | **Yes**: Aadhaar Verhoeff, card Luhn, PAN format, +91 phone | Free (local compute only) |
| OpenAI ChatGPT agent (ex-Operator) | No, cloud VM browser | Not documented *(?)* | Cloud | No | Not documented | Operator shut down 31 Aug 2025. ChatGPT agent was reportedly removed in Aug 2026 *(?, single source)*. ChatGPT plans run Free to $200/mo |
| Claude in Chrome (Anthropic) | Extension runs in the user's Chrome, but the model is in the cloud | Not documented *(?)* | Cloud | No | Not documented | Paid plans only (Pro/Max/Team/Enterprise). GA on 26 Aug 2026 |
| Perplexity Comet | Browser installed locally, AI runs in the cloud | Not documented *(?)* | Cloud | No (Chromium-based, closed) | Not documented | Free with small agent credits. Pro $20/mo, Max $200/mo |
| Browser Use | **Can be local** (Python library + Ollama) | No built-in PII layer *(?)* | Local LLM on your hardware, or a cloud LLM / Browser Use Cloud | **Yes, MIT** | No *(?)* | Library is free. Cloud and LLM APIs are pay-per-use |
| Google Project Mariner (optional) | No, cloud | Not documented | Cloud | No | Not documented | Was $249.99/mo (AI Ultra). **Shut down 4 May 2026** and folded into Gemini Agent |

**Honest caveats for the slide**
- AIVA-NEX picks one action per step (click/focus/scroll/summarize). The competitors run long multi-step tasks.
- "No PII redaction" for the competitors means we found no public documentation of redaction before the model. It does not mean we verified that none exists.

**Sources**
- [OpenAI Operator (Wikipedia)](https://en.wikipedia.org/wiki/OpenAI_Operator)
- [ChatGPT pricing 2026 (opslyft)](https://www.opslyft.com/blog/chatgpt-pricing-2026)
- [Claude in Chrome GA (GIGAZINE)](https://gigazine.net/gsc_news/en/20260827-claude-chrome-available/)
- [Claude Chrome plugin for paid users (itechguides)](https://www.itechguides.com/claudes-chrome-plugin-is-now-available-to-all-paid-users-what-pro-max-team-and-enterprise-get/)
- [Comet pricing (eesel)](https://www.eesel.ai/blog/perplexity-comet-pricing)
- [Comet free (TechCrunch)](https://techcrunch.com/2025/10/02/perplexitys-comet-ai-browser-now-free-max-users-get-new-background-assistant/)
- [browser-use GitHub](https://github.com/browser-use/browser-use)
- [Browser Use supported models](https://docs.browser-use.com/open-source/supported-models)
- [Mariner shutdown (GIGAZINE)](https://gigazine.net/gsc_news/en/20260507-google-shuts-down-project-mariner/)
- [Mariner shutdown (TechSpot)](https://www.techspot.com/news/112334-project-mariner-dead-but-google-browser-controlling-ai.html)
