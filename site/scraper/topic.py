#!/usr/bin/env python3
"""Add and edit topics without hand-editing topics.json.

  python topic.py add                                  # asks for everything, step by step
  python topic.py add "ARGO" --desc "CIA operation that got six US diplomats out of Iran in 1980." \\
                     --alias "ARGO, Canadian caper" --ask "Argo movie" --related iran_contra
  python topic.py alias stargate SCANATE "Project Scanate"   # more aliases for an existing topic
  python topic.py alias jfk --ask "magic bullet"             # question-only alias
  python topic.py review                               # go through phrase_candidates.csv: y/n per suggestion
  python topic.py list                                 # all topics with alias counts
  python topic.py show stargate                        # one topic in full
  python topic.py remove argo

Add --push to any change to commit and push topics.json, so the nightly cloud job picks it up.

Alias rules (same as the site): ALL-CAPS aliases match documents case-sensitively (code names like CHAOS);
others match any case. "MK-ULTRA" = "MK ULTRA" = "MKULTRA", and a trailing plural "s" is optional.
--ask aliases only recognize the topic in a question (for broad words like "Kennedy" that would mis-tag documents).
"""
import argparse, csv, json, os, re, sqlite3, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOPICS = os.path.join(HERE, "topics.json")
CANDIDATES = os.path.join(HERE, "phrase_candidates.csv")
REJECTED = os.path.join(HERE, "rejected_phrases.txt")   # review answers "no" land here, so they don't come back


# ---------------------------------------------------------------- topics.json
def load():
    with open(TOPICS, encoding="utf-8") as f: return json.load(f)


def save(data):
    tmp = TOPICS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)   # same layout as the file in git: clean diffs
    os.replace(tmp, TOPICS)


def split(values):
    """--alias "A, B" --alias C  ->  [A, B, C]"""
    out = []
    for v in values or []: out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s[:40] or "topic"


def owner(topics, alias):
    """Topic id that already has this alias (any case), or None."""
    a = alias.lower()
    for tid, t in topics.items():
        if a in (x.lower() for x in t["aliases"] + t.get("query_aliases", [])): return tid
    return None


def find_topic(topics, key):
    if key in topics: return key
    hits = [tid for tid, t in topics.items() if key.lower() in (tid.lower(), t["name"].lower())]
    hits = hits or [tid for tid, t in topics.items() if key.lower() in t["name"].lower()]
    if len(hits) == 1: return hits[0]
    sys.exit(f"No single topic matches {key!r}" + (f": {', '.join(hits)}" if hits else ". Try: python topic.py list"))


# ---------------------------------------------------------------- quick check against the local corpus
def doc_counts(aliases):
    """Approximate number of local documents mentioning each alias (title + first 3,000 chars, like the index)."""
    path = os.path.join(HERE, "state.db")
    if not os.path.exists(path) or not aliases: return {}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for a in aliases:
        exact = a.upper() == a and any(c.isalpha() for c in a)    # code names: case-sensitive, like the tagger
        if exact:
            q = "select count(*) from docs where instr(title, ?) or instr(substr(text, 1, 3000), ?)"
            out[a] = db.execute(q, (a, a)).fetchone()[0]
        else:
            q = "select count(*) from docs where title like ? or substr(text, 1, 3000) like ?"
            out[a] = db.execute(q, (f"%{a}%", f"%{a}%")).fetchone()[0]
    return out


def report_counts(aliases):
    counts = doc_counts(aliases)
    if not counts: return
    print("Already in your local documents (rough count):")
    for a, n in counts.items():
        note = "  <- very common; consider --ask instead" if n > 5000 else "  (none yet; the next update will search for it)" if not n else ""
        print(f"  {a:<34} {n:>6} docs{note}")


# ---------------------------------------------------------------- prompts
def ask(prompt, default=""):
    try: v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError: v = ""
    return v or default


