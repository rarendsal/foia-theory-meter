#!/usr/bin/env python3
"""Polite, resumable scraper for the CIA FOIA Electronic Reading Room.

- Obeys robots.txt (urllib.robotparser) incl. Crawl-delay; default delay 3s otherwise.
- Every response is cached to disk (cache/<sha1>.gz), so a re-run never re-downloads.
- State in SQLite (state.db): URL frontier + parsed documents. Kill it any time; rerun resumes.
- Priority: metadata + title + on-page text first. PDFs (for OCR text) only with --pdfs.
- Hard stops: --max-requests, --max-hours, --max-mb (bandwidth), and a $ budget tracker
  (cost is $0 for local scraping; the tracker estimates egress cost if you run on a paid VM).

Usage:
  python3 scrape.py --estimate                 # probe listing pages, estimate corpus size/time
  python3 scrape.py --max-requests 5000        # crawl (resume-safe)
  python3 scrape.py --pdfs --max-mb 2000       # also fetch PDFs, extract text with pdftotext
"""
import argparse, gzip, hashlib, json, os, re, sqlite3, subprocess, sys, time
import urllib.request, urllib.robotparser, urllib.parse
from html.parser import HTMLParser

BASE = "https://www.cia.gov"
ROOT = BASE + "/readingroom/"
# /readingroom/search/ is Disallowed by robots.txt -> walk collections instead.
SEEDS = [ROOT, ROOT + "historical-collections"] + [ROOT + "collection/" + c for c in (
    "stargate", "animal-partners", "mkultra", "ufos-fact-or-fiction", "nixon-collection",
    "aquiline", "presidents-daily-brief-1961-1969")]
UA = "Mozilla/5.0 (compatible; FOIA-Theory-Meter/1.0; +mailto:you@example.com)"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
DOC_RE = re.compile(r"^/readingroom/document/[\w\-.]+$")
LIST_RE = re.compile(r"^/readingroom/(collection/|historical-collections|$)")
PDF_RE = re.compile(r"^/readingroom/docs/.+\.pdf$", re.I)
COST_PER_GB_EGRESS = 0.09  # only relevant on a paid cloud VM; $0 on your laptop


class Page(HTMLParser):
    """Dependency-free extraction: title, links, labelled Drupal fields, visible text."""
    def __init__(self):
        super().__init__(); self.links = []; self.title = ""; self._t = None
        self.text = []; self._skip = 0; self.h1 = ""
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "a" and a.get("href"): self.links.append(a["href"])
        if tag in ("script", "style", "nav", "header", "footer"): self._skip += 1
        if tag in ("title", "h1"): self._t = tag
        if tag in ("div", "p", "br", "li", "h1", "h2", "h3", "tr"): self.text.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer") and self._skip: self._skip -= 1
        if tag == self._t: self._t = None
    def handle_data(self, d):
        if self._t == "title": self.title += d
        elif self._t == "h1": self.h1 += d
        if not self._skip: self.text.append(d)


FIELDS = ["Document Type", "Collection", "Document Number (FOIA) /ESDN (CREST)",
          "Release Decision", "Original Classification", "Document Page Count",
          "Document Creation Date", "Document Release Date", "Sequence Number",
          "Case Number", "Publication Date", "Content Type", "File", "Body"]


def parse_fields(text):
    """Reading-room document pages render 'Label:\nValue' pairs; pull known ones."""
    out = {}
    for i, f in enumerate(FIELDS):
        m = re.search(re.escape(f) + r":?\s*\n+\s*(.+?)(?=\n\s*(?:" +
                      "|".join(re.escape(x) for x in FIELDS) + r"):?\s*\n|\Z)", text, re.S)
        if m:
            out[f] = re.sub(r"\s+", " ", m.group(1)).strip()[:20000 if f == "Body" else 300]
    return out


