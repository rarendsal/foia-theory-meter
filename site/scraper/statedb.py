#!/usr/bin/env python3
"""Back up and restore state.db (the scraped corpus) as one gzipped file, so it can live off your laptop.

  python3 statedb.py pack   [out.gz]   # consistent, compacted snapshot -> state.db.gz (repo root by default)
  python3 statedb.py unpack [in.gz]    # restore state.db from a snapshot (refuses to overwrite a bigger corpus)
  python3 statedb.py count             # number of documents in state.db

The nightly GitHub Actions workflow keeps the snapshot as the asset state.db.gz on the "state" release.
First upload, from the repo root:
  python site/scraper/statedb.py pack
  gh release create state state.db.gz --title "Scraper database" --notes "state.db snapshot, used by the nightly update"
"""
import gzip, os, shutil, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "state.db")
GZ = os.path.join(HERE, "..", "..", "state.db.gz")


def count(path=DB):
    if not os.path.exists(path): return 0
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try: return db.execute("select count(*) from docs").fetchone()[0]
    except sqlite3.OperationalError: return 0
    finally: db.close()


def check(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try: ok = db.execute("pragma quick_check").fetchone()[0]
    finally: db.close()
    if ok != "ok": sys.exit(f"{path} failed its integrity check: {ok}")


def pack(out=GZ):
    n = count()
    if not n: sys.exit("state.db has no documents; refusing to write an empty backup")
    tmp = DB + ".snapshot"
    if os.path.exists(tmp): os.remove(tmp)
    db = sqlite3.connect(DB)
    db.execute("vacuum into ?", (tmp,))     # consistent copy without free pages, safe while nothing is writing
    db.close()
    check(tmp)
    with open(tmp, "rb") as src, gzip.open(out, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    os.remove(tmp)
    print(f"{n} docs -> {os.path.abspath(out)} ({os.path.getsize(out) / 1e6:.0f} MB)")


def unpack(src=GZ):
    tmp = DB + ".restore"
    with gzip.open(src, "rb") as f, open(tmp, "wb") as dst:
        shutil.copyfileobj(f, dst, 1 << 20)
    check(tmp)
    new, old = count(tmp), count()
    if new < old: os.remove(tmp); sys.exit(f"snapshot has {new} docs but state.db has {old}; not overwriting")
    os.replace(tmp, DB)
    print(f"restored {new} docs into {DB}")


if __name__ == "__main__":
    cmd, arg = (sys.argv[1:] + [None, None])[:2]
    if cmd == "pack": pack(arg or GZ)
    elif cmd == "unpack": unpack(arg or GZ)
    elif cmd == "count": print(count())
    else: sys.exit(__doc__)
