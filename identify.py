"""Reads a straightened card (either way up) and names it with matcher/match.ts.

Only the top strip (name) and bottom strip (collector number, set code) are read: the rest of the card adds little
for matching and costs time. The top strip is read both ways up, and whichever way gives a known card name is up.
Lorcana names sit mid-card, so that strip is read too when the top and bottom don't settle the card, and Lorcana
locations (printed sideways) get their collector line read turned a quarter either way.
"""

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime

onnxruntime.preload_dlls()
# "no registered plugin EP device" on every session, though CUDA is in use
onnxruntime.set_default_logger_severity(3)
from rapidocr import RapidOCR  # noqa: E402  (needs the CUDA libraries preloaded)

import art  # noqa: E402

HERE = Path(__file__).resolve().parent
# (name strip end, collector strip start, collector strip zoom). The tight crop reads most cards; when it isn't sure,
# the looser one catches cards that sit a little off in the warp (name clipped at the top, collector line pushed up),
# at a cost to clean cards, so it's only a second try. The collector line is the smallest print on the card.
PASSES = ((0.11, 0.9, 2), (0.16, 0.88, 3))
# Lorcana prints the name across the middle of the card, under the art, and nothing readable at the top.
MID = (0.45, 0.75)
RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass
class Result:
    id: str | None
    name: str | None = None
    set_code: str | None = None
    set_name: str | None = None
    number: str | None = None
    confidence: str | None = None
    game: str | None = None
    upside_down: bool = False
    candidates: list | None = None
    by_number: bool = False
    # only a collector number read: the card is a guess among every set printing that number
    name_unread: bool = False
    # the printing was chosen by comparing artwork; `art_sure` when it won clearly
    by_art: bool = False
    art_sure: bool = False


def _lines(ocr, img, y0, y1, scale=1, join=False):
    """OCR lines of rows y0..y1 (fractions of the card) in card-relative coordinates."""
    h, w = img.shape[:2]
    part = img[round(h * y0):round(h * y1)]
    if scale != 1:
        part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    r = ocr(part)
    if r.txts is None:
        return []
    out = []
    for box, text in zip(r.boxes, r.txts):
        xs, ys = box[:, 0] / scale, box[:, 1] / scale
        out.append({
            "text": text,
            "x": float(xs.min() / w),
            "y": float((h * y0 + ys.min()) / h),
            "w": float((xs.max() - xs.min()) / w),
            "h": float((ys.max() - ys.min()) / h),
        })
    return _join_rows(out) if join else out


def _join_rows(lines):
    """Join boxes sitting side by side on one row: the OCR splits long names at wide word gaps ("Gandalf, Wandering"
    + "Wizard"), while the matcher expects whole lines (it was written for ML Kit). A mana cost or HP further along
    the row stays separate. Only for the name strip: the matcher wants the collector number as a line of its own."""
    out = []
    for l in sorted(lines, key=lambda l: l["x"]):
        for o in out:
            overlap = min(o["y"] + o["h"], l["y"] + l["h"]) - max(o["y"], l["y"])
            gap = l["x"] - (o["x"] + o["w"])
            # x is a fraction of the width and h of the height; a card is 63:88
            if overlap > 0.5 * min(o["h"], l["h"]) and gap * 63 < 1.5 * max(o["h"], l["h"]) * 88:
                o["text"] += " " + l["text"]
                top = min(o["y"], l["y"])
                o["h"] = max(o["y"] + o["h"], l["y"] + l["h"]) - top
                o["y"] = top
                o["w"] = l["x"] + l["w"] - o["x"]
                break
        else:
            out.append(dict(l))
    return out


