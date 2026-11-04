"""Tells printings of one card apart by comparing the capture with each printing's official image.

Used when the name and set were read but the collector number wasn't: a set's showcase, borderless and extended-art
versions share the name and set code, but their frames and often their art differ from the main printing. A plain
greyscale correlation at low resolution picked the right one in all 24 test captures; same-art reprints that differ
only in small frame details come out close, so a narrow win isn't treated as certain.
"""

import urllib.request
from pathlib import Path

import cv2
import numpy as np

IMAGES = Path(__file__).resolve().parent / "data" / "images"
SIZE = (126, 176)
# Beyond this many printings (basic lands without a set code), downloading every image costs more than it settles.
MAX_CANDIDATES = 12
# the closest correct win in testing was 0.03 (two printings with the same art); clear wins were 0.1 and up
SURE_MARGIN = 0.08
# Correct picks scored 0.54 and up. A covered or glared capture scores low against every printing and its best
# score means nothing.
MIN_SCORE = 0.45


def image_url(card):
    """Full-size official image: Scryfall's id-based layout for Magic, TCGdex or TCGplayer for Pokémon."""
    if card["game"] == "mtg":
        i = card["id"]
        return f"https://cards.scryfall.io/normal/front/{i[0]}/{i[1]}/{i}.jpg"
    image = card["image"]
    if not image:
        return None
    if image.startswith("tcgplayer/"):
        return f"https://tcgplayer-cdn.tcgplayer.com/product/{image.removeprefix('tcgplayer/')}_in_1000x1000.jpg"
    return f"https://assets.tcgdex.net/{image}/high.webp"


def official(card):
    """The card's official image, downloaded once into data/images/."""
    path = IMAGES / f"{card['id'].replace('/', '_')}.jpg"
    if not path.exists():
        url = image_url(card)
        if not url:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "cardscan/1.0", "Accept": "image/*"})
        try:
            data = urllib.request.urlopen(req, timeout=20).read()
        except OSError:
            return None
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        IMAGES.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), img)
    return cv2.imread(str(path))


def _prep(img):
    g = cv2.cvtColor(cv2.resize(img, SIZE, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    g -= g.mean()
    return g / (np.linalg.norm(g) + 1e-6)


def rank(capture, cards):
    """[(score, card)] best first; cards without an image are left out."""
    cap = _prep(capture)
    scored = []
    for card in cards:
        img = official(card)
        if img is not None:
            scored.append((float((cap * _prep(img)).sum()), card))
    return sorted(scored, key=lambda s: -s[0])


def pick(capture, cards):
    """(card, sure) for the printing the capture looks like, or (None, False) when there's nothing to compare."""
    scored = rank(capture, cards)
    if not scored or scored[0][0] < MIN_SCORE:
        return None, False
    sure = len(scored) == 1 or scored[0][0] - scored[1][0] >= SURE_MARGIN
    return scored[0][1], sure
