// Client-side BM25 + topic/alias database + evidence heuristic. No server, no API calls, $0 per query.
const STOP = new Set("a an the of to in on and or is was were be been are that this it its by for with as at from did do does have has had they their them he she his her not but if so than then there these those who whom which what when where why how all any can could would should will into about cia agency us".split(" "));
const fold = s => (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
const tok = s => (fold(s).toLowerCase().match(/[a-z0-9]+/g) || []).filter(w => w.length > 1 && !STOP.has(w)).map(stem);
function stem(w){ return w.replace(/(ies)$/,"y").replace(/(ing|ed|es|s)$/,"").slice(0,12) || w; }

const CONFIRM = /\b(confirm\w*|approv\w*|authori[sz]\w*|fund\w*|conduct\w*|implement\w*|operation\w*|program\w*|establish\w*|admit\w*|acknowledg\w*|carried out|succeed\w*|verified)\b/gi;
const DENY = /\b(no evidence|unfounded|baseless|false|hoax|disinformation|propaganda|fabricat\w*|rumou?r\w*|den(y|ied|ies)|inconclusive|unsubstantiated|no indication|not true|terminated|myth|speculat\w*)\b/gi;

let D = [], META = {}, T = {}, idx = null;

// Same rule as scraper/topics.py: tokens joined by [space/-/slash/nothing], optional plural s,
// ALL-CAPS aliases case-sensitive in documents. User questions are always matched case-insensitively.
function aliasRx(alias, forQuery){
  const a = fold(alias), toks = a.match(/[A-Za-z0-9]+/g) || [];
  const body = toks.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s\\-/]*");
  const cs = !forQuery && a.toUpperCase() === a && /[A-Za-z]/.test(a);
  return new RegExp("(?<![A-Za-z0-9])" + body + "s?(?![A-Za-z0-9])", cs ? "g" : "gi");
}

async function load(){
  const r = await fetch("data/corpus.json"); const j = await r.json();
  D = j.docs; META = j.meta; T = j.topics || {};
  for (const [id, t] of Object.entries(T)) { t.id = id;
    t.rxDoc = t.aliases.map(a => aliasRx(a, false)); t.rxQ = [...t.aliases, ...(t.qaliases || [])].map(a => aliasRx(a, true)); }
  const N = D.length, df = {}, tf = [], len = [];
  D.forEach(d => { d.k = d.k || []; const t = tok(d.t + " " + d.t + " " + d.x); const m = {};
    t.forEach(w => m[w] = (m[w]||0)+1); tf.push(m); len.push(t.length);
    Object.keys(m).forEach(w => df[w] = (df[w]||0)+1); });
  const avg = len.reduce((a,b)=>a+b,0)/Math.max(N,1);
  idx = {N, df, tf, len, avg};
  const b = document.getElementById("banner");
  if (META.demo) b.innerHTML = "⚠️ <b>DEMO MODE:</b> the corpus loaded is " + N + " synthetic placeholder documents, <b>not</b> real CIA records. Run the scraper (see README) to use real data.";
  else b.innerHTML = `Coverage: ${N.toLocaleString()} documents scraped (~${META.coverage_pct}% of an estimated ${META.est_total.toLocaleString()}). ${META.with_text.toLocaleString()} have extractable text. Missing documents can't count as evidence either way.`;
  document.getElementById("cov").textContent = `Index built ${META.built}. ${N} docs, ${META.tagged ?? 0} tagged with ${Object.keys(T).length} topics.`;
  renderChips();
}

// ---- topics in the question -------------------------------------------------
// Returns [{id, toks}] where toks = query tokens "explained" by that topic's alias (e.g. "psychic", "spy").
function queryTopics(raw){
  const out = [], s = fold(raw);
  for (const t of Object.values(T)) {
    const toks = new Set();
    t.rxQ.forEach(rx => { rx.lastIndex = 0; for (const m of s.matchAll(rx)) tok(m[0]).forEach(w => toks.add(w)); });
    if (toks.size) out.push({id: t.id, toks});
  }
  return out;
}
const mentions = (text, tid) => T[tid].rxDoc.some(rx => { rx.lastIndex = 0; return rx.test(fold(text)); });

