"""The collection: scanning sessions, every capture they made, and the copies owned.

`items` is what you own. A capture counts towards it while its status is `added`; changing a capture's card,
finish or status moves that one copy. Copies added by hand (the +/- buttons) live only in `items`.
"""

import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tools"))
from build_db import norm  # noqa: E402

CARDS_DB = HERE / "data" / "cards.db"
# CARDSCAN_COLLECTION points everything at another file, for testing without touching the real collection
COLLECTION_DB = Path(os.environ.get("CARDSCAN_COLLECTION") or HERE / "data" / "collection.db")
# a test collection keeps its captures beside it, so its session numbers can't land on the real ones
CAPTURES = HERE / "captures" if not os.environ.get("CARDSCAN_COLLECTION") else COLLECTION_DB.parent / "captures"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  started_at INTEGER NOT NULL,
  source TEXT NOT NULL,
  dir TEXT NOT NULL,
  finish_mode TEXT NOT NULL DEFAULT 'normal'
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  n INTEGER NOT NULL,
  file TEXT NOT NULL,
  t REAL NOT NULL,
  card_id TEXT,
  finish TEXT,
  condition TEXT NOT NULL DEFAULT 'NM',
  -- added (counts towards items), review (needs a decision), repeat (same card read again), rejected
  status TEXT NOT NULL,
  confidence TEXT,
  rough INTEGER NOT NULL DEFAULT 0,
  candidates TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS scans_session ON scans(session_id, n);
