// Client-side BM25 + topic/alias database + evidence heuristic. No server, no API calls, $0 per query.
// The index is split into small static files (built by scraper/build_index.py -> scraper/shard.py):
// the page loads meta.json + len.json up front, then fetches only the postings and documents a query needs.
const STOP = new Set("a an the of to in on and or is was were be been are that this it its by for with as at from did do does have has had they their them he she his her not but if so than then there these those who whom which what when where why how all any can could would should will into about cia agency us".split(" "));
const fold = s => (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
const tok = s => (fold(s).toLowerCase().match(/[a-z0-9]+/g) || []).filter(w => w.length > 1 && !STOP.has(w)).map(stem);
function stem(w){ return w.replace(/(ies)$/,"y").replace(/(ing|ed|es|s)$/,"").slice(0,12) || w; }

// Evidence cues, counted only in sentences that state most of the claim.
// "program"/"operation" were dropped: they name things (the Apollo program) rather than confirm them.
const CONFIRM = /\b(confirm\w*|approv\w*|authori[sz]\w*|fund\w*|conduct\w*|implement\w*|establish\w*|admit\w*|acknowledg\w*|carried out|succeed\w*|verified|documented|revealed)\b/gi;
const DENY = /\b(no evidence|unfounded|baseless|false|disinformation|propaganda|rumou?r\w*|den(y|ied|ies)|inconclusive|unsubstantiated|no indication|not true|myth|debunk\w*|never happened)\b/gi;
// Reported beliefs and fiction: the sentence repeats the claim without backing it. Counts half against.
const HEDGE = /\b(conspiracy theor\w*|theorists?|believ\w*|think|thinks|claim\w*|alleg\w*|supposed\w*|speculat\w*|movie|film|fiction\w*|novel|documentary|tabloid)\b/gi;

// Words that mean the same thing in a claim and in the files ("people" vs "subjects"). Stemmed forms (see stem()).
const SYN = {};
[["people","person","human","subject","patient","individual","volunteer","prisoner","citizen","civilian"],
 ["test","experiment","trial","dos","dose","administer"],
 ["kill","killed","murder","assassinat","assassinatio","assassin"],
 ["spy","spi","surveil","surveillance","monitor","wiretap","eavesdropp","infiltrat","spying"],
 ["protester","protest","demonstrator","activist","dissident","radical","antiwar"],
 ["fak","fake","hoax","fabricat","staged","forgery"],
 ["alien","extraterrest","ufo","saucer"],
 ["brainwash","mind","control"],
 ["studi","study","research","investigat","evaluat","examin","analyz","analysi"],
 ["scientist","engineer","specialist","expert","researcher","technician"],
 ["government","federal","administration","army","military"],
 ["america","american","united"],
 ["brought","bring","recruit","import","smuggl","transferr"]].forEach(g => g.forEach(w => SYN[w] = g[0]));
const canon = w => SYN[w] || w;
// Question filler that isn't part of the claim itself ("did the CIA *really* ...").
const FILLER = new Set("really actually truly secretly ever true real just also still even once".split(" ").map(stem));
// "cia"/"agency" are stop words for search (nearly every file says them), but when the question names the CIA
// as the actor, a supporting sentence (or its document's title) must too. Otherwise "the CIA killed JFK" is judged as just "killed JFK".
const ACTOR = /\b(cia|c\.\s?i\.\s?a|agency|central intelligence)\b/i;

let META = {}, T = {}, idx = null;

// ---- sharded index access ---------------------------------------------------
const cache = new Map();
const getJSON = path => {
  if (!cache.has(path)) cache.set(path, fetch("data/" + path).then(r => {
    if (!r.ok) throw new Error(`${r.status} loading data/${path}`); return r.json(); }));
  return cache.get(path);
};
// FNV-1a 32-bit, identical to term_shard() in scraper/shard.py
function termShard(term){
  let h = 0x811c9dc5;
  for (let i = 0; i < term.length; i++) h = Math.imul(h ^ term.charCodeAt(i), 0x01000193) >>> 0;
  return h % idx.termShards;
}
// -> {term: Map(docId -> tf)}
async function postings(terms){
  const out = {};
  await Promise.all(terms.map(async w => {
    const flat = (await getJSON(`p/${termShard(w)}.json`))[w] || [];
    const m = new Map(); let id = 0;
    for (let k = 0; k < flat.length; k += 2) { id += flat[k]; m.set(id, flat[k + 1]); }
    out[w] = m;
  }));
  return out;
}
const topicDocs = async tid => { const t = T[tid]; if (!t.set) t.set = new Set(await getJSON(`t/${tid}.json`)); return t.set; };
// -> Map(docId -> doc record)
async function getDocs(ids){
  const out = new Map(), per = idx.docShard;
  await Promise.all([...new Set(ids.map(i => Math.floor(i / per)))].map(async s => {
    const rows = await getJSON(`d/${s}.json`);
    rows.forEach((d, j) => { d.id = s * per + j; d.k = d.k || []; out.set(d.id, d); });
  }));
  return out;
}

// Same rule as scraper/topics.py: tokens joined by [space/-/slash/nothing], optional plural s,
// ALL-CAPS aliases case-sensitive in documents. User questions are always matched case-insensitively.
function aliasRx(alias, forQuery){
  const a = fold(alias), toks = a.match(/[A-Za-z0-9]+/g) || [];
  const body = toks.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s\\-/]*");
  const cs = !forQuery && a.toUpperCase() === a && /[A-Za-z]/.test(a);
  return new RegExp("(?<![A-Za-z0-9])" + body + "s?(?![A-Za-z0-9])", cs ? "g" : "gi");
}

