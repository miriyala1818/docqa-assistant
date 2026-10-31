"""Query pipeline: understand -> retrieve -> evidence gate -> answer -> verify ->
conflict / clarification checks -> response contract.

Response statuses (the response contract):
  ANSWERED                 strong evidence, verified claims, no conflict
  ANSWERED_WITH_CONFLICT   credible sources state incompatible facts; all sides shown
  NEEDS_CLARIFICATION      the answer depends on the user's plan; options listed with citations
  NOT_FOUND                evidence below threshold / question about something not documented
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import EVIDENCE_PASSAGES, K_FINAL, load_thresholds
from ..retrieval.index import Hit, ce_scores, get_index
from . import llm
from ..utils.textutil import (PLANS, STOPWORDS, Quantity, content_terms, extract_quantities, plan_in_query, plans_in,
                       split_sentences, stem, version_in_query, words)

_NUMBERED = re.compile(r"^\d+[.)]\s")
_TH_CACHE: dict = {}


def thresholds(reload: bool = False) -> dict:
    if reload or not _TH_CACHE:
        _TH_CACHE.clear()
        _TH_CACHE.update(load_thresholds())
    return _TH_CACHE


@dataclass
class Unit:
    text: str
    hit_rank: int            # position of the passage in the evidence list
    hit: Hit
    pos: int                 # position of the unit inside its passage
    score: float = 0.0
    qtys: list[Quantity] = field(default_factory=list)
    numbered: bool = False


# ------------------------------------------------------------------ helpers
def unknown_terms(question: str, vocab: set[str]) -> list[str]:
    """Named things in the question (acronyms / capitalised words) that never occur in the
    collection, e.g. "SAP" or "HIPAA". A strong, cheap signal that the answer is not documented."""
    toks = re.findall(r"\b[A-Za-z][A-Za-z0-9\-]*\b", question)
    out = []
    for i, t in enumerate(toks):
        if len(t) < 2 or t.lower() in STOPWORDS:
            continue
        is_acronym = t.isupper()
        is_capitalised = t[0].isupper() and i > 0
        if not (is_acronym or is_capitalised):
            continue
        low = t.lower()
        if stem(low) not in vocab and low not in vocab and not any(stem(p) in vocab for p in low.split("-")):
            out.append(t)
    return out


def verify(sentence: str, passage: str) -> bool:
    """Post-generation check that a sentence is supported by the passage it cites:
    every number must appear in the passage and most content words must be covered."""
    nums = set(re.findall(r"\d+(?:[.,]\d+)?", sentence))
    pnums = set(re.findall(r"\d+(?:[.,]\d+)?", passage))
    if not nums <= pnums:
        return False
    terms = content_terms(sentence)
    if not terms:
        return True
    cover = len(terms & content_terms(passage)) / len(terms)
    return cover >= 0.6


def _jaccard(a: str, b: str) -> float:
    x, y = content_terms(a), content_terms(b)
    return len(x & y) / max(1, len(x | y))


def _make_units(evidence: list[Hit]) -> list[Unit]:
    units = []
    for r, h in enumerate(evidence):
        pos = 0
        for s in split_sentences(h.chunk.text):
            if len(words(s)) < 3:
                continue
            units.append(Unit(text=s, hit_rank=r, hit=h, pos=pos, qtys=extract_quantities(s),
                              numbered=bool(_NUMBERED.match(s))))
            pos += 1
    return units


_POINTER = re.compile(r"^(?:please\s+)?see (?:the |our )?[\w\s-]{2,60}?(?:for|to)\b[^.]*\.?$", re.IGNORECASE)


def _is_pointer(text: str) -> bool:
    """Cross-reference sentences ('See the SSO Configuration Guide for setup steps.') carry no
    answer content themselves."""
    return bool(_POINTER.match(text.strip())) and len(words(text)) <= 14


def _score_units(question: str, units: list[Unit], expansion: list[str] | None = None) -> None:
    """Sentence relevance = cross-encoder score of the sentence (with its section heading).
    Short follow-up sentences ("A locked account stays locked for 15 minutes.") depend on the
    sentence before them, so a windowed score (previous + current) is also computed and the
    better of the two is used, with a small penalty for the borrowed context."""
    alone = [f"{u.hit.chunk.section}: {u.text}" for u in units]
    windowed, idx = [], []
    for i, u in enumerate(units):
        if i > 0 and units[i - 1].hit is u.hit and not u.numbered:
            windowed.append(f"{u.hit.chunk.section}: {units[i - 1].text} {u.text}")
            idx.append(i)
    scores = ce_scores(question, expansion or [], alone + windowed)
    base = scores[: len(units)]
    for u, s in zip(units, base):
        u.score = s
    # A strong window means the answer is in this sentence pair; we cannot tell which half, so
    # both sentences are credited (minus a small penalty for the borrowed context).
    for i, s in zip(idx, scores[len(units):]):
        for j in (i - 1, i):
            units[j].score = max(units[j].score, s - 0.5)


def _trim_to_plan(text: str, plan: str) -> str:
    """'Storage per workspace: Basic: 10 GB; Pro: 100 GB; Enterprise: 1 TB' -> '... Pro: 100 GB'."""
    if "; " not in text or len(plans_in(text)) < 2:
        return text
    segs = text.split("; ")
    head = segs[0]
    m = re.match(r"^(.*?):\s*(Basic|Pro|Enterprise):", head)
    label = m.group(1) if m else None
    keep = [s for s in segs if re.search(rf"\b{plan}\b", s)]
    if not keep:
        return text
    seg = keep[0]
    if label and not seg.startswith(label):
        return f"{label}: {seg}"
    return seg


def _other_plan_only(text: str, plan: str | None) -> bool:
    if not plan:
        return False
    ps = plans_in(text)
    return bool(ps) and plan not in ps


def _doc_date(h: Hit) -> str:
    return str(h.chunk.meta.get("effective_date") or "")


# ------------------------------------------------------------------ main entry
def ask(question: str, version: str | None = None, plan: str | None = None, mode: str = "full",
        use_llm: bool | None = None, k_final: int = K_FINAL) -> dict:
    t0 = time.perf_counter()
    th = thresholds()
    idx = get_index()
    q_version = version or version_in_query(question)
    q_plan = plan or plan_in_query(question)

    hits, info = idx.search(question, mode=mode, version=q_version, k_final=k_final)
    if hits and "rerank" not in hits[0].scores:  # modes without reranker still need a calibrated gate score
        ce = ce_scores(question, info.get("expansion", []), [h.chunk.embed_text for h in hits])
        for h, s in zip(hits, ce):
            h.scores["rerank"] = s
    t_ret = time.perf_counter()

    resp = {
        "question": question, "status": None, "answer": [], "citations": [], "conflicts": [],
        "clarifying_question": None, "options": [], "closest_topics": [], "confidence": None,
        "signals": {}, "filters": {"version": q_version, "plan": q_plan},
        "query_expansion": info.get("expansion", []), "generator": "extractive", "warnings": [],
        "retrieved": [_hit_debug(h) for h in hits],
    }
    top = max((h.scores["rerank"] for h in hits), default=-99.0)
    second = sorted((h.scores["rerank"] for h in hits), reverse=True)[1] if len(hits) > 1 else -99.0
    resp["signals"].update({"top_score": round(top, 3), "margin": round(top - second, 3)})

    unknown = unknown_terms(question, idx.vocab)
    if unknown:
        resp["signals"]["unknown_terms"] = unknown
    if not hits or unknown:
        return _finish(_not_found(resp, hits, unknown), t0, t_ret)

    evidence = hits[:EVIDENCE_PASSAGES]
    units = _make_units(evidence)
    _score_units(question, units, info.get("expansion"))
    usable = [u for u in units if not _other_plan_only(u.text, q_plan) and not _is_pointer(u.text)]
    best = max((u.score for u in usable), default=-99.0)
    resp["signals"]["best_sentence_score"] = round(best, 3)
    # Evidence gate: the best passage OR the best single sentence must be strong enough. A long
    # passage (e.g. a whole limits table) can score low overall while one row answers exactly.
    if max(top, best) < th["nf_top"] or best < th["sent_min"]:
        return _finish(_not_found(resp, hits, []), t0, t_ret)

    cands = [u for u in usable if u.score >= th["claim_min"] and u.score >= best - th["claim_margin"]]
    clar = _clarification(cands, best, q_plan, th)
    conflicts = _conflicts(cands, q_plan)

    cites = _CitationTable()
    if llm.available() and use_llm is not False:
        try:
            _llm_answer(question, evidence, resp, cites)
        except Exception as e:  # network, auth, refusal, bad JSON -> local generator
            resp["warnings"].append(f"LLM unavailable, used extractive answer ({type(e).__name__}: {str(e)[:120]})")
            resp["answer"] = []
            cites = _CitationTable()
    if not resp["answer"] and resp["status"] != "NOT_FOUND":
        _extractive_answer(usable, best, q_plan, th, resp, cites)

    if clar:
        resp["status"] = "NEEDS_CLARIFICATION"
        resp["clarifying_question"] = clar["question"]
        resp["options"] = [{"plan": o["plan"], "text": o["text"], "citation": cites.add(o["unit"].hit, o["text"])}
                           for o in clar["options"]]
        resp["answer"] = [{"kind": "note", "text": f"The answer depends on your plan. {clar['question']}",
                           "citations": [], "verified": True}]
    if conflicts:
        resp["conflicts"] = [_conflict_view(c, cites) for c in conflicts]
        if resp["status"] in (None, "ANSWERED"):
            resp["status"] = "ANSWERED_WITH_CONFLICT"
            _conflict_answer(resp, conflicts, cites)
    if resp["status"] is None:
        resp["status"] = "ANSWERED" if resp["answer"] else "NOT_FOUND"
    if resp["status"] == "NOT_FOUND":
        return _finish(_not_found(resp, hits, []), t0, t_ret)

    resp["citations"] = cites.items
    claims = [a for a in resp["answer"] if a["kind"] != "note"]
    resp["signals"]["verified_ratio"] = round(sum(a["verified"] for a in claims) / max(1, len(claims)), 3)
    resp["signals"]["supporting_passages"] = len({c["chunk_id"] for c in cites.items})
    resp["confidence"] = _confidence(top, best, resp, th)
    return _finish(resp, t0, t_ret)


# ------------------------------------------------------------------ citations
class _CitationTable:
    def __init__(self):
        self.items: list[dict] = []
        self.by_chunk: dict[str, int] = {}

    def add(self, hit: Hit, quote: str) -> int:
        c = hit.chunk
        if c.chunk_id in self.by_chunk:
            n = self.by_chunk[c.chunk_id]
            item = self.items[n - 1]
            if quote and quote not in item["quotes"]:
                item["quotes"].append(quote)
            return n
        n = len(self.items) + 1
        self.by_chunk[c.chunk_id] = n
        link = f"/api/documents/{c.doc_id}/file"
        if c.page_start:
            link += f"#page={c.page_start}"
        elif c.anchor:
            link += f"#{c.anchor}"
        self.items.append({
            "n": n, "chunk_id": c.chunk_id, "doc_id": c.doc_id, "doc_title": c.doc_title, "source": c.source,
            "format": c.fmt, "section": c.section, "location": c.location, "page": c.page_start,
            "anchor": c.anchor, "version": c.meta.get("version"), "effective_date": c.meta.get("effective_date"),
            "excerpt": c.text, "quotes": [quote] if quote else [], "link": link,
            "score": round(hit.scores.get("rerank", 0.0), 3),
        })
        return n


# ------------------------------------------------------------------ generators
def _extractive_answer(units: list[Unit], best: float, plan: str | None, th: dict, resp: dict,
                       cites: _CitationTable) -> None:
    pool = sorted((u for u in units if u.score >= th["sent_min"] and u.score >= best - th["sent_margin"]),
                  key=lambda u: -u.score)
    chosen: list[Unit] = []
    lists_used: set[str] = set()
    for u in pool:
        if len(chosen) >= int(th["max_units"]):
            break
        if any(_jaccard(u.text, c.text) > 0.6 for c in chosen):
            continue
        if u.numbered and u.hit.chunk.chunk_id in lists_used:
            continue
        chosen.append(u)
        if u.numbered:
            lists_used.add(u.hit.chunk.chunk_id)
    for u in chosen:
        if u.numbered:  # a procedure: return all its steps, in order
            steps = [s for s in split_sentences(u.hit.chunk.text) if _NUMBERED.match(s)]
            text = "\n".join(steps)
            kind = "list"
        else:
            text = _trim_to_plan(u.text, plan) if plan else u.text
            kind = "claim"
        n = cites.add(u.hit, text)
        resp["answer"].append({"kind": kind, "text": text, "citations": [n],
                               "verified": verify(text, u.hit.chunk.text)})


def _llm_answer(question: str, evidence: list[Hit], resp: dict, cites: _CitationTable) -> None:
    passages = []
    for i, h in enumerate(evidence, 1):
        c = h.chunk
        label = f"{c.doc_title} > {c.section}"
        extra = ", ".join(x for x in (f"version {c.meta['version']}" if c.meta.get("version") else "",
                                      f"effective {c.meta['effective_date']}" if c.meta.get("effective_date") else "") if x)
        passages.append({"n": i, "label": f"{label} ({extra})" if extra else label, "text": c.text})
    out = llm.generate(question, passages)
    resp["generator"] = f"llm:{llm.LLM_MODEL}"
    dropped = 0
    for s in out.get("sentences", []):
        refs = [r for r in s.get("citations", []) if isinstance(r, int) and 1 <= r <= len(evidence)]
        text = s.get("text", "").strip()
        if not text or not refs:
            dropped += 1
            continue
        joined = "\n".join(evidence[r - 1].chunk.text for r in refs)
        if not verify(text, joined):
            dropped += 1  # unsupported sentence removed (FR-04)
            continue
        ns = [cites.add(evidence[r - 1], text) for r in refs]
        resp["answer"].append({"kind": "claim", "text": text, "citations": ns, "verified": True})
    if dropped:
        resp["warnings"].append(f"{dropped} generated sentence(s) removed by verification")
    status = out.get("status")
    if status == "NOT_FOUND" or (not resp["answer"] and status != "NEEDS_CLARIFICATION"):
        resp["status"] = "NOT_FOUND"
        resp["answer"] = []
        return
    if status == "NEEDS_CLARIFICATION" and out.get("clarifying_question"):
        resp["status"] = "NEEDS_CLARIFICATION"
        resp["clarifying_question"] = out["clarifying_question"]
    for c in out.get("conflicts", []):
        ps = [p for p in c.get("passages", []) if isinstance(p, int) and 1 <= p <= len(evidence)]
        if len({evidence[p - 1].chunk.doc_id for p in ps}) >= 2:  # only cross-document disagreements
            resp.setdefault("llm_conflicts", []).append(
                {"description": c.get("description", ""), "citations": [cites.add(evidence[p - 1], "") for p in ps]})
    if resp.get("llm_conflicts") and resp["status"] is None:
        resp["status"] = "ANSWERED_WITH_CONFLICT"


# ------------------------------------------------------------------ FR-06 / FR-07 checks
def _clarification(cands: list[Unit], best: float, plan: str | None, th: dict) -> dict | None:
    """NEEDS_CLARIFICATION when the strongest evidence gives different values per plan and the
    user did not say which plan they are on."""
    if plan:
        return None
    near = [u for u in cands if u.score >= best - th.get("clarify_margin", 1.5)]
    per_dim: dict[str, dict[str, list[tuple[Unit, Quantity]]]] = defaultdict(lambda: defaultdict(list))
    for u in near:
        for q in u.qtys:
            if q.plan:
                per_dim[q.dim][q.plan].append((u, q))
    for dim, by_plan in sorted(per_dim.items(), key=lambda kv: -len(kv[1])):
        values = {round(q.value, 6) for lst in by_plan.values() for _, q in lst}
        if len(by_plan) >= 2 and len(values) >= 2:
            # triggered by the strongest evidence; options come from all relevant evidence
            for u in cands:
                for q in u.qtys:
                    if q.plan and q.dim == dim and all(x[0] is not u for x in by_plan[q.plan]):
                        by_plan[q.plan].append((u, q))
            options = []
            for p in PLANS:
                if p in by_plan:
                    u, q = max(by_plan[p], key=lambda x: (x[1].dim == dim, x[0].score))
                    trimmed = _trim_to_plan(u.text, p)
                    text = trimmed if trimmed != u.text else f"{p}: {q.raw}"
                    options.append({"plan": p, "unit": u, "text": text})
            names = [o["plan"] for o in options]
            plist = ", ".join(names[:-1]) + f" or {names[-1]}"
            return {"question": f"Which plan are you on: {plist}?", "options": options, "dim": dim}
    return None


def _conflicts(cands: list[Unit], plan: str | None) -> list[dict]:
    """Cross-document numeric disagreements about the same thing (FR-06).
    Candidate pair = same quantity dimension, different values, different documents,
    same plan scope, and the two sentences share at least two content words."""
    claims = []
    for u in cands:
        for q in u.qtys:
            if q.dim in ("count", "percent", "characters", "widgets"):
                continue
            if plan and q.plan and q.plan != plan:
                continue
            claims.append((u, q))
    groups: dict[str, list[tuple[Unit, Quantity]]] = {}
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            (ua, qa), (ub, qb) = claims[i], claims[j]
            if ua.hit.chunk.doc_id == ub.hit.chunk.doc_id or qa.dim != qb.dim:
                continue
            if abs(qa.value - qb.value) < 1e-9:
                continue
            if (qa.plan or plan) != (qb.plan or plan):
                continue
            ta = content_terms(ua.text) - {p.lower() for p in PLANS}
            tb = content_terms(ub.text) - {p.lower() for p in PLANS}
            if len(ta & tb) < 2:
                continue
            g = groups.setdefault(f"{qa.dim}:{qa.plan or plan or ''}", [])
            for item in ((ua, qa), (ub, qb)):
                if all(not (x[0] is item[0] and x[1].value == item[1].value) for x in g):
                    g.append(item)
    out = []
    for key, items in groups.items():
        dim, scope = key.split(":", 1)
        by_value: dict[float, list[tuple[Unit, Quantity]]] = defaultdict(list)
        for u, q in items:
            by_value[round(q.value, 6)].append((u, q))
        if len(by_value) < 2:
            continue
        sides = []
        for v, lst in by_value.items():
            u, q = max(lst, key=lambda x: (_doc_date(x[0].hit), x[0].score))
            sides.append({"unit": u, "qty": q, "date": _doc_date(u.hit)})
        sides.sort(key=lambda s: s["date"], reverse=True)
        out.append({"dim": dim, "scope": scope, "sides": sides})
    out.extend(_version_updates(cands, {id(s["unit"]) for c in out for s in c["sides"]}))
    return out


_UPDATE_MARKERS = re.compile(r"\b(in addition to|no longer|replaced by|deprecated)\b", re.IGNORECASE)


def _is_change_statement(u: Unit) -> bool:
    """An explicit change: change wording AND (a changelog / release-notes source, or 'now')."""
    if not _UPDATE_MARKERS.search(u.text):
        return False
    return u.hit.chunk.meta.get("doc_type") == "release_notes" or bool(re.search(r"\bnow\b", u.text, re.IGNORECASE))


def _version_updates(cands: list[Unit], already: set[int]) -> list[dict]:
    """Non-numeric, version-sensitive changes: a newer document explicitly changes something an
    older document states ("exports now support Parquet in addition to CSV and JSON")."""
    out = []
    for u in cands:
        if id(u) in already or not _is_change_statement(u):
            continue
        newer = _doc_date(u.hit)
        tu = content_terms(u.text)
        olds = [o for o in cands if o.hit.chunk.doc_id != u.hit.chunk.doc_id and _doc_date(o.hit) < newer
                and id(o) not in already and len(tu & content_terms(o.text)) >= 2 and not _UPDATE_MARKERS.search(o.text)]
        if not olds:
            continue
        old = max(olds, key=lambda o: o.score)
        q_dummy = Quantity(value=0, raw="changed in a newer version", dim="update", start=0)
        out.append({"dim": "update", "scope": "", "sides": [
            {"unit": u, "qty": q_dummy, "date": newer},
            {"unit": old, "qty": Quantity(value=0, raw="earlier statement", dim="update", start=0),
             "date": _doc_date(old.hit)}]})
        already.update({id(u), id(old)})
    return out


def _conflict_view(c: dict, cites: _CitationTable) -> dict:
    newest = c["sides"][0]["date"]
    claims = []
    for s in c["sides"]:
        ch = s["unit"].hit.chunk
        claims.append({
            "value": s["qty"].raw, "text": s["unit"].text, "citation": cites.add(s["unit"].hit, s["unit"].text),
            "doc_title": ch.doc_title, "version": ch.meta.get("version"), "effective_date": ch.meta.get("effective_date"),
            "newest": bool(s["date"]) and s["date"] == newest,
        })
    label = {"time": "duration", "req_per_min": "rate limit", "attempts": "number of attempts", "size": "size",
             "money": "price", "users": "user limit", "calls": "API call limit"}.get(c["dim"], c["dim"])
    newest_doc = next((x for x in claims if x["newest"]), None)
    if c["dim"] == "update":
        old = claims[-1]
        return {"dimension": "version change", "scope": None, "claims": claims,
                "note": f"{claims[0]['doc_title']} (version {claims[0]['version'] or '?'}, effective "
                        f"{claims[0]['effective_date'] or '?'}) changes what {old['doc_title']} says. "
                        "The older statement is shown for transparency; check which version applies to you."}
    note = f"Sources disagree on the {label}" + (f" for the {c['scope']} plan" if c["scope"] else "") + "."
    if newest_doc:
        note += (f" The most recent source is {newest_doc['doc_title']}"
                 + (f" (version {newest_doc['version']}" if newest_doc["version"] else " (")
                 + (f", effective {newest_doc['effective_date']})" if newest_doc["effective_date"] else ")")
                 + ". Check which version applies to you; the older statement is shown for transparency.")
    return {"dimension": label, "scope": c["scope"] or None, "claims": claims, "note": note}


def _conflict_answer(resp: dict, conflicts: list[dict], cites: _CitationTable) -> None:
    """Rebuild the answer so that every side of each conflict is stated with its citation."""
    answer = []
    seen: set[str] = set()
    for c in conflicts:
        for i, s in enumerate(c["sides"]):
            u = s["unit"]
            if u.text in seen:
                continue
            seen.add(u.text)
            ch = u.hit.chunk
            src = ch.doc_title + (f" v{ch.meta['version']}" if ch.meta.get("version") else "")
            prefix = "Most recent" if i == 0 else "Older"
            answer.append({"kind": "claim", "text": u.text, "citations": [cites.add(u.hit, u.text)],
                           "verified": verify(u.text, ch.text), "label": f"{prefix} ({src}, {s['date'] or 'undated'})"})
    for a in resp["answer"]:  # keep other, non-conflicting answer sentences
        if a["text"] not in seen and a["kind"] != "note":
            answer.append(a)
    resp["answer"] = answer


def _confidence(top: float, best: float, resp: dict, th: dict) -> str:
    if resp["status"] in ("NEEDS_CLARIFICATION", "ANSWERED_WITH_CONFLICT"):
        return "medium"
    if top >= th["nf_top"] + 5 and best >= th["sent_min"] + 4:
        return "high"
    if top >= th["nf_top"] + 2 and best >= th["sent_min"] + 2:
        return "medium"
    return "low"


def _not_found(resp: dict, hits: list[Hit], unknown: list[str]) -> dict:
    resp["status"] = "NOT_FOUND"
    resp["answer"], resp["citations"], resp["conflicts"], resp["options"] = [], [], [], []
    resp["clarifying_question"] = None
    resp["confidence"] = None
    msg = "I couldn't find this in the documentation."
    if unknown:
        msg += f" The documents never mention {', '.join(unknown)}."
    resp["message"] = msg
    seen = set()
    for h in hits:
        t = f"{h.chunk.doc_title} > {h.chunk.section}"
        if t not in seen:
            seen.add(t)
            resp["closest_topics"].append({"topic": t, "doc_id": h.chunk.doc_id, "chunk_id": h.chunk.chunk_id})
        if len(resp["closest_topics"]) == 3:
            break
    return resp


def _hit_debug(h: Hit) -> dict:
    c = h.chunk
    return {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "doc_title": c.doc_title, "section": c.section, "location": c.location,
            "scores": {k: round(v, 4) if isinstance(v, float) else v for k, v in h.scores.items()}}


def _finish(resp: dict, t0: float, t_ret: float) -> dict:
    t1 = time.perf_counter()
    resp["timings_ms"] = {"retrieval": round((t_ret - t0) * 1000, 1), "answer": round((t1 - t_ret) * 1000, 1),
                          "total": round((t1 - t0) * 1000, 1)}
    if resp["status"] != "NOT_FOUND":
        resp.pop("message", None)
    return resp
