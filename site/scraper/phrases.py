#!/usr/bin/env python3
"""Discover candidate key phrases / code names in the scraped text that are NOT yet in topics.json.

  python3 phrases.py                 # reads state.db (real scrape) -> phrase_candidates.csv
  python3 phrases.py --corpus        # reads ../data/corpus.json instead (works with --demo builds)

Two detectors (both free, local, no model download):
  1. CODE NAMES: runs of ALL-CAPS words (e.g. GRILL FLAME, MKSEARCH), with common acronyms filtered out.
  2. PHRASES: 2-3 word n-grams scored by document frequency x distinctiveness (how concentrated in
     one collection vs the whole corpus) – a cheap TF-IDF-style "keyness".
Review the CSV, then copy good ones into topics.json as aliases or new topics.
"""
import collections, csv, json, math, os, re, sqlite3, sys
from site.scraper.topics import load_topics, compile_topics, tag, fold

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = set("""a an the of to in on and or is was were be been are that this it its by for with as at from not but
if so than then there these those who which what when where how all any can could would should will into about
he she his her they their them we our you your has have had do does did no yes per also may such other only
said one two three new same mr page date memorandum subject secret confidential approved release""".split())
COMMON_CAPS = set("""CIA FBI USSR US USA UK UN NATO DCI DDI DDO DDP NSA DOD DIA OSS KGB GRU NSC FOIA CREST RDP
SECRET TOP CONFIDENTIAL UNCLASSIFIED NOFORN ORCON SUBJECT MEMORANDUM FOR FROM REF THE AND OF TO IN ON A NO
DATE PAGE APPROVED RELEASE DOCUMENT ATTACHMENT COPY OFFICE DIRECTOR SOVIET EYES ONLY ROUTING SLIP NOTE""".split())
CAPS_RUN = re.compile(r"\b([A-Z][A-Z0-9\-]{3,}(?:\s+[A-Z][A-Z0-9\-]{3,}){0,2})\b")
WORD = re.compile(r"[A-Za-z][A-Za-z\-]+")


def load_docs(use_corpus):
    if use_corpus:
        j = json.load(open(os.path.join(HERE, "..", "data", "corpus.json")))
        return [(d.get("col") or "?", d["t"] + ". " + d["x"]) for d in j["docs"]]
    db = sqlite3.connect(os.path.join(HERE, "state.db"))
    out = []
    for t, f, x in db.execute("select title, fields, text from docs"):
        out.append((json.loads(f or "{}").get("Collection", "?"), (t or "") + ". " + (x or "")))
    return out


def main():
    docs = load_docs("--corpus" in sys.argv)
    known = compile_topics(load_topics())
    N = len(docs)
    df = collections.Counter(); col_df = collections.defaultdict(collections.Counter)
    kind = {}; example = {}
    for col, text in docs:
        text = fold(text); seen = set()
        for m in CAPS_RUN.finditer(text):
            p = m.group(1).strip()
            if all(w in COMMON_CAPS for w in p.split()) or p.isdigit(): continue
            seen.add(p); kind[p] = "codename"; example.setdefault(p, text[max(0, m.start()-80):m.end()+80])
        words = [w.lower() for w in WORD.findall(text)]
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                g = words[i:i+n]
                if g[0] in STOP or g[-1] in STOP or any(len(w) < 3 for w in g): continue
                p = " ".join(g); seen.add(p); kind.setdefault(p, "phrase")
        for p in seen:
            df[p] += 1; col_df[p][col] += 1
    rows = []
    for p, n in df.items():
        if n < (1 if N < 50 else 3): continue
        if tag(p, known): continue                       # already covered by an alias
        top_col, top_n = col_df[p].most_common(1)[0]
        conc = top_n / n                                 # 1.0 = only appears in one collection
        score = n * math.log(1 + N / n) * (0.5 + conc) * (1.5 if kind[p] == "codename" else 1)
        rows.append((round(score, 2), p, kind[p], n, top_col, round(conc, 2),
                     re.sub(r"\s+", " ", example.get(p, ""))[:200]))
    rows.sort(reverse=True)
    out = os.path.join(HERE, "phrase_candidates.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["score", "phrase", "kind", "docs", "top_collection", "concentration", "example"])
        w.writerows(rows[:2000])
    print(f"{N} docs -> {len(rows)} candidates; top 15:")
    for r in rows[:15]: print(f"  {r[0]:>7}  {r[2]:<8} {r[1]}  ({r[3]} docs, {r[4]})")
    print("wrote", out)


if __name__ == "__main__":
    main()
