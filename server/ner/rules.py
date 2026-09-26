"""
Lightweight rule layer for Indian personal names and postal addresses.

Pure regex / word lists - no model, no I/O, microseconds per call. It runs
alongside the statistical model (see model.py) and is the only layer that
knows Indian-specific conventions the small English model misses:

  * names:   honorifics (Mr/Mrs/Ms/Shri/Sri/Smt/Kumari/Dr/Prof),
             relation markers (S/o, D/o, W/o, C/o), "my name is ...",
             salutations ("Dear ...", "Welcome back, ..."), and a short
             gazetteer of common Indian surnames ("Priya Nair").
  * address: 6-digit PIN codes, house/door numbers (H.No 12-3-45, Flat 4B,
             Plot No 7), locality suffixes (Nagar, Colony, Road, Mandal,
             District ...), state and major-city names, and cue phrases
             ("I live at", "deliver to"). A comma-separated run of such
             segments is scored; only runs above a threshold become an
             ADDRESS, so a lone city ("Hyderabad weather") never does.

Keep this file in sync (semantically) with extension/ner-rules.js, the
client-side port that runs BEFORE anything leaves the browser.
"""

import re
from typing import Iterable, List, Optional, Tuple

from .types import Span

# ---------------------------------------------------------------------------
# Word lists
# ---------------------------------------------------------------------------

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Orissa", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "New Delhi", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Puducherry", "Chandigarh",
]

MAJOR_CITIES = [
    "Hyderabad", "Secunderabad", "Bengaluru", "Bangalore", "Chennai", "Mumbai",
    "Kolkata", "Pune", "Ahmedabad", "Jaipur", "Lucknow", "Kanpur", "Nagpur",
    "Indore", "Bhopal", "Patna", "Vadodara", "Surat", "Visakhapatnam", "Vijayawada",
    "Warangal", "Guntur", "Tirupati", "Coimbatore", "Madurai", "Kochi", "Thiruvananthapuram",
    "Mysuru", "Mysore", "Mangaluru", "Noida", "Gurugram", "Gurgaon", "Ghaziabad",
    "Chandigarh", "Bhubaneswar", "Guwahati", "Ranchi", "Raipur", "Dehradun",
    "Vikarabad", "Karimnagar", "Nizamabad", "Khammam", "Nellore", "Kurnool",
]

# Suffix keywords follow a proper noun: "Gandhi Nagar", "Ameerpet Road".
ADDRESS_SUFFIXES = [
    "Road", "Rd", "Street", "St", "Nagar", "Colony", "Layout", "Marg", "Lane",
    "Chowk", "Cross", "Main", "Enclave", "Residency", "Apartments", "Apartment",
    "Apts", "Towers", "Tower", "Complex", "Society", "Heights", "Gardens", "Vihar",
    "Puram", "Pet", "Peta", "Palli", "Guda", "Bagh", "Ganj", "Mandal", "District",
    "Dist", "Taluk", "Taluka", "Tehsil", "Village", "Circle", "Extension", "Hills",
    "Mohalla", "Basti", "Bazar", "Bazaar", "Avenue", "Nivas", "Bhavan", "Villas",
    "Villa", "Township", "Halli", "Wadi", "Kunj",
]

# Prefix keywords are followed by an identifier: "Sector 5", "Block C".
ADDRESS_PREFIXES = ["Sector", "Block", "Phase", "Ward", "Mandal", "District", "Dist", "Village"]

INDIAN_SURNAMES = [
    "Kumar", "Reddy", "Rao", "Sharma", "Verma", "Singh", "Nair", "Pillai", "Iyer",
    "Iyengar", "Menon", "Patel", "Shah", "Gupta", "Agarwal", "Aggarwal", "Mehta",
    "Joshi", "Das", "Dutta", "Banerjee", "Chatterjee", "Mukherjee", "Bose", "Ghosh",
    "Naidu", "Chowdary", "Choudhary", "Chaudhary", "Yadav", "Mishra", "Pandey",
    "Tiwari", "Srivastava", "Khan", "Ahmed", "Hussain", "Devi", "Kaur", "Gill",
    "Sandhu", "Desai", "Kulkarni", "Patil", "Jadhav", "Deshmukh", "Goud", "Varma",
    "Murthy", "Krishnan", "Subramanian", "Raju", "Shetty", "Hegde", "Bhat",
    "Kamath", "Naik", "Sinha", "Jain", "Saxena", "Chopra", "Kapoor", "Malhotra",
]

