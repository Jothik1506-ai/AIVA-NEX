"""
Generates bench/dataset.json - a SYNTHETIC, SELF-LABELLED benchmark set.

Every number here is a documented test value (UIDAI test Aadhaar, public
Luhn test cards) or is generated on the fly (Verhoeff/Luhn check digits
computed below). Names, addresses, e-mails and phones are invented.
Run:  server\\.venv\\Scripts\\python bench\\gen_dataset.py
"""

import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
from pii_checks import verhoeff_check_digit, verhoeff_valid  # noqa: E402

rng = random.Random(26171)


def luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def aadhaar():
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    n = body + verhoeff_check_digit(body)
    return f"{n[:4]} {n[4:8]} {n[8:]}"


def bad_aadhaar():
    """12 digits, Aadhaar shape, wrong Verhoeff check digit (hard negative)."""
    while True:
        n = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(11))
        if not verhoeff_valid(n):
            return f"{n[:4]} {n[4:8]} {n[8:]}"


def order_id():
    """12-digit order id that is NOT Verhoeff-valid (hard negative)."""
    return bad_aadhaar().replace(" ", "")


def card(prefix="4"):
    body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(15 - len(prefix)))
    n = body + luhn_digit(body)
    return " ".join(n[i:i + 4] for i in range(0, 16, 4))


def bad_card():
    c = card().replace(" ", "")
    last = str((int(c[-1]) + 1) % 10)
    c = c[:-1] + last
    return " ".join(c[i:i + 4] for i in range(0, 16, 4))


# ---------------------------------------------------------------------------
# Page builders. Each item: {kind, text, entities:[{value,type}], ...}
# kind = heading | snippet | input ; inputs also carry label/type/required.
# ---------------------------------------------------------------------------

def S(text, *ents):
    return {"kind": "snippet", "text": text, "entities": [{"value": v, "type": t} for v, t in ents]}


def H(text, *ents):
    return {"kind": "heading", "text": text, "entities": [{"value": v, "type": t} for v, t in ents]}


def I(label, value="", etype=None, itype="text", required=True):
    ents = [{"value": value, "type": etype}] if value and etype else []
    return {"kind": "input", "label": label, "inputType": itype, "required": required,
            "text": value, "entities": ents}


def page(pid, category, title, items, buttons, expected, url="https://example.test/"):
    return {"id": pid, "category": category, "title": title, "url": url,
            "items": items, "buttons": buttons, "expected": expected}


# expected: {"action": ..., "target": <input label | button text | None>}
def focus(label):
    return {"action": "focus", "target": label}


def click(text):
    return {"action": "click", "target": text}


SUMMARY = {"action": "summarize", "target": None}
SCROLL = {"action": "scroll", "target": None}

TEST_AADHAAR = "9999 4105 7058"  # UIDAI published test number
CARDS = ["4111 1111 1111 1111", "5555 5555 5555 4444", "4012 8888 8888 1881"]

pages = []

# --- KYC ---------------------------------------------------------------------
pages.append(page("kyc-01", "kyc", "e-KYC Verification", [
    H("Complete your e-KYC"),
    I("Full Name", "Ravi Kumar Sharma", "NAME"),
    I("Aadhaar Number", TEST_AADHAAR, "AADHAAR"),
    I("PAN", "ABCPS1234K", "PAN"),
    I("Mobile Number", "98765 43210", "PHONE"),
    I("Email", ""),
], ["Verify", "Cancel"], focus("Email")))

pages.append(page("kyc-02", "kyc", "Video KYC - Details", [
    S("Applicant: Smt. Lakshmi Devi, D/O Venkata Rao", ("Lakshmi Devi", "NAME"), ("Venkata Rao", "NAME")),
    S("Residential address: Flat 4B, Sai Residency, Ameerpet Road, Hyderabad, Telangana 500016",
      ("Flat 4B, Sai Residency, Ameerpet Road, Hyderabad, Telangana 500016", "ADDRESS")),
    I("Aadhaar Number", aadhaar(), "AADHAAR"),
    I("PAN Number", "BNZPM2501F", "PAN"),
], ["Continue"], click("Continue")))

pages.append(page("kyc-03", "kyc", "Update KYC", [
    I("Name as per PAN", "Priya Nair", "NAME"),
    I("PAN", "AAACR5055K", "PAN"),
    I("Date of Birth", "1990-01-01", None, "date"),
    I("OTP sent to your mobile", "", None, "text"),
], ["Submit"], focus("OTP sent to your mobile")))

