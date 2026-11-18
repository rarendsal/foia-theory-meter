#!/usr/bin/env python3
"""Suggest new aliases and new topics: code names and phrases in the scraped text that topics.json doesn't cover.

  python3 phrases.py                  # reads state.db (+ topics.db from the last build_index.py run)
  python3 phrases.py --corpus FILE    # reads an old-style corpus.json export instead

Writes phrase_candidates.csv with a `suggestion` column:
  alias -> <topic>   the phrase mostly appears in documents already tagged with that topic,
                     e.g. SCANATE inside STARGATE files. Add it to that topic's "aliases" in topics.json.
  new topic?         a code name that isn't tied to any existing topic. Look it up; if it's interesting,
                     add a new topic to topics.json.
Then rerun build_index.py. sources.py ia-topics will also fetch more documents for every new alias.

Free and local: no model downloads, no API calls. Memory stays around 1-2 GB however large state.db gets.
"""
import collections, csv, json, math, os, re, sqlite3, sys
from topics import load_topics, compile_topics, tag, fold

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = set("""a an the of to in on and or is was were be been are that this it its by for with as at from not but
if so than then there these those who which what when where how all any can could would should will into about
he she his her they their them we our you your has have had do does did no yes per also may such other only
said one two three new same mr page date memorandum subject secret confidential approved release sanitized copy
declassified cia-rdp rdp part""".split())
COMMON_CAPS = set("""CIA FBI USSR US USA UK UN NATO DCI DDI DDO DDP NSA DOD DIA OSS KGB GRU NSC FOIA CREST RDP
SECRET TOP CONFIDENTIAL UNCLASSIFIED NOFORN ORCON SUBJECT MEMORANDUM FOR FROM REF THE AND OF TO IN ON A NO
DATE PAGE PAGES APPROVED RELEASE DOCUMENT ATTACHMENT COPY OFFICE DIRECTOR SOVIET EYES ONLY ROUTING SLIP NOTE
SANITIZED DELETED UNTITLED STAT INFO CIA- NPIC WNINTEL LIMDIS""".split())
# Common English words: a single capitalized word like CABLE or CHANGE is not a code name.
COMMON_WORDS = set(open(os.path.join(HERE, "common_words.txt"), encoding="utf-8").read().split())
CAPS_RUN = re.compile(r"\b([A-Z][A-Z0-9\-]{3,}(?:\s+[A-Z][A-Z0-9\-]{3,}){0,2})\b")
WORD = re.compile(r"[A-Za-z][A-Za-z\-]+")

MAX_CHARS = 8000        # text read per document: keeps memory bounded on a large state.db
PRUNE_EVERY = 20000     # drop phrases seen in only one document every N docs (bounds memory)
BOILERPLATE = .03       # phrases in more than 3% of all documents are letterhead and stamps, not topics
ALIAS_SHARE = .4        # at least 40% of a phrase's documents carry one topic -> suggest it as that topic's alias
TOP_N = 1000


def load_docs(corpus_path):
    """-> list of (collection, text, [topic ids])"""
    if corpus_path:
        j = json.load(open(corpus_path, encoding="utf-8"))
        return [(d.get("col") or "?", d["t"] + ". " + d["x"], d.get("k", [])) for d in j["docs"]]
    url_topics = collections.defaultdict(list)
    tdb = os.path.join(HERE, "topics.db")
    if os.path.exists(tdb):
        for u, tid in sqlite3.connect(tdb).execute("select doc_url, topic from doc_topics"):
            url_topics[u].append(tid)
    else:
        print("topics.db not found: run build_index.py first for alias suggestions")
    db = sqlite3.connect(os.path.join(HERE, "state.db"))
    return [(json.loads(f or "{}").get("Collection", "?"), (t or "") + ". " + (x or ""), url_topics.get(u, []))
            for u, t, f, x in db.execute(f"select url, title, fields, substr(text, 1, {MAX_CHARS}) from docs")]


def is_codename(p):
    words = p.split()
    if all(w in COMMON_CAPS for w in words) or p.replace("-", "").isdigit(): return False
    if len(words) == 1 and p.lower() in COMMON_WORDS: return False          # CABLE, CHANGE, PRESIDENT
    return True