class Crawler:
    def __init__(s, a):
        s.a = a; os.makedirs(CACHE, exist_ok=True)
        s.db = sqlite3.connect(os.path.join(HERE, "state.db"))
        s.db.executescript("""
          create table if not exists frontier(url text primary key, kind text, done int default 0, prio int);
          create table if not exists docs(url text primary key, title text, fields text, text text, pdf text);
          create table if not exists stats(k text primary key, v real);""")
        s.rp = urllib.robotparser.RobotFileParser(BASE + "/robots.txt"); s.rp.read()
        cd = s.rp.crawl_delay(UA) or s.rp.crawl_delay("*")
        s.delay = max(a.delay, float(cd or 0)); s.last = 0; s.t0 = time.time()
        s.reqs = 0; s.bytes = s.stat("bytes"); print(f"robots.txt loaded; delay={s.delay}s")

    def stat(s, k, v=None):
        if v is None:
            r = s.db.execute("select v from stats where k=?", (k,)).fetchone(); return r[0] if r else 0
        s.db.execute("insert or replace into stats values(?,?)", (k, v))

    def cost(s): return s.bytes / 1e9 * COST_PER_GB_EGRESS

    def get(s, url, binary=False):
        key = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".gz")
        if os.path.exists(key):
            with gzip.open(key, "rb") as f: return f.read()
        if not s.rp.can_fetch(UA, url): print("robots disallow:", url); return None
        if s.reqs >= s.a.max_requests or time.time() - s.t0 > s.a.max_hours * 3600 \
           or s.bytes > s.a.max_mb * 1e6 or s.cost() > s.a.budget:
            raise StopIteration("limit reached")
        wait = s.delay - (time.time() - s.last)
        if wait > 0: time.sleep(wait)
        for attempt in range(4):
            s.last = time.time(); s.reqs += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r: body = r.read(); final = r.geturl()
                # The bot filter now 302s every non-home URL back to /readingroom. urllib follows
                # that silently, so treat "asked for X, landed on the homepage" as a block.
                redirected_home = final.rstrip("/") in (ROOT.rstrip("/"), BASE + "/readingroom/home") \
                    and url.rstrip("/") != final.rstrip("/")
                if redirected_home or b"<title>Access Denied</title>" in body[:300]:
                    s.blocked = getattr(s, "blocked", 0) + 1; s.was_blocked = True
                    if s.blocked >= 5: raise StopIteration("5 Access Denied responses - site is blocking this client; stopping politely")
                    print("Blocked (redirected to homepage)" if redirected_home else "Access Denied:", url)
                    return None
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 503): time.sleep(30 * (attempt + 1)); continue
                if e.code in (302, 403): s.blocked = getattr(s, "blocked", 0) + 1; s.was_blocked = True
                if getattr(s, "blocked", 0) >= 5: raise StopIteration("5 Access Denied responses - site is blocking this client; stopping politely")
                print("HTTP", e.code, url); return None
            except StopIteration:
                raise
            except Exception as e:
                print("err", e, url); time.sleep(10 * (attempt + 1))
        else:
            return None
        s.bytes += len(body); s.stat("bytes", s.bytes)
        with gzip.open(key, "wb") as f: f.write(body)
        return body

    def add(s, url, kind, prio):
        s.db.execute("insert or ignore into frontier(url,kind,prio) values(?,?,?)", (url, kind, prio))

    def norm(s, href, cur):
        u = urllib.parse.urljoin(cur, href).split("#")[0]
        p = urllib.parse.urlparse(u)
        if p.netloc not in ("www.cia.gov", "cia.gov"): return None, None
        if DOC_RE.match(p.path): return BASE + p.path, "doc"
        if PDF_RE.match(p.path): return BASE + p.path, "pdf"
        if LIST_RE.match(p.path): return u.replace("://cia.gov", "://www.cia.gov"), "list"
        return None, None

    def run(s):
        for u in SEEDS: s.add(u, "list", 0)
        s.db.commit()
        try:
            while True:
                # documents first (metadata/title/text is the priority), then listings to discover more
                row = s.db.execute("select url,kind from frontier where done=0 and kind!='pdf' "
                                   "order by kind='list', prio limit 1").fetchone()
                if not row and s.a.pdfs:
                    row = s.db.execute("select url,kind from frontier where done=0 and kind='pdf' limit 1").fetchone()
                if not row: print("frontier empty – done"); break
                url, kind = row
                s.was_blocked = False
                body = s.get(url)
                if s.was_blocked:  # leave it queued for a rerun from a network that isn't blocked
                    s.db.execute("update frontier set prio=prio+10 where url=?", (url,)); continue
                s.db.execute("update frontier set done=1 where url=?", (url,))
                if body is None: continue
                if kind == "pdf": s.pdf(url, body)
                else: s.page(url, kind, body.decode("utf-8", "replace"))
                if s.reqs % 25 == 0:
                    s.db.commit(); s.report()
        except StopIteration as e:
            print("STOP:", e)
        s.db.commit(); s.report()

    def page(s, url, kind, html):
        p = Page(); p.feed(html)
        for h in p.links:
            u, k = s.norm(h, url)
            if u: s.add(u, k, 1 if k == "doc" else 2)
        if kind == "doc":
            text = re.sub(r"[ \t]+", " ", "".join(p.text)); text = re.sub(r"\n\s*\n+", "\n", text)
            f = parse_fields(text)
            pdfs = [s.norm(h, url)[0] for h in p.links if s.norm(h, url)[1] == "pdf"]
            title = (p.h1 or p.title.split("|")[0]).strip()
            s.db.execute("insert or replace into docs values(?,?,?,?,?)",
                         (url, title, json.dumps(f), f.get("Body", ""), pdfs[0] if pdfs else None))

    def pdf(s, url, body):
        tmp = os.path.join(HERE, "_tmp.pdf"); open(tmp, "wb").write(body)
        try:
            txt = subprocess.run(["pdftotext", "-l", "20", tmp, "-"], capture_output=True,
                                 timeout=120).stdout.decode("utf-8", "replace")
        except Exception: txt = ""
        txt = re.sub(r"\s+", " ", txt)[:30000]
        s.db.execute("update docs set text = coalesce(text,'') || ' ' || ? where pdf=?", (txt, url))

    def report(s):
        n = s.db.execute("select count(*) from docs").fetchone()[0]
        q = s.db.execute("select count(*) from frontier where done=0").fetchone()[0]
        print(f"[{time.strftime('%H:%M:%S')}] reqs={s.reqs} docs={n} queued={q} "
              f"MB={s.bytes/1e6:.1f} est_egress_cost=${s.cost():.4f}", flush=True)