pages.append(page("kyc-04", "kyc", "Aadhaar Seeding", [
    S("Enter the 12-digit Aadhaar printed on your card, e.g. XXXX XXXX 1234."),
    I("Aadhaar", aadhaar(), "AADHAAR"),
    I("Confirm Aadhaar", ""),
], ["Proceed"], focus("Confirm Aadhaar")))

pages.append(page("kyc-05", "kyc", "KYC Status", [
    S(f"KYC reference 20{rng.randint(10**9, 10**10 - 1)} has been approved."),
    S(f"Your Aadhaar ending in {TEST_AADHAAR[-4:]} is linked."),
    S("Contact support at kyc.help@example.com for queries.", ("kyc.help@example.com", "EMAIL")),
], ["Download acknowledgement"], SUMMARY))

# --- Bank --------------------------------------------------------------------
pages.append(page("bank-01", "bank", "Open Savings Account", [
    I("Applicant Name", "Arjun Reddy", "NAME"),
    I("Mobile", "+91 91234 56789", "PHONE"),
    I("Email Address", "arjun.reddy@example.in", "EMAIL"),
    I("Communication Address", "H.No 12-3-45, Gandhi Nagar, Hyderabad 500080", "ADDRESS"),
    I("PAN", "", None),
], ["Next", "Save draft"], focus("PAN")))

pages.append(page("bank-02", "bank", "Fund Transfer", [
    S("Beneficiary: Meera Iyer", ("Meera Iyer", "NAME")),
    S(f"Transaction ID {bad_card().replace(' ', '')} initiated for Rs. 25,000."),
    I("Amount", "25000", None),
    I("Remarks", "Rent for March", None, required=False),
], ["Pay now", "Back"], click("Pay now")))

pages.append(page("bank-03", "bank", "Net Banking Login", [
    I("Customer ID", "", None),
    I("Password", "", None, "password"),
], ["Login"], focus("Customer ID")))

pages.append(page("bank-04", "bank", "Account Statement", [
    H("Statement for A/c XXXX4521"),
    S(f"UPI/{order_id()}/Swiggy - Rs. 450.00"),
    S(f"NEFT/{order_id()}/Salary credit - Rs. 85,000.00"),
    S("Account holder: Suresh Babu Pillai", ("Suresh Babu Pillai", "NAME")),
], ["Download PDF"], SUMMARY))

pages.append(page("bank-05", "bank", "Add Beneficiary", [
    I("Beneficiary Name", "Kavya Menon", "NAME"),
    I("Account Number", "50100123456789", None),
    I("IFSC", "HDFC0001234", None),
    I("Beneficiary mobile", "87654 32109", "PHONE"),
], ["Add beneficiary"], click("Add beneficiary")))

pages.append(page("bank-06", "bank", "Debit Card Services", [
    S(f"Your card {CARDS[0]} is active.", (CARDS[0], "CARD")),
    S(f"Card {bad_card()} could not be verified."),
    I("Set new PIN", "", None, "password"),
], ["Confirm"], focus("Set new PIN")))

# --- Railway -----------------------------------------------------------------
pages.append(page("rail-01", "railway", "Book Ticket - Passenger Details", [
    S("Train 12627 KARNATAKA EXP | SBC to NDLS | 14-Oct"),
    I("Passenger Name", "Anil Kumar", "NAME"),
    I("Age", "45", None),
    I("Mobile Number", "99887 76655", "PHONE"),
], ["Continue"], click("Continue")))

pages.append(page("rail-02", "railway", "PNR Status", [
    S("PNR 8524613790: CNF / B2 / 34"),
    S("Booked by: Deepak Chaturvedi", ("Deepak Chaturvedi", "NAME")),
    S("Train 22691 departs 20:00 from platform 5."),
], ["Refresh"], SUMMARY))

pages.append(page("rail-03", "railway", "IRCTC Login", [
    I("Username", "", None),
    I("Password", "", None, "password"),
    S("Enter the captcha shown below."),
], ["Sign in"], focus("Username")))

pages.append(page("rail-04", "railway", "Booking Confirmation", [
    S(f"Transaction {order_id()} successful. Amount Rs 1,245.00"),
    S("Ticket sent to anil.k@example.org", ("anil.k@example.org", "EMAIL")),
    S("Passenger 1: Anil Kumar, 45, M", ("Anil Kumar", "NAME")),
], ["Print ticket"], SUMMARY))

