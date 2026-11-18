#!/usr/bin/env python3
"""Topic/alias matching + SQLite topic database.

Matching rule (mirrored in site/app.js -> aliasRegex):
  * tokens of an alias may be separated by space, hyphen, slash or nothing  ("MK-ULTRA" == "MK ULTRA" == "MKULTRA")
  * optional trailing "s"                                                     ("psychoenergetic" -> "...ics" via 'psychoenergetics' alias)
  * ALL-CAPS aliases are case-sensitive (code names like CHAOS, CORONA, AJAX); others case-insensitive
"""
import json, os, re, sqlite3, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))


def load_topics(path=os.path.join(HERE, "topics.json")):
    return json.load(open(path, encoding="utf-8"))["topics"]


def fold(s):
    """Strip accents so 'Peenemünde' == 'Peenemunde' (OCR often drops them)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def alias_regex(alias):
    a = fold(alias)
    toks = re.findall(r"[A-Za-z0-9]+", a)
    body = r"[\s\-/]*".join(re.escape(t) for t in toks)
    flags = 0 if (a.upper() == a and any(c.isalpha() for c in a)) else re.I
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"s?(?![A-Za-z0-9])", flags)


def compile_topics(topics):
    return {tid: [(al, alias_regex(al)) for al in t["aliases"]] for tid, t in topics.items()}


def tag(text, compiled):
    """-> {topic_id: (hit_count, first matching sentence)}"""
    text = fold(text or "")
    out = {}
    for tid, pats in compiled.items():
        n, first = 0, None
        for _, rx in pats:
            for m in rx.finditer(text):
                n += 1
                if first is None or m.start() < first: first = m.start()
        if n:
            a = max(text.rfind(".", 0, first) + 1, first - 200)
            b = text.find(".", first); b = len(text) if b < 0 else min(b + 1, first + 300)
            out[tid] = (n, re.sub(r"\s+", " ", text[a:b]).strip())
    return out


YEAR = re.compile(r"\b(19[0-9]\d|20[0-2]\d)\b")


def build_db(docs, topics, path=os.path.join(HERE, "topics.db")):
    """docs: list of dicts with id,u,t,x,d. Writes topics.db and returns the compact export for the site."""
    comp = compile_topics(topics)
    if os.path.exists(path): os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
      create table topics(id text primary key, name text, descr text);
      create table aliases(topic text, alias text);
      create table see_also(topic text, other text);
      create table doc_topics(doc_id int, doc_url text, topic text, hits int, year int, sample text);
      create index dt_topic on doc_topics(topic);""")
    for tid, t in topics.items():
        db.execute("insert into topics values(?,?,?)", (tid, t["name"], t["desc"]))
        db.executemany("insert into aliases values(?,?)", [(tid, a) for a in t["aliases"]])
        db.executemany("insert into see_also values(?,?)", [(tid, o) for o in t.get("see_also", [])])
    doc_topics = {}
    for d in docs:
        hits = tag(d["t"] + ". " + d["x"], comp)
        if not hits: continue
        y = YEAR.search(d.get("d") or "")
        y = int(y.group(1)) if y else None
        doc_topics[d["id"]] = sorted(hits, key=lambda k: -hits[k][0])
        db.executemany("insert into doc_topics values(?,?,?,?,?,?)",
                       [(d["id"], d["u"], tid, n, y, s) for tid, (n, s) in hits.items()])
    db.commit()

    export = {}
    for tid, t in topics.items():
        rows = db.execute("select doc_id, hits, year from doc_topics where topic=? order by hits desc", (tid,)).fetchall()
        years = [r[2] for r in rows if r[2]]
        co = db.execute("""select b.topic, count(*) c from doc_topics a join doc_topics b
                           on a.doc_id=b.doc_id and b.topic!=a.topic where a.topic=?
                           group by b.topic order by c desc limit 5""", (tid,)).fetchall()
        export[tid] = {"name": t["name"], "desc": t["desc"], "aliases": t["aliases"],
                       "qaliases": t.get("query_aliases", []),
                       "see_also": t.get("see_also", []), "n": len(rows),
                       "years": [min(years), max(years)] if years else None,
                       "top": [r[0] for r in rows[:8]], "co": [[c[0], c[1]] for c in co]}
    db.close()
    return export, doc_topics
