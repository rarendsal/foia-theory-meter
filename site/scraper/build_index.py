#!/usr/bin/env python3
"""Export scraped docs (state.db) -> a split search index in site/data/ (see shard.py), plus site-data.zip
   at the repo root for uploading to the "index" GitHub Release that the Pages workflow deploys from.
   python3 build_index.py            # real scraped data
   python3 build_index.py --demo     # synthetic placeholder docs (UI testing only)"""
import json, os, re, sqlite3, sys, time, zipfile
from topics import load_topics, build_db
from shard import write_index

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data")
ZIP = os.path.join(HERE, "..", "..", "site-data.zip")
EST_TOTAL = 930000  # approx. public size of the reading room (documents)
MAX_CHARS = 3000    # text kept per doc to keep the static JSON small

DEMO = [
 ("[DEMO] Synthetic memo about a mind-control research program",
  "Placeholder text. Project funding approved for behavioral research including drug testing on unwitting subjects. Records were destroyed in 1973. Program confirmed in later congressional hearings."),
 ("[DEMO] Synthetic report on remote viewing experiments",
  "Placeholder text. Remote viewing psychic experiments were funded for years. Evaluation concluded results provided no actionable intelligence and program was terminated."),
 ("[DEMO] Synthetic analysis of UFO sightings",
  "Placeholder text. Many unidentified flying object sightings were attributed to reconnaissance aircraft flights at high altitude. No evidence of extraterrestrial origin was found."),
 ("[DEMO] Synthetic cable regarding moon landing propaganda",
  "Placeholder text. Foreign press claims that the moon landing was staged are disinformation; no evidence supports the hoax claims."),
 ("[DEMO] Synthetic memo on German rocket specialists",
  "Placeholder text. Under Project PAPERCLIP the JIOA approved contracts for German scientists including Wernher von Braun. The group was later transferred to Redstone Arsenal in Huntsville."),
 ("[DEMO] Synthetic report on the Army Ballistic Missile Agency",
  "Placeholder text. The Army Ballistic Missile Agency at Redstone Arsenal conducted tests of the Jupiter missile. Former Peenemünde engineers led the work."),
 ("[DEMO] Synthetic GRILL FLAME evaluation",
  "Placeholder text. GRILL FLAME psychoenergetic sessions were funded by the Army. Reviewers found the psycho-energetic results inconclusive."),
 ("[DEMO] Synthetic memo on weather modification",
  "Placeholder text. Cloud seeding weather modification was tested in a limited operation. Results were inconclusive."),
]


def main():
    demo = "--demo" in sys.argv
    docs = []
    if demo:
        for i, (t, x) in enumerate(DEMO):
            docs.append({"id": i, "t": t, "u": "#demo", "d": "", "c": "DEMO", "x": x})
    else:
        db = sqlite3.connect(os.path.join(HERE, "state.db"))
        for i, (u, t, f, x, pdf) in enumerate(db.execute("select url,title,fields,text,pdf from docs")):
            f = json.loads(f or "{}")
            x = re.sub(r"\s+", " ", x or "").strip()[:MAX_CHARS]
            docs.append({"id": i, "t": t, "u": u, "pdf": pdf, "col": f.get("Collection", ""),
                         "d": f.get("Document Creation Date") or f.get("Publication Date", ""),
                         "c": f.get("Original Classification", ""), "x": x})
    topics, doc_topics = build_db(docs, load_topics())
    for d in docs: d["k"] = doc_topics.get(d["id"], [])
    meta = {"demo": demo, "n": len(docs), "est_total": EST_TOTAL,
            "with_text": sum(1 for d in docs if len(d["x"]) > 50),
            "coverage_pct": round(100 * len(docs) / EST_TOTAL, 4),
            "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    os.makedirs(OUT, exist_ok=True)
    meta["tagged"] = len(doc_topics)
    meta = write_index(docs, topics, meta, OUT)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, _, files in os.walk(OUT):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, OUT))
    print(json.dumps(meta))
    print(f"Wrote {os.path.abspath(ZIP)} ({os.path.getsize(ZIP) / 1e6:.1f} MB). Upload it with:\n"
          f"  gh release upload index site-data.zip --clobber   (then: gh workflow run pages.yml)")


if __name__ == "__main__":
    main()