async function load(){
  const [j, len] = await Promise.all([getJSON("meta.json"), getJSON("len.json")]);
  META = j.meta; T = j.topics || {};
  for (const [id, t] of Object.entries(T)) { t.id = id;
    t.rxDoc = t.aliases.map(a => aliasRx(a, false)); t.rxQ = [...t.aliases, ...(t.qaliases || [])].map(a => aliasRx(a, true)); }
  idx = {N: len.length, len, avg: META.index.avg_len, termShards: META.index.term_shards, docShard: META.index.doc_shard};
  const N = idx.N, b = document.getElementById("banner");
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
// P = postings for the query terms, TS = {topicId: Set(docIds)}
function docCover(i, q, qt, P, TS){
  const got = new Set(q.filter(w => P[w].has(i)));
  qt.forEach(t => { if (TS[t.id].has(i)) t.toks.forEach(w => got.add(w)); });
  return got;
}

function rank(q, qt, P, TS){
  const {N, len, avg} = idx, k1 = 1.4, b = .75;
  const idf = w => Math.log(1 + (N - P[w].size + .5)/(P[w].size + .5));
  const cand = new Set();
  q.forEach(w => P[w].forEach((_, i) => cand.add(i)));
  qt.forEach(t => TS[t.id].forEach(i => cand.add(i)));
  return [...cand].sort((a, b) => a - b).map(i => { let s = 0;
      q.forEach(w => { const f = P[w].get(i); if (f) s += idf(w)*f*(k1+1)/(f + k1*(1-b+b*len[i]/avg)); });
      qt.forEach(t => { if (TS[t.id].has(i)) s += 2.5; });     // alias match = strong relevance signal
      return {i, s, hit: docCover(i, q, qt, P, TS).size}; })
    .filter(r => r.s > 0).sort((a,b) => b.s - a.s);
}

function sentCover(s, q, qt){
  const qc = new Set(q.map(canon)), got = new Set();
  tok(s).forEach(w => { const c = canon(w); if (qc.has(c)) q.forEach(x => { if (canon(x) === c) got.add(x); }); });
  qt.forEach(t => { if (mentions(s, t.id)) t.toks.forEach(w => got.add(w)); });
  return got;
}
function sentences(d, q, qt = []){
  return (d.x.match(/[^.!?]+[.!?]?/g) || []).filter(s => sentCover(s, q, qt).size);
}

async function judge(raw){
  const q = [...new Set(tok(raw))], qt = queryTopics(raw);
  if (!q.length) return {score: 0, q, qt, hits: [], docs: new Map(), why: "No searchable words."};
  const TS = {};
  const [P] = await Promise.all([postings(q), ...qt.map(async t => { TS[t.id] = await topicDocs(t.id); })]);
  const res = rank(q, qt, P, TS), top = res.slice(0, 5);
  const docs = await getDocs(top.map(r => r.i));
  const covered = q.filter(w => top.some(r => docCover(r.i, q, qt, P, TS).has(w))).length / q.length;
  const best = top.length ? Math.max(...top.map(r => r.hit)) / q.length : 0;
  const vol = Math.min(1, res.filter(r => r.hit / q.length >= .5).length / 8);
  // PAPER TRAIL: do the files discuss this topic at all? (aliases count)
  const topic = top.length ? Math.round(100 * (.4*covered + .4*best + .2*vol)) : 0;
  // CLAIM: a sentence (titles count) is evidence only if it covers most of the question AND the claim's own words,
  // i.e. the words not already explained by a recognized topic ("faked" in "the moon landing was faked").
  const topicToks = new Set(qt.flatMap(t => [...t.toks])), claim = q.filter(w => !topicToks.has(w) && !FILLER.has(w));
  const need = Math.max(1, Math.ceil(q.length * .6)), actor = ACTOR.test(raw);
  // Rare claim words carry the claim ("bodies" in "Area 51 has alien bodies"); words in >3% of files don't count.
  const cw = Object.fromEntries(claim.map(w => [w, P[w].size / idx.N > .03 ? 0 : Math.log(idx.N / (P[w].size + 1))]));
  const claimTotal = claim.reduce((a, w) => a + cw[w], 0);
  const claimOk = got => !claimTotal || claim.reduce((a, w) => a + (got.has(w) ? cw[w] : 0), 0) >= .6 * claimTotal;
  let c = 0, n = 0, strong = 0;
  const seen = new Set();   // the same clipping is often scanned several times: count each sentence once
  top.forEach(r => { const d = docs.get(r.i), titleActor = ACTOR.test(d.t);
    const docTopicToks = qt.filter(t => d.k.includes(t.id)).flatMap(t => [...t.toks]);
    [d.t, ...sentences(d, q, qt)].forEach((s, j) => {
      const got = sentCover(s, q, qt);
      docTopicToks.forEach(w => got.add(w));       // the whole document is about the topic
      if (got.size < need || !claimOk(got)) return;
      if (actor && !titleActor && !ACTOR.test(s)) return;
      const key = s.toLowerCase().replace(/[^a-z]/g, "").slice(0, 40);
      if (seen.has(key)) return; seen.add(key);
      strong++;
      const cc = (s.match(CONFIRM)||[]).length, dn = (s.match(DENY)||[]).length;
      const hd = (s.match(HEDGE)||[]).length + (/\?\s*$/.test(s) ? 1 : 0);   // a question isn't an assertion
      c += cc; n += dn + hd / 2;
      if (!cc && !dn && !hd) c += j === 0 ? .5 : .25;   // plain statement of the claim: weak support (title > body)
    }); });
  // Smoothed stance: one stray "confirmed" can't max the needle; it takes several cues to get near +/-1.
  const stance = (c - n) / (c + n + 2), weight = Math.min(1, strong / 3);
  const base = .3 * topic;                                   // paper trail alone: at most 30
  let score = !top.length ? 3 : stance >= 0 ? base + 70 * weight * stance : base * (1 + stance * weight);
  if (!strong) score = Math.min(score, 30);                  // files discuss the topic but never state the claim
  if (strong && n >= c) score = Math.min(score, 25);
  score = Math.max(0, Math.min(100, Math.round(score)));
  return {score, topic, strong, q, qt, hits: top, docs, covered, best, stance, c: +c.toFixed(1), n: +n.toFixed(1)};
}

function verdictText(r, raw){
  const s = r.score;
  const lab = s < 20 ? "Tinfoil hat! 🛸" : s < 40 ? "Mostly tinfoil." : s < 60 ? "Hmm… there's a paper trail on the topic."
            : s < 80 ? "The files talk about this a lot." : "Proven-ish! 📁 (by our very silly standards)";
  if (!r.hits.length) return `<b>${lab}</b> Nothing in the ${idx.N.toLocaleString()} scraped documents matches “${esc(raw)}”. That may just mean we haven't scraped it.`;
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
function topicCard(id, docs){
  const t = T[id], yrs = t.years ? ` · ${t.years[0]}–${t.years[1]}` : "";
  const related = [...new Set([...t.see_also, ...t.co.map(c => c[0])])];
  return `<div class="tcard"><h3>📂 ${esc(t.name)}</h3><p>${esc(t.desc)}</p>
    <p><b>${t.n}</b> scraped document(s) mention this${yrs}.</p>
    <p class="aka"><b>Also searched as:</b> ${t.aliases.map(esc).join(" · ")}</p>
    ${related.length ? `<p><b>Related:</b> ${related.map(r => chip(r)).join(" ")}</p>` : ""}
    ${t.top.length ? `<details><summary>Top documents</summary><ol>${t.top.map(i => docLi(docs.get(i))).join("")}</ol></details>` : ""}</div>`;
}
async function showTopics(ids){
  const el = document.getElementById("tpanel");
  const docs = await getDocs(ids.flatMap(id => T[id].top));
  el.innerHTML = ids.map(id => topicCard(id, docs)).join(""); el.hidden = !ids.length;
}
document.addEventListener("click", async e => {
  const b = e.target.closest(".chip"); if (!b) return;
  await showTopics([b.dataset.topic]); document.getElementById("tpanel").scrollIntoView({behavior: "smooth"});
});

document.getElementById("f").addEventListener("submit", async e => {
  e.preventDefault(); const raw = document.getElementById("q").value.trim(); if (!idx) return;
  const btn = e.target.querySelector("button"), v = document.getElementById("verdict");
  btn.disabled = true; btn.textContent = "Checking the files…";
  try {
    const r = await judge(raw);
    document.getElementById("out").hidden = false;
    requestAnimationFrame(() => document.getElementById("needle").style.transform = `rotate(${-90 + r.score*1.8}deg)`);
    v.innerHTML = verdictText(r, raw) +
      (r.qt.length ? `<br><small>Recognized topic(s): ${r.qt.map(t => chip(t.id)).join(" ")}, so aliases and code names were searched too.</small>` : "");
    document.getElementById("hits").innerHTML = r.hits.map(h => docLi(r.docs.get(h.i), r.q, r.qt)).join("");
    await showTopics(r.qt.map(t => t.id));
  } catch (err) {
    document.getElementById("out").hidden = false;
    v.textContent = "Couldn't load part of the index (" + err.message + "). Try again in a moment.";
  } finally { btn.disabled = false; btn.textContent = "Check the files"; }
});
load().catch(err => { document.getElementById("banner").textContent = "Couldn't load the index: " + err.message; });
