#!/usr/bin/env python3
"""Build data/cards.db from Scryfall (MTG), TCGdex (English and Japanese Pokemon), Lorcast (Lorcana), tcgcsv (TCGplayer
prices) and Card Kingdom."""

import gzip
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
OUT = ROOT.parent / "data" / "cards.db"
UA = "CardScan-builder/0.1 (+https://github.com/Sirsyorrz/cardscan)"
MAX_AGE = 20 * 3600
TCGDEX_ASSETS = "https://assets.tcgdex.net/"


def image_path(tcgdex_url, product_id):
    """TCGdex asset path, or `tcgplayer/<id>` when TCGdex has no scan (most Japanese sets); the full URL is built from it when shown."""
    if tcgdex_url:
        return tcgdex_url.removeprefix(TCGDEX_ASSETS)
    return f"tcgplayer/{product_id}" if product_id else None


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == 4:
                raise
            print(f"  retry {url}: {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            if attempt == 4:
                raise
            print(f"  retry {url}: {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def cached_json(name, url, max_age=MAX_AGE):
    path = CACHE / name
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return json.loads(path.read_bytes())
    data = fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return json.loads(data)


def _kana_fold():
    table = {}
    # hiragana to katakana: OCR mixes up lookalikes such as へ/ヘ, and a few names are written in hiragana
    for cp in range(0x3041, 0x3097):
        table[cp] = cp + 0x60
    # small kana read as full size (and the other way round) is a common OCR slip
    small, large = "ァィゥェォッャュョヮヵヶ", "アイウエオツヤユヨワカケ"
    table.update({ord(s): l for s, l in zip(small, large)})
    # kanji that look exactly like katakana
    table.update({ord(k): v for k, v in zip("力口工二夕卜八", "カロエニタトハ")})
    return table


KANA_FOLD = _kana_fold()


def norm(s):
    """Must match norm() in matcher/normalize.ts."""
    s = unicodedata.normalize("NFKD", s).lower().translate(KANA_FOLD)
    # dakuten marks, the long vowel mark and its lookalikes are dropped: OCR misses or swaps them too often
    return re.sub(r"[^a-z0-9\u30a1-\u30fa\u3400-\u4dbf\u4e00-\u9fff]|一", "", s)


def number_sort(n):
    m = re.search(r"\d+", n or "")
    return int(m.group()) if m else 0


CARD_COLS = 34

# Legality bits (cards.legal).
MTG_FORMATS = ["standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander"]
MTG_RESTRICTED = 1 << 7
PKM_STANDARD, PKM_EXPANDED = 1, 2

# Scryfall set types that only show up in the binder list when extras are on.
MTG_EXTRA_SET_TYPES = {"token", "promo", "memorabilia", "minigame", "vanguard", "alchemy", "treasure_chest"}
PKM_EXTRA_SET = re.compile(
    r"promo|mcdonald|trainer kit|futsal|trick or trade|jumbo|deck|battle academy|championship|pop series|best of|starter|-P$",
    re.I,
)
# Treatments that make a Magic printing an alternate of a main-set card rather than a binder slot of its own.
MTG_ALT_FRAMES = {"showcase", "extendedart", "inverted"}

PLAY_IDS = {}


def play_id(key):
    """Small integer shared by functionally identical cards, so a deck can count any printing you own."""
    return PLAY_IDS.setdefault(key, len(PLAY_IDS) + 1)


def assign_slots(entries):
    """
    Binder slots: (card_id, set_id, name, number, number_sort, is_base[, may_be_base]) -> {card_id: slot number}.
    Base cards fill their own number. Secret rares and alternate arts fill the base slot of the same name,
    so owning only the full art still completes it; ones with no base counterpart get None.
    """
    by_set = defaultdict(list)
    for e in entries:
        by_set[e[1]].append(e)
    slots = {}
    for items in by_set.values():
        if not any(e[5] for e in items):
            # a set of only special printings (Mystical Archive) is its own base; an optional 7th field marks the
            # printings that may be (English, when a set also has language-only ones)
            primary = {e[0] for e in items if len(e) < 7 or e[6]} or {e[0] for e in items}
            items = [(*e[:5], e[0] in primary) for e in items]
        base_by_name = {}
        for cid, _, name, number, _, base, *_ in sorted(items, key=lambda e: (e[4], e[3])):
            if base:
                slots[cid] = number
                base_by_name.setdefault(name, number)
        for cid, _, name, _, _, base, *_ in items:
            if not base:
                slots[cid] = base_by_name.get(name)
    return slots


def schema(db):
    db.executescript(
        """
        CREATE TABLE cards (
          id TEXT PRIMARY KEY,
          game TEXT NOT NULL,
          name TEXT NOT NULL,
          name_norm TEXT NOT NULL,
          set_code TEXT NOT NULL,
          set_name TEXT NOT NULL,
          number TEXT NOT NULL,
          number_sort INTEGER NOT NULL,
          printed_total TEXT,
          rarity TEXT,
          type_line TEXT,
          mana_cost TEXT,
          cmc REAL,
          colors TEXT,
          power TEXT,
          toughness TEXT,
          -- HP on Pokemon, lore on Lorcana
          hp INTEGER,
          artist TEXT,
          released TEXT,
          image TEXT,
          finishes TEXT,
          text_id TEXT,
          tcg_id INTEGER,
          tcg_group INTEGER,
          ck_url TEXT,
          has_back INTEGER NOT NULL DEFAULT 0,
          lang TEXT NOT NULL DEFAULT 'en',
          -- the name as printed on non-English cards; `name` holds the English name when one is known
          local_name TEXT,
          local_norm TEXT,
          legal INTEGER NOT NULL DEFAULT 0,
          play_id INTEGER NOT NULL,
          -- Magic colour identity, for Commander
          identity TEXT,
          set_id TEXT NOT NULL,
          -- binder slot (a base card's number) this printing fills; NULL for extras outside the base set
          slot TEXT
        );
        CREATE TABLE sets (
          id TEXT PRIMARY KEY,
          game TEXT NOT NULL,
          lang TEXT NOT NULL,
          code TEXT NOT NULL,
          name TEXT NOT NULL,
          released TEXT,
          kind TEXT NOT NULL,
          series TEXT,
          icon TEXT,
          base INTEGER NOT NULL DEFAULT 0,
          total INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE texts (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE ocr_names (norm TEXT NOT NULL, name TEXT NOT NULL, game TEXT NOT NULL);
        CREATE TABLE base_prices (
          card_id TEXT NOT NULL,
          finish TEXT NOT NULL,
          source TEXT NOT NULL,
          usd REAL NOT NULL,
          PRIMARY KEY (card_id, finish, source)
        ) WITHOUT ROWID;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        -- Pokemon print variants (patterned reverses, stamps, 1st edition...), each with its own TCGplayer price
        CREATE TABLE variants (
          card_id TEXT NOT NULL,
          key TEXT NOT NULL,
          label TEXT NOT NULL,
          kind TEXT NOT NULL,
          tcg_id INTEGER,
          tcg_group INTEGER,
          tcg_sub TEXT,
          sort INTEGER NOT NULL,
          PRIMARY KEY (card_id, key)
        ) WITHOUT ROWID;
        """
    )


def build_mtg(db):
    bulk = json.loads(fetch("https://api.scryfall.com/bulk-data/default-cards"))
    path = CACHE / "scryfall-default.jsonl"
    if not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE:
        print("downloading scryfall bulk")
        path.write_bytes(fetch(bulk["jsonl_download_uri"], timeout=600))

    ck = cached_json("ck-pricelist.json", "https://api.cardkingdom.com/api/v2/pricelist")
    ck_by_id = defaultdict(dict)
    for row in ck["data"]:
        sid = row.get("scryfall_id")
        if not sid:
            continue
        finish = "foil" if row.get("is_foil") == "true" else "nonfoil"
        if "etched" in (row.get("variation") or "").lower() and finish == "foil":
            finish = "etched"
        ck_by_id[sid][finish] = (float(row.get("price_retail") or 0), row.get("url"))

    scry_sets = {s["code"]: s for s in cached_json("scryfall-sets.json", "https://api.scryfall.com/sets", 7 * 86400)["data"]}
    cards, texts, prices, names = [], {}, [], set()
    slot_entries, sets = [], {}
    with path.open() as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            c = json.loads(line)
            # Scryfall's default cards are English apart from printings that exist only in another language
            # (Phyrexian ONE, Japanese alternate-art Mystical Archive, foreign black-bordered sets), so those stay
            if c.get("digital") or c.get("layout") == "art_series":
                continue
            lang = c.get("lang") or "en"
            faces = c.get("card_faces") or []
            oracle = c.get("oracle_text")
            if oracle is None and faces:
                oracle = "\n//\n".join(fc.get("oracle_text", "") for fc in faces)
            text_id = c.get("oracle_id") or (faces[0].get("oracle_id") if faces else None) or c["id"]
            if oracle:
                texts[text_id] = oracle
            front = faces[0] if faces else c
            legalities = c.get("legalities") or {}
            legal = sum(1 << i for i, fmt in enumerate(MTG_FORMATS) if legalities.get(fmt) in ("legal", "restricted"))
            if legalities.get("vintage") == "restricted":
                legal |= MTG_RESTRICTED
            number = c["collector_number"]
            base = (
                re.fullmatch(r"\d+", number) is not None
                and not c.get("promo")
                and not c.get("variation")
                and c.get("border_color") != "borderless"
                and "boosterfun" not in (c.get("promo_types") or [])
                and not MTG_ALT_FRAMES & set(c.get("frame_effects") or [])
                # a language-only printing fills the slot of its English card; assign_slots makes a set with no
                # English cards at all base by itself
                and lang == "en"
            )
            slot_entries.append((c["id"], c["set"], c["name"], number, number_sort(number), base, lang == "en"))
            if c["set"] not in sets:
                info = scry_sets.get(c["set"], {})
                set_type = c.get("set_type") or info.get("set_type") or ""
                sets[c["set"]] = (
                    c["set"], "mtg", "en", c["set"].upper(), c["set_name"], info.get("released_at") or c.get("released_at"),
                    "extra" if set_type in MTG_EXTRA_SET_TYPES else "main", set_type.replace("_", " ").capitalize(),
                    info.get("icon_svg_uri"),
                )
            # Phyrexian is printed in its own script, which only reads as noise in a font without it
            local = c.get("printed_name") if lang not in ("en", "ph") and c.get("printed_name") != c["name"] else None
            ck_entry = ck_by_id.get(c["id"], {})
            ck_url = next((u for _, u in ck_entry.values() if u), None)
            cards.append((
                c["id"], "mtg", c["name"], norm(c["name"]), c["set"].upper(), c["set_name"],
                c["collector_number"], number_sort(c["collector_number"]), c.get("printed_size") and str(c["printed_size"]),
                c.get("rarity"), c.get("type_line") or front.get("type_line"),
                c.get("mana_cost") if c.get("mana_cost") is not None else front.get("mana_cost"),
                c.get("cmc"), "".join(c.get("colors") or front.get("colors") or []),
                c.get("power") or front.get("power"), c.get("toughness") or front.get("toughness"), None,
                # image URLs are derived from the Scryfall id when shown
                c.get("artist"), c.get("released_at"), None, ",".join(c.get("finishes") or []),
                text_id if oracle else None, c.get("tcgplayer_id"), None, ck_url,
                # transform/MDFC cards have a real second face image at .../back/...
                int(bool(faces) and "image_uris" not in c), lang, local, local and norm(local),
                legal, play_id(("mtg", c.get("oracle_id") or c["name"])), "".join(c.get("color_identity") or []), c["set"],
            ))
            p = c.get("prices") or {}
            for finish, key in (("nonfoil", "usd"), ("foil", "usd_foil"), ("etched", "usd_etched")):
                if p.get(key):
                    prices.append((c["id"], finish, "tcg", float(p[key])))
            for finish, (usd, _) in ck_entry.items():
                if usd:
                    prices.append((c["id"], finish, "ck", usd))
            names.add((norm(c["name"]), c["name"], "mtg"))
            for fc in faces:
                if fc.get("name") and fc["name"] != c["name"]:
                    names.add((norm(fc["name"]), c["name"], "mtg"))

    slots = assign_slots(slot_entries)
    cards = [(*row, slots.get(row[0])) for row in cards]
    db.executemany(f"INSERT OR REPLACE INTO cards VALUES ({','.join('?' * CARD_COLS)})", cards)
    db.executemany("INSERT OR REPLACE INTO texts VALUES (?,?)", texts.items())
    db.executemany("INSERT OR REPLACE INTO base_prices VALUES (?,?,?,?)", prices)
    db.executemany("INSERT INTO ocr_names VALUES (?,?,?)", names)
    db.executemany("INSERT INTO sets (id, game, lang, code, name, released, kind, series, icon) VALUES (?,?,?,?,?,?,?,?,?)", sets.values())
    print(f"mtg: {len(cards)} printings, {len(texts)} texts, {len(prices)} prices")


VARIANT_ORDER = {"normal": 0, "holo": 1, "reverse": 2}
PATTERN_LABEL = {
    "pokeball": "Poké Ball",
    "masterball": "Master Ball",
    "duskball": "Dusk Ball",
    "loveball": "Love Ball",
    "friendball": "Friend Ball",
    "quickball": "Quick Ball",
    "energy": "Energy symbol",
    "cracked-ice": "Cracked Ice",
}
STAMP_LABEL = {
    "1st-edition": "1st Edition",
    "set-logo": "Set logo stamp",
    "player-rewards-program": "Play! Pokémon stamp",
    "professor-program": "Professor Program",
    "pre-release": "Prerelease",
    "pokemon-center": "Pokémon Center",
    "25th-celebration": "25th Anniversary",
    "staff": "Staff stamp",
    "snowflake": "Snowflake stamp",
    "mcdonalds": "McDonald's",
    "countdown-calendar": "Countdown calendar",
}
SUBTYPE_LABEL = {
    "1999-2000-copyright": "1999-2000 copyright",
    "no-e-reader": "No e-Reader",
    "missing-expansion-symbol": "No set symbol",
}
TYPE_LABEL = {"normal": "Normal", "holo": "Holo", "reverse": "Reverse holo"}


def title(slug):
    return " ".join(w.capitalize() for w in slug.split("-"))


def variant_key(v):
    """Stable finish key: the plain type ("normal"/"holo"/"reverse") plus any pattern, print run, stamp or size."""
    parts = [v["type"]]
    if v.get("foil"):
        parts.append(v["foil"])
    if v.get("subtype"):
        parts.append(v["subtype"])
    parts += sorted(v.get("stamp") or [])
    if v.get("size") and v["size"] != "standard":
        parts.append(v["size"])
    return ":".join(parts)


def variant_label(v):
    """Base finish first, then what makes this print different: "Holo · 1st Edition · Shadowless"."""
    if v.get("foil"):
        suffix = {"reverse": "reverse", "holo": "holo"}.get(v["type"], "foil")
        base = f"{PATTERN_LABEL.get(v['foil'], title(v['foil']))} {suffix}"
    else:
        base = TYPE_LABEL.get(v["type"], title(v["type"]))
    mods = [STAMP_LABEL.get(s, title(s)) for s in sorted(v.get("stamp") or [], key=lambda s: s != "1st-edition")]
    if v.get("subtype"):
        mods.append(SUBTYPE_LABEL.get(v["subtype"], title(v["subtype"])))
    if v.get("size") and v["size"] != "standard":
        mods.append(title(v["size"]))
    return " · ".join([base, *mods])


def pick_subtype(kind, stamps, available):
    """Choose which TCGplayer sub-type price belongs to this variant of the product."""
    first = "1st-edition" in stamps
    wanted = {
        "normal": ["1st Edition Normal", "1st Edition"] if first else ["Normal", "Unlimited", "Unlimited Normal"],
        "holo": ["1st Edition Holofoil"] if first else ["Holofoil", "Unlimited Holofoil"],
        "reverse": ["Reverse Holofoil", "Holofoil"],
    }.get(kind, [])
    for name in wanted:
        if name in available:
            return name
    return next(iter(available)) if len(available) == 1 else None


TCG_POKEMON = 3
TCG_POKEMON_JAPAN = 85
TCG_LORCANA = 71


def tcgcsv(category=TCG_POKEMON):
    groups_file = "tcgcsv/groups.json" if category == TCG_POKEMON else f"tcgcsv/groups-{category}.json"
    groups = cached_json(groups_file, f"https://tcgcsv.com/tcgplayer/{category}/groups")["results"]

    # group ids are unique across categories, so the per-group cache files can share a folder
    def load(g):
        gid = g["groupId"]
        products = cached_json(f"tcgcsv/{gid}-products.json", f"https://tcgcsv.com/tcgplayer/{category}/{gid}/products")["results"]
        prices = cached_json(f"tcgcsv/{gid}-prices.json", f"https://tcgcsv.com/tcgplayer/{category}/{gid}/prices")["results"]
        return gid, products, prices

    with ThreadPoolExecutor(8) as ex:
        loaded = list(ex.map(load, groups))

    product_group, product_url, group_numbers, prices = {}, {}, defaultdict(dict), defaultdict(dict)
    for gid, products, plist in loaded:
        for p in products:
            product_group[p["productId"]] = gid
            product_url[p["productId"]] = p.get("url")
            num = next((e["value"] for e in p.get("extendedData", []) if e["name"] == "Number"), None)
            if num:
                key = num.split("/")[0].lstrip("0").upper() or "0"
                group_numbers[gid].setdefault(key, []).append(p)
        for pr in plist:
            # keep sub-types even without a market price so pick_subtype sees what the product offers
            prices[pr["productId"]][pr["subTypeName"]] = pr.get("marketPrice")
    return groups, product_group, group_numbers, prices


def build_pokemon(db):
    # TCG Pocket is a digital-only game that TCGdex lists alongside the physical sets.
    set_info, details = tcgdex_cards("en", exclude_series={"tcgp"})
    set_details = list(set_info.values())

    groups, product_group, group_numbers, prices = tcgcsv()

    # Derive tcgdex set -> TCGplayer group from cards that carry a TCGplayer product id.
    set_group_votes = defaultdict(Counter)
    for d in details:
        for v in d.get("variants_detailed") or []:
            pid = (v.get("thirdParty") or {}).get("tcgplayer")
            if pid in product_group:
                set_group_votes[d["set"]["id"]][product_group[pid]] += 1
    set_group = {sid: votes.most_common(1)[0][0] for sid, votes in set_group_votes.items()}

    group_by_name = {norm(re.sub(r"^[A-Z0-9 ]+:\s*", "", g["name"])): g["groupId"] for g in groups}
    for s in set_details:
        if s["id"] not in set_group:
            gid = group_by_name.get(norm(s["name"]))
            if gid:
                set_group[s["id"]] = gid

    cards, texts, plist, names, variant_rows = [], {}, [], set(), []
    extras, sets = [], {}
    matched = 0
    for d in details:
        s = set_info[d["set"]["id"]]
        pid = None
        for v in d.get("variants_detailed") or []:
            p = (v.get("thirdParty") or {}).get("tcgplayer")
            if p and not v.get("stamp"):
                pid = p
                break
        gid = product_group.get(pid) if pid else set_group.get(s["id"])
        if not pid and gid:
            key = d["localId"].lstrip("0").upper() or "0"
            at_number = group_numbers[gid].get(key, [])
            candidates = [p for p in at_number if norm(d["name"]) in norm(p["name"])]
            # names can differ ("Growing Grass Energy" is "Growing G Energy" on TCGplayer); a lone product at
            # that number is still the card
            if not candidates and len(at_number) == 1:
                candidates = at_number
            if candidates:
                pid = candidates[0]["productId"]
        if pid:
            matched += 1

        card_variants = {}
        detailed = d.get("variants_detailed") or []
        # "generated" variants are TCGdex guessing, often "normal" only (new sets until it fills them in, much of
        # XY and Sun & Moon); TCGplayer's price sub-types are what was actually printed
        if detailed and all(v.get("variantId") == "generated" for v in detailed):
            subs = [sub for sub in prices.get(pid, {}) if sub in SUBTYPE_FINISH] if pid else []
            if subs:
                detailed = []
                card_variants = {SUBTYPE_FINISH[sub]: (TYPE_LABEL[SUBTYPE_FINISH[sub]], SUBTYPE_FINISH[sub], pid,
                                                       product_group.get(pid), sub, True) for sub in subs}
        for v in detailed:
            if v.get("type") not in VARIANT_ORDER:
                continue
            key = variant_key(v)
            if key in card_variants:
                continue
            vid = (v.get("thirdParty") or {}).get("tcgplayer")
            plain = key == v["type"]
            # plain finishes usually share the card's main TCGplayer product as a sub-type
            vid = vid or (pid if plain else None)
            sub = pick_subtype(v["type"], v.get("stamp") or [], prices.get(vid, {})) if vid else None
            card_variants[key] = (variant_label(v), v["type"], vid, product_group.get(vid), sub, plain)
        if not card_variants:
            legacy = d.get("variants") or {}
            for t in ("normal", "holo", "reverse"):
                if legacy.get(t):
                    sub = pick_subtype(t, [], prices.get(pid, {})) if pid else None
                    card_variants[t] = (TYPE_LABEL[t], t, pid, product_group.get(pid), sub, True)
        finishes = add_variants(d["id"], card_variants, prices, variant_rows, plist)
        type_line, body = pokemon_text(d)
        if body:
            texts[d["id"]] = body

        img = d.get("image")
        abbr = (s.get("abbreviation") or {}).get("official") or s["id"].upper()
        cards.append((
            d["id"], "pkm", d["name"], norm(d["name"]), abbr, s["name"],
            d["localId"], number_sort(d["localId"]), str((s.get("cardCount") or {}).get("official") or ""),
            d.get("rarity"), type_line, None, None, ",".join(d.get("types") or []),
            None, None, d.get("hp"), d.get("illustrator"), s.get("releaseDate"),
            image_path(img, pid), ",".join(finishes),
            d["id"] if body else None, pid, product_group.get(pid, gid), None, 0, "en", None, None,
        ))
        extras.append(pokemon_extras(d, s, s["id"], "en", d["name"], body))
        sets.setdefault(s["id"], pokemon_set(s, s["id"], "en", abbr, s["name"]))
        names.add((norm(d["name"]), d["name"], "pkm"))

    added = tcgplayer_only_sets(groups, group_numbers, prices, set(set_group.values()), set_details,
                                cards, texts, plist, names, variant_rows, extras, sets)
    if added:
        print(f"pokemon: {added} cards from TCGplayer only (sets TCGdex doesn't have yet)")
    cards = finish_pokemon(cards, extras)
    save_pokemon(db, "pokemon", cards, texts, plist, names, variant_rows, matched, sets)


SUBTYPE_FINISH = {"Normal": "normal", "Holofoil": "holo", "Reverse Holofoil": "reverse"}
# numbered expansions ("ME06: Delta Reign"), not promos, decks or sealed-only groups
EXPANSION_GROUP = re.compile(r"^[A-Z]{1,4}\d{1,3}[a-z]?:\s")


def tcgplayer_only_sets(groups, group_numbers, prices, covered, set_details, cards, texts, plist, names, variant_rows,
                        extras, sets):
    """Cards of expansions TCGplayer lists but TCGdex doesn't have yet (new releases), from the product data alone.
    They get name, number, rarity, HP, attacks, finishes, prices and the TCGplayer image; no artist or legality
    beyond "a current set is legal". Their ids are `tcg-<productId>`; once TCGdex adds the set, these rows go away
    and collection.py moves owned copies over by product id."""
    newest = max((s.get("releaseDate") or "") for s in set_details)
    count = 0
    for g in groups:
        gid = g["groupId"]
        released = (g.get("publishedOn") or "")[:10]
        if gid in covered or g.get("isSupplemental") or released <= newest or not EXPANSION_GROUP.match(g["name"]):
            continue
        products = [p for ps in group_numbers.get(gid, {}).values() for p in ps]
        if not products:
            continue
        code = g.get("abbreviation") or g["name"].split(":")[0]
        set_name = re.sub(r"^[A-Z0-9 ]+:\s*", "", g["name"])
        set_id = f"tcg-{gid}"
        numbers = [next(e["value"] for e in p["extendedData"] if e["name"] == "Number") for p in products]
        official = max((int(n.split("/")[1]) for n in numbers if "/" in n and n.split("/")[1].isdigit()), default=0)
        s = {"id": set_id, "name": set_name, "releaseDate": released, "cardCount": {"official": official}}
        for p, number in zip(products, numbers):
            ext = {e["name"]: e["value"] for e in p["extendedData"]}
            pid = p["productId"]
            local = number.split("/")[0]
            name = re.sub(r"\s*\((Left|Right)\)\s*$", "", product_card_name(p["name"]))
            card_type = ext.get("Card Type", "")
            category = "Trainer" if card_type.startswith("Trainer") else "Energy" if "Energy" in card_type else "Pokemon"
            body = "\n".join(re.sub(r"<br>\s*", ": ", re.sub(r"<(?!br)[^>]+>", "", ext[k])) for k in ("Attack 1", "Attack 2", "Attack 3", "CardText") if ext.get(k))
            card_id = f"tcg-{pid}"
            card_variants = {
                SUBTYPE_FINISH[sub]: (TYPE_LABEL[SUBTYPE_FINISH[sub]], SUBTYPE_FINISH[sub], pid, gid, sub, True)
                for sub in prices.get(pid, {}) if sub in SUBTYPE_FINISH
            } or {"normal": (TYPE_LABEL["normal"], "normal", pid, gid, None, True)}
            finishes = add_variants(card_id, card_variants, prices, variant_rows, plist)
            if body:
                texts[card_id] = body
            type_line = " · ".join(t for t in (category, ext.get("Stage"), card_type if category != "Pokemon" else None) if t)
            types = card_type if category == "Pokemon" else ""
            cards.append((
                card_id, "pkm", name, norm(name), code, set_name,
                local, number_sort(local), str(official or ""),
                ext.get("Rarity"), type_line, None, None, types,
                None, None, int(ext["HP"]) if ext.get("HP", "").isdigit() else None, None, released,
                image_path(None, pid), ",".join(finishes),
                card_id if body else None, pid, gid, None, 0, "en", None, None,
            ))
            d = {"category": category, "energyType": "Special" if "Special" in card_type else "Normal", "localId": local,
                 "legal": {"standard": True, "expanded": True}}
            extras.append(pokemon_extras(d, s, set_id, "en", name, body))
            sets.setdefault(set_id, pokemon_set(s, set_id, "en", code, set_name))
            names.add((norm(name), name, "pkm"))
            count += 1
    return count


# TCGplayer Japan lists mirror and patterned holos as their own products: "Bulbasaur - 001/165 (Poke Ball Pattern)".
PATTERN_SLUG = {"energy symbol": "energy", "team rocket": "team-rocket"}


def product_finish(name):
    """The finish key a TCGplayer Japan product stands for, or None for the card's main product."""
    m = re.search(r"\(([^)]*)\)\s*$", name)
    tag = m.group(1).strip().lower() if m else ""
    if re.search(r"mirror|reverse", tag):
        return "reverse"
    if tag.endswith(" pattern"):
        base = tag.removesuffix(" pattern")
        return "reverse:" + PATTERN_SLUG.get(base, base.replace(" ", ""))
    return None


def product_card_name(name):
    """"Charizard ex - 006/165 (Poke Ball Pattern)" -> "Charizard ex"."""
    m = re.match(r"^(.*?)\s+-\s+\S*\d", name)
    name = (m.group(1) if m else re.sub(r"\s*\([^)]*\)\s*$", "", name)).strip()
    # TCGplayer spells the gender symbols out ("Nidoran F"); the cards and every other source print them
    return re.sub(r"\bNidoran ([FM])\b", lambda m: "Nidoran " + ("♀" if m[1] == "F" else "♂"), name)


# TCGdex sets whose TCGplayer Japan group has no (or a different) abbreviation, by group name.
# The unnumbered vintage sets (PMCG, neo) can't be matched on number, so they are left out.
JA_GROUP_ALIASES = {
    "SM1p": "sm1+: Enhanced Expansion Pack Sun & Moon",
    "SM2p": "SM2+: Facing a New Trial",
    "SM3p": "SM3+: Shining Legends",
    "SM4p": "SM4+: GX Battle Boost",
    "SM5p": "SM5+: Ultra Force",
    "VS1": "Pokemon VS",
    "web1": "Pokemon Web",
    "E1": "Base Expansion Pack",
    "E2": "The Town on No Map",
    "E3": "Wind from the Sea",
    "E4": "Split Earth",
    "E5": "Mysterious Mountains",
    "PCG1": "Flight of Legends",
    "PCG2": "Clash of the Blue Sky",
    "PCG3": "Rocket Gang Strikes Back",
    "PCG4": "Golden Sky, Silvery Ocean",
    "PCG5": "Mirage Forest",
    "PCG6": "Holon Research Tower",
    "PCG7": "Holon Phantom",
    "PCG8": "Miracle Crystal",
    "PCG9": "Offense and Defense of the Furthest Ends",
}


def build_pokemon_ja(db):
    set_info, details = tcgdex_cards("ja")
    groups, product_group, group_numbers, prices = tcgcsv(TCG_POKEMON_JAPAN)
    group_size = {gid: sum(len(ps) for ps in nums.values()) for gid, nums in group_numbers.items()}
    by_name = {g["name"]: g for g in groups}
    by_code = defaultdict(list)
    for g in groups:
        if g.get("abbreviation"):
            by_code[g["abbreviation"].lower()].append(g)

    def group_for(s):
        if s["id"] in JA_GROUP_ALIASES:
            return by_name.get(JA_GROUP_ALIASES[s["id"]])
        # TCGplayer Japan abbreviations are the set codes printed on the cards, same as TCGdex's ids. Deck kits
        # often reuse their set's code, so prefer the same spelling, then the group closest in size.
        total = (s.get("cardCount") or {}).get("total") or 0
        candidates = by_code.get(s["id"].lower(), [])
        return min(candidates, key=lambda g: (g["abbreviation"] != s["id"], abs(group_size.get(g["groupId"], 0) - total)), default=None)

    set_groups = {sid: group_for(s) for sid, s in set_info.items()}
    cards, texts, plist, names, variant_rows = [], {}, [], set(), []
    extras, sets = [], {}
    matched = english = 0
    for d in details:
        s = set_info[d["set"]["id"]]
        card_id = f"ja-{d['id']}"
        official = str((s.get("cardCount") or {}).get("official") or "")
        # promos print the set instead of a total: "001/SV-P"
        printed_total = s["id"] if s["id"].endswith("-P") else official
        group = set_groups[s["id"]]
        gid = group["groupId"] if group else None

        by_finish, base = {}, None
        if gid:
            key = d["localId"].lstrip("0").upper() or "0"
            products = group_numbers[gid].get(key, [])
            for p in products:
                by_finish.setdefault(product_finish(p["name"]), []).append(p)

            def rank(p):
                num = next((e["value"] for e in p.get("extendedData", []) if e["name"] == "Number"), "")
                total = num.split("/")[1].lstrip("0") if "/" in num else ""
                return (total != printed_total.lstrip("0"), "(" in p["name"], p["productId"])

            mains = sorted(by_finish.get(None, []), key=rank)
            base = mains[0] if mains else None
        pid = base["productId"] if base else None
        if pid:
            matched += 1

        card_variants = {}
        for v in d.get("variants_detailed") or []:
            if v.get("type") not in VARIANT_ORDER:
                continue
            # the Japanese data spells stamps loosely: "1st Edition", "1st edition", "1st-edition"
            v = {**v, "stamp": [st.lower().replace(" ", "-") for st in v.get("stamp") or []]}
            key = variant_key(v)
            if key in card_variants:
                continue
            plain = key == v["type"]
            own = by_finish.get(key)
            if own:
                vid = own[0]["productId"]
            elif plain and (v["type"] != "reverse" or "Reverse Holofoil" in prices.get(pid, {})):
                vid = pid
            else:
                vid = None
            sub = pick_subtype(v["type"], v.get("stamp") or [], prices.get(vid, {})) if vid else None
            card_variants[key] = (variant_label(v), v["type"], vid, product_group.get(vid), sub, plain)
        if not card_variants:
            legacy = d.get("variants") or {}
            for t in ("normal", "holo", "reverse"):
                if legacy.get(t):
                    vid = (by_finish.get(t) or [{}])[0].get("productId") or (pid if t != "reverse" else None)
                    sub = pick_subtype(t, [], prices.get(vid, {})) if vid else None
                    card_variants[t] = (TYPE_LABEL[t], t, vid, product_group.get(vid), sub, True)
        finishes = add_variants(card_id, card_variants, prices, variant_rows, plist)
        type_line, body = pokemon_text(d)
        if body:
            texts[card_id] = body

        name = product_card_name(base["name"]) if base else d["name"]
        if base:
            english += 1
        set_name = re.sub(r"^[A-Za-z0-9 .+-]+:\s*", "", group["name"]) if group else s["name"]
        img = d.get("image")
        cards.append((
            card_id, "pkm", name, norm(name), s["id"], set_name,
            d["localId"], number_sort(d["localId"]), printed_total,
            d.get("rarity"), type_line, None, None, ",".join(d.get("types") or []),
            None, None, d.get("hp"), d.get("illustrator"), s.get("releaseDate"),
            image_path(img, pid), ",".join(finishes),
            card_id if body else None, pid, product_group.get(pid, gid), None, 0, "ja", d["name"], norm(d["name"]),
        ))
        set_id = f"ja-{s['id']}"
        extras.append(pokemon_extras(d, s, set_id, "ja", name, body))
        sets.setdefault(set_id, pokemon_set(s, set_id, "ja", s["id"], set_name))
        # the scanner reads the Japanese name and looks the card up by its stored name
        names.add((norm(d["name"]), name, "pkj"))

    cards = finish_pokemon(cards, extras)
    save_pokemon(db, "pokemon ja", cards, texts, plist, names, variant_rows, matched, sets)
    print(f"pokemon ja: {english} English names, {len({c[4] for c in cards})} sets, {sum(1 for g in set_groups.values() if g)} on TCGplayer")


def add_variants(card_id, card_variants, prices, variant_rows, plist):
    """Order a card's variants, queue their rows and snapshot prices; returns the finish keys in order."""
    # plain finishes first, then the common unlimited print run, then special prints
    rank = lambda key, plain: 0 if plain else 1 if key.endswith(":unlimited") else 2
    ordered = sorted(card_variants.items(), key=lambda kv: (VARIANT_ORDER[kv[1][1]], rank(kv[0], kv[1][5]), kv[1][0]))
    for i, (key, (label, kind, vid, vgroup, sub, _)) in enumerate(ordered):
        variant_rows.append((card_id, key, label, kind, vid, vgroup, sub, i))
        usd = prices.get(vid, {}).get(sub) if vid and sub else None
        if usd:
            plist.append((card_id, key, "tcg", usd))
    return [k for k, _ in ordered]


def pokemon_text(d):
    type_parts = [d.get("category"), d.get("stage"), d.get("trainerType"), d.get("energyType"), d.get("suffix")]
    type_parts += d.get("types") or []
    type_line = " · ".join(t for t in type_parts if t)

    body = []
    for a in d.get("abilities") or []:
        body.append(f"[{a.get('type', 'Ability')}] {a.get('name', '')}: {a.get('effect', '')}")
    for a in d.get("attacks") or []:
        cost = "".join(f"{{{c}}}" for c in a.get("cost") or [])
        dmg = f" {a['damage']}" if a.get("damage") else ""
        body.append(f"{cost} {a.get('name', '')}{dmg}" + (f": {a['effect']}" if a.get("effect") else ""))
    if d.get("effect"):
        body.append(d["effect"])
    return type_line, "\n".join(body)


def pokemon_extras(d, s, set_id, lang, name, body):
    """(play key, legality bits, set id, is base card) for one Pokemon card; finish_pokemon turns them into columns."""
    # Pokemon count as the same card when name and attacks match; trainers and energy by name alone
    key = ("pkm", lang, name, body) if d.get("category") == "Pokemon" else ("pkm", lang, name)
    legal = d.get("legal") or {}
    bits = (PKM_STANDARD if legal.get("standard") else 0) | (PKM_EXPANDED if legal.get("expanded") else 0)
    if d.get("category") == "Energy" and d.get("energyType") == "Normal":
        bits = PKM_STANDARD | PKM_EXPANDED
    official = (s.get("cardCount") or {}).get("official") or 0
    local = d["localId"]
    base = local.isdigit() and (not official or int(local) <= official)
    return key, bits, set_id, base


def pokemon_set(s, set_id, lang, code, name):
    symbol = s.get("symbol")
    kind = "extra" if PKM_EXTRA_SET.search(name) or PKM_EXTRA_SET.search(s["id"]) else "main"
    return (set_id, "pkm", lang, code, name, s.get("releaseDate"), kind, (s.get("serie") or {}).get("name"), symbol and f"{symbol}.png")


def finish_pokemon(cards, extras):
    """Append legality, play id, identity, set id and binder slot to each card row."""
    # an older print is legal wherever a functionally identical reprint is
    group_legal = defaultdict(int)
    for key, bits, _, _ in extras:
        group_legal[key] |= bits
    # Japanese rows keep the printed name in local_name, which matches across a set more reliably
    slots = assign_slots([(row[0], e[2], row[27] or row[2], row[6], row[7], e[3]) for row, e in zip(cards, extras)])
    return [
        (*row, group_legal[key], play_id(key), None, set_id, slots.get(row[0]))
        for row, (key, _, set_id, _) in zip(cards, extras)
    ]


def save_pokemon(db, label, cards, texts, plist, names, variant_rows, matched, sets):
    db.executemany(f"INSERT OR REPLACE INTO cards VALUES ({','.join('?' * CARD_COLS)})", cards)
    db.executemany("INSERT OR REPLACE INTO sets (id, game, lang, code, name, released, kind, series, icon) VALUES (?,?,?,?,?,?,?,?,?)", sets.values())
    db.executemany("INSERT OR REPLACE INTO texts VALUES (?,?)", texts.items())
    db.executemany("INSERT OR REPLACE INTO base_prices VALUES (?,?,?,?)", plist)
    db.executemany("INSERT INTO ocr_names VALUES (?,?,?)", names)
    db.executemany("INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?,?,?)", variant_rows)
    print(f"{label}: {len(variant_rows)} variants")
    print(f"{label}: {len(cards)} cards, {matched} linked to TCGplayer, {len(plist)} prices")


LORCAST = "https://api.lorcast.com/v0"
LOR_CORE, LOR_INFINITY = 1, 2
LOR_BASE_RARITIES = {"Common", "Uncommon", "Rare", "Super_rare", "Legendary"}
# TCGplayer sub-types: Cold Foil is the regular foil, Holofoil the Enchanted and other foil-only prints
LOR_SUBTYPES = {"normal": ["Normal"], "foil": ["Cold Foil", "Holofoil"]}
LOR_LABEL = {"normal": "Normal", "foil": "Foil"}
LOR_FOIL_ONLY = {"Enchanted", "Epic", "Iconic"}


def lorcana_total(cards):
    """The printed "/204": the run of base-rarity numbers from 1. Enchanted, Epic and Iconic cards are numbered past it."""
    base = {number_sort(c["collector_number"]) for c in cards if c["rarity"] in LOR_BASE_RARITIES and lorcana_numbered(c)}
    n = 0
    while n + 1 in base:
        n += 1
    return n


def lorcana_numbered(c):
    """A plain collector number. Into the Inklands prints one card five ways as 4a to 4e; the first takes the slot."""
    return re.fullmatch(r"\d+a?", c["collector_number"]) is not None


def build_lorcana(db):
    lorcast_sets = cached_json("lorcast/sets.json", f"{LORCAST}/sets", 7 * 86400)["results"]

    loaded = []
    for s in lorcast_sets:
        loaded.append((s, cached_json(f"lorcast/{s['id']}.json", f"{LORCAST}/sets/{s['id']}/cards", 3 * 86400)))
        # Lorcast asks for no more than 10 requests a second
        time.sleep(0.1)
    groups, product_group, group_numbers, prices = tcgcsv(TCG_LORCANA)
    product = {p["productId"]: p for nums in group_numbers.values() for ps in nums.values() for p in ps}

    set_group_votes = defaultdict(Counter)
    for s, cards in loaded:
        for c in cards:
            if c.get("tcgplayer_id") in product_group:
                set_group_votes[s["id"]][product_group[c["tcgplayer_id"]]] += 1
    set_group = {sid: votes.most_common(1)[0][0] for sid, votes in set_group_votes.items()}

    rows, texts, plist, names, variant_rows, slot_entries, sets = [], {}, [], set(), [], [], {}
    matched = 0
    for s, cards in loaded:
        # kept as Lorcast spells it ("cp", "D23"): its card pages use it
        code = s["code"]
        set_id = f"lor-{code}"
        total = lorcana_total(cards)
        main = code.isdigit()
        sets[set_id] = (set_id, "lor", "en", code, s["name"], s.get("released_at"), "main" if main else "extra",
                        None if main else "Promo", None)
        for c in cards:
            name = f"{c['name']} - {c['version']}" if c.get("version") else c["name"]
            number = c["collector_number"]
            pid = c.get("tcgplayer_id")
            gid = set_group.get(s["id"])
            if not pid and gid:
                key = number.lstrip("0").upper() or "0"
                hits = [p for p in group_numbers[gid].get(key, []) if norm(c["name"]) in norm(p["name"])]
                pid = hits[0]["productId"] if hits else None
            if pid in product_group:
                matched += 1
            offered = prices.get(pid, {})
            printed = next((e["value"] for e in (product.get(pid) or {}).get("extendedData", []) if e["name"] == "Number"), "")
            printed_total = printed.split("/")[1] if "/" in printed else str(total or "")

            lorcast_usd = {"normal": (c.get("prices") or {}).get("usd"), "foil": (c.get("prices") or {}).get("usd_foil")}
            sub_of = {k: next((x for x in subs if x in offered), None) for k, subs in LOR_SUBTYPES.items()}
            finishes = [k for k in LOR_SUBTYPES if sub_of[k] or lorcast_usd[k]]
            if not finishes:
                finishes = ["foil"] if c["rarity"] in LOR_FOIL_ONLY else ["normal", "foil"]
            for i, key in enumerate(finishes):
                sub = sub_of[key]
                variant_rows.append((c["id"], key, LOR_LABEL[key], key, pid if sub else None,
                                     product_group.get(pid) if sub else None, sub, i))
                usd = (offered.get(sub) if sub else None) or lorcast_usd[key]
                if usd:
                    plist.append((c["id"], key, "tcg", float(usd)))
            inks = c.get("inks") or ([c["ink"]] if c.get("ink") else [])

            if c.get("text"):
                texts[c["id"]] = c["text"]
            legalities = c.get("legalities") or {}
            # Infinity allows everything not banned; Lorcast leaves it out on its newest cards
            legal = (LOR_CORE if legalities.get("core") == "legal" else 0) | (LOR_INFINITY if legalities.get("infinity", "legal") == "legal" else 0)
            image = ((c.get("image_uris") or {}).get("digital") or {}).get("normal")
            base = main and c["rarity"] in LOR_BASE_RARITIES and lorcana_numbered(c) and number_sort(number) <= total
            slot_entries.append((c["id"], set_id, name, number, number_sort(number), base))
            rows.append((
                c["id"], "lor", name, norm(name), code, s["name"],
                number, number_sort(number), printed_total,
                c["rarity"].replace("_", " "), " · ".join([*(c.get("type") or []), *(c.get("classifications") or [])]),
                None if c.get("cost") is None else str(c["cost"]), c.get("cost"), ",".join(inks),
                None if c.get("strength") is None else str(c["strength"]),
                None if c.get("willpower") is None else str(c["willpower"]), c.get("lore"),
                " / ".join(c.get("illustrators") or []) or None, c.get("released_at") or s.get("released_at"),
                # the file name after .../card/digital/<size>/; review.py puts the size back in
                image and image.rsplit("/", 1)[1], ",".join(finishes),
                c["id"] if c.get("text") else None, pid, product_group.get(pid, gid), None, 0, "en", None, None,
                legal, play_id(("lor", name)), None, set_id,
            ))
            names.add((norm(name), name, "lor"))
            # the name line alone ("OWL") when the version under it doesn't read; the number then picks the version
            names.add((norm(c["name"]), name, "lor"))

    slots = assign_slots(slot_entries)
    rows = [(*row, slots.get(row[0])) for row in rows]
    db.executemany(f"INSERT OR REPLACE INTO cards VALUES ({','.join('?' * CARD_COLS)})", rows)
    db.executemany("INSERT OR REPLACE INTO sets (id, game, lang, code, name, released, kind, series, icon) VALUES (?,?,?,?,?,?,?,?,?)", sets.values())
    db.executemany("INSERT OR REPLACE INTO texts VALUES (?,?)", texts.items())
    db.executemany("INSERT OR REPLACE INTO base_prices VALUES (?,?,?,?)", plist)
    db.executemany("INSERT INTO ocr_names VALUES (?,?,?)", names)
    db.executemany("INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?,?,?)", variant_rows)
    print(f"lorcana: {len(rows)} cards, {matched} linked to TCGplayer, {len(plist)} prices, {len(sets)} sets")


def tcgdex_cards(lang, exclude_series=()):
    """Every card of every set TCGdex has in `lang`, with full details. Returns (sets by id, card details)."""
    folder = "tcgdex" if lang == "en" else f"tcgdex-{lang}"
    sets = cached_json(f"{folder}/sets.json", f"https://api.tcgdex.net/v2/{lang}/sets")

    def load(kind, item_id, max_age):
        # ids such as "SM1+" need escaping
        safe = urllib.parse.quote(item_id, safe="")
        endpoint = {"set-": "sets", "cards/": "cards"}[kind]
        try:
            return cached_json(f"{folder}/{kind}{safe}.json", f"https://api.tcgdex.net/v2/{lang}/{endpoint}/{safe}", max_age=max_age)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            print(f"  skip {lang} {item_id}: not found", file=sys.stderr)
            return None

    with ThreadPoolExecutor(8) as ex:
        set_details = [s for s in ex.map(lambda s: load("set-", s["id"], 7 * 86400), sets) if s]
    set_details = [s for s in set_details if (s.get("serie") or {}).get("id") not in exclude_series]

    ids = [c["id"] for s in set_details for c in s.get("cards", [])]
    print(f"pokemon {lang}: fetching {len(ids)} cards")
    # card details rarely change; prices come from tcgcsv, so a long cache is fine
    with ThreadPoolExecutor(16) as ex:
        details = [d for d in ex.map(lambda cid: load("cards/", cid, 30 * 86400), ids) if d]
    # a set TCGdex lists before publishing its cards counts as missing, so tcgplayer_only_sets still fills it in
    with_cards = {d["set"]["id"] for d in details}
    set_info = {s["id"]: s for s in set_details if s["id"] in with_cards}
    return set_info, [d for d in details if d["set"]["id"] in set_info]


def main():
    CACHE.mkdir(exist_ok=True)
    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    schema(db)
    build_mtg(db)
    build_pokemon(db)
    build_pokemon_ja(db)
    build_lorcana(db)
    db.executescript(
        """
        CREATE INDEX cards_name ON cards(name_norm);
        CREATE INDEX cards_set_num ON cards(set_code, number);
        CREATE INDEX cards_game_released ON cards(game, released);
        CREATE INDEX cards_local_norm ON cards(local_norm);
        CREATE INDEX ocr_names_norm ON ocr_names(norm);
        CREATE INDEX variants_group ON variants(tcg_group);
        CREATE INDEX cards_set_id ON cards(set_id, number_sort);
        CREATE INDEX cards_play ON cards(play_id);
        UPDATE sets SET
          base = (SELECT COUNT(*) FROM cards c WHERE c.set_id = sets.id AND c.slot = c.number),
          total = (SELECT COUNT(*) FROM cards c WHERE c.set_id = sets.id);
        DELETE FROM sets WHERE total = 0;
        CREATE INDEX sets_game ON sets(game, released);
        """
    )
    db.execute("INSERT INTO meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
