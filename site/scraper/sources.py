#!/usr/bin/env python3
"""Pull FOIA documents from public mirrors/APIs into state.db (same `docs` table scrape.py uses).

cia.gov blocks automated clients, but the same records are published elsewhere with proper APIs:

  ia-topics      Internet Archive: one search per topic alias (topics.json) across FOIA mirror
                 collections. Each request returns up to --per-query docs WITH their OCR text,
                 so this gets the most relevant documents fastest. Start here.
  ia-bulk        Internet Archive: walk an entire collection (default: ciareadingroom, ~1M docs, the
                 full CIA reading-room mirror). Resumable cursor; 50 docs of text per request.
  documentcloud  DocumentCloud (journalists' + MuckRock FOIA uploads): search per topic alias,
                 then fetch each document's full text.
  all            ia-topics + documentcloud (ia-bulk is opt-in: it is big).

Usage:
  python3 sources.py ia-topics                       # ~100 requests, tens of thousands of docs
  python3 sources.py documentcloud --per-query 50
  python3 sources.py ia-bulk --max-requests 2000     # Ctrl-C any time; rerun resumes
  python3 sources.py ia-bulk --collection stargatefiles
  python3 sources.py status

Everything is resumable: finished queries / bulk cursors are recorded in the `stats` table and
docs are de-duplicated across sources (CIA-RDP numbers, DocumentCloud file hashes).
"""
import argparse, json, os, re, sqlite3, sys, time
import urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "FOIA-Theory-Meter/1.0 (non-commercial research; +https://github.com/rarendsal/foia-theory-meter)"
MAX_TEXT = 30000

# Internet Archive collections that mirror FOIA releases (item counts as of 2026-11).
IA_COLLECTIONS = {
    "ciareadingroom": "CIA Reading Room (mirror)",                  # ~1,012,000
    "cia-collection": "CIA CREST (National Security Archive)",       # ~275,000
    "stargatefiles": "STARGATE files",                               # ~12,000
    "ufo-files": "UFO Files",                                        # ~10,800
    "nwcda": "Nazi War Crimes Disclosure Act",                       # ~27,000
    "office-strategic-services": "OSS records",
    "helms-cia-collection": "Richard Helms CIA collection",
    "nationalsecurityarchive-weisberg": "Weisberg JFK assassination collection",  # ~5,700
    "thevenonafiles": "VENONA files",                                # ~3,300
    "nsia-venona": "VENONA documents",
    "nsa-friedman": "William F. Friedman NSA collection",            # ~7,600
    "lazarfoia": "Ernie Lazar FBI FOIA collection",                  # ~3,400
    "state-foia-dump": "State Department FOIA",                      # ~24,000
}
IA_FIELDS = ["identifier", "title", "date", "description", "collection"]


# ---------------------------------------------------------------- storage
class Store:
    def __init__(s):
        s.db = sqlite3.connect(os.path.join(HERE, "state.db"))
        s.db.executescript("""
          create table if not exists frontier(url text primary key, kind text, done int default 0, prio int);
          create table if not exists docs(url text primary key, title text, fields text, text text, pdf text);
          create table if not exists stats(k text primary key, v real);
          create table if not exists dedupe(k text primary key, url text);
          create table if not exists progress(k text primary key, v text);""")

    def get(s, k):
        r = s.db.execute("select v from progress where k=?", (k,)).fetchone(); return r[0] if r else None

    def set(s, k, v):
        s.db.execute("insert or replace into progress values(?,?)", (k, v)); s.db.commit()

    def add(s, key, url, title, fields, text, pdf=None):
        """Insert one doc unless an equivalent one (same dedupe key) is already stored. -> bool new"""
        if key and s.db.execute("select 1 from dedupe where k=?", (key,)).fetchone(): return False
        text = re.sub(r"\s+", " ", text or "").strip()[:MAX_TEXT]
        cur = s.db.execute("insert or ignore into docs values(?,?,?,?,?)",
                           (url, (title or "").strip()[:500], json.dumps(fields), text, pdf))
        if key: s.db.execute("insert or ignore into dedupe values(?,?)", (key, url))
        return cur.rowcount > 0

    def count(s): return s.db.execute("select count(*) from docs").fetchone()[0]