class Identifier:
    def __init__(self, games=("mtg", "pkm", "pkj", "lor")):
        self.games = list(games)
        self.cards = sqlite3.connect(f"file:{HERE / 'data' / 'cards.db'}?mode=ro", uri=True, check_same_thread=False)
        self.cards.row_factory = sqlite3.Row
        self.ocr = RapidOCR(params={
            "Global.log_level": "error",
            "Global.use_cls": False,
            "EngineConfig.onnxruntime.use_cuda": True,
            # The default scales every image up to a 736 px short side, which blows a strip under 100 px tall up
            # several times over and loses the collector number on many cards.
            "Det.limit_type": "min",
            "Det.limit_side_len": 320,
        })
        self.node = subprocess.Popen(
            ["node", str(HERE / "matcher/matcher.ts")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=HERE,
        )

    def close(self):
        self.node.stdin.close()
        self.node.wait()

    def _match(self, reads, games=None):
        self.node.stdin.write(json.dumps({"reads": reads, "games": games or self.games}) + "\n")
        self.node.stdin.flush()
        return json.loads(self.node.stdout.readline())

    def __call__(self, card, likely_down=False):
        """`likely_down`: which way up to assume when no name reads either way, from the session's cards so far."""
        best = Result(None, upside_down=likely_down)
        upside_down = None
        # the middle strip doesn't depend on the pass, so it's read at most once each way up
        mid = {}
        for top_end, bottom_start, zoom in PASSES:
            r = self._read(card, top_end, bottom_start, zoom, upside_down, likely_down, mid)
            # a low-confidence guess may come from the wrong way up; let the looser pass decide again
            if r.id and r.confidence != "low" and upside_down is None:
                upside_down = r.upside_down
            if r.id and (not best.id or RANK[r.confidence] > RANK[best.confidence]):
                best = r
            if best.confidence == "high":
                break
        if not best.id and "lor" in self.games:
            best = self._sideways(card) or best
        if best.id and not best.by_number:
            self._by_art(card, best)
        return best

    def _by_art(self, card, r):
        """Settle the printing from the artwork when the collector number didn't. Only printings of the matched name
        in the matched set, unless the name has few printings overall."""
        cur = self.cards.execute("SELECT game, lang FROM cards WHERE id = ?", (r.id,)).fetchone()
        rows = self.cards.execute("SELECT id, game, image, set_code, set_name, number FROM cards WHERE name = ? AND game = ? AND lang = ?",
                                  (r.name, cur["game"], cur["lang"])).fetchall()
        if len(rows) > art.MAX_CANDIDATES:
            rows = [x for x in rows if x["set_code"] == r.set_code]
        if not 2 <= len(rows) <= art.MAX_CANDIDATES:
            return
        chosen, sure = art.pick(upright(card, r), rows)
        if chosen is None:
            return
        r.id, r.set_code, r.set_name, r.number = chosen["id"], chosen["set_code"], chosen["set_name"], chosen["number"]
        r.by_art, r.art_sure = True, sure
        if sure and r.confidence == "low":
            r.confidence = "medium"

    def _mid(self, img, key, cache):
        if key not in cache:
            cache[key] = _lines(self.ocr, img, *MID, join=True)
        return cache[key]

    def _sideways(self, card):
        """A Lorcana location: landscape art warped into a portrait card. Its collector line runs along the bottom
        once turned, which gives set and number; the name isn't read."""
        for turn in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
            bottom = _lines(self.ocr, cv2.rotate(card, turn), 0.85, 1, 3)
            reply = self._match([{"key": "card", "lines": bottom}], ["lor"])
            m = reply.get("match")
            if m and m.get("byNumber"):
                return self._result(reply, False)
        return None

    def _read(self, card, top_end, bottom_start, zoom, upside_down, likely_down=False, mid=None):
        mid = {} if mid is None else mid
        lorcana = "lor" in self.games
        flipped = card[::-1, ::-1]
        if upside_down is None:
            up = _lines(self.ocr, card, 0, top_end, join=True)
            down = _lines(self.ocr, flipped, 0, top_end, join=True)
            # The bottom strip upside down holds the artist's name, which looks as wordy as a card name, so only a
            # name the matcher knows says which way is up.
            reads = [{"key": "up", "lines": up}, {"key": "down", "lines": down}]
            # an equally good read either way goes to the first
            guess = self._match(reads[::-1] if likely_down else reads)
            if not guess.get("key") and lorcana:
                # only for Lorcana: mid-card rules text ("create a 2/2") reads as names and numbers of the others
                reads = [{"key": "up", "lines": up + self._mid(card, "up", mid)},
                         {"key": "down", "lines": down + self._mid(flipped, "down", mid)}]
                guess = self._match(reads[::-1] if likely_down else reads, ["lor"])
            upside_down = guess["key"] == "down" if guess.get("key") else likely_down
            top = down if upside_down else up
        else:
            top = _lines(self.ocr, flipped if upside_down else card, 0, top_end, join=True)
        img = flipped if upside_down else card
        bottom = _lines(self.ocr, img, bottom_start, 1, zoom)
        way = "down" if upside_down else "up"
        reply = self._match([{"key": "card", "lines": top + bottom}])
        rank = lambda r: RANK[r["match"]["confidence"]] if r.get("match") else 0
        if lorcana and rank(reply) < RANK["high"]:
            again = self._match([{"key": "card", "lines": top + self._mid(img, way, mid) + bottom}], ["lor"])
            if rank(again) > rank(reply):
                reply = again
        if not reply.get("match"):
            return Result(None, upside_down=upside_down)
        return self._result(reply, upside_down)

    @staticmethod
    def _result(reply, upside_down):
        m = reply["match"]
        return Result(
            id=m["id"], name=m["name"], set_code=m["set_code"], set_name=reply["card"]["set_name"],
            number=m["number"], confidence=m["confidence"], game=reply["game"], upside_down=upside_down,
            candidates=m.get("candidates"), by_number=bool(m.get("byNumber")), name_unread=bool(m.get("nameUnread")),
        )


def upright(card, result):
    return card[::-1, ::-1] if result.upside_down else card


if __name__ == "__main__":
    import sys
    import time

    ident = Identifier()
    for path in sys.argv[1:]:
        t = time.perf_counter()
        r = ident(cv2.imread(path))
        print(f"{Path(path).name} {time.perf_counter() - t:.2f}s {r.confidence} {r.name} [{r.set_code} {r.number}]"
              + (" (flipped)" if r.upside_down else ""))
    ident.close()