# Capitalised words that are never (part of) a personal name in UI/product text.
COMMON_WORDS = {
    "The", "This", "That", "These", "Those", "A", "An", "And", "Or", "Of", "In", "On",
    "At", "To", "For", "From", "With", "By", "About", "As", "Is", "Are", "Was", "It",
    "Please", "Contact", "Call", "Email", "Ask", "Meet", "Order", "Shop", "Buy",
    "Visit", "Thanks", "Thank", "Dear", "Hi", "Hello", "Hey", "Welcome", "Back",
    "Sign", "Log", "Login", "Submit", "Search", "Home", "Menu", "Cart", "Add",
    "Account", "Profile", "Settings", "Help", "Support", "Name", "Address", "Full",
    "First", "Last", "Middle", "User", "Guest", "Team", "Sir", "Madam", "Everyone",
    "All", "World", "There", "Friend", "Friends", "Customer", "Customers", "Admin",
    "Today", "Tomorrow", "Yesterday", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "January", "February", "March", "April", "May",
    "June", "July", "August", "September", "October", "November", "December",
    "India", "Indian", "Weather", "News", "Price", "Offer", "Sale", "Deal", "Deals",
    "New", "Best", "Top", "More", "Details", "Free", "Delivery", "Now", "Get",
    "Your", "My", "Our", "We", "You", "He", "She", "They", "Mr", "Mrs", "Ms", "Dr",
    "Shri", "Sri", "Smt", "Prof", "Kumari", "Via", "Per", "Not", "No", "Yes",
    "Card", "Bank", "Pay", "Payment", "Total", "Amount", "Summary", "Continue",
} | set(ADDRESS_SUFFIXES) | set(ADDRESS_PREFIXES)

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_NAME_WORD = r"(?:[A-Z][a-z]+(?:-[A-Z][a-z]+)?|[A-Z]\.?(?=\s))"
_NAME_SEQ = rf"{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}}"

HONORIFIC_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Mx|Shri|Shrimati|Sri(?!\s+Lanka)|Smt|Kumari|Km|Dr|Prof|Thiru|Thirumathi|Selvi)\b\.?\s+"
    rf"(?P<name>{_NAME_SEQ})"
)
RELATION_RE = re.compile(
    r"(?:\b[SDWCH]/[Oo]\b|\b(?:Son|Daughter|Wife|Husband|Care)\s+of\b)\.?\s*:?\s*"
    r"(?:(?:Mr|Mrs|Ms|Shri|Sri|Smt|Late|Dr)\b\.?\s+)?"
    rf"(?P<name>{_NAME_SEQ})"
)
# A name immediately before a relation marker: "Ravi Kumar S/o ..."
BEFORE_RELATION_RE = re.compile(rf"(?P<name>{_NAME_SEQ})\s*,?\s*(?=\b[SDWC]/[Oo]\b)")
NAME_CUE_RE = re.compile(
    r"(?:\b[Mm]y\s+name\s+is|\b[Nn]ame\s*[:\-]|\b[Mm]yself)\s+"
    rf"(?P<name>{_NAME_SEQ})"
)
IAM_RE = re.compile(rf"(?:\bI\s+am|\bI'm)\s+(?P<name>{_NAME_WORD}\s+{_NAME_WORD}(?:\s+{_NAME_WORD})?)")
SALUTATION_RE = re.compile(
    r"\b(?:Dear|Hi|Hello|Hey|Welcome(?:\s+back)?|Thanks|Thank\s+you)\s*,?\s+"
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,2}})"
)
SURNAME_RE = re.compile(
    rf"(?P<name>(?:{_NAME_WORD}\s+){{1,2}}(?:{'|'.join(INDIAN_SURNAMES)}))\b"
)