def yes(prompt, default=True):
    v = ask(prompt + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not v else v.startswith("y")


# ---------------------------------------------------------------- commands
def cmd_add(a, data):
    topics = data["topics"]
    name = a.name or ask("Topic name (e.g. ARGO / Iran hostage exfiltration)")
    if not name: sys.exit("A topic needs a name.")
    tid = a.id or slug(name)
    if tid in topics: sys.exit(f"Topic id {tid!r} already exists ({topics[tid]['name']}). Use: python topic.py alias {tid} ...")
    desc = a.desc or ask("One-sentence description")
    aliases = split(a.alias) or split([ask("Aliases / code names that tag documents, comma-separated", name)])
    asks = split(a.ask) if (a.alias or a.ask) else split([ask("Question-only words, comma-separated (Enter to skip)")])
    related = split(a.related) if (a.alias or a.related) else split([ask("Related topic ids, comma-separated (Enter to skip)")])
    aliases = add_unique([], aliases, topics, tid)
    asks = add_unique([], asks, topics, tid)
    bad = [r for r in related if r not in topics]
    if bad: print(f"Ignoring unknown related topics: {', '.join(bad)} (see: python topic.py list)")
    t = {"name": name, "desc": desc, "aliases": aliases, "see_also": [r for r in related if r in topics]}
    if asks: t["query_aliases"] = asks
    topics[tid] = t
    for r in t["see_also"]:                                    # link both ways
        if tid not in topics[r].setdefault("see_also", []): topics[r]["see_also"].append(tid)
    save(data)
    print(f"\nAdded topic {tid!r}: {name}\n  aliases: {', '.join(aliases)}"
          + (f"\n  question-only: {', '.join(asks)}" if asks else ""))
    report_counts(aliases)
    return f"Add topic {tid}"


def add_unique(existing, new, topics, tid):
    for x in new:
        other = owner(topics, x)
        if x.lower() in (e.lower() for e in existing): continue
        if other and other != tid: print(f"Note: {x!r} is also an alias of {other!r}; documents will be tagged with both.")
        existing.append(x)
    return existing


def cmd_alias(a, data):
    topics = data["topics"]
    tid = find_topic(topics, a.topic)
    t = topics[tid]
    new, asks = split(a.aliases), split(a.ask)
    if not new and not asks: new = split([ask(f"New aliases for {t['name']}, comma-separated")])
    before = len(t["aliases"]) + len(t.get("query_aliases", []))
    add_unique(t["aliases"], new, topics, tid)
    if asks: add_unique(t.setdefault("query_aliases", []), asks, topics, tid)
    added = len(t["aliases"]) + len(t.get("query_aliases", [])) - before
    if not added: print("Nothing new to add."); return None
    save(data)
    print(f"{t['name']}: added {added} alias(es).")
    report_counts(new)
    return f"Add aliases to {tid}: {', '.join(new + asks)}"


def cmd_remove(a, data):
    topics = data["topics"]
    tid = find_topic(topics, a.topic)
    if not a.yes and not yes(f"Remove topic {tid!r} ({topics[tid]['name']})?", False): return None
    del topics[tid]
    for t in topics.values():
        if tid in t.get("see_also", []): t["see_also"].remove(tid)
    save(data)
    print(f"Removed {tid}.")
    return f"Remove topic {tid}"


def cmd_list(a, data):
    for tid, t in sorted(data["topics"].items(), key=lambda kv: kv[1]["name"].lower()):
        print(f"  {tid:<22} {t['name']:<45} {len(t['aliases']):>3} aliases")


def cmd_show(a, data):
    tid = find_topic(data["topics"], a.topic)
    t = data["topics"][tid]
    print(f"{tid}: {t['name']}\n  {t['desc']}\n  aliases: {', '.join(t['aliases'])}")
    if t.get("query_aliases"): print(f"  question-only: {', '.join(t['query_aliases'])}")
    if t.get("see_also"): print(f"  related: {', '.join(t['see_also'])}")
    if a.count: report_counts(t["aliases"])


def cmd_review(a, data):
    """Walk phrase_candidates.csv (written by phrases.py) and accept or reject each suggestion."""
    if not os.path.exists(CANDIDATES): sys.exit("No phrase_candidates.csv yet. Run: python phrases.py")
    topics = data["topics"]
    rejected = set(open(REJECTED, encoding="utf-8").read().splitlines()) if os.path.exists(REJECTED) else set()
    names = {t["name"].lower(): tid for tid, t in topics.items()}
    rows = [r for r in csv.DictReader(open(CANDIDATES, encoding="utf-8"))
            if r["phrase"] not in rejected and not owner(topics, r["phrase"])]
    if a.only == "aliases": rows = [r for r in rows if r["suggestion"].startswith("alias")]
    if a.only == "new": rows = [r for r in rows if r["suggestion"].startswith("new")]
    print(f"{len(rows)} suggestions to review. Answers: y = add, n = never show again, Enter = skip, q = save and quit.\n")
    changes, no = [], []
    for i, r in enumerate(rows[:a.max]):
        target = r["suggestion"].split("->", 1)[1].strip() if "->" in r["suggestion"] else None
        target = target if target in topics else names.get((target or "").lower())
        print(f"[{i + 1}] {r['phrase']!r}  ({r['docs']} docs, {r['kind']}; mostly in {r['top_topic'] or r['top_collection']})")
        if r.get("example"): print(f"    e.g. \"{r['example'][:160]}\"")
        if target:
            v = ask(f"    Add as an alias of {topics[target]['name']}? (y/n/Enter/q)").lower()
        else:
            v = ask("    Make it a new topic? (y/n/Enter/q)").lower()
        if v.startswith("q"): break
        if v.startswith("n"): no.append(r["phrase"]); continue
        if not v.startswith("y"): continue
        if target:
            add_unique(topics[target]["aliases"], [r["phrase"]], topics, target)
            changes.append(f"{r['phrase']} -> {target}")
        else:
            name = ask("    Topic name", r["phrase"])
            tid = slug(name)
            if tid in topics:
                add_unique(topics[tid]["aliases"], [r["phrase"]], topics, tid)
            else:
                topics[tid] = {"name": name, "desc": ask("    One-sentence description"), "aliases": [r["phrase"]], "see_also": []}
            changes.append(f"new topic {tid}")
        save(data)                                             # save as we go: Ctrl-C loses nothing
    if no:
        with open(REJECTED, "a", encoding="utf-8") as f: f.write("".join(p + "\n" for p in no))
    print(f"\n{len(changes)} change(s), {len(no)} rejected.")
    return ("Review topic suggestions: " + "; ".join(changes))[:300] if changes else None


# ---------------------------------------------------------------- git
def push(message):
    repo = os.path.abspath(os.path.join(HERE, "..", ".."))
    files = [TOPICS] + ([REJECTED] if os.path.exists(REJECTED) else [])
    git = lambda *x: subprocess.run(["git", "-C", repo, *x], check=True)
    git("add", *files)
    if subprocess.run(["git", "-C", repo, "diff", "--cached", "--quiet", "--", *files]).returncode == 0:
        print("topics.json unchanged; nothing to push."); return
    git("commit", "-m", message, "--", *files)
    git("push")
    print("Pushed. The nightly update will search the new aliases, retag every document and republish.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="add a new topic (asks for anything you leave out)")
    p.add_argument("name", nargs="?", help='display name, e.g. "ARGO / Iran hostage exfiltration"')
    p.add_argument("--id", help="short id (default: made from the name)")
    p.add_argument("--desc", help="one-sentence description")
    p.add_argument("--alias", action="append", help="alias or code name that tags documents (repeat, or comma-separate)")
    p.add_argument("--ask", action="append", help="question-only alias (repeat, or comma-separate)")
    p.add_argument("--related", action="append", help="related topic id (repeat, or comma-separate)")
    p = sub.add_parser("alias", help="add aliases to an existing topic")
    p.add_argument("topic"); p.add_argument("aliases", nargs="*")
    p.add_argument("--ask", action="append", help="question-only alias")
    p = sub.add_parser("review", help="accept/reject suggestions from phrase_candidates.csv")
    p.add_argument("--only", choices=["aliases", "new"]); p.add_argument("--max", type=int, default=50)
    p = sub.add_parser("remove", help="delete a topic"); p.add_argument("topic"); p.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("list", help="list topics")
    p = sub.add_parser("show", help="show one topic"); p.add_argument("topic")
    p.add_argument("--count", action="store_true", help="count matching local documents per alias")
    for s in sub.choices.values():
        if s.prog.split()[-1] in ("add", "alias", "review", "remove"):
            s.add_argument("--push", action="store_true", help="commit and push topics.json afterwards")
    a = ap.parse_args()
    data = load()
    msg = {"add": cmd_add, "alias": cmd_alias, "review": cmd_review, "remove": cmd_remove,
           "list": cmd_list, "show": cmd_show}[a.cmd](a, data)
    if msg and getattr(a, "push", False): push(msg)
    elif msg:
        print("\nSaved topics.json. Next, either:\n"
              "  - commit and push it (or add --push next time); the nightly cloud update does the rest, or\n"
              "  - run .\\update.ps1 to fetch and rebuild on this PC (only the new aliases are searched).")


if __name__ == "__main__":
    main()
