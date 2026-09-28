"""Rule-engine fallback for /analyze (main.decide_action_rules).

Graphs here use the field names extension/content.js actually sends:
inputs {ref, type, label, required, isSensitive, sanitizedValue}, buttons {ref, text}.
"""
from main import decide_action_rules


def _inp(ref, label, value="", *, type="text", required=True, sensitive=None, **extra):
    if sensitive is None:
        sensitive = bool(value) and value.isupper()
    return {"ref": ref, "type": type, "label": label, "required": required,
            "isSensitive": sensitive, "sanitizedValue": value, **extra}


def _graph(inputs=(), buttons=(), snippets=(), headings=(), **extra):
    return {"pageTitle": "T", "inputs": list(inputs), "textSnippets": list(snippets),
            "headings": list(headings),
            "buttons": [{"ref": f"button-{i}", "text": t} for i, t in enumerate(buttons)], **extra}


# --- 1. sensitive fields use the extension's real field names -------------

def test_focuses_password_field_tokenised_by_content_js():
    g = _graph([_inp("input-0", "Email", "EMAIL_1"),
                _inp("input-1", "Password", "PASSWORD_FIELD", type="password", required=False)],
               ["Sign in"])
    assert decide_action_rules(g) == {**decide_action_rules(g), "action": "focus", "targetRef": "input-1"}


def test_focuses_otp_field_from_token_or_categories():
    g = _graph([_inp("input-0", "Mobile", "PHONE_1"),
                _inp("input-1", "Code", "OTP_FIELD", required=False)], ["Verify"])
    assert decide_action_rules(g)["targetRef"] == "input-1"
    g2 = _graph([_inp("input-0", "Code", "[REDACTED]", required=False, categories={"OTP_FIELD": 1})],
                ["Verify"])
    # categories marks it as a credential, but a non-token value means it is filled
    assert decide_action_rules(g2)["action"] == "click"
    g3 = _graph([_inp("input-0", "Code", "OTP_FIELD_2", required=False, categories={"OTP_FIELD": 1})],
                ["Verify"])
    assert decide_action_rules(g3)["targetRef"] == "input-0"


def test_empty_cvv_or_pin_is_focused_before_pay():
    g = _graph([_inp("input-0", "Card number", "[REDACTED]"),
                _inp("input-1", "CVV", "", type="password")], ["Pay"])
    assert decide_action_rules(g)["targetRef"] == "input-1"


def test_legacy_sensitive_type_still_honoured():
    g = _graph([{"ref": "input-0", "label": "x", "sensitiveType": "OTP", "sanitizedValue": "OTP_FIELD"}],
               ["Submit"])
    assert decide_action_rules(g)["targetRef"] == "input-0"


def test_filled_password_does_not_block_login_click():
    g = _graph([_inp("input-0", "Username", "admin"),
                _inp("input-1", "Password", "[REDACTED]", type="password")], ["Log in"])
    act = decide_action_rules(g)
    assert act["action"] == "click" and act["targetRef"] == "button-0"


def test_has_value_flag_overrides_token_ambiguity():
    g = _graph([_inp("input-0", "Password", "PASSWORD_FIELD", type="password", hasValue=True)], ["Log in"])
    assert decide_action_rules(g)["action"] == "click"
    g["inputs"][0]["hasValue"] = False
    assert decide_action_rules(g)["action"] == "focus"


def test_hidden_fields_are_never_focused():
    g = _graph([_inp("input-0", "csrf", "HIDDEN_FIELD", type="hidden"),
                _inp("input-1", "", "", type="hidden")], ["Continue"])
    assert decide_action_rules(g)["action"] == "click"


# --- 2. required empty fields come before Submit --------------------------

def test_focuses_first_empty_required_field_instead_of_submit():
    g = _graph([_inp("input-0", "Full name", "PERSON_1"),
                _inp("input-1", "Date of birth", "1990-01-01"),
                _inp("input-2", "Registration Number", ""),
                _inp("input-3", "Referral", "")], ["Submit"])
    act = decide_action_rules(g)
    assert act["action"] == "focus" and act["targetRef"] == "input-2"


def test_optional_empty_field_does_not_block_submit():
    g = _graph([_inp("input-0", "Amount", "500"), _inp("input-1", "Remarks", "", required=False)],
               ["Pay now", "Back"])
    act = decide_action_rules(g)
    assert act["action"] == "click" and act["targetRef"] == "button-0"


# --- 3. login-style button labels ------------------------------------------

def test_matches_login_variants():
    for label in ["Log in", "Log In", "Login", "Sign in", "Sign In", "Signin", "LOGIN"]:
        g = _graph([_inp("input-0", "Username", "admin")], [label])
        act = decide_action_rules(g)
        assert act["action"] == "click", label


def test_secondary_buttons_are_skipped():
    g = _graph([], ["Forgot password?", "Cancel", "Continue shopping", "Sign in"])
    assert decide_action_rules(g)["targetRef"] == "button-3"
    assert decide_action_rules(_graph([], ["Download PDF", "Print ticket", "Refresh"]))["action"] == "summarize"


def test_word_boundaries_in_button_match():
    # "context" contains "next", "repay" contains "pay"; neither is a primary action
    assert decide_action_rules(_graph([], ["Context menu", "Repayment info"]))["action"] == "summarize"


# --- 4. scroll ----------------------------------------------------------------

def test_scrolls_when_page_says_more_content_follows():
    for text in ["Showing 1-20 of 2,431 results.", "Scroll for ball-by-ball commentary.",
                 "Continue reading below.", "Load more"]:
        act = decide_action_rules(_graph(snippets=["Intro text here", text]))
        assert act == {**act, "action": "scroll", "direction": "down"}, text


def test_scroll_from_viewport_geometry_when_present():
    g = _graph(snippets=["Plain article"], viewport={"height": 800, "scrollY": 0, "scrollHeight": 4000})
    assert decide_action_rules(g)["action"] == "scroll"
    g["viewport"]["scrollHeight"] = 820
    assert decide_action_rules(g)["action"] == "summarize"


def test_actionable_element_beats_scroll():
    g = _graph([], ["Add to cart"], snippets=["Scroll down for reviews"])
    assert decide_action_rules(g)["action"] == "click"


def test_plain_page_summarizes():
    act = decide_action_rules(_graph(snippets=["The next launch window opens in November."]))
    assert act["action"] == "summarize" and "summary" in act


def test_scroll_action_passes_api_validation(client, auth):
    g = _graph(snippets=["Showing 1-20 of 90 results"])
    r = client.post("/analyze", json=g, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "scroll" and r.json()["direction"] == "down"