PIN_RE = re.compile(r"(?<![\d.,â‚¹$])\b[1-9]\d{2}\s?\d{3}\b(?![.,]?\d)")
CURRENCY_BEFORE_RE = re.compile(r"(?:â‚¹|Rs\.?|INR|\$|USD)\s*$", re.IGNORECASE)
HOUSE_RE = re.compile(
    r"(?:\b(?:H|D|House|Door|Flat|Plot|Shop|Room|Survey|Unit)\s?\.?\s?No\b\.?|\bFlat\b|\bPlot\b|#)"
    r"\s*[:#.\-]?\s*[A-Za-z]?\d[\w/\-]*",
    re.IGNORECASE,
)
DOOR_NUMBER_RE = re.compile(r"\b\d{1,4}(?:-\d{1,4}){1,3}(?:/[\w]+)?\b")
SUFFIX_RE = re.compile(rf"\b(?:{'|'.join(ADDRESS_SUFFIXES)})\b\.?", re.IGNORECASE)
PREFIX_RE = re.compile(rf"\b(?:{'|'.join(ADDRESS_PREFIXES)})\b\s*[:\-]?\s*(?:No\.?\s*)?[A-Z0-9][\w\-]*")
_STATE_ALT = "|".join(s.replace(" ", r"\s+") for s in INDIAN_STATES)
STATE_RE = re.compile(rf"\b(?:{_STATE_ALT})\b")
CITY_RE = re.compile(rf"\b(?:{'|'.join(MAJOR_CITIES)})\b")
LANDMARK_RE = re.compile(r"\b(?:Near|Opp|Opposite|Behind|Beside|Next\s+to)\b\.?\s+(?=[A-Z])", re.IGNORECASE)
ADDRESS_CUE_RE = re.compile(
    r"\b(?:li(?:ve|ves|ved|ving)\s+(?:at|in)|resid(?:e|es|ing)\s+at|stay(?:s|ing)?\s+at|"
    r"deliver(?:ed|y)?\s+to|ship(?:ped)?\s+to|located\s+at|address(?:\s+is)?\s*[:\-]?|"
    r"pin\s?code\s*[:\-]?|pin\s*[:\-])\s*",
    re.IGNORECASE,
)
# Hard boundaries: a sentence end. Soft boundaries: comma, semicolon, newline, pipe.
SENTENCE_END_RE = re.compile(r"(?<=[a-z0-9]{2})[.!?](?=\s+[A-Z]|\s*$)")
SOFT_SPLIT_RE = re.compile(r"[,;\n|]")
TOKEN_LIKE_RE = re.compile(r"\b[A-Z]+(?:_[A-Z]+)*_(?:\d+|FIELD)\b")

ADDRESS_THRESHOLD = 3.0


