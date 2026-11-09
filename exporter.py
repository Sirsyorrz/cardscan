"""Writes the collection as a CSV, one row per card, finish and condition. cardscan's own import reads it back, and
fakehams.com/cards builds its collection pages from it.

    python exporter.py [out.csv]      # default: Downloads/cardscan-collection-<date>.csv
"""

import argparse
import csv
import io
import time
from datetime import datetime, timezone
from pathlib import Path

import collection

COLUMNS = ["Card ID", "Game", "Language", "Name", "Set code", "Set name", "Collector number", "Finish", "Condition",
           "Quantity", "Added", "Price (USD)"]


def rows(db):
    return db.execute(
        """SELECT i.card_id, c.game, c.lang, c.name, c.set_code, c.set_name, c.number, i.finish, i.condition, i.qty,
                  i.added_at, COALESCE(p.usd, b.usd) AS usd
           FROM items i
           JOIN c.cards c ON c.id = i.card_id
           LEFT JOIN prices p ON p.card_id = i.card_id AND p.finish = i.finish AND p.source = 'tcg'
           LEFT JOIN c.base_prices b ON b.card_id = i.card_id AND b.finish = i.finish AND b.source = 'tcg'
           ORDER BY i.added_at, c.name""").fetchall()


def to_csv(found):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS)
    for r in found:
        added = datetime.fromtimestamp(r["added_at"] / 1000, timezone.utc).isoformat(timespec="seconds")
        w.writerow([r["card_id"], r["game"], r["lang"], r["name"], r["set_code"], r["set_name"], r["number"],
                    r["finish"], r["condition"], r["qty"], added, "" if r["usd"] is None else f"{r['usd']:.2f}"])
    return out.getvalue()


def default_path():
    folder = Path.home() / "Downloads"
    if not folder.is_dir():
        folder = Path.home()
    stem = f"cardscan-collection-{time.strftime('%Y-%m-%d')}"
    path = folder / f"{stem}.csv"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}).csv"
        n += 1
    return path


def write(db, path=None):
    """{path, rows, copies} of the written file."""
    path = Path(path) if path else default_path()
    # utf-8-sig so Excel on Windows shows accented and Japanese names properly
    found = rows(db)
    path.write_text(to_csv(found), encoding="utf-8-sig")
    return {"path": str(path), "rows": len(found), "copies": sum(r["qty"] for r in found)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?")
    args = ap.parse_args()
    r = write(collection.connect(), args.out)
    print(f"{r['copies']} cards ({r['rows']} rows) written to {r['path']}")


if __name__ == "__main__":
    main()
