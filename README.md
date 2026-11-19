# How crazy is your conspiracy theory?

Type "Is it true that ____?" The page searches a scraped copy of the **CIA FOIA Electronic Reading Room**
(cia.gov/readingroom). It shows a meter from **Tinfoil hat!** to **Proven!** and cites the matching documents (title, URL, excerpt).

> **Disclaimer:** the meter is entertainment, **not a fact-check**. It uses keyword retrieval plus a crude
> word-cue heuristic over a partial corpus.

## Current status
About **112,600 documents** (~12% of the reading room), imported from public mirrors (see Sources), because
cia.gov blocks scripts. The site is live on GitHub Pages and the banner shows the current coverage.

## Run it in the cloud ($0)
The nightly update runs on **GitHub Actions** (`.github/workflows/update.yml`, free for public repos), so your PC
doesn't need to stay on. The search index is served from **Cloudflare R2** (10 GB storage and egress are free), which
avoids GitHub Pages' 1 GB cap. A full ~1M-document index is about 1 GB gzipped. The HTML stays on GitHub Pages.

| Piece | Where | Free allowance | Our use |
|---|---|---|---|
| Web page (HTML/JS) | GitHub Pages | 1 GB, 100 GB/month | < 1 MB |
| Search index | Cloudflare R2 | 10 GB storage, 10M reads/month, free egress | ~0.1–1 GB, ~5–30 reads per search |
| Nightly job | GitHub Actions | unlimited minutes (public repo), 6 h per run | ~3–4 h |
| `state.db` backup | GitHub Release asset | 2 GB per file | ~0.2 GB now |

R2 asks for a card on file. Past the free tier it bills (no hard cap), but this site would need ~500k searches
a month to get there.

**One-time setup**
1. **Back up the database** to a release (repo root, `gh auth login` first):
   ```powershell
   python site/scraper/statedb.py pack
   gh release create state state.db.gz --title "Scraper database" --notes "state.db snapshot used by the nightly update"
   ```
   Once the cloud job runs, it owns `state.db`. Turn off the local scheduled task so the two copies don't diverge
   (`Unregister-ScheduledTask "FOIA Theory Meter update"`). To work locally later, run
   `gh release download state -p state.db.gz --clobber` and then `python site/scraper/statedb.py unpack`.
2. **Create the bucket.** In the Cloudflare dashboard, open R2, create a bucket (e.g. `foia-index`), and under
   *Settings → Public access* enable the `r2.dev` URL (or connect a custom domain, which is better for heavy traffic).
   Under *Settings → CORS policy*, add:
   ```json
   [{"AllowedOrigins": ["https://rarendsal.github.io", "http://localhost:8000"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 86400}]
   ```
3. **Create an API token**: *R2 → Manage API tokens → Create*, with *Object Read & Write* on that bucket only.
4. **Add GitHub repo settings** (*Settings → Secrets and variables → Actions*):
   - Secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`
   - Variable: `DATA_URL` = the bucket's public URL, e.g. `https://pub-xxxx.r2.dev/`
5. Run **Actions → Nightly update → Run workflow**. When it finishes, run **Deploy site to GitHub Pages** once, so the
   page is pointed at `DATA_URL`.

Each run publishes the index to `builds/<timestamp>/` in the bucket, switches `latest.json` to it, and keeps the
three newest builds. Open pages never mix files from two builds. Alias suggestions are kept as the
`phrase-candidates` artifact on each run. Without the R2 secrets, the job falls back to the old path: it uploads
`site-data.zip` to the `index` release and redeploys Pages.

## Keeping it updated (locally)
`update.ps1` (repo root) runs the whole cycle on your PC. Every step resumes where the last run stopped:
1. `sources.py ia-topics` and `documentcloud`: search for every topic alias. Aliases already searched are skipped,
   so anything new you add to `topics.json` gets fetched on the next run.
2. `sources.py ia-bulk`: walks the full ~1M-document reading-room mirror, about 140k documents per 3,000 requests.
3. `build_index.py`: rebuilds the split index in `site/data/` and `site-data.zip`.
4. `phrases.py`: writes alias and topic suggestions to `site/scraper/phrase_candidates.csv`.
5. Uploads `site-data.zip` to the `index` release and redeploys the site (needs `gh auth login`).
   It refuses to publish if the index passes 900 MB, because GitHub Pages sites are limited to 1 GB.

