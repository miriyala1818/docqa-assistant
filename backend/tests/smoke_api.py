"""API smoke test against a running server:  python tests/smoke_api.py [base_url]

Exercises every endpoint, including upload of a new document, asking about it, and deleting it."""
import json
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def call(method, path, body=None, files=None):
    headers = {}
    data = None
    if files:
        boundary = uuid.uuid4().hex
        parts = []
        for name, content in files:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read()
            return r.status, (json.loads(raw) if "json" in ctype else raw), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        check.failed += 1


check.failed = 0

s, h, _ = call("GET", "/api/health")
check(s == 200 and h["documents"] >= 1, f"health: {h.get('documents')} docs, {h.get('chunks')} chunks")

s, r, _ = call("POST", "/api/ask", {"question": "How do I reset my password?"})
check(s == 200 and r["status"] == "ANSWERED" and r["citations"], f"ask direct -> {r.get('status')}, {len(r.get('citations', []))} citations")
c = r["citations"][0]
check(all(k in c for k in ("doc_title", "section", "excerpt", "link", "source")), "citation has doc, section, excerpt, link")

s, r, _ = call("POST", "/api/ask", {"question": "What is the API rate limit?"})
check(r["status"] == "ANSWERED_WITH_CONFLICT" and r["conflicts"], f"conflict -> {r['status']}")

s, r, _ = call("POST", "/api/ask", {"question": "How much storage do I get?"})
check(r["status"] == "NEEDS_CLARIFICATION" and len(r["options"]) == 3, f"clarify -> {r['status']} options={[o['plan'] for o in r['options']]}")

s, r, _ = call("POST", "/api/ask", {"question": "How much storage do I get?", "plan": "Pro"})
check(r["status"] == "ANSWERED" and "100 GB" in " ".join(a["text"] for a in r["answer"]), f"plan filter Pro -> {r['status']}: {r['answer'][0]['text'] if r['answer'] else ''}")

s, r, _ = call("POST", "/api/ask", {"question": "Does Orbitly integrate with SAP?"})
check(r["status"] == "NOT_FOUND" and r["closest_topics"], f"unsupported -> {r['status']}, closest: {[t['topic'] for t in r['closest_topics']][:1]}")

s, r, _ = call("POST", "/api/ask", {"question": "x"})
check(s == 422, f"validation rejects too-short question ({s})")

s, d, _ = call("GET", "/api/documents")
check(s == 200 and len(d["documents"]) == h["documents"], "list documents")
doc = next(x for x in d["documents"] if x["format"] == "pdf")
s, raw, hdr = call("GET", f"/api/documents/{doc['doc_id']}/file")
check(s == 200 and raw[:4] == b"%PDF" and "sandbox" in hdr.get("content-security-policy", hdr.get("Content-Security-Policy", "")),
      "source file served with sandbox CSP")
s, ch, _ = call("GET", f"/api/documents/{doc['doc_id']}/chunks")
check(s == 200 and ch["chunks"] and ch["chunks"][0]["page_start"], "chunks endpoint returns page numbers")

new_doc = b"# Travel Policy\n\n## Hotel limits\n\nEmployees may book hotels up to 180 USD per night in tier-1 cities.\n"
s, up, _ = call("POST", "/api/documents", files=[("zz_smoke_travel_policy.md", new_doc)])
check(s == 200 and "zz_smoke_travel_policy.md" in up["uploaded"], f"upload markdown -> {up.get('index', {}).get('chunks')} chunks")
s, r, _ = call("POST", "/api/ask", {"question": "What is the hotel limit per night?"})
check(r["status"] == "ANSWERED" and any(c["source"] == "zz_smoke_travel_policy.md" for c in r["citations"]),
      f"new document answerable -> {r['status']}")
s, _, _ = call("DELETE", "/api/documents/zz_smoke_travel_policy")
check(s == 200, "delete uploaded document")
s, d2, _ = call("GET", "/api/documents")
check(len(d2["documents"]) == len(d["documents"]), "collection restored")

s, e, _ = call("POST", "/api/documents", files=[("evil.exe", b"MZ")])
check(s == 400, f"rejects unsupported file type ({s})")
s, e, _ = call("POST", "/api/documents", files=[("broken.pdf", b"not really a pdf")])
check(s == 422, f"rejects unparsable PDF ({s})")

print(f"\n{'ALL PASSED' if not check.failed else f'{check.failed} FAILED'}")
sys.exit(1 if check.failed else 0)
