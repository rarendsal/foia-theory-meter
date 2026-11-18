# How crazy is your conspiracy theory?

Type "Is it true that ____?" The page searches a scraped copy of the **CIA FOIA Electronic Reading Room**
(cia.gov/readingroom). It shows a meter from **Tinfoil hat!** to **Proven!** and cites the matching documents (title, URL, excerpt).

> **Disclaimer:** the meter is entertainment, **not a fact-check**. It uses keyword retrieval plus a crude
> word-cue heuristic over a partial corpus.

## Current status (be honest)
**0 real documents scraped.** `data/corpus.json` holds 8 synthetic DEMO docs, and the site shows a DEMO banner.
- First sandbox: no network access at all.
- With the VM network opened, robots.txt and the reading-room homepage loaded (HTTP 200). But the
  sitemap, `/historical-collections` and `/collection/*` pages all returned **"Access Denied"** (Akamai bot
  blocking of datacenter IPs). The user's own agent saw the same from its cloud server. We did **not** try to
  get around the block (no browser impersonation, no proxies). In total we made about 8 requests, all ≥10 s apart.
- So run the scraper from a residential/home connection. It stops by itself after 5 "Access Denied" responses.

## What robots.txt says (checked live)
`User-agent: *` → Disallow `/readingroom/search/`, `/readingroom/advanced-search-view/`, `/readingroom/request/`,
`*.json`, `*.js`. **Crawl-delay: 10.** Collection pages and `/readingroom/document/*` are allowed. The scraper
therefore seeds from collections (STARGATE, Animal Partners, MKULTRA, UFOs, Nixon, AQUILINE, PDB 1961-69) and
never touches `/search/`. The default delay is now 10 s.

## Size & cost estimate
| | Estimate |
|---|---|
| Reading room size | ~930k documents / ~13M pages (public figure; `--estimate` tries to read the live count) |
| Metadata crawl @ 10 s/request | ~8,600 docs/day → **~3.5 months** for everything. So: targeted collections first |
| HTML bandwidth | ~40 KB/doc → ~37 GB total |
| PDFs | ~300 KB/doc avg → ~280 GB (optional, `--pdfs`) |
| **$ cost** | **$0** on a laptop (no LLM, no APIs). On a cloud VM, egress is the only cost and the `--budget 5` tracker stops the crawl before $5 |

The full library doesn't fit in a reasonable polite crawl, so the scraper **prioritises document pages** (title,
metadata, on-page body text) over listings and PDFs. The site's banner reports the actual coverage %.

## Sources (use these: cia.gov blocks scripts)
cia.gov now 302-redirects every automated request to its homepage. The same records are mirrored with
public APIs, which `scraper/sources.py` imports into the same `state.db` (de-duplicated by CIA-RDP number):
- **Internet Archive** `ciareadingroom`: ~1.01M docs, a full mirror of the reading room, OCR text included.
  Plus `cia-collection` (CREST, ~275k), `stargatefiles`, `ufo-files`, `nwcda`, VENONA, NSA Friedman, OSS,
  Helms, Weisberg JFK, Lazar FBI FOIA, State Dept FOIA.
- **DocumentCloud**: journalists' and MuckRock FOIA uploads, full text.

```bash
cd site/scraper
python3 sources.py ia-topics                      # best first step: every topic alias, docs + text
python3 sources.py documentcloud --per-query 50
python3 sources.py ia-bulk --max-requests 2000    # walk the whole ~1M-doc mirror (resumable)
python3 sources.py status
python3 build_index.py
```

## Run it (direct cia.gov scrape, currently blocked)
```bash
cd site/scraper
python3 scrape.py --estimate                      # robots.txt check + size/time estimate
python3 scrape.py --max-requests 5000 --max-hours 6   # resumable; Ctrl-C and rerun any time
python3 scrape.py --pdfs --max-mb 2000            # optional: fetch PDFs, extract text (needs pdftotext)
python3 phrases.py                                # optional: suggest new key phrases/code names
python3 build_index.py                            # writes site/data/corpus.json (no --demo!)
cd .. && python3 -m http.server 8000         # open http://localhost:8000
```
The site is fully static: deploy the `site/` folder to GitHub Pages, Netlify, or similar for free.

## Politeness
- Reads `robots.txt` and honours Disallow and Crawl-delay (the delay is never below 3 s). Uses a descriptive User-Agent (edit the contact address).
- Backs off on 429/503 responses. Every response is gzipped into `scraper/cache/`, so pages are never fetched twice.
- Keeps the frontier and documents in `scraper/state.db` (SQLite), so the crawl is resume-safe.
- Hard stops: `--max-requests`, `--max-hours`, `--max-mb`, and `--budget` (USD).

## Key-phrase / topic database
Topics, aliases and code names live in **`scraper/topics.json`**: 30 topics, including Kennedy, UFOs,
STARGATE/psychoenergetics, MKULTRA, **PAPERCLIP** and **Redstone Arsenal** (cross-linked). Edit it, then rerun `build_index.py`.
- **`aliases`** tag documents and expand searches. Spacing/hyphen variants and plurals match automatically
  ("psycho-energetic" = "psychoenergetics", "MK-ULTRA" = "MKULTRA"), and accents are folded ("Peenemünde").
  ALL-CAPS code names (CHAOS, CORONA, PAPERCLIP) match documents case-sensitively, so "paperclip on the desk" isn't tagged.
- **`query_aliases`** only recognize a topic in a *question* ("Kennedy", "chemtrails", "psychic spies"). They're too broad to tag documents.
- `build_index.py` writes **`scraper/topics.db`** (SQLite) with the tables `topics`, `aliases`, `see_also`, and
  `doc_topics(doc_id, doc_url, topic, hits, year, sample)`. It's the reusable base for later features. The site gets
  a compact copy inside `data/corpus.json` (per-topic doc counts, year range, top docs, co-occurring topics).
- **`scraper/phrases.py`** finds new candidates: ALL-CAPS code-name runs plus distinctive 2–3-word phrases not
  yet covered by any alias → `phrase_candidates.csv`. Review it and promote good ones into `topics.json`. Run it after real scraping (on demo data it only finds demo boilerplate).
- On the site: a "Browse topics" list, topic cards (description, aliases, related topics, top docs), and
  alias matches highlighted in blue. An alias match counts as covering the topic's words in both scores.
  It doesn't change the rule that a *sentence* must confirm the claim before the needle passes 45.

## How the meter works
1. **BM25** runs in the browser over title and text. The title is weighted ×2, and each doc's text is capped at 3,000 chars to keep the JSON small.
2. Two separate numbers, because "the program existed" ≠ "the wild version is true":
   - **Paper trail** (shown as a number): how much the top documents cover your keywords. Example: the CIA did fund psychic
     "remote viewing" research, so that topic has a big paper trail.
   - **Claim support** (drives the needle): looks only at sentences containing ≥60% of your claim's words, and
     compares "confirm" cues (approved, funded, conducted…) with "deny" cues (no evidence, hoax, inconclusive…).
     The needle is **capped at 45** unless such a sentence actually confirms the claim, and **capped at 30** when deny cues win.
     So "psychics produced useful intelligence" or "Hitler survived" can't reach "Proven!" just because a file mentions them.
3. 0–20 Tinfoil hat, …, 80–100 "Proven-ish".

Known limits: the scoring is keyword-based, so negation and sarcasm aren't understood. CREST OCR text is noisy. Many records
are PDF-only. For corpora over ~50k docs, shard `corpus.json` or switch to a prebuilt inverted index (e.g. lunr/minisearch).
