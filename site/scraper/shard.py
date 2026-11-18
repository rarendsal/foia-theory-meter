#!/usr/bin/env python3
"""Write the search index as many small static files so the browser only downloads what a query needs.

Layout under site/data/:
  meta.json          coverage stats, topic list, index settings          (loaded on page open)
  len.json           token count per document (needed for BM25 scoring)   (loaded on page open)
  p/<n>.json         inverted index shard: term -> [docDelta, tf, docDelta, tf, ...]
  d/<n>.json         document records, DOC_SHARD docs per file            (fetched for the top hits only)
  t/<topic>.json     ids of documents tagged with a topic

The tokenizer and the term->shard hash below MUST stay identical to tok()/stem()/termShard() in site/app.js.
"""
import json, os, re, shutil, unicodedata

TERM_SHARDS = 512
DOC_SHARD = 100

STOP = set("a an the of to in on and or is was were be been are that this it its by for with as at from did do does have has "
           "had they their them he she his her not but if so than then there these those who whom which what when where why how "
           "all any can could would should will into about cia agency us".split())
_COMB = re.compile("[̀-ͯ]")
_WORD = re.compile(r"[a-z0-9]+")
_IES = re.compile(r"(ies)$")
_SUF = re.compile(r"(ing|ed|es|s)$")


def fold(s):
    return _COMB.sub("", unicodedata.normalize("NFKD", s or ""))


def stem(w):
    s = _SUF.sub("", _IES.sub("y", w, count=1), count=1)[:12]
    return s or w


def tok(s):
    return [stem(w) for w in _WORD.findall(fold(s).lower()) if len(w) > 1 and w not in STOP]


def term_shard(term):
    """FNV-1a 32-bit hash of the (ASCII) term, mod TERM_SHARDS."""
    h = 0x811C9DC5
    for ch in term.encode():
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % TERM_SHARDS


def _dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)


def write_index(docs, topics, meta, outdir):
    """docs: list of dicts with id (0..N-1, in order), t, u, pdf, d, c, x, k (topic ids)."""
    for sub in ("p", "d", "t"):
        shutil.rmtree(os.path.join(outdir, sub), ignore_errors=True)
        os.makedirs(os.path.join(outdir, sub))
    old = os.path.join(outdir, "corpus.json")
    if os.path.exists(old): os.remove(old)

    shards = [dict() for _ in range(TERM_SHARDS)]   # term -> [lastId, flat list]
    lens, topic_docs = [], {tid: [] for tid in topics}
    for d in docs:
        toks = tok(d["t"] + " " + d["t"] + " " + d["x"])   # title counted twice, same as before
        lens.append(len(toks))
        tf = {}
        for w in toks: tf[w] = tf.get(w, 0) + 1
        for w, n in tf.items():
            e = shards[term_shard(w)].setdefault(w, [0, []])
            e[1].extend((d["id"] - e[0], n)); e[0] = d["id"]
        for tid in d.get("k", []):
            if tid in topic_docs: topic_docs[tid].append(d["id"])

    for i, sh in enumerate(shards):
        _dump({w: e[1] for w, e in sh.items()}, os.path.join(outdir, "p", f"{i}.json"))
    for s in range(0, len(docs), DOC_SHARD):
        _dump([{k: d.get(k, "") for k in ("t", "u", "pdf", "d", "c", "x", "k")} for d in docs[s:s + DOC_SHARD]],
              os.path.join(outdir, "d", f"{s // DOC_SHARD}.json"))
    for tid, ids in topic_docs.items():
        _dump(ids, os.path.join(outdir, "t", f"{tid}.json"))
    _dump(lens, os.path.join(outdir, "len.json"))
    meta = dict(meta, index={"term_shards": TERM_SHARDS, "doc_shard": DOC_SHARD,
                             "avg_len": sum(lens) / max(len(lens), 1), "vocab": sum(len(s) for s in shards)})
    _dump({"meta": meta, "topics": topics}, os.path.join(outdir, "meta.json"))
    return meta
