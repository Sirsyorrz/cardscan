"""Refresh TCGplayer market prices for the cards in the collection, and the USD to AUD rate.

    python prices.py

Magic prices come from Scryfall (which carries TCGplayer's), Pokémon from tcgcsv, one request per TCGplayer set.
They override the snapshot that came with cards.db.
"""

import json
import time
import urllib.request

import collection

HEADERS = {"User-Agent": "cardscan/1.0", "Accept": "application/json"}
# TCGplayer product lines: Japanese Pokémon is separate from English
TCG_CATEGORY = {"en": 3, "ja": 85}
MTG_FINISH = {"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}


def _get(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={**HEADERS, **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res)


def _save(db, rows):
    stamp = collection.now()
    with db:
        db.executemany("INSERT OR REPLACE INTO prices (card_id, finish, source, usd, updated_at) VALUES (?, ?, 'tcg', ?, ?)",
                       [(c, f, usd, stamp) for c, f, usd in rows])


def refresh_mtg(db, ids):
    count = 0
    for i in range(0, len(ids), 75):
        body = _get("https://api.scryfall.com/cards/collection", {"identifiers": [{"id": x} for x in ids[i:i + 75]]})
        rows = [(card["id"], finish, float(card["prices"][key]))
                for card in body["data"] for finish, key in MTG_FINISH.items() if card["prices"].get(key)]
        _save(db, rows)
        count += len(rows)
        # Scryfall asks for no more than about 10 requests a second
        time.sleep(0.12)
    return count


def refresh_pokemon(db, groups):
    """Every variant in these TCGplayer groups, owned or not: a set's prices come in one request anyway."""
    count = 0
    for group, lang in groups:
        try:
            body = _get(f"https://tcgcsv.com/tcgplayer/{TCG_CATEGORY.get(lang, 3)}/{group}/prices")
        except OSError as e:
            print(f"  group {group}: {e}")
            continue
        market = {(p["productId"], p["subTypeName"]): p["marketPrice"] for p in body["results"] if p.get("marketPrice")}
        # each variant knows which product and sub-type its price comes from (worked out when cards.db is built)
        variants = db.execute("SELECT card_id, key, tcg_id, tcg_sub FROM c.variants WHERE tcg_group = ? AND tcg_sub IS NOT NULL",
                              (group,)).fetchall()
        rows = [(v["card_id"], v["key"], market[(v["tcg_id"], v["tcg_sub"])]) for v in variants
                if (v["tcg_id"], v["tcg_sub"]) in market]
        _save(db, rows)
        count += len(rows)
    return count


def refresh_rate(db):
    body = _get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=AUD")
    collection.set_meta(db, "aud_per_usd", str(body["rates"]["AUD"]))
    return body["rates"]["AUD"]


def refresh(db, log=print):
    owned = db.execute("SELECT DISTINCT i.card_id, c.game, c.lang FROM items i JOIN c.cards c ON c.id = i.card_id").fetchall()
    mtg = [r["card_id"] for r in owned if r["game"] == "mtg"]
    pkm = [r["card_id"] for r in owned if r["game"] == "pkm"]
    groups = set()
    for i in range(0, len(pkm), 500):
        chunk = pkm[i:i + 500]
        groups |= {(r[0], r[1]) for r in db.execute(
            f"""SELECT DISTINCT v.tcg_group, c.lang FROM c.variants v JOIN c.cards c ON c.id = v.card_id
                WHERE v.card_id IN ({','.join('?' * len(chunk))}) AND v.tcg_group IS NOT NULL""", chunk)}
    log(f"Magic: {refresh_mtg(db, mtg)} prices for {len(mtg)} cards")
    log(f"Pokémon: {refresh_pokemon(db, sorted(groups))} prices from {len(groups)} sets")
    log(f"US$1 = A${refresh_rate(db):.4f}")
    collection.set_meta(db, "prices_at", str(collection.now()))


if __name__ == "__main__":
    refresh(collection.connect())
