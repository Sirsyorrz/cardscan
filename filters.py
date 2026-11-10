"""Per-game card filters (colours, type, rules text, stats, rarity, set, format, price) for search.

Must match app/src/filters.ts, which applies the same filters to the collection in the app, and its port in
fakehams' site/collections/cards.js.
"""

import json
import re
import unicodedata
from functools import lru_cache

# cards.legal bits per game, in bit order; must match FORMATS in filters.ts
FORMATS = {
    "mtg": ["standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander"],
    "lor": ["core", "infinity"],
    "pkm": ["standard", "expanded"],
}
RANGE_COLUMN = {"cost": "cmc", "power": "power", "toughness": "toughness", "hp": "hp"}
TERM = re.compile(r'(-?)(?:"([^"]*)"|(\S+))')


def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def terms(q):
    """Words and "quoted phrases" that must appear, and -excluded ones."""
    inc, exc = [], []
    for neg, phrase, word in TERM.findall(q or ""):
        t = fold(phrase or word)
        if t:
            (exc if neg else inc).append(t)
    return inc, exc


def has_terms(value, inc, exc):
    v = fold(value)
    return all(t in v for t in inc) and not any(t in v for t in exc)


def colours(game, value):
    """Magic colours are letters ("RUW") and colourless counts as C; the others are comma lists."""
    if game == "mtg":
        return set(value or "") or {"C"}
    return {c for c in (value or "").split(",") if c}


def colour_match(have, want, mode):
    if mode == "all":
        return want <= have
    if mode == "exact":
        return have == want
    if mode == "within":
        return have <= want
    return bool(have & want)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@lru_cache(64)
def parse(text):
    """The filters object from the app, with only the parts that filter anything."""
    f = json.loads(text) if text else {}
    out = {
        "colors": set(f.get("colors") or []),
        "mode": f.get("colorMode") or "any",
        "type": terms(f.get("type")),
        "text": terms(f.get("text")),
        "rarity": set(f.get("rarity") or []),
        "set": fold(f.get("set") or "").strip(),
        "format": f.get("format") or "",
        "ranges": {},
    }
    for key, r in (f.get("ranges") or {}).items():
        lo, hi = num((r or {}).get("min")), num((r or {}).get("max"))
        if lo is not None or hi is not None:
            out["ranges"][key] = (lo, hi)
    return out


def active(f):
    return bool(f["colors"] or any(f["type"]) or any(f["text"]) or f["rarity"] or f["set"] or f["format"]
                or f["ranges"])


def matches(f, card, rate=None):
    """card: a dict with game, colors, identity, type_line, text, cmc, power, toughness, hp, rarity, set_code,
    set_name, legal and usd (the lowest TCGplayer price). Price ranges are in the shown currency: AUD at `rate`."""
    game = card["game"]
    if f["colors"]:
        if f["mode"] == "identity":
            if not set(card.get("identity") or "") <= f["colors"] - {"C"}:
                return False
        elif not colour_match(colours(game, card.get("colors")), f["colors"], f["mode"]):
            return False
    if any(f["type"]) and not has_terms(card.get("type_line"), *f["type"]):
        return False
    if any(f["text"]) and not has_terms(card.get("text"), *f["text"]):
        return False
    if f["rarity"] and card.get("rarity") not in f["rarity"]:
        return False
    if f["set"] and fold(card.get("set_code")) != f["set"] and f["set"] not in fold(card.get("set_name")):
        return False
    if f["format"]:
        formats = FORMATS.get(game, [])
        if f["format"] not in formats or not (card.get("legal") or 0) >> formats.index(f["format"]) & 1:
            return False
    for key, (lo, hi) in f["ranges"].items():
        if key == "price":
            v = card.get("usd")
            v = v * rate if v is not None and rate else v
        else:
            v = num(card.get(RANGE_COLUMN.get(key, key)))
        if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
            return False
    return True