pages.append(page("rail-05", "railway", "Tatkal Booking - Payment", [
    I("Card number", CARDS[1], "CARD"),
    I("Name on card", "Rohan Gupta", "NAME"),
    I("CVV", "", None, "password"),
], ["Pay"], focus("CVV")))

# --- Job ---------------------------------------------------------------------
pages.append(page("job-01", "job", "Apply - Software Engineer", [
    I("Full name", "Sneha Kulkarni", "NAME"),
    I("Email", "sneha.k@example.com", "EMAIL"),
    I("Phone", "70123 45678", "PHONE"),
    I("Current address", "22, MG Road, Near City Mall, Pune, Maharashtra 411001", "ADDRESS"),
    I("Resume link", "", None, "url"),
], ["Submit application"], focus("Resume link")))

pages.append(page("job-02", "job", "Candidate Profile", [
    S("Hi, I am Vikram Singh Rathore, a data engineer from Jaipur.", ("Vikram Singh Rathore", "NAME")),
    S("Reach me at +91 98111 22334 or vikram.r@example.net",
      ("+91 98111 22334", "PHONE"), ("vikram.r@example.net", "EMAIL")),
    S("Experience: 6 years in Spark, Kafka and Airflow."),
], ["Edit profile"], SUMMARY))

pages.append(page("job-03", "job", "Government Job Portal - Registration", [
    I("Candidate Name", "Pooja Yadav", "NAME"),
    I("Father's Name", "Ramesh Yadav", "NAME"),
    I("Aadhaar Number", aadhaar(), "AADHAAR"),
    I("Registration Number", "", None),
], ["Register"], focus("Registration Number")))

pages.append(page("job-04", "job", "Job Listings", [
    H("2,431 jobs found for 'Python developer' in Bengaluru"),
    S("Senior Python Developer - 5-8 yrs - Rs 18-25 LPA"),
    S("Backend Engineer - Remote - Posted 2 days ago"),
    S("Showing 1-20 of 2,431 results. Scroll to load more."),
], [], SCROLL))

# --- Login -------------------------------------------------------------------
pages.append(page("login-01", "login", "Sign in", [
    I("Email", "user.test@example.com", "EMAIL"),
    I("Password", "", None, "password"),
], ["Sign in", "Forgot password?"], focus("Password")))

pages.append(page("login-02", "login", "Login with OTP", [
    I("Mobile number", "90000 12345", "PHONE"),
    I("Enter OTP", "", None),
], ["Verify OTP"], focus("Enter OTP")))

pages.append(page("login-03", "login", "DigiLocker Sign In", [
    I("Aadhaar / Mobile", TEST_AADHAAR, "AADHAAR"),
    I("6-digit Security PIN", "", None, "password"),
], ["Sign In"], focus("6-digit Security PIN")))

pages.append(page("login-04", "login", "Admin Console Login", [
    I("Username", "admin", None),
    I("Password", "hunter2hunter2", None, "password"),
], ["Log in"], click("Log in")))

# --- Checkout ----------------------------------------------------------------
pages.append(page("co-01", "checkout", "Checkout - Delivery", [
    I("Full name", "Neha Joshi", "NAME"),
    I("Address line 1", "Flat 302, Green Park Apartments, Sector 21", "ADDRESS"),
    I("City", "Gurugram", "ADDRESS"),
    I("Pincode", "122016", "ADDRESS"),
    I("Phone", "81234 56789", "PHONE"),
], ["Continue to payment"], click("Continue to payment")))

pages.append(page("co-02", "checkout", "Payment", [
    S("Order total: Rs 3,499"),
    I("Card number", CARDS[2], "CARD"),
    I("Expiry", "12/29", None),
    I("CVV", "", None, "password"),
], ["Pay Rs 3,499"], focus("CVV")))

pages.append(page("co-03", "checkout", "Order Placed", [
    S(f"Order #{order_id()} confirmed!"),
    S("Delivering to Neha Joshi, Flat 302, Green Park Apartments, Sector 21, Gurugram 122016",
      ("Neha Joshi", "NAME"), ("Flat 302, Green Park Apartments, Sector 21, Gurugram 122016", "ADDRESS")),
    S("Expected delivery: Thursday"),
], ["Track order", "Continue shopping"], SUMMARY))

pages.append(page("co-04", "checkout", "Saved Cards", [
    S(f"Visa ending 1111 - {card()}", ),
    S("Gift card code 1234 5678 9012 3456 applied."),
], ["Use this card"], click("Use this card")))
# fix labels for co-04: first snippet is a valid generated card
pages[-1]["items"][0]["entities"] = [{"value": pages[-1]["items"][0]["text"].split(" - ")[1], "type": "CARD"}]