// Tokens of q a document covers: literal words + words explained by a topic the doc is tagged with.
function docCover(i, q, qt){
  const m = idx.tf[i], got = new Set(q.filter(w => m[w]));
  qt.forEach(t => { if (D[i].k.includes(t.id)) t.toks.forEach(w => got.add(w)); });
  return got;
}

function rank(q, qt){
  const {N, df, tf, len, avg} = idx, k1 = 1.4, b = .75;
  const idf = w => Math.log(1 + (N - (df[w]||0) + .5)/((df[w]||0) + .5));
  return tf.map((m,i) => { let s = 0;
      q.forEach(w => { const f = m[w]; if (f) s += idf(w)*f*(k1+1)/(f + k1*(1-b+b*len[i]/avg)); });
      qt.forEach(t => { if (D[i].k.includes(t.id)) s += 2.5; });     // alias match = strong relevance signal
      return {i, s, hit: docCover(i, q, qt).size}; })
    .filter(r => r.s > 0).sort((a,b) => b.s - a.s);
}

function sentCover(s, q, qt){
  const got = new Set(tok(s).filter(w => q.includes(w)));
  qt.forEach(t => { if (mentions(s, t.id)) t.toks.forEach(w => got.add(w)); });
  return got;
}
function sentences(d, q, qt = []){
  return (d.x.match(/[^.!?]+[.!?]?/g) || []).filter(s => sentCover(s, q, qt).size);
}

function judge(raw){
  const q = [...new Set(tok(raw))], qt = queryTopics(raw);
  if (!q.length) return {score: 0, q, qt, hits: [], why: "No searchable words."};
  const res = rank(q, qt), top = res.slice(0, 5);
  const covered = q.filter(w => top.some(r => docCover(r.i, q, qt).has(w))).length / q.length;
  const best = top.length ? Math.max(...top.map(r => r.hit)) / q.length : 0;
  const vol = Math.min(1, res.filter(r => r.hit / q.length >= .5).length / 8);
  // PAPER TRAIL: do the files discuss this topic at all? (aliases count)
  const topic = top.length ? Math.round(100 * (.4*covered + .4*best + .2*vol)) : 0;
  // CLAIM: only sentences containing most of the claim's words (aliases count) are evidence for the claim.
  let c = 0, n = 0, strong = 0;
  const need = Math.max(1, Math.ceil(q.length * .6));
  top.forEach(r => sentences(D[r.i], q, qt).forEach(s => {
    if (sentCover(s, q, qt).size < need) return;
    strong++; c += (s.match(CONFIRM)||[]).length; n += (s.match(DENY)||[]).length; }));
  const stance = (c + n) ? (c - n) / (c + n) : 0;
  let score = top.length ? .35*topic + 65*Math.min(1, strong/3)*(stance+1)/2 : 3;
  // "Program existed" != "wild version is true": without a confirming full-claim sentence, cap the needle.
  if (!strong || c === 0) score = Math.min(score, 45);
  if (n >= c && strong) score = Math.min(score, 30);
  score = Math.max(0, Math.min(100, Math.round(score)));
  return {score, topic, strong, q, qt, hits: top, covered, best, stance, c, n};
}