def _strip_common(text: str, start: int, end: int) -> Optional[Tuple[int, int]]:
    """Trim leading/trailing COMMON_WORDS off a candidate name span."""
    words = [(m.start() + start, m.end() + start, m.group()) for m in re.finditer(r"\S+", text[start:end])]
    while words and words[0][2].rstrip(".,") in COMMON_WORDS:
        words.pop(0)
    while words and words[-1][2].rstrip(".,") in COMMON_WORDS:
        words.pop()
    if not words:
        return None
    s, e = words[0][0], words[-1][1]
    while e > s and text[e - 1] in ".,":
        e -= 1
    if text[s:e].endswith(("'s", "’s")):  # possessive: "Anita Rao's" -> "Anita Rao"
        e -= 2
    return (s, e) if e > s else None


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def find_name_spans(text: str) -> List[Span]:
    spans: List[Span] = []

    def add(match_start: int, name_start: int, name_end: int, score: float, rule: str, keep_prefix: bool):
        trimmed = _strip_common(text, name_start, name_end)
        if not trimmed:
            return
        s, e = trimmed
        if keep_prefix:  # tokenise the honorific together with the name
            s = min(s, match_start)
        spans.append(Span(s, e, "NAME", score, rule))

    for m in HONORIFIC_RE.finditer(text):
        add(m.start(), m.start("name"), m.end("name"), 0.95, "honorific", True)
    for m in RELATION_RE.finditer(text):
        add(m.start("name"), m.start("name"), m.end("name"), 0.95, "relation", False)
    for m in BEFORE_RELATION_RE.finditer(text):
        add(m.start("name"), m.start("name"), m.end("name"), 0.9, "relation", False)
    for m in NAME_CUE_RE.finditer(text):
        add(m.start("name"), m.start("name"), m.end("name"), 0.9, "cue", False)
    for m in IAM_RE.finditer(text):
        add(m.start("name"), m.start("name"), m.end("name"), 0.8, "cue", False)
    for m in SALUTATION_RE.finditer(text):
        add(m.start("name"), m.start("name"), m.end("name"), 0.8, "salutation", False)
    for m in SURNAME_RE.finditer(text):
        s = m.start("name")
        words = list(re.finditer(r"\S+", m.group("name")))
        # "Later Ravi Kumar": at a sentence start the first capitalised word is
        # more likely an ordinary word than a first name - keep only the last
        # given name (the model can still widen the span when it agrees).
        if len(words) == 3 and re.search(r"(?:^|[.!?]\s+)$", text[:s]):
            s += words[1].start()
        add(s, s, m.end("name"), 0.85, "surname", False)

    # A "name" that is really a place/locality ("Gandhi Nagar") or a token is not a name.
    out = []
    for sp in spans:
        frag = text[sp.start:sp.end]
        if TOKEN_LIKE_RE.search(frag) or SUFFIX_RE.search(frag.split()[-1]) and not HONORIFIC_RE.match(frag):
            continue
        out.append(sp)
    return out


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def _segments(text: str) -> List[Tuple[int, int, bool]]:
    """Split into (start, end, hard_break_before) segments."""
    cuts = []  # (pos, is_hard)
    for m in SENTENCE_END_RE.finditer(text):
        cuts.append((m.start(), m.end(), True))
    for m in SOFT_SPLIT_RE.finditer(text):
        cuts.append((m.start(), m.end(), False))
    cuts.sort()
    segs = []
    pos, hard = 0, True
    for cs, ce, is_hard in cuts:
        if cs < pos:
            continue
        segs.append((pos, cs, hard))
        pos, hard = ce, is_hard
    segs.append((pos, len(text), hard))
    # trim whitespace
    out = []
    for s, e, h in segs:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        out.append((s, e, h))
    return out


def _proper_run_start(text: str, kw_start: int, floor: int) -> int:
    """Walk back from a suffix keyword over Capitalised words / numbers (max 4)."""
    start = kw_start
    words = 0
    for m in reversed(list(re.finditer(r"[A-Za-z0-9][\w\-/.]*", text[floor:kw_start]))):
        w = m.group()
        gap = text[floor + m.end():start]
        if gap.strip():
            break
        if not (w[0].isupper() or w[0].isdigit()) or w.rstrip(".") in {"The", "A", "An"}:
            break
        start = floor + m.start()
        words += 1
        if words >= 4:
            break
    return start


