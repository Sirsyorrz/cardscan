"""Refresh Card Kingdom and TCGplayer prices for the cards in the collection, and the USD to AUD rate.

    python prices.py

Magic prices come from Scryfall (which carries TCGplayer's) and Card Kingdom's price list, Pokémon and Lorcana from
tcgcsv, one request per TCGplayer set.
They override the snapshot that came with cards.db.
"""

import gzip
import json
import time
import urllib.request

import collection

HEADERS = {"User-Agent": "cardscan/1.0", "Accept": "application/json"}
# TCGplayer product lines by (game, lang): Japanese Pokémon is separate from English
TCG_CATEGORY = {("pkm", "en"): 3, ("pkm", "ja"): 85, ("lor", "en"): 71}
TCG_GAME_LABEL = {"pkm": "Pokémon", "lor": "Lorcana"}
MTG_FINISH = {"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}


def _get(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {**HEADERS, "Accept-Encoding": "gzip", **({"Content-Type": "application/json"} if data else {})}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as res:
        raw = res.read()
        return json.loads(gzip.decompress(raw) if res.headers.get("Content-Encoding") == "gzip" else raw)


def _save(db, rows, source="tcg"):
    stamp = collection.now()
    with db:
        db.executemany("INSERT OR REPLACE INTO prices (card_id, finish, source, usd, updated_at) VALUES (?, ?, ?, ?, ?)",
                       [(c, f, source, usd, stamp) for c, f, usd in rows])


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


def refresh_ck(db, ids):
    """Card Kingdom retail prices, from its whole price list (one ~70 MB request, gzipped on the wire)."""
    wanted = set(ids)
    rows = {}
    for row in _get("https://api.cardkingdom.com/api/v2/pricelist")["data"]:
        sid = row.get("scryfall_id")
        usd = float(row.get("price_retail") or 0)
        if sid not in wanted or not usd:
            continue
        finish = "foil" if row.get("is_foil") == "true" else "nonfoil"
        if finish == "foil" and "etched" in (row.get("variation") or "").lower():
            finish = "etched"
        rows[(sid, finish)] = usd
    _save(db, [(c, f, usd) for (c, f), usd in rows.items()], "ck")
    return len(rows)


def refresh_tcgcsv(db, groups):
    """Every variant in these TCGplayer groups, owned or not: a set's prices come in one request anyway."""
    count = 0
    for group, category in groups:
        try:
            body = _get(f"https://tcgcsv.com/tcgplayer/{category}/{group}/prices")
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
    log(f"Magic: {refresh_mtg(db, mtg)} TCGplayer prices for {len(mtg)} cards")
    log(f"Magic: {refresh_ck(db, mtg)} Card Kingdom prices")
    for game, label in TCG_GAME_LABEL.items():
        ids = [r["card_id"] for r in owned if r["game"] == game]
        groups = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            groups |= {(r[0], TCG_CATEGORY[(game, r[1])]) for r in db.execute(
                f"""SELECT DISTINCT v.tcg_group, c.lang FROM c.variants v JOIN c.cards c ON c.id = v.card_id
                    WHERE v.card_id IN ({','.join('?' * len(chunk))}) AND v.tcg_group IS NOT NULL""", chunk)}
        log(f"{label}: {refresh_tcgcsv(db, sorted(groups))} prices from {len(groups)} sets")
    log(f"US$1 = A${refresh_rate(db):.4f}")
    collection.set_meta(db, "prices_at", str(collection.now()))


if __name__ == "__main__":
    refresh(collection.connect())