# ---------------------------------------------------------------- polite HTTP
class Http:
    def __init__(s, delay, max_requests):
        s.delay, s.max, s.n, s.last = delay, max_requests, 0, {}

    def get(s, url, as_json=True):
        if s.n >= s.max: raise StopIteration(f"--max-requests {s.max} reached")
        host = urllib.parse.urlparse(url).netloc
        wait = s.delay - (time.time() - s.last.get(host, 0))
        if wait > 0: time.sleep(wait)
        for attempt in range(5):
            s.last[host] = time.time(); s.n += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=120) as r: body = r.read()
                return json.loads(body) if as_json else body.decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    ra = e.headers.get("Retry-After")
                    pause = int(ra) if ra and ra.isdigit() else 30 * (attempt + 1)
                    print(f"  HTTP {e.code}, backing off {pause}s"); time.sleep(pause); continue
                print(f"  HTTP {e.code} {url[:120]}"); return None
            except Exception as e:
                print(f"  err {e} {url[:120]}"); time.sleep(10 * (attempt + 1))
        return None


def load_aliases():
    t = json.load(open(os.path.join(HERE, "topics.json"), encoding="utf-8"))["topics"]
    seen, out = set(), []
    for tid, v in t.items():
        for a in v["aliases"]:
            if a.lower() not in seen: seen.add(a.lower()); out.append((tid, a))
    return out


# ---------------------------------------------------------------- Internet Archive
RDP = re.compile(r"cia-?rdp[\w-]+", re.I)


def rdp_key(s):
    m = RDP.search(s or ""); return "rdp:" + re.sub(r"[^a-z0-9]", "", m.group(0).lower()) if m else None


def ia_doc(store, d):
    ident = d["identifier"]
    cols = d.get("collection") or []; cols = [cols] if isinstance(cols, str) else cols
    col = next((IA_COLLECTIONS[c] for c in cols if c in IA_COLLECTIONS), cols[0] if cols else "")
    desc = d.get("description") or ""; desc = " ".join(desc) if isinstance(desc, list) else desc
    title = d.get("title") or ident; title = title[0] if isinstance(title, list) else title
    date = (d.get("date") or "")[:10]
    ia_url = "https://archive.org/details/" + ident
    url = ia_url
    if ident.startswith("cia-readingroom-document-"):   # cite the original cia.gov record
        url = "https://www.cia.gov/readingroom/document/" + ident[len("cia-readingroom-document-"):]
        title = re.sub(r"^CIA Reading Room [^:]+:\s*", "", title)
    fields = {"Collection": col, "Source": "Internet Archive", "Document Creation Date": date,
              "Archive URL": ia_url}
    return store.add(rdp_key(ident) or "ia:" + ident, url, title, fields, desc, ia_url)