pages.append(page("co-05", "checkout", "Apply Coupon", [
    I("Coupon code", "", None, required=False),
    S("Invalid Aadhaar-like number in note: " + bad_aadhaar()),
], ["Place order"], click("Place order")))

pages.append(page("co-06", "checkout", "Guest Checkout", [
    I("Email for order updates", "", None),
    I("Mobile", "", None),
], ["Continue"], focus("Email for order updates")))

# --- Distractors (no PII) ----------------------------------------------------
pages.append(page("news-01", "distractor", "ISRO launches PSLV-C62", [
    H("PSLV-C62 places 9 satellites in orbit"),
    S("The launch from Sriharikota lifted off at 10:17 IST on schedule."),
    S("The mission carried payloads weighing 1,425 kg in total."),
    S("Officials said the next launch window opens in November."),
], [], SUMMARY))

pages.append(page("news-02", "distractor", "Cricket Live Score", [
    S("IND 287/6 (50 ov) vs AUS 214/9 (46.2 ov)"),
    S("Match ID 202611140045 - Wankhede Stadium"),
    S("Next update in 30 seconds. Scroll for ball-by-ball commentary."),
], [], SCROLL))

pages.append(page("shop-01", "distractor", "Samsung Galaxy M14 5G", [
    S("Buy Samsung Galaxy M14 5G at best price. 6000 mAh battery."),
    S("Model number SM-M146BZBGINS | EAN 8806095032104"),
    S("Price: Rs 13,499. EMI from Rs 654/month."),
], ["Add to cart", "Buy now"], click("Add to cart")))

pages.append(page("docs-01", "distractor", "Python Tutorial - Lists", [
    H("Lists in Python"),
    S("A list is an ordered, mutable collection. Use append() to add items."),
    S("Example: nums = [1, 2, 3]; nums.append(4)"),
    S("Continue reading below for list comprehensions."),
], [], SCROLL))

pages.append(page("wiki-01", "distractor", "Hyderabad - Wikipedia", [
    S("Hyderabad is the capital of Telangana and has a population of about 10 million."),
    S("The Charminar was built in 1591."),
    S("Coordinates: 17.3850 N, 78.4867 E"),
], [], SUMMARY))

pages.append(page("search-01", "distractor", "Search results - flights to Delhi", [
    S("Showing results for flights to Delhi on 14 Oct."),
    S("6E 2134 departs 06:05, arrives 08:25 - Rs 5,432"),
    S("AI 0544 departs 09:40, arrives 12:05 - Rs 6,120"),
], ["Search", "Filter"], click("Search")))

pages.append(page("stats-01", "distractor", "Census Data Table", [
    S("District code 5421 0987 6543 - population 1,234,567"),
    S("GDP 2025-26: Rs 3,24,00,000 crore"),
    S("Phone numbers are not listed on this page."),
], [], SUMMARY))

pages.append(page("support-01", "distractor", "Help Centre", [
    S("Call our toll-free helpline 1800 180 1234 (not a mobile number)."),
    S("Ticket #7788990011 has been closed."),
    I("Search help articles", "", None, required=False),
], ["Search"], click("Search")))

pages.append(page("form-01", "distractor", "Feedback Form", [
    I("How was your experience?", "", None),
    I("Comments", "", None, "textarea", required=False),
], ["Submit feedback"], focus("How was your experience?")))

pages.append(page("blog-01", "distractor", "Monsoon Recipes", [
    S("Try this quick pakora recipe for rainy evenings."),
    S("Ingredients: 2 cups besan, 1 onion, salt, chilli powder."),
    S("Serves 4. Preparation time 20 minutes."),
], [], SUMMARY))

out = {
    "meta": {
        "note": "Synthetic and self-labelled by the benchmark author. Not real user data.",
        "seed": 26171,
        "pii_types": ["AADHAAR", "PAN", "CARD", "PHONE", "EMAIL", "NAME", "ADDRESS"],
        "action_policy": ("focus the first empty required field; else click the primary "
                          "submit-like button; else scroll if the page says more content "
                          "follows; else summarize. Password/CVV/PIN/OTP count as fields."),
    },
    "pages": pages,
}
with open(os.path.join(HERE, "dataset.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1, ensure_ascii=False)
print(f"wrote {len(pages)} pages")
