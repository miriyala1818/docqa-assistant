// DocQA Assistant frontend: talks to the FastAPI backend (/api/*) and renders answers.
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
};
const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2600); };
let LLM_AVAILABLE = false;

const EXAMPLES = [
  ["direct", "How do I reset my password?"],
  ["paraphrase", "I lost the phone with my authenticator app. How do I sign in?"],
  ["multi-passage", "Can I get a refund if I upgrade mid-cycle?"],
  ["conflict", "What is the API rate limit?"],
  ["ambiguous", "How much storage do I get?"],
  ["unsupported", "Does Orbitly integrate with SAP?"],
];

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    LLM_AVAILABLE = h.llm.available;
    const b = $("#gen-badge");
    b.lastChild.textContent = h.llm.available ? `Claude · ${h.llm.model}` : "Local extractive mode";
    b.title = h.llm.available ? "Answers are worded by Claude, then every sentence is verified against its cited passage."
                              : "No API key configured: answers are extracted verbatim from the documents (fully offline).";
    $("#llm-toggle").hidden = !h.llm.available;
    $("#stats-badge").textContent = `${h.documents} documents · ${h.chunks} passages`;
  } catch (e) { $("#gen-badge").lastChild.textContent = "server offline"; }
}

async function loadDocs() {
  const box = $("#docs");
  try {
    const d = await api("/api/documents");
    box.replaceChildren();
    if (!d.documents.length) { box.append(el("div", { class: "empty" }, "No documents yet.")); return; }
    for (const doc of d.documents) {
      const meta = [doc.meta.version && `v${doc.meta.version}`, doc.meta.effective_date, `${doc.n_chunks} passages`, doc.meta.pages && `${doc.meta.pages} pages`].filter(Boolean).join(" · ");
      box.append(el("div", { class: "doc" },
        el("span", { class: "fmt" }, doc.format === "markdown" ? "MD" : doc.format),
        el("a", { class: "t", href: `/api/documents/${encodeURIComponent(doc.doc_id)}/file`, target: "_blank", rel: "noopener", style: "color:inherit;text-decoration:none" }, doc.title),
        el("button", { class: "icon-btn", title: `Remove ${doc.source}`, "aria-label": `Remove ${doc.source}`, onclick: () => removeDoc(doc) }, "✕"),
        el("div", { class: "m" }, `${doc.source} · ${meta}`)));
    }
  } catch (e) { box.replaceChildren(el("div", { class: "empty" }, `Could not load documents: ${e.message}`)); }
}

async function removeDoc(doc) {
  if (!confirm(`Remove "${doc.title}" (${doc.source}) from the collection? The file is deleted from knowledge_base/.`)) return;
  try { await api(`/api/documents/${encodeURIComponent(doc.doc_id)}`, { method: "DELETE" }); toast("Removed and re-indexed"); }
  catch (e) { toast(e.message); }
  loadDocs(); loadHealth();
}

async function upload(files) {
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  const drop = $("#drop");
  const old = drop.innerHTML;
  drop.querySelector("strong").textContent = "Indexing…";
  try {
    const r = await api("/api/documents", { method: "POST", body: fd });
    toast(`Indexed ${r.uploaded.join(", ")} (${r.index.chunks} passages total)`);
  } catch (e) { toast(e.message); }
  drop.innerHTML = old;
  bindDrop();
  loadDocs(); loadHealth();
}

function bindDrop() {
  const drop = $("#drop"), input = $("#file");
  input.onchange = () => upload([...input.files]);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); };
}

/* ---------- rendering ---------- */
const STATUS_TEXT = {
  ANSWERED: "ANSWERED", ANSWERED_WITH_CONFLICT: "SOURCES DISAGREE", NEEDS_CLARIFICATION: "NEEDS CLARIFICATION", NOT_FOUND: "NOT FOUND",
};

