"""Imports a collection CSV exported by another app into `items`.

ManaBox exports carry Scryfall ids, which are the Magic card ids here. Other exports (Moxfield, Deckbox, Dragon Shield,
TCGplayer and the like) are matched by set code and collector number, checked against the name, or by name and set
name when there's no number. Every import is logged so it can be undone.

    python importer.py export.csv            # what would be imported, without importing
    python importer.py export.csv --apply
"""

import argparse
import csv
import io
import json
from collections import Counter

import collection
from build_db import norm

# Header names other apps use for each field, compared lowercased with spaces, dashes and underscores dropped
COLUMNS = {
    "qty": ["quantity", "count", "qty", "amount", "havequantity"],
    "name": ["name", "cardname", "card", "productname", "simplename"],
    "set": ["setcode", "set", "editioncode", "edition", "expansioncode", "setnumber"],
    "set_name": ["setname", "editionname", "expansion", "expansionname"],
    "number": ["collectornumber", "cardnumber", "number", "cn", "no", "collectorsnumber"],
    "scryfall": ["scryfallid"],
    "finish": ["foil", "finish", "printing", "foiling", "variant", "variance"],
    "condition": ["condition", "cond"],
    "lang": ["language", "lang"],
}

# the scanner's finish toggle mode closest to each kind, for cards that don't come in that exact finish
KIND_MODE = {"plain": "normal", "foil": "foil", "etched": "foil", "reverse": "foil", "holo": "holo"}
PLAIN_KEY = {"mtg": "nonfoil", "pkm": "normal", "lor": "normal"}

CONDITIONS = {
    "NM": ["nearmint", "nm", "mint", "m", "nmm"],
    "LP": ["lightlyplayed", "lp", "excellent", "ex", "slightlyplayed", "sp"],
    "MP": ["moderatelyplayed", "mp", "good", "gd", "played", "pl"],
    "HP": ["heavilyplayed", "hp"],
    "DMG": ["damaged", "dmg", "poor", "po"],
}
CONDITION = {alias: code for code, aliases in CONDITIONS.items() for alias in aliases}
def condition(raw):
    """Deckbox writes "Good (Lightly Played)", so a full condition name inside the text wins over a short code."""
    k = key(raw)
    for code, aliases in CONDITIONS.items():
        if aliases[0] in k:
            return code
    return CONDITION.get(k, "NM")


LANGS = {"english": "en", "japanese": "ja"}


def key(header):
    return "".join(ch for ch in header.lower() if ch.isalnum())


def read_rows(text):
    """[(line, {field: value})] with the fields in COLUMNS, and the detected format."""
    text = text.lstrip("\ufeff")
    first = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = [key(h) for h in next(reader, [])]
    where = {}
    for field, aliases in COLUMNS.items():
        for alias in aliases:
            if alias in header:
                where[field] = header.index(alias)
                break
    if "name" not in where and "scryfall" not in where:
        raise ValueError("no Name or Scryfall ID column; is this a collection CSV?")
    source = "ManaBox" if "manaboxid" in header else "CSV"
    rows = []
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        rows.append((line, {f: values[i].strip() if i < len(values) else "" for f, i in where.items()}))
    return rows, source


def names_match(wanted, card):
    """Exports spell double-faced cards as "Front // Back" or just "Front", Lorcana as "Name - Version"."""
    if not wanted:
        return True
    n = norm(wanted)
    front = norm(wanted.split("//")[0])
    return n in (card["name_norm"], card["local_norm"]) or (front and card["name_norm"].startswith(front))


class Cards:
    """Lookups into cards.db for one import: set codes and names to codes, and each set's cards, loaded once."""

    def __init__(self, db):
        self.db = db
        self.sets = {}
        for code, name in db.execute("SELECT DISTINCT set_code, set_name FROM c.cards"):
            self.sets.setdefault(code.lower(), set()).add(code)
            self.sets.setdefault(name.lower(), set()).add(code)
        self.by_set = {}

    def in_sets(self, value):
        out = []
        for code in sorted(self.sets.get(value.lower(), ())):
            if code not in self.by_set:
                self.by_set[code] = self.db.execute("SELECT * FROM c.cards WHERE set_code = ?", (code,)).fetchall()
            out += self.by_set[code]
        return out

    def named(self, name):
        n = norm(name)
        return self.db.execute("SELECT * FROM c.cards WHERE name_norm = ? OR local_norm = ?", (n, n)).fetchall()

    def by_id(self, card_id):
        return self.db.execute("SELECT * FROM c.cards WHERE id = ?", (card_id,)).fetchone()


def same_number(a, b):
    return (a.lower().lstrip("0") or "0") == (b.lower().lstrip("0") or "0")


