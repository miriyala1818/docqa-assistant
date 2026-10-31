"""Unit tests for parsing, chunking, quantity extraction, verification and decision logic.

Run:  python -m pytest tests -q      (or simply: python tests/test_core.py)
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.config import KB_DIR  # noqa: E402
from app.ingestion.chunker import chunk_document  # noqa: E402
from app.ingestion.parsers import parse_file  # noqa: E402
from app.qa.answer import _is_pointer, _trim_to_plan, unknown_terms, verify  # noqa: E402
from app.utils.textutil import (expand_query, extract_quantities, plan_in_query, split_sentences,  # noqa: E402
                          version_in_query)

KB = KB_DIR


def test_pdf_parsing_keeps_pages_and_strips_running_headers():
    d = parse_file(KB / "orbitly_user_manual_v3.1.pdf")
    assert d.meta["version"] == "3.1" and d.meta["effective_date"] == "2026-01-15"
    text = "\n".join(b.text for b in d.blocks)
    assert "Internal" not in text and "Page 2" not in text          # running header/footer removed
    assert "ﬀ" not in text and "Effective" not in text               # ligatures normalised, metadata line dropped
    chunks = chunk_document(d)
    lockout = next(c for c in chunks if "Account lockout" in c.section)
    assert lockout.page_start == 2 and "5 consecutive" in lockout.text
    steps = next(c for c in chunks if "Resetting your password" in c.section)
    assert steps.text.count("\n") >= 5                                # numbered procedure kept whole
    assert (steps.page_start, steps.page_end) == (1, 2)               # spans a page break


def test_html_parsing_drops_boilerplate_and_keeps_anchors():
    d = parse_file(KB / "faq.html")
    text = " ".join(b.text for b in d.blocks)
    assert "Cookie settings" not in text and "analytics" not in text  # footer + script removed
    assert d.meta["effective_date"] == "2025-11-10"
    c = next(c for c in chunk_document(d) if "account locked" in c.section.lower())
    assert c.anchor == "account-locked" and c.location == "#account-locked"


def test_markdown_front_matter_and_table_linearisation():
    d = parse_file(KB / "usage_limits.md")
    assert d.meta["version"] == "3.2" and d.meta["effective_date"] == "2026-06-01"
    table = next(b for b in d.blocks if b.type == "table")
    assert "Storage per workspace: Basic: 10 GB; Pro: 100 GB; Enterprise: 1 TB" in table.text


def test_txt_caps_headings_and_metadata():
    d = parse_file(KB / "troubleshooting_guide.txt")
    secs = [c.section for c in chunk_document(d)]
    assert any("ORB-429" in s for s in secs)
    assert d.meta["version"] == "3.1" and d.meta["effective_date"] == "2026-05-20"


def test_quantities_units_plans_and_superseded_values():
    q = extract_quantities("The default API rate limit has been increased from 100 to 300 requests per minute.")
    assert [(x.raw, x.dim) for x in q] == [("300 requests per minute", "req_per_min")]
    q = extract_quantities("Web sessions now expire after 60 minutes of inactivity (previously 30 minutes).")
    assert [x.value for x in q] == [60]
    q = extract_quantities("Storage per workspace: Basic: 10 GB; Pro: 100 GB; Enterprise: 1 TB")
    assert [(x.plan, x.dim) for x in q] == [("Basic", "size"), ("Pro", "size"), ("Enterprise", "size")]
    assert q[2].value == 1024 * 1024                                   # 1 TB normalised to MB
    q = extract_quantities("Pro and Enterprise workspaces can buy storage in blocks of 50 GB.")
    assert q[0].plan is None                                           # a plan group is not one plan
    q = extract_quantities("Error ORB-429 in version 3.2 on 2026-06-01.")
    assert q == []                                                     # codes, versions, dates are not claims
    q = extract_quantities("locked after 5 consecutive failed sign-in attempts.")
    assert q[0].dim == "attempts"


def test_verifier_rejects_unsupported_numbers_and_content():
    passage = "Annual subscriptions can be refunded within 30 days of the purchase date."
    assert verify("Annual subscriptions can be refunded within 30 days.", passage)
    assert not verify("Annual subscriptions can be refunded within 60 days.", passage)
    assert not verify("Monthly plans include free phone support and onboarding.", passage)


def test_query_understanding():
    assert plan_in_query("How much storage on the Pro plan?") == "Pro"
    assert plan_in_query("How much storage?") is None
    assert version_in_query("rate limit in v3.1") == "3.1"
    _, added = expand_query("I forgot my login credentials")
    assert "password" in added


def test_unknown_terms_and_pointers():
    vocab = {"orbitly", "integr", "slack", "okta"}
    assert unknown_terms("Does Orbitly integrate with SAP?", vocab) == ["SAP"]
    assert unknown_terms("Can we use Okta?", vocab) == []
    assert _is_pointer("See the SSO Configuration Guide for setup steps.")
    assert not _is_pointer("Open Settings > Security and click Configure SAML.")


def test_plan_trimming_and_sentence_splitting():
    row = "Storage per workspace: Basic: 10 GB; Pro: 100 GB; Enterprise: 1 TB"
    assert _trim_to_plan(row, "Pro") == "Storage per workspace: Pro: 100 GB"
    assert split_sentences("Use e.g. Okta. Then test it.") == ["Use e.g. Okta.", "Then test it."]


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", f.__name__, e)
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