```powershell
.\update.ps1                  # full cycle
.\update.ps1 -NoPublish       # collect and rebuild only
.\update.ps1 -BulkRequests 6000
```
To run it nightly, see "Schedule it" below.

### Adding topics
One command, from the repo root. It asks for anything you leave out, then shows roughly how many of your
documents already mention each alias:
```powershell
python site\scraper\topic.py add                                    # step-by-step prompts
python site\scraper\topic.py add "ARGO / Iran hostage exfiltration" --alias "ARGO, Canadian caper" --ask "Argo movie" --related iran_contra --desc "CIA operation that got six US diplomats out of Iran in 1980."
python site\scraper\topic.py alias stargate SCANATE                 # more aliases for an existing topic
python site\scraper\topic.py review                                 # y/n through the suggestions in phrase_candidates.csv
python site\scraper\topic.py list                                   # or: show stargate, remove argo
```
- `--alias` tags documents. ALL-CAPS aliases match case-sensitively (code names), and the rest match any case.
- `--ask` only recognizes the topic in a question. Use it for broad words like "Kennedy" that would mis-tag documents.
- `review` walks the `alias -> <topic>` and `new topic?` suggestions that `phrases.py` writes. Answer `y` to add,
  `n` to hide the suggestion for good (saved in `rejected_phrases.txt`), or Enter to skip.
- Add `--push` to commit and push `topics.json`. The nightly cloud update then searches the new aliases, retags every
  document, and republishes. To do that on this PC instead, run `update.ps1`.

### Schedule it (Windows Task Scheduler)
```powershell
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\update.ps1`""
$t = New-ScheduledTaskTrigger -Daily -At 2am
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName "FOIA Theory Meter update" -Action $a -Trigger $t -Settings $s
```
Run it from the repo folder. Output goes to `update.log`.

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
python3 build_index.py                            # writes the split index to site/data/ (no --demo!)
cd .. && python3 -m http.server 8000         # open http://localhost:8000
```
The site is fully static: deploy the `site/` folder to GitHub Pages, Netlify, or similar for free. `site/config.js`
says where the index lives (default `data/`, next to the page).

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
  a compact copy inside `data/meta.json` (per-topic doc counts, year range, top docs, co-occurring topics).
- **`scraper/phrases.py`** finds code names and phrases no alias covers yet, and labels each one
  `alias -> <topic>` or `new topic?` in `phrase_candidates.csv` (see "Adding topics" above). It filters out
  ordinary words (`common_words.txt`), letterhead, and collection boilerplate, and uses under 1 GB of RAM at any corpus size.
- On the site: a "Browse topics" list, topic cards (description, aliases, related topics, top docs), and
  alias matches highlighted in blue. An alias match counts as covering the topic's words in both scores.
  It doesn't change the rule that a *sentence* must confirm the claim before the needle passes 45.

## How the meter works
1. **BM25** runs in the browser over title and text (title weighted ×2, text capped at 3,000 chars per doc). The index is split
   into small gzipped files (`scraper/shard.py`, unzipped in the browser): the page loads ~0.15 MB up front and ~0.5–1 MB per search.
2. Two separate numbers, because "the program existed" ≠ "the wild version is true":
   - **Paper trail** (shown as a number): how much the top documents cover your keywords. Example: the CIA did fund psychic
     "remote viewing" research, so that topic has a big paper trail.
   - **Claim support** (drives the needle): looks only at sentences (and titles) that state the claim: most of its words,
     including its distinctive words ("faked" in "the moon landing was faked"). Synonyms count ("people" = subjects/patients).
     If the question names the CIA, the sentence or its document's title must too. It then weighs "confirm" cues
     (approved, funded, conducted…) against "deny" cues (no evidence, inconclusive…) and hedges (alleged, conspiracy
     theorists, movie, questions). The needle is **capped at 30** when no sentence states the claim and **at 25** when denials win.
3. 0–20 Tinfoil hat, …, 80–100 "Proven-ish".

Known limits: the scoring is keyword-based, so negation and sarcasm aren't understood. CREST OCR text is noisy. Many records
are PDF-only, and documents imported in bulk carry only the archive's description text.
