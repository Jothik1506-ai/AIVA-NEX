# AIVA-NEX: target users

The privacy claims below hold for the **default config** (`AIVA_ALLOW_CLOUD=0`, feedback opt-in off). With those defaults the extension talks only to `127.0.0.1:8000`, and the server talks only to a loopback LLM or falls back to its rule engine.

| # | Segment | Pain point | What AIVA-NEX gives them |
|---|---|---|---|
| 1 | Government / ISRO staff on sensitive internal portals | Cloud browser agents would send screenshots and form data to third-party servers, which policy does not allow. | Page understanding (DOM + masked screenshot, OCR) runs on the local machine on CPU. PII is tokenised in the browser and re-checked on the server, and pixels are blacked out before the JPEG is encoded. |
| 2 | Citizens filling Indian e-gov forms (scholarships, PAN, Aadhaar-linked services) | Their Aadhaar, PAN, phone number and address sit on the screen while they need help navigating the form. | Aadhaar (Verhoeff), card (Luhn), PAN, +91 phone and email checks, plus Indian name/address rules (S/o, H.No, PIN, Nagar...). A payload that still carries a validated ID is rejected with a 400. |
| 3 | Enterprises / PSUs with data-residency or DPDP-style rules | They can't approve SaaS agents that process customer data offshore. | Self-hosted: a local FastAPI server with a token, CORS lock and body caps. Cloud (Gemini) is off unless an admin sets `AIVA_ALLOW_CLOUD=1` and a key. The image is never stored and logs contain no bodies or tokens. |
| 4 | Low-resource or offline users (no GPU, patchy internet) | Vision agents usually need a GPU or a paid cloud API. | CPU only, with ~15 MB of OCR models (ONNX) and ~12 MB of spaCy. Measured at ~0.4-0.9 s per form on a Ryzen 7 7730U laptop. If no local LLM is running, the rule engine still picks an action. |
| 5 | Accessibility / assisted-browsing users (optional) | They need help finding and clicking controls on cluttered pages. | Visual mode labels buttons, inputs and links from pixels and highlights the chosen element with a purple outline before it acts. |
