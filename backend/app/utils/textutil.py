"""Text utilities: normalisation, tokenisation, stemming, sentence splitting,
quantity extraction, plan detection and the terminology glossary."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

STOPWORDS = set("""
a an and are as at be been being but by can could did do does doing done for from had has have having
he her here hers him his how i if in into is it its itself just me my myself no nor not now of off on
once only or other our ours out over own same she should so some such than that the their theirs them
then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours yourself yourselves get got gets getting want need
please tell know let lets via per also any all each about after before again further more most much many
am im ive id ill youre dont doesnt cant wont isnt arent wasnt shouldnt couldnt us way thing things
""".split())

PLANS = ["Basic", "Pro", "Enterprise"]
_PLAN_RE = re.compile(r"\b(Basic|Pro|Enterprise)\b")

# Bidirectional terminology glossary used for query expansion (FR-03).
# Each group lists phrases that users and documents use for the same concept.
GLOSSARY: list[list[str]] = [
    ["sso", "single sign-on", "saml", "company login", "okta accounts"],
    ["2fa", "two-factor authentication", "mfa", "multi-factor", "authenticator app"],
    ["password", "login credentials", "credentials", "forgot password"],
    ["locked out", "lockout", "account locked", "failed sign-in attempts", "too many tries"],
    ["invoice", "bill", "receipt", "billing statement"],
    ["refund", "money back", "reimburse"],
    ["usage limit", "quota", "limit", "cap"],
    ["rate limit", "requests per minute", "throttle", "throttling", "too many requests", "orb-429"],
    ["cancel", "cancellation", "close account", "stop subscription"],
    ["storage", "disk space", "space"],
    ["session timeout", "logged out", "inactivity", "signed out automatically"],
    ["price", "cost", "pricing", "how much", "pay"],
    ["upgrade", "move to a higher plan", "pricier plan", "change plan"],
    ["downgrade", "lower plan", "cheaper plan"],
    ["export", "download data", "get data out", "backup"],
    ["integration", "integrates", "connect", "hook up", "team chat", "slack", "microsoft teams"],
    ["invite", "add colleagues", "let colleagues in", "invitation", "add users", "add members"],
    ["member", "user", "colleague", "teammate", "person", "people"],
    ["verification email", "confirmation email", "email never showed up"],
    ["slow", "sluggish", "performance", "loads slowly"],
    ["delete workspace", "remove workspace", "workspace deletion"],
    ["remove a member", "leaves the company", "offboard", "leave the company"],
    ["support", "help desk", "customer service", "respond"],
    ["upload", "attach file", "upload fails"],
    ["audit log", "activity log", "audit trail"],
    ["data region", "data residency", "where data is stored"],
    ["api key", "api token", "access token"],
]

# Units grouped into comparable dimensions for conflict detection (FR-06).
_UNIT_CANON = [
    (r"requests?\s+(?:per|a|/)\s*min(?:ute)?s?", "req_per_min"),
    (r"(?:api\s+)?calls?(?:\s+per\s+day)?", "calls"),
    (r"(?:consecutive\s+)?(?:failed\s+)?(?:sign-in\s+|login\s+)?attempts?|tries", "attempts"),
    (r"seconds?", "time:s"),
    (r"minutes?|mins?", "time:min"),
    (r"hours?|hrs?", "time:h"),
    (r"days?", "time:d"),
    (r"weeks?", "time:w"),
    (r"months?", "time:mo"),
    (r"years?", "time:y"),
    (r"tb", "size:tb"),
    (r"gb", "size:gb"),
    (r"mb", "size:mb"),
    (r"users?", "users"),
    (r"projects?", "projects"),
    (r"widgets?", "widgets"),
    (r"characters?", "characters"),
    (r"times", "times"),
    (r"%|percent", "percent"),
]
_TIME_TO_MIN = {"s": 1 / 60, "min": 1, "h": 60, "d": 1440, "w": 10080, "mo": 43200, "y": 525600}
_SIZE_TO_MB = {"mb": 1, "gb": 1024, "tb": 1024 * 1024}

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_QTY_RE = re.compile(
    r"(?P<cur>\$)?(?P<num>" + _NUM + r")\s*(?P<unit>"
    + "|".join(u for u, _ in _UNIT_CANON)
    + r")?(?!\w|\.\d)",
    re.IGNORECASE,
)
_SUPERSEDED_RE = re.compile(
    r"(?:from\s+" + _NUM + r"(?=\s+to\s)|previously\s+" + _NUM + r"[^)]*|\(previously[^)]*\))",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Unicode-normalise (fixes PDF ligatures), unify quotes/dashes, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace("­", "")
    return re.sub(r"[ \t ]+", " ", text).strip()


def stem(word: str) -> str:
    """Very small suffix-stripping stemmer (good enough for BM25 on short docs)."""
    w = word
    if len(w) <= 3:
        return w
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("edly", ""), ("ed", ""), ("ly", ""),
                     ("ers", "er"), ("es", "e"), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: len(w) - len(suf)] + rep
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")


def words(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def tokenize(text: str, keep_stop: bool = False) -> list[str]:
    out = []
    for w in words(text):
        w = w.replace("'", "")
        parts = [w] + (w.split("-") if "-" in w else [])
        for p in parts:
            if not p or (not keep_stop and p in STOPWORDS):
                continue
            out.append(stem(p))
    return out


def content_terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if not t.isdigit()}


_ABBREV = re.compile(r"\b(e\.g|i\.e|etc|vs|approx|no)\.$", re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    """Split a block into sentences. List items / table rows (one per line) stay whole."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^(\d+\.|[-*•])\s", line) or "|" in line or ";" in line and ":" in line:
            out.append(line)
            continue
        buf = ""
        for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])", line):
            buf = f"{buf} {piece}".strip() if buf else piece
            if _ABBREV.search(buf):
                continue
            out.append(buf)
            buf = ""
        if buf:
            out.append(buf)
    return out