def find_card(cards, row, game_hint):
    if row.get("scryfall"):
        card = cards.by_id(row["scryfall"].lower())
        if card:
            return card, None
    name = row.get("name", "")
    # TCGplayer and most Pokemon apps write "025/165"
    number, _, total = row.get("number", "").partition("/")
    number, total = number.strip(), total.strip()
    lang = LANGS.get(row.get("lang", "").lower(), row.get("lang", "").lower()) or "en"
    # "Set" or "Edition" holds the set's name in some exports (Deckbox, TCGplayer)
    in_set = cards.in_sets(row.get("set", "")) or cards.in_sets(row.get("set_name", ""))
    if not in_set:
        # set names differ between apps ("SV: Scarlet & Violet 151" is "151" here), so a card whose name and
        # number appear in just one set is taken from there
        if name and number:
            found = [c for c in cards.named(name) if same_number(c["number"], number)
                     and (not total or same_number(c["printed_total"] or "", total))]
            found = [c for c in found if c["lang"] == lang] or found
            if len({c["set_id"] for c in found}) == 1:
                return found[0], None
        return None, "no set" if not (row.get("set") or row.get("set_name")) else "unknown set"
    in_set = [c for c in in_set if names_match(name, c)]
    if game_hint:
        in_set = [c for c in in_set if c["game"] == game_hint] or in_set
    # the export's language first; a card printed in English too is only here in English
    in_set.sort(key=lambda c: c["lang"] != lang)
    if number:
        found = [c for c in in_set if same_number(c["number"], number)]
        return (found[0], None) if found else (None, "not in the card database")
    if len({c["number"] for c in in_set}) == 1:
        return in_set[0], None
    if in_set:
        return None, "several printings in that set; needs a collector number"
    return None, "not in the card database"


def finish_kind(raw):
    """ManaBox says normal/foil/etched, Moxfield and Deckbox foil or blank, TCGplayer "1st Edition Holofoil" and
    "Reverse Holofoil"."""
    raw = raw.lower()
    if "reverse" in raw:
        return "reverse"
    if "etched" in raw:
        return "etched"
    if "holo" in raw:
        return "holo"
    if "foil" in raw.replace("nonfoil", "").replace("non-foil", "") or raw in ("true", "yes", "1"):
        return "foil"
    return "plain"


def pick(db, card, raw_finish):
    kind = finish_kind(raw_finish)
    keys = [k for k, _ in collection.finishes(db, card["id"])]
    want = PLAIN_KEY.get(card["game"], "normal") if kind == "plain" else kind
    for k in keys:
        if k == want:
            return k
    for k in keys:
        if k.split(":")[0] == want:
            return k
    return collection.pick_finish(db, card["id"], KIND_MODE[kind])


def plan(db, text):
    """What importing `text` would add: {source, rows, copies, lines: [(card_id, finish, condition, qty)],
    unmatched: [{line, name, set, number, reason}], games: {game: copies}}."""
    rows, source = read_rows(text)
    hint = "mtg" if source == "ManaBox" else None
    cards = Cards(db)
    lines, unmatched, games = Counter(), [], Counter()
    copies = 0
    for line, row in rows:
        try:
            qty = int(float(row.get("qty") or 1))
        except ValueError:
            qty = 1
        if qty <= 0:
            continue
        copies += qty
        card, reason = find_card(cards, row, hint)
        if not card:
            unmatched.append({"line": line, "name": row.get("name", ""), "set": row.get("set") or row.get("set_name", ""),
                              "number": row.get("number", ""), "qty": qty, "reason": reason})
            continue
        cond = condition(row.get("condition", ""))
        lines[(card["id"], pick(db, card, row.get("finish", "")), cond)] += qty
        games[card["game"]] += qty
    return {"source": source, "rows": len(rows), "copies": copies, "matched": sum(lines.values()),
            "lines": [[*k, q] for k, q in lines.items()], "unmatched": unmatched, "games": dict(games)}


def apply(db, text, name):
    p = plan(db, text)
    with db:
        for card_id, finish, cond, qty in p["lines"]:
            collection._bump(db, card_id, finish, cond, qty)
        p["import_id"] = db.execute(
            "INSERT INTO imports (at, name, source, copies, lines) VALUES (?, ?, ?, ?, ?)",
            (collection.now(), name, p["source"], p["matched"], json.dumps(p["lines"])),
        ).lastrowid
    return p


def history(db):
    return [dict(r) for r in db.execute(
        "SELECT id, at, name, source, copies, undone FROM imports ORDER BY id DESC")]


def undo(db, import_id):
    """Takes the import's copies back out. Copies of those cards removed since then stay removed."""
    row = db.execute("SELECT lines, undone FROM imports WHERE id = ?", (import_id,)).fetchone()
    if row is None:
        raise ValueError("no such import")
    if row["undone"]:
        raise ValueError("already undone")
    with db:
        for card_id, finish, cond, qty in json.loads(row["lines"]):
            collection._bump(db, card_id, finish, cond, -qty)
        db.execute("UPDATE imports SET undone = 1 WHERE id = ?", (import_id,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--apply", action="store_true", help="import into the collection")
    args = ap.parse_args()
    text = open(args.csv, encoding="utf-8-sig", errors="replace").read()
    db = collection.connect()
    p = apply(db, text, args.csv) if args.apply else plan(db, text)
    print(f"{p['source']}: {p['rows']} rows, {p['copies']} cards, {p['matched']} matched {p['games']}")
    for u in p["unmatched"]:
        print(f"  line {u['line']}: {u['name']} {u['set']} {u['number']} x{u['qty']}: {u['reason']}")


if __name__ == "__main__":
    main()