def estimate(c):
    """Read the result count on the first search page and project crawl time / bandwidth."""
    html = (c.get(ROOT) or b"").decode("utf-8", "replace")
    m = re.search(r"([\d,]+)\s+(?:results|items|documents)", html, re.I) or \
        re.search(r"of\s+([\d,]+)", html)
    n = int(m.group(1).replace(",", "")) if m else None
    print("Reported document count:", n or "unknown (could not parse)")
    n = n or 930000  # public figure is roughly 930k docs / ~13M pages if parse fails
    per = c.delay
    print(f"Metadata-only crawl @ {per}s/request: {n*per/86400:.1f} days, ~{n*40e3/1e9:.0f} GB HTML")
    print(f"With PDFs (avg ~300KB): ~{n*300e3/1e9:.0f} GB extra, cost on laptop $0; "
          f"on cloud VM egress ~${n*340e3/1e9*COST_PER_GB_EGRESS:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--pdfs", action="store_true")
    ap.add_argument("--delay", type=float, default=10.0)
    ap.add_argument("--max-requests", type=int, default=2000)
    ap.add_argument("--max-hours", type=float, default=6)
    ap.add_argument("--max-mb", type=float, default=1000)
    ap.add_argument("--budget", type=float, default=5.0, help="USD hard stop (egress estimate)")
    a = ap.parse_args(); c = Crawler(a)
    estimate(c) if a.estimate else c.run()