@dataclass
class Quantity:
    value: float          # value normalised to the dimension's base unit
    raw: str              # text as written, e.g. "15 minutes"
    dim: str              # comparable dimension, e.g. "time", "size", "req_per_min"
    start: int
    plan: str | None = None


def _canon_unit(unit: str | None, currency: bool) -> tuple[str, float]:
    if currency:
        return "money", 1.0
    if not unit:
        return "count", 1.0
    for pat, canon in _UNIT_CANON:
        if re.fullmatch(pat, unit, re.IGNORECASE):
            if canon.startswith("time:"):
                return "time", _TIME_TO_MIN[canon.split(":")[1]]
            if canon.startswith("size:"):
                return "size", _SIZE_TO_MB[canon.split(":")[1]]
            return canon, 1.0
    return "count", 1.0


def extract_quantities(sentence: str) -> list[Quantity]:
    """Extract numeric claims. Values marked as superseded inside the same sentence
    ("increased from 100 to 300", "(previously 30 minutes)") are ignored so that only the
    current value is compared."""
    masked = _SUPERSEDED_RE.sub(lambda m: " " * len(m.group(0)), sentence)
    out = []
    for m in _QTY_RE.finditer(masked):
        num = m.group("num")
        start = m.start()
        # skip error codes (ORB-429), section numbers (2.3), versions (v3.2), years, ordinals in lists
        before = masked[max(0, start - 10):start]
        if re.search(r"[A-Za-z]-$|[vV]$|\d\.$|version\s*$|release\s*$", before, re.IGNORECASE):
            continue
        if re.match(r"^\d+\.\s", sentence[start:start + 4]) and start == 0:
            continue
        val = float(num.replace(",", ""))
        unit = m.group("unit")
        if not unit and not m.group("cur"):
            if 1900 <= val <= 2100:
                continue
            nxt = masked[m.end():m.end() + 1]
            if nxt in ".-/:":
                continue
        dim, mult = _canon_unit(unit, bool(m.group("cur")))
        raw = sentence[m.start():m.end()].strip()
        out.append(Quantity(value=val * mult, raw=raw, dim=dim, start=start))
    _attach_plans(sentence, out)
    return out


def _attach_plans(sentence: str, qtys: list[Quantity]) -> None:
    """Key each quantity to the closest preceding plan name in the sentence
    (works for "Basic: 10 GB; Pro: 100 GB" and "Pro workspaces retain ... 90 days")."""
    plans = [(m.start(), m.end(), m.group(1)) for m in _PLAN_RE.finditer(sentence)]
    if not plans:
        return
    # "Pro and Enterprise workspaces ..." names a group of plans: a value after it applies to all
    # of them, so it is not keyed to a single plan.
    grouped = set()
    for (s1, e1, _), (s2, _, _) in zip(plans, plans[1:]):
        if re.fullmatch(r"\s*(?:,|and|or|,\s*and|,\s*or)\s*", sentence[e1:s2]):
            grouped.update({s1, s2})
    for q in qtys:
        prev = [p for p in plans if p[0] < q.start]
        cand = prev[-1] if prev else (plans[0] if len(plans) == 1 else None)
        if cand and cand[0] not in grouped:
            q.plan = cand[2]


def plans_in(text: str) -> list[str]:
    return sorted({m.group(1) for m in _PLAN_RE.finditer(text)})


def plan_in_query(query: str) -> str | None:
    found = [p for p in PLANS if re.search(rf"\b{p}\b", query, re.IGNORECASE)]
    return found[0] if len(found) == 1 else None


_VERSION_RE = re.compile(r"\b(?:v|version\s*)(\d+(?:\.\d+)+)\b", re.IGNORECASE)


def version_in_query(query: str) -> str | None:
    m = _VERSION_RE.search(query)
    return m.group(1) if m else None


def _phrase_matches(phrase: str, q_text: str, q_terms: set[str]) -> bool:
    """A glossary phrase matches if it appears verbatim, or if every content word of a
    multi-word phrase appears (stemmed) in the query ('let my colleagues into' ~ 'let colleagues in')."""
    if f" {phrase} " in q_text:
        return True
    terms = [t for t in tokenize(phrase)]
    if len(phrase.split()) < 2 or len(terms) < 2:
        return False
    return all(t in q_terms for t in terms)


def expand_query(query: str) -> tuple[str, list[str]]:
    """Return the query with glossary synonyms appended, plus the synonyms that were added."""
    q = " " + " ".join(words(query)) + " "
    q_terms = set(tokenize(query))
    added: list[str] = []
    for group in GLOSSARY:
        if any(_phrase_matches(phrase, q, q_terms) for phrase in group):
            for phrase in group:
                if f" {phrase} " not in q and phrase not in added:
                    added.append(phrase)
    return (query + " " + " ".join(added)).strip(), added