CREATE TABLE IF NOT EXISTS items (
  card_id TEXT NOT NULL,
  finish TEXT NOT NULL,
  condition TEXT NOT NULL DEFAULT 'NM',
  qty INTEGER NOT NULL,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (card_id, finish, condition)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS items_added ON items(added_at);
CREATE TABLE IF NOT EXISTS prices (
  card_id TEXT NOT NULL,
  finish TEXT NOT NULL,
  source TEXT NOT NULL,
  usd REAL NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (card_id, finish, source)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Finish keys: Magic uses Scryfall's nonfoil/foil/etched; Pokemon keys start with normal/holo/reverse and append
# what makes the print distinct ("reverse:pokeball"), with labels in the cards.db variants table; Lorcana has
# normal/foil.
# What the scanner's finish toggle can say about the pile; Magic and Lorcana have no holo/reverse split, so both
# mean foil.
MODES = {"normal": "Normal", "foil": "Foil / Reverse", "holo": "Holo"}
PREFER = {
    "mtg": {"normal": ["nonfoil"], "foil": ["foil", "etched"], "holo": ["foil", "etched"]},
    "pkm": {"normal": ["normal", "holo"], "foil": ["reverse", "holo"], "holo": ["holo", "reverse"]},
    "lor": {"normal": ["normal", "foil"], "foil": ["foil"], "holo": ["foil"]},
}
LABELS = {"nonfoil": "Normal", "foil": "Foil", "etched": "Etched foil", "normal": "Normal", "holo": "Holo",
          "reverse": "Reverse holo"}


def now():
    return int(time.time() * 1000)


def connect(path=None):
    """The collection with cards.db attached as `c`."""
    path = Path(path or COLLECTION_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{path}", uri=True, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    # the scanner writes while the review page reads
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(SCHEMA)
    db.execute("ATTACH DATABASE ? AS c", (f"file:{CARDS_DB}?mode=ro",))
    remap_missing(db)
    return db


def remap_missing(db):
    """Move rows whose card left cards.db to its replacement. Cards of sets TCGdex didn't have yet are stored as
    `tcg-<productId>` until a later build has the real card, which carries the same TCGplayer product id."""
    gone = [r[0] for r in db.execute(
        """SELECT card_id FROM items WHERE card_id NOT IN (SELECT id FROM c.cards)
           UNION SELECT card_id FROM scans WHERE card_id IS NOT NULL AND card_id NOT IN (SELECT id FROM c.cards)""")]
    with db:
        for old in gone:
            if not old.startswith("tcg-") or not old[4:].isdigit():
                continue
            new = db.execute("SELECT id FROM c.cards WHERE tcg_id = ? AND id NOT LIKE 'tcg-%' LIMIT 1",
                             (int(old[4:]),)).fetchone()
            if not new:
                continue
            for row in db.execute("SELECT * FROM items WHERE card_id = ?", (old,)).fetchall():
                _bump(db, new[0], row["finish"], row["condition"], row["qty"])
            db.execute("DELETE FROM items WHERE card_id = ?", (old,))
            db.execute("UPDATE scans SET card_id = ? WHERE card_id = ?", (new[0], old))


def get_meta(db, key):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(db, key, value):
    with db:
        db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def finishes(db, card_id):
    """[(key, label)] the card exists in, plain first."""
    rows = db.execute("SELECT key, label FROM c.variants WHERE card_id = ? ORDER BY sort", (card_id,)).fetchall()
    if rows:
        return [(r["key"], r["label"]) for r in rows]
    card = db.execute("SELECT game, finishes FROM c.cards WHERE id = ?", (card_id,)).fetchone()
    keys = [k for k in (card["finishes"] or "").split(",") if k] if card else []
    if not keys:
        keys = ["nonfoil" if card and card["game"] == "mtg" else "normal"]
    return [(k, label(k)) for k in keys]


def label(key):
    return LABELS.get(key) or " · ".join(LABELS.get(p, p.replace("-", " ")) for p in key.split(":"))


def pick_finish(db, card_id, mode="normal"):
    """The finish a card scanned in `mode` (normal or foil) most likely has. Plain prints win over special ones."""
    keys = [k for k, _ in finishes(db, card_id)]
    game = db.execute("SELECT game FROM c.cards WHERE id = ?", (card_id,)).fetchone()["game"]
    for kind in PREFER[game][mode]:
        for k in keys:
            if k == kind:
                return k
        for k in keys:
            if k.split(":")[0] == kind:
                return k
    return keys[0]


def _bump(db, card_id, finish, condition, delta):
    db.execute(
        """INSERT INTO items (card_id, finish, condition, qty, added_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (card_id, finish, condition) DO UPDATE SET qty = qty + excluded.qty,
             added_at = CASE WHEN excluded.qty > 0 THEN excluded.added_at ELSE added_at END""",
        (card_id, finish, condition, delta, now()),
    )
    db.execute("DELETE FROM items WHERE card_id = ? AND finish = ? AND condition = ? AND qty <= 0",
               (card_id, finish, condition))


def adjust(db, card_id, finish, condition, delta):
    with db:
        _bump(db, card_id, finish, condition, delta)


def start_session(db, source, finish_mode="normal", out=None):
    """New session id and its capture folder: `out`, or captures/<id>."""
    with db:
        session_id = db.execute("INSERT INTO sessions (started_at, source, dir, finish_mode) VALUES (?, ?, '', ?)",
                                (now(), source, finish_mode)).lastrowid
        out = Path(out) if out else CAPTURES / str(session_id)
        db.execute("UPDATE sessions SET dir = ? WHERE id = ?", (str(out.resolve()), session_id))
    return session_id, out


def delete_session(db, session_id):
    """Drop a session, the copies its added captures put in the collection, and its capture folder. Copies added
    by hand (+1) stay."""
    row = db.execute("SELECT dir FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ValueError("no such session")
    with db:
        for s in db.execute("SELECT * FROM scans WHERE session_id = ? AND status = 'added'", (session_id,)).fetchall():
            _bump(db, s["card_id"], s["finish"], s["condition"], -1)
        db.execute("DELETE FROM scans WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    folder = Path(row["dir"]).resolve() if row["dir"] else None
    # only folders this app made; a replay's --out could be anywhere
    if folder and folder.parent == CAPTURES.resolve():
        shutil.rmtree(folder, ignore_errors=True)


def add_scan(db, session_id, entry):
    """Record a capture (an entry from scan.Session) and count it if it was added."""
    with db:
        scan_id = db.execute(
            """INSERT INTO scans (session_id, n, file, t, card_id, finish, status, confidence, rough, candidates,
                                  created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, entry["n"], entry["file"], entry["t"], entry["id"], entry.get("finish"), entry["status"],
             entry["confidence"], int(entry["rough"]), json.dumps(entry.get("candidates") or []), now()),
        ).lastrowid
        if entry["status"] == "added":
            _bump(db, entry["id"], entry["finish"], "NM", 1)
    return scan_id


def update_scan(db, scan_id, **changes):
    """Change a capture's card_id, finish, condition, status or confidence, moving its copy in `items`."""
    with db:
        old = db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        new = {**dict(old), **changes}
        if new["card_id"] and not new["finish"]:
            mode = db.execute("SELECT finish_mode FROM sessions WHERE id = ?", (old["session_id"],)).fetchone()[0]
            new["finish"] = pick_finish(db, new["card_id"], mode)
        if new["status"] == "added" and not new["card_id"]:
            raise ValueError("a capture needs a card before it can be added")
        if old["status"] == "added":
            _bump(db, old["card_id"], old["finish"], old["condition"], -1)
        if new["status"] == "added":
            _bump(db, new["card_id"], new["finish"], new["condition"], 1)
        db.execute(
            "UPDATE scans SET card_id = ?, finish = ?, condition = ?, status = ?, confidence = ? WHERE id = ?",
            (new["card_id"], new["finish"], new["condition"], new["status"], new["confidence"], scan_id),
        )