def extract(text):
    """-> (set of candidate phrases in this doc, {phrase: kind}, {phrase: example})"""
    seen, kind, ex = set(), {}, {}
    for m in CAPS_RUN.finditer(text):
        p = m.group(1).strip()
        if not is_codename(p): continue
        seen.add(p); kind[p] = "codename"; ex.setdefault(p, text[max(0, m.start()-80):m.end()+80])
    words = [w.lower() for w in WORD.findall(text)]
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            g = words[i:i+n]
            if g[0] in STOP or g[-1] in STOP or any(len(w) < 3 for w in g): continue
            if all(w in COMMON_WORDS for w in g): continue                    # "deputy director", "soviet union"
            p = " ".join(g); seen.add(p); kind.setdefault(p, "phrase")
    return seen, kind, ex


def main():
    cp = sys.argv[sys.argv.index("--corpus") + 1] if "--corpus" in sys.argv else None
    docs = load_docs(cp)
    topics = load_topics(); known = compile_topics(topics)
    N = len(docs)
    col_size = collections.Counter(col for col, _, _ in docs)
    # Pass 1: how many documents each candidate appears in (pruned as we go so memory stays bounded).
    df = collections.Counter(); kind = {}; example = {}
    for i, (col, text, _) in enumerate(docs, 1):
        seen, k, ex = extract(fold(text)[:MAX_CHARS])
        df.update(seen)
        for p in seen:
            kind.setdefault(p, k[p])
            if p in ex and p not in example: example[p] = ex[p]
        if i % PRUNE_EVERY == 0:
            for p in [p for p, n in df.items() if n < 2]:
                del df[p]; kind.pop(p, None); example.pop(p, None)
            print(f"  pass 1: {i:,}/{N:,} docs, {len(df):,} phrases kept", flush=True)
    min_df = 1 if N < 50 else 3
    cands = {p: n for p, n in df.items()
             if min_df <= n <= BOILERPLATE * N and not tag(p, known)}      # skip phrases an alias already covers
    del df
    short = set(sorted(cands, key=lambda p: -cands[p] * math.log(1 + N / cands[p]))[:TOP_N * 5])
    # Pass 2 (shortlist only): which collection and which existing topic each phrase lives in.
    col_df = collections.defaultdict(collections.Counter); top_df = collections.defaultdict(collections.Counter)
    mentions = collections.Counter()          # total code-name occurrences, for "burstiness"
    for col, text, tids in docs:
        t = fold(text)[:MAX_CHARS]
        hit = extract(t)[0] & short
        if not hit: continue
        for m in CAPS_RUN.finditer(t):
            if m.group(1).strip() in hit: mentions[m.group(1).strip()] += 1
        for p in hit:
            col_df[p][col] += 1
            for tid in tids: top_df[p][tid] += 1
    rows = []
    for p in short:
        n = cands[p]
        top_col, top_n = col_df[p].most_common(1)[0] if col_df[p] else ("?", 0)
        # Repeated in most of one collection = that collection's description or letterhead, not a topic.
        if top_n > 20 and top_n / col_size[top_col] > .3: continue
        plain = all(w.lower() in COMMON_WORDS for w in p.split())   # NEAR EAST, NEXT REVIEW DATE
        tid, tn = top_df[p].most_common(1)[0] if top_df[p] else ("", 0)
        share = tn / n
        if tid and share >= ALIAS_SHARE:
            suggestion = f"alias -> {tid}"
            score = math.sqrt(n) * share * (0.3 if plain else 1)
        elif kind[p] == "codename":
            suggestion = "new topic?"
            # Real code names recur inside the documents that use them; form headers appear once per page.
            burst = mentions[p] / n
            if burst < 1.5: continue
            score = math.sqrt(n) * burst * (0.2 if plain else 1)
        else:
            continue                                     # a loose phrase with no topic: rarely useful
        rows.append((suggestion, round(score, 1), p, kind[p], n,
                     f"{topics[tid]['name']} ({round(100 * share)}%)" if tid else "",
                     top_col, re.sub(r"\s+", " ", example.get(p, ""))[:200]))
    rows.sort(key=lambda r: (not r[0].startswith("alias"), -r[1]))
    rows = [r for r in rows if r[0].startswith("alias")][:TOP_N // 2] + [r for r in rows if not r[0].startswith("alias")][:TOP_N // 2]
    out = os.path.join(HERE, "phrase_candidates.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["suggestion", "score", "phrase", "kind", "docs", "top_topic", "top_collection", "example"])
        w.writerows(rows)
    for label in ("alias", "new topic"):
        sel = [r for r in rows if r[0].startswith(label)][:15]
        print(f"\nTop {label} suggestions:")
        for r in sel: print(f"  {r[2]:<34} {r[4]:>5} docs  {r[0] if label == 'alias' else r[6]}")
    print(f"\n{N:,} docs scanned; wrote {len(rows)} suggestions to {out}")


if __name__ == "__main__":
    main()