function verdictText(r, raw){
  const s = r.score;
  const lab = s < 20 ? "Tinfoil hat! 🛸" : s < 40 ? "Mostly tinfoil." : s < 60 ? "Hmm… there's a paper trail on the topic."
            : s < 80 ? "The files talk about this a lot." : "Proven-ish! 📁 (by our very silly standards)";
  if (!r.hits.length) return `<b>${lab}</b> Nothing in the ${D.length.toLocaleString()} scraped documents matches “${esc(raw)}”. That may just mean we haven't scraped it.`;
  return `<b>${lab}</b> Claim support ${s}/100 · Paper trail ${r.topic}/100.<br>
   ${r.topic >= 50 && s < 50 ? "The files clearly discuss this <i>topic</i>, but that doesn't mean they back the <i>claim</i>. " : ""}
   ${r.hits.length} matching document(s); ${r.strong} sentence(s) contain most of your claim's words, and they leaned ${r.stance > .2 ? "confirmatory" : r.stance < -.2 ? "skeptical/denying" : "neutral"} (${r.c} confirm-ish vs ${r.n} deny-ish cues).`;
}

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function highlight(text, q, tids){
  let h = esc(text).replace(/[A-Za-z0-9]+/g, w => q.includes(stem(fold(w).toLowerCase())) ? `<mark>${w}</mark>` : w);
  tids.forEach(id => T[id].rxDoc.forEach(rx => { h = h.replace(rx, m => m.includes("<mark>") ? m : `<mark class="alias">${m}</mark>`); }));
  return h;
}
function excerpt(d, q, qt){
  const s = sentences(d, q, qt).slice(0, 2).join(" … ") || d.x.slice(0, 240);
  return highlight(s.slice(0, 400), q, qt.map(t => t.id));
}
function docLi(d, q = [], qt = []){
  return `<li><a href="${esc(d.u)}" target="_blank" rel="noopener">${esc(d.t || "(untitled)")}</a>
    <small>${esc(d.d)} ${esc(d.c)}${d.pdf ? ` · <a href="${esc(d.pdf)}" target="_blank" rel="noopener">PDF</a>` : ""}
    ${d.k.length ? " · " + d.k.map(id => T[id] ? `<span class="tag">${esc(T[id].name)}</span>` : "").join(" ") : ""}</small>
    <div>${excerpt(d, q, qt)}</div></li>`;
}

// ---- topic browser ------------------------------------------------------------
function chip(id, extra = ""){ const t = T[id]; return t ? `<button type="button" class="chip" data-topic="${id}">${esc(t.name)}${extra}</button>` : ""; }
function renderChips(){
  const ids = Object.keys(T).sort((a,b) => T[b].n - T[a].n || T[a].name.localeCompare(T[b].name));
  document.getElementById("chips").innerHTML = ids.map(id => chip(id, ` <small>${T[id].n}</small>`)).join("");
}
function topicCard(id){
  const t = T[id], yrs = t.years ? ` · ${t.years[0]}–${t.years[1]}` : "";
  const related = [...new Set([...t.see_also, ...t.co.map(c => c[0])])];
  return `<div class="tcard"><h3>📂 ${esc(t.name)}</h3><p>${esc(t.desc)}</p>
    <p><b>${t.n}</b> scraped document(s) mention this${yrs}.</p>
    <p class="aka"><b>Also searched as:</b> ${t.aliases.map(esc).join(" · ")}</p>
    ${related.length ? `<p><b>Related:</b> ${related.map(r => chip(r)).join(" ")}</p>` : ""}
    ${t.top.length ? `<details><summary>Top documents</summary><ol>${t.top.map(i => docLi(D[i])).join("")}</ol></details>` : ""}</div>`;
}
function showTopics(ids){
  const el = document.getElementById("tpanel");
  el.innerHTML = ids.map(topicCard).join(""); el.hidden = !ids.length;
}
document.addEventListener("click", e => {
  const b = e.target.closest(".chip"); if (!b) return;
  showTopics([b.dataset.topic]); document.getElementById("tpanel").scrollIntoView({behavior: "smooth"});
});

document.getElementById("f").addEventListener("submit", e => {
  e.preventDefault(); const raw = document.getElementById("q").value.trim(); if (!idx) return;
  const r = judge(raw);
  document.getElementById("out").hidden = false;
  requestAnimationFrame(() => document.getElementById("needle").style.transform = `rotate(${-90 + r.score*1.8}deg)`);
  document.getElementById("verdict").innerHTML = verdictText(r, raw) +
    (r.qt.length ? `<br><small>Recognized topic(s): ${r.qt.map(t => chip(t.id)).join(" ")}, so aliases and code names were searched too.</small>` : "");
  document.getElementById("hits").innerHTML = r.hits.map(h => docLi(D[h.i], r.q, r.qt)).join("");
  showTopics(r.qt.map(t => t.id));
});
load();