def ia_search(http, q, rows):
    p = [("q", q), ("rows", str(rows)), ("output", "json")] + [("fl[]", f) for f in IA_FIELDS]
    j = http.get("https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(p))
    return (j or {}).get("response", {})


def run_ia_topics(store, http, a):
    cols = "collection:(" + " OR ".join(IA_COLLECTIONS) + ")"
    aliases = load_aliases()
    for i, (tid, alias) in enumerate(aliases):
        k = f"ia-topic:{alias.lower()}:{a.per_query}"
        if store.get(k): continue
        r = ia_search(http, f'{cols} AND "{alias}"', a.per_query)
        new = sum(ia_doc(store, d) for d in r.get("docs", []))
        store.db.commit(); store.set(k, "done")
        print(f"[ia-topics {i+1}/{len(aliases)}] {alias!r}: {r.get('numFound', 0)} matches, "
              f"+{new} new  (total docs {store.count()})", flush=True)


def run_ia_bulk(store, http, a):
    col = a.collection; k = f"ia-bulk-cursor:{col}"
    cursor = store.get(k)
    if cursor == "DONE": print(f"{col}: already fully imported"); return
    while True:
        p = {"q": f"collection:{col}", "fields": "identifier", "count": "1000"}
        if cursor: p["cursor"] = cursor
        j = http.get("https://archive.org/services/search/v1/scrape?" + urllib.parse.urlencode(p))
        if not j: print("scrape API failed; rerun to resume"); return
        ids = [x["identifier"] for x in j.get("items", [])]
        todo = [i for i in ids if not store.db.execute("select 1 from dedupe where k=?",
                                                         (rdp_key(i) or "ia:" + i,)).fetchone()]
        new = 0
        for b in range(0, len(todo), 50):    # text for 50 docs per request (IA rejects longer queries)
            chunk = todo[b:b + 50]
            r = ia_search(http, "identifier:(" + " OR ".join(chunk) + ")", len(chunk))
            new += sum(ia_doc(store, d) for d in r.get("docs", []))
            store.db.commit()
        cursor = j.get("cursor")
        store.set(k, cursor or "DONE")      # page fully stored -> advance cursor
        print(f"[ia-bulk {col}] +{new} new  (total docs {store.count()}, of ~{j.get('total')})", flush=True)
        if not cursor: print(f"{col}: complete"); return


# ---------------------------------------------------------------- DocumentCloud
def run_documentcloud(store, http, a):
    aliases = load_aliases()
    for i, (tid, alias) in enumerate(aliases):
        k = f"dc:{alias.lower()}:{a.per_query}"
        if store.get(k): continue
        got, new, page = 0, 0, 1
        while got < a.per_query:
            q = urllib.parse.urlencode({"q": f'"{alias}"', "per_page": min(100, a.per_query), "page": page})
            j = http.get("https://api.www.documentcloud.org/api/documents/search/?" + q)
            res = (j or {}).get("results") or []
            if not res: break
            for d in res:
                got += 1
                if d.get("access") != "public" or d.get("status") != "success": continue
                key = "dc:" + (d.get("file_hash") or str(d["id"]))
                if store.db.execute("select 1 from dedupe where k=?", (key,)).fetchone(): continue
                base = (d.get("asset_url") or "https://s3.documentcloud.org/") + f"documents/{d['id']}/{d['slug']}"
                text = http.get(base + ".txt", as_json=False) or ""
                fields = {"Collection": (d.get("source") or "DocumentCloud")[:200], "Source": "DocumentCloud",
                          "Publication Date": (d.get("created_at") or "")[:10],
                          "Document Page Count": d.get("page_count")}
                url = d.get("canonical_url") or f"https://www.documentcloud.org/documents/{d['id']}-{d['slug']}"
                new += store.add(rdp_key(d.get("title")) or key, url, d.get("title"), fields, text, base + ".pdf")
                if rdp_key(d.get("title")): store.db.execute("insert or ignore into dedupe values(?,?)", (key, url))
                if got >= a.per_query: break
            store.db.commit()
            if not j.get("next"): break
            page += 1
        store.set(k, "done")
        print(f"[documentcloud {i+1}/{len(aliases)}] {alias!r}: +{new} new  (total docs {store.count()})", flush=True)


def status(store):
    print("docs:", store.count())
    for src, n in store.db.execute("select json_extract(fields,'$.Source'), count(*) from docs group by 1"):
        print(f"  {src or 'cia.gov (scrape.py)'}: {n}")
    for k, v in store.db.execute("select k,v from progress where k like 'ia-bulk-cursor:%'"):
        print(f"  {k}: {'complete' if v == 'DONE' else 'in progress'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["ia-topics", "ia-bulk", "documentcloud", "all", "status"])
    ap.add_argument("--per-query", type=int, default=None,
                    help="max docs per topic alias (default: ia-topics 2000, documentcloud 50)")
    ap.add_argument("--collection", default="ciareadingroom", choices=list(IA_COLLECTIONS),
                    help="ia-bulk: which IA collection to walk")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests to the same host")
    ap.add_argument("--max-requests", type=int, default=100000)
    a = ap.parse_args()
    store, http = Store(), Http(a.delay, a.max_requests)
    try:
        if a.source == "status": status(store); sys.exit()
        user_pq = a.per_query
        if a.source in ("ia-topics", "all"):
            a.per_query = user_pq or 2000; run_ia_topics(store, http, a)
        if a.source in ("documentcloud", "all"):
            a.per_query = user_pq or 50; run_documentcloud(store, http, a)
        if a.source == "ia-bulk":
            run_ia_bulk(store, http, a)
    except (StopIteration, KeyboardInterrupt) as e:
        print("\nSTOP:", e or "interrupted", "- rerun the same command to resume")
    store.db.commit(); status(store)