def _segment_features(text: str, s: int, e: int):
    """Return (score, [(feat_start, feat_end)], cue_end) for one segment."""
    seg = text[s:e]
    score = 0.0
    feats: List[Tuple[int, int]] = []
    cue_end = None

    for m in ADDRESS_CUE_RE.finditer(seg):
        score += 1.0
        cue_end = s + m.end()
        break
    for m in PIN_RE.finditer(seg):
        if CURRENCY_BEFORE_RE.search(seg[: m.start()]):
            continue
        score += 2.0
        feats.append((s + m.start(), s + m.end()))
    house = False
    for m in HOUSE_RE.finditer(seg):
        house = True
        feats.append((s + m.start(), s + m.end()))
    for m in DOOR_NUMBER_RE.finditer(seg):
        house = True
        feats.append((s + m.start(), s + m.end()))
    if house:
        score += 2.0
    n_suffix = 0
    for m in SUFFIX_RE.finditer(seg):
        ks = s + m.start()
        begin = _proper_run_start(text, ks, s)
        if begin == ks:
            continue  # "the road", lone "Road Safety" - no proper noun before it
        n_suffix += 1
        if n_suffix <= 3:
            score += 1.0
        if re.match(r"\d", text[begin:ks]):
            score += 0.5  # "21 MG Road"
        feats.append((begin, s + m.end()))
    for m in PREFIX_RE.finditer(seg):
        score += 1.0
        feats.append((s + m.start(), s + m.end()))
    for m in STATE_RE.finditer(seg):
        score += 1.0
        feats.append((s + m.start(), s + m.end()))
    for m in CITY_RE.finditer(seg):
        score += 0.5
        feats.append((s + m.start(), s + m.end()))
    for m in LANDMARK_RE.finditer(seg):
        score += 0.5
        feats.append((s + m.start(), s + m.end()))
    return score, feats, cue_end


def _is_filler(text: str, s: int, e: int) -> bool:
    words = text[s:e].split()
    return 1 <= len(words) <= 3 and all(w[0].isupper() or w[0].isdigit() for w in words)


def find_address_spans(text: str, place_hints: Iterable[Tuple[int, int]] = (),
                       name_spans: Iterable[Span] = ()) -> List[Span]:
    """Detect addresses. `place_hints` are model GPE/LOC/FAC offsets (extra evidence)."""
    hints = list(place_hints)
    names = list(name_spans)
    segs = _segments(text)
    info = [_segment_features(text, s, e) for s, e, _ in segs]

    def overlaps_name(s, e):
        return any(s < n.end and e > n.start for n in names)

    out: List[Span] = []
    i = 0
    while i < len(segs):
        score, feats, cue_end = info[i]
        if not feats:
            i += 1
            continue
        # Start a run at this address-like segment.
        first = i
        j = i
        run_score = score
        trailing_filler = 0
        while j + 1 < len(segs) and not segs[j + 1][2]:
            ns, ne, _ = segs[j + 1]
            nscore, nfeats, _ = info[j + 1]
            if nfeats:
                run_score += nscore
                trailing_filler = 0
                j += 1
            elif _is_filler(text, ns, ne) and trailing_filler < 2 and not overlaps_name(ns, ne):
                trailing_filler += 1
                j += 1
            else:
                break
        # Optionally absorb one single-word filler just before the run ("Hyderabad, Telangana 500080").
        if first > 0 and not segs[first][2]:
            ps, pe, _ = segs[first - 1]
            if not info[first - 1][1] and len(text[ps:pe].split()) == 1 and _is_filler(text, ps, pe) \
                    and not overlaps_name(ps, pe):
                first -= 1

        # Evidence from the model's place entities overlapping the run.
        run_s = segs[first][0]
        run_e = segs[j][1]
        model_bonus = sum(0.5 for hs, he in hints if hs < run_e and he > run_s)
        run_score += min(model_bonus, 1.0)

        if run_score >= ADDRESS_THRESHOLD:
            # Exact start: earliest feature of the first address-like segment (or after a cue).
            s0, e0, _ = segs[i]
            fstart = min(fs for fs, _ in info[i][1])
            if first < i:
                start = segs[first][0]
            else:
                start = max(fstart, cue_end) if cue_end is not None and cue_end <= fstart else fstart
            # Exact end: last feature end of the last address-like segment, plus trailing fillers.
            last_addr = max(k for k in range(i, j + 1) if info[k][1])
            end = max(fe for _, fe in info[last_addr][1])
            if j > last_addr:
                end = segs[j][1]
            while end > start and text[end - 1] in " .,;":
                end -= 1
            out.append(Span(start, end, "ADDRESS", min(0.99, 0.5 + run_score / 10), "address-rules"))
        i = j + 1
    return out