function highlight(text, quotes, question) {
  // Build DOM nodes (never innerHTML): quoted sentences -> <mark>, question words -> .term
  const frag = document.createDocumentFragment();
  const ranges = [];
  for (const q of quotes || []) {
    const parts = q.split("\n").map(s => s.trim()).filter(s => s.length > 3);
    for (const p of parts) { const i = text.indexOf(p); if (i >= 0) ranges.push([i, i + p.length]); }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const words = [...new Set((question.toLowerCase().match(/[a-z0-9]{4,}/g) || []).filter(w => !["what", "when", "does", "with", "have", "from", "your", "that", "this", "much", "many", "long", "where", "which", "there", "about"].includes(w)))];
  const termRe = words.length ? new RegExp(`\\b(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\w*`, "gi") : null;
  const addTerms = (s, parent) => {
    if (!termRe) { parent.append(s); return; }
    let last = 0;
    for (const m of s.matchAll(termRe)) { parent.append(s.slice(last, m.index), el("span", { class: "term" }, m[0])); last = m.index + m[0].length; }
    parent.append(s.slice(last));
  };
  let pos = 0;
  for (const [a, b] of ranges) {
    if (a < pos) continue;
    addTerms(text.slice(pos, a), frag);
    const m = el("mark"); addTerms(text.slice(a, b), m); frag.append(m);
    pos = b;
  }
  addTerms(text.slice(pos), frag);
  return frag;
}

function citeChip(n, ctx) {
  return el("button", { class: "cite", type: "button", title: "Show the cited passage", onclick: (e) => toggleExcerpt(n, ctx, e.currentTarget) }, `${n}`);
}

function excerptNode(c, question) {
  const where = [c.section, c.location].filter(Boolean).join(" · ");
  const ver = [c.version && `v${c.version}`, c.effective_date && `effective ${c.effective_date}`].filter(Boolean).join(", ");
  return el("div", { class: "excerpt", "data-n": c.n },
    el("div", { class: "head" }, el("b", {}, `[${c.n}] ${c.doc_title}`), el("span", { class: "meta" }, where), ver && el("span", { class: "meta" }, ver),
      el("a", { href: c.link, target: "_blank", rel: "noopener" }, "Open source ↗")),
    el("div", { class: "body" }, highlight(c.excerpt, c.quotes, question)),
    el("div", { class: "meta" }, `${c.source} · passage ${c.chunk_id} · rerank ${c.score}`));
}

function toggleExcerpt(n, ctx, chip) {
  const holder = ctx.holder;
  const open = holder.querySelector(`.excerpt[data-n="${n}"]`);
  ctx.card.querySelectorAll(".cite").forEach(b => b.classList.toggle("on", false));
  holder.replaceChildren();
  if (open) return;
  const c = ctx.cites[n];
  if (!c) return;
  holder.append(excerptNode(c, ctx.question));
  ctx.card.querySelectorAll(".cite").forEach(b => { if (b.textContent === String(n)) b.classList.add("on"); });
}

function render(r) {
  const card = el("div", { class: "panel card" });
  const cites = Object.fromEntries((r.citations || []).map(c => [c.n, c]));
  const holder = el("div");
  const ctx = { card, cites, holder, question: r.question };
  card.append(el("div", { class: "qline" },
    el("span", { class: `status s-${r.status}` }, STATUS_TEXT[r.status] || r.status),
    el("span", { class: "q" }, r.question),
    r.confidence && el("span", { class: "conf" }, `confidence: ${r.confidence}`)));

  const ans = el("div", { class: "answer" });
  for (const a of r.answer || []) {
    if (a.kind === "note" && r.status === "NEEDS_CLARIFICATION") continue;
    const chips = a.citations.map(n => citeChip(n, ctx));
    if (a.kind === "list") {
      const ol = el("ol");
      a.text.split("\n").forEach((line, i, arr) => ol.append(el("li", {}, line.replace(/^\d+[.)]\s*/, ""), i === arr.length - 1 ? chips : null)));
      ans.append(ol);
    } else {
      ans.append(el("p", {}, a.label && el("span", { class: "label" }, a.label), a.text.replace(/^-\s+/, ""), chips));
    }
  }
  card.append(ans);

  for (const c of r.conflicts || []) {
    card.append(el("div", { class: "box conflict" },
      el("h3", {}, `⚠ Sources disagree: ${c.dimension}${c.scope ? ` (${c.scope} plan)` : ""}`),
      el("div", { class: "sides" }, c.claims.map(x => el("div", { class: "side-claim" },
        el("div", { class: "v" }, x.value, x.newest && el("span", { class: "newest" }, "NEWEST")),
        el("div", {}, x.text.replace(/^-\s+/, ""), citeChip(x.citation, ctx)),
        el("div", { class: "src" }, [x.doc_title, x.version && `v${x.version}`, x.effective_date].filter(Boolean).join(" · "))))),
      el("p", { style: "margin:10px 0 0;font-size:13px" }, c.note)));
  }
  if (r.status === "NEEDS_CLARIFICATION") {
    card.append(el("div", { class: "box clarify" },
      el("h3", {}, r.clarifying_question || "Can you clarify?"),
      el("div", { style: "font-size:13px;color:var(--muted);margin-bottom:6px" }, "The documents give different answers depending on this. Choose one to get a specific answer:"),
      (r.options || []).map(o => el("div", { class: "opt" },
        el("button", { type: "button", onclick: () => { $("#plan").value = o.plan; ask(r.question); } }, o.plan),
        el("span", {}, o.text, citeChip(o.citation, ctx))))));
  }
  if (r.status === "NOT_FOUND") {
    card.append(el("div", { class: "box notfound" },
      el("h3", {}, r.message || "Not found in the documentation."),
      r.closest_topics?.length ? el("div", {}, el("div", { style: "font-size:13px" }, "Closest related topics in the collection:"),
        el("ul", { class: "topics" }, r.closest_topics.map(t => el("li", {}, t.topic)))) : null));
  }
  card.append(holder);

  if (r.citations?.length) {
    card.append(el("div", { class: "sources" }, el("h2", {}, "Sources"),
      r.citations.map(c => el("div", { class: "src-row" }, el("span", { class: "n" }, c.n),
        el("a", { href: "#", onclick: (e) => { e.preventDefault(); toggleExcerpt(c.n, ctx); } }, c.doc_title),
        el("span", { class: "muted" }, [c.section, c.location, c.version && `v${c.version}`, c.effective_date].filter(Boolean).join(" · "))))));
  }
  for (const w of r.warnings || []) card.append(el("div", { class: "warn-line" }, w));

  const sig = r.signals || {};
  card.append(el("details", {},
    el("summary", {}, `Retrieval details · ${r.timings_ms?.total ?? "?"} ms · ${r.generator}`),
    el("div", { style: "margin-top:6px;color:var(--muted)" },
      `Signals: top passage score ${sig.top_score}, rank-1/2 margin ${sig.margin}` +
      (sig.best_sentence_score !== undefined ? `, best sentence ${sig.best_sentence_score}` : "") +
      (sig.verified_ratio !== undefined ? `, verified ${Math.round(sig.verified_ratio * 100)}%` : "") +
      (sig.unknown_terms ? `, terms not in collection: ${sig.unknown_terms.join(", ")}` : "") +
      (r.query_expansion?.length ? ` · query expansion: ${r.query_expansion.join(", ")}` : "") +
      (r.filters?.plan || r.filters?.version ? ` · filters: ${[r.filters.plan, r.filters.version && "v" + r.filters.version].filter(Boolean).join(", ")}` : "")),
    el("table", {},
      el("tr", {}, ["#", "Passage", "Section", "BM25", "Dense", "RRF", "Rerank"].map(h => el("th", {}, h))),
      (r.retrieved || []).map((h, i) => el("tr", {},
        el("td", {}, i + 1), el("td", {}, h.chunk_id), el("td", {}, `${h.doc_title} › ${h.section}`),
        ["bm25", "dense", "rrf", "rerank"].map(k => el("td", { class: "num" }, h.scores[k] !== undefined ? Number(h.scores[k]).toFixed(3) : "–")))))));
  return card;
}

async function ask(question) {
  question = (question ?? $("#q").value).trim();
  if (question.length < 2) return;
  $("#q").value = question;
  const btn = $("#ask-btn");
  btn.disabled = true; btn.replaceChildren(el("span", { class: "spinner" }));
  try {
    const body = { question, plan: $("#plan").value || null, version: $("#version").value || null, use_llm: LLM_AVAILABLE ? $("#use-llm").checked : null };
    const r = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("#results").prepend(render(r));
  } catch (e) { toast(`Error: ${e.message}`); }
  btn.disabled = false; btn.textContent = "Ask";
}

$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
for (const [cat, q] of EXAMPLES) $("#examples").append(el("button", { class: "ex", type: "button", onclick: () => { $("#plan").value = ""; ask(q); } }, el("small", {}, cat), q));
bindDrop();
loadHealth().then(() => {
  // shareable links: /?q=question&plan=Pro (several q= params are asked in order)
  const params = new URLSearchParams(location.search);
  if (params.get("plan")) $("#plan").value = params.get("plan");
  params.getAll("q").reduce((p, q) => p.then(() => ask(q)), Promise.resolve());
});
loadDocs();
