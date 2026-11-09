"""Local web page for checking scans and editing the collection: http://localhost:8765

    python review.py [--port 8765]

Run it beside scan.py; the page refreshes as cards come in. The desktop app (app/) runs it in the background and
uses the same JSON API, plus /live: the scanner's preview JPEG (scan.py --preview) as an MJPEG stream.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import collection
import exporter
import importer
import prices
from collection import norm

PAGE = Path(__file__).resolve().parent / "static" / "review.html"
CARD_COLS = "id, game, lang, name, local_name, set_code, set_name, number, rarity, image, released"
_local = threading.local()
LIVE = {"path": None}
GAME_SCOPE = {"lor": "game = 'lor'", "mtg": "game = 'mtg'", "pkm": "game = 'pkm' AND lang = 'en'",
              "pkj": "game = 'pkm' AND lang = 'ja'"}


def db():
    if not hasattr(_local, "db"):
        _local.db = collection.connect()
    return _local.db


def image_url(card, size="small"):
    """Scryfall's id-based layout for Magic; Lorcana keeps Lorcast's file name; Pokemon keeps the TCGdex asset path or
    `tcgplayer/<productId>`."""
    if card["game"] == "mtg":
        i = card["id"]
        return f"https://cards.scryfall.io/{'small' if size == 'small' else 'normal'}/front/{i[0]}/{i[1]}/{i}.jpg"
    image = card["image"]
    if not image:
        return None
    if card["game"] == "lor":
        return f"https://cards.lorcast.io/card/digital/{'small' if size == 'small' else 'large'}/{image}"
    if image.startswith("tcgplayer/"):
        product = image.removeprefix("tcgplayer/")
        return f"https://tcgplayer-cdn.tcgplayer.com/product/{product}_{'200w' if size == 'small' else 'in_1000x1000'}.jpg"
    return f"https://assets.tcgdex.net/{image}/{'low' if size == 'small' else 'high'}.webp"


def card_json(row):
    if row is None:
        return None
    c = dict(row)
    c["img"] = image_url(c)
    c["img_large"] = image_url(c, "large")
    return c


def get_card(card_id):
    if not card_id:
        return None
    return card_json(db().execute(f"SELECT {CARD_COLS} FROM c.cards WHERE id = ?", (card_id,)).fetchone())


def price(card_id, finish):
    """TCGplayer market price in USD: a refreshed one when there is one, else the snapshot in cards.db."""
    for sql in ("SELECT usd FROM prices WHERE card_id = ? AND finish = ? AND source = 'tcg'",
                "SELECT usd FROM c.base_prices WHERE card_id = ? AND finish = ? AND source = 'tcg'"):
        row = db().execute(sql, (card_id, finish)).fetchone()
        if row:
            return row[0]
    return None


def sessions(_q):
    rows = db().execute(
        """SELECT s.*, COUNT(x.id) AS scans, SUM(x.status = 'added') AS added, SUM(x.status = 'review') AS review
           FROM sessions s LEFT JOIN scans x ON x.session_id = s.id GROUP BY s.id ORDER BY s.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def scans(q):
    session = int(q.get("session", [0])[0]) or db().execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    rows = db().execute("SELECT * FROM scans WHERE session_id = ? ORDER BY n", (session,)).fetchall()
    out = []
    for r in rows:
        s = dict(r)
        s["candidates"] = json.loads(s["candidates"] or "[]")
        s["card"] = get_card(s["card_id"])
        s["finish_label"] = collection.label(s["finish"]) if s["finish"] else None
        out.append(s)
    return {"session": session, "scans": out}


def options(q):
    """Printings to choose from for a scan: every printing of the matched name, plus the matcher's candidates."""
    scan = db().execute("SELECT * FROM scans WHERE id = ?", (int(q["scan"][0]),)).fetchone()
    ids = json.loads(scan["candidates"] or "[]")
    rows = []
    if scan["card_id"]:
        # a Magic card's language-only printings are more printings of it; Japanese Pokémon is its own card pool
        cur = db().execute("SELECT name, game, lang FROM c.cards WHERE id = ?", (scan["card_id"],)).fetchone()
        rows = db().execute(
            f"SELECT {CARD_COLS} FROM c.cards WHERE name = ? AND game = ? AND (game = 'mtg' OR lang = ?) ORDER BY released DESC LIMIT 120",
            (cur["name"], cur["game"], cur["lang"]),
        ).fetchall()
    seen = {r["id"] for r in rows}
    extra = [i for i in ids if i not in seen]
    if extra:
        rows += db().execute(
            f"SELECT {CARD_COLS} FROM c.cards WHERE id IN ({','.join('?' * len(extra))})", extra
        ).fetchall()
    return [card_json(r) for r in rows]


NUMBER_QUERY = re.compile(r"(?:([a-z][a-z0-9]{1,5}?)\s*)?#?(\d{1,4})[a-z]?(?:\s*/\s*(\d{1,4}))?", re.I)
# Lorcana sets are numbered, so "6 1", "6-1" or "6 #1" is set 6 card 1
LOR_NUMBER_QUERY = re.compile(r"(\d{1,2})\s*[-#\s]\s*#?(\d{1,3})[a-z]?")


def search(q):
    """Printings by name, or by set code and collector number ("DLR 44", "dlr044", "44/103"), with their lowest
    TCGplayer price and the copies owned."""
    text = q.get("q", [""])[0].strip()
    key = norm(text)
    if len(key) < 2:
        return []
    game = q.get("game", [""])[0]
    scope = GAME_SCOPE.get(game, "1")
    rows = []
    if game == "lor" and (m := LOR_NUMBER_QUERY.fullmatch(text)):
        rows = db().execute(
            f"SELECT {CARD_COLS} FROM c.cards WHERE {scope} AND set_code = ? AND number_sort = ? ORDER BY number",
            (m[1], int(m[2])),
        ).fetchall()
    elif m := NUMBER_QUERY.fullmatch(text):
        code, number, total = m.groups()
        where, args = [scope, "number_sort = ?"], [int(number)]
        if code:
            where.append("set_code = ? COLLATE NOCASE")
            args.append(code)
        if total:
            # the printed "/103" is the set's base count; secret rares push `total` past it
            where.append("set_id IN (SELECT id FROM c.sets WHERE ? IN (base, total))")
            args.append(int(total))
        if code or total:
            rows = db().execute(
                f"SELECT {CARD_COLS} FROM c.cards WHERE {' AND '.join(where)} ORDER BY released DESC LIMIT 80", args
            ).fetchall()
    if not rows:
        rows = db().execute(
            f"""SELECT {CARD_COLS} FROM c.cards
                WHERE {scope} AND (name_norm LIKE ? OR local_norm LIKE ?)
                ORDER BY name_norm = ? DESC, name_norm LIKE ? DESC, released DESC LIMIT 80""",
            (f"%{key}%", f"%{key}%", key, f"{key}%"),
        ).fetchall()
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    held = dict(db().execute(f"SELECT card_id, SUM(qty) FROM items WHERE card_id IN ({marks}) GROUP BY card_id", ids))
    lowest = {}
    for (cid, _), v in price_map(ids).items():
        if v.get("tcg") is not None:
            lowest[cid] = min(lowest.get(cid, v["tcg"]), v["tcg"])
    return [{**card_json(r), "usd": lowest.get(r["id"]), "qty": held.get(r["id"], 0)} for r in rows]


def finishes(q):
    return [{"key": k, "label": l} for k, l in collection.finishes(db(), q["card"][0])]


def items(q):
    text = norm(q.get("q", [""])[0])
    sort = {"name": "c.name, c.set_code", "recent": "i.added_at DESC", "set": "c.set_code, c.number_sort"}.get(
        q.get("sort", ["recent"])[0], "i.added_at DESC")
    rows = db().execute(
        f"""SELECT i.*, {', '.join('c.' + col for col in CARD_COLS.split(', '))}, c.number_sort,
                   (SELECT MAX(x.id) FROM scans x WHERE x.card_id = i.card_id AND x.status = 'added') AS capture_id
            FROM items i
            JOIN c.cards c ON c.id = i.card_id
            WHERE ? = '' OR c.name_norm LIKE ? OR c.local_norm LIKE ? ORDER BY {sort}""",
        (text, f"%{text}%", f"%{text}%"),
    ).fetchall()
    out = []
    for r in rows:
        it = card_json(r)
        it["finish_label"] = collection.label(it["finish"])
        it["usd"] = price(it["card_id"], it["finish"])
        out.append(it)
    if q.get("sort", [""])[0] == "value":
        out.sort(key=lambda it: -(it["usd"] or 0) * it["qty"])
    rate = collection.get_meta(db(), "aud_per_usd")
    return {"items": out, "aud_per_usd": float(rate) if rate else None, "prices_at": collection.get_meta(db(), "prices_at")}


MTG_FORMATS = ["standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander"]
MTG_RESTRICTED = 1 << 7


def legality(game, bits):
    """cards.legal as [(format, legal, restricted)], bits as in tools/build_db.py."""
    if game == "mtg":
        return [{"format": f, "legal": bool(bits >> i & 1), "restricted": f == "vintage" and bool(bits & MTG_RESTRICTED)}
                for i, f in enumerate(MTG_FORMATS)]
    if game == "lor":
        return [{"format": f, "legal": bool(bits & b), "restricted": False} for f, b in (("core", 1), ("infinity", 2))]
    return [{"format": f, "legal": bool(bits & b), "restricted": False} for f, b in (("standard", 1), ("expanded", 2))]


def price_map(ids):
    """{(card_id, finish): {"tcg": usd, "ck": usd}}: refreshed TCGplayer prices over the cards.db snapshot."""
    out = {}
    marks = ",".join("?" * len(ids))
    for sql in (f"SELECT card_id, finish, source, usd FROM c.base_prices WHERE card_id IN ({marks})",
                f"SELECT card_id, finish, source, usd FROM prices WHERE card_id IN ({marks})"):
        for r in db().execute(sql, ids):
            out.setdefault((r["card_id"], r["finish"]), {})[r["source"]] = r["usd"]
    return out


def links(c):
    out = []
    if c["game"] == "mtg":
        out.append({"label": "Scryfall", "url": f"https://scryfall.com/card/{c['set_id']}/{c['number']}"})
    if c["game"] == "lor":
        slug = re.sub(r"[^a-z0-9]+", "-", c["name"].lower().replace("'", "")).strip("-")
        out.append({"label": "Lorcast", "url": f"https://lorcast.com/cards/{c['set_code']}/{c['number']}/{slug}"})
    if c["tcg_id"]:
        out.append({"label": "TCGplayer", "url": f"https://www.tcgplayer.com/product/{c['tcg_id']}"})
    if c["ck_url"]:
        out.append({"label": "Card Kingdom", "url": f"https://www.cardkingdom.com/{c['ck_url']}"})
    return out


def card_detail(q):
    """One printing with its stats, prices per finish, what's owned and scanned of it, and the other printings."""
    row = db().execute(
        f"""SELECT c.*, s.series, s.total AS set_total, s.icon AS set_icon, t.body AS text FROM c.cards c
            LEFT JOIN c.sets s ON s.id = c.set_id LEFT JOIN c.texts t ON t.id = c.text_id WHERE c.id = ?""",
        (q["id"][0],),
    ).fetchone()
    if row is None:
        raise ValueError("no such card")
    card = card_json(row)
    owned = {r["finish"]: dict(r) for r in db().execute(
        "SELECT finish, SUM(qty) AS qty, MAX(added_at) AS added_at FROM items WHERE card_id = ? GROUP BY finish",
        (card["id"],))}
    prices_by = price_map([card["id"]])
    finishes = [{"key": k, "label": l, **prices_by.get((card["id"], k), {}),
                 "qty": owned.get(k, {}).get("qty", 0), "added_at": owned.get(k, {}).get("added_at")}
                for k, l in collection.finishes(db(), card["id"])]
    scans_of = db().execute(
        """SELECT id, session_id, status, finish, created_at FROM scans
           WHERE card_id = ? AND status IN ('added', 'review') ORDER BY created_at DESC""",
        (card["id"],),
    ).fetchall()
    rows = db().execute(
        f"""SELECT {CARD_COLS}, set_id FROM c.cards WHERE name = ? AND game = ? AND (game = 'mtg' OR lang = ?)
            ORDER BY released DESC, set_code, number_sort LIMIT 150""",
        (card["name"], card["game"], card["lang"]),
    ).fetchall()
    ids = [r["id"] for r in rows]
    held = dict(db().execute(f"SELECT card_id, SUM(qty) FROM items WHERE card_id IN ({','.join('?' * len(ids))}) GROUP BY card_id",
                             ids).fetchall()) if ids else {}
    all_prices = price_map(ids) if ids else {}
    printings = []
    for r in rows:
        p = card_json(r)
        p["qty"] = held.get(r["id"], 0)
        tcg = [v["tcg"] for (cid, _), v in all_prices.items() if cid == r["id"] and "tcg" in v]
        p["usd"] = min(tcg) if tcg else None
        printings.append(p)
    for k in ("name_norm", "local_norm", "text_id", "play_id", "tcg_group", "slot", "number_sort"):
        card.pop(k, None)
    card["legal"] = legality(card["game"], card["legal"] or 0)
    rate = collection.get_meta(db(), "aud_per_usd")
    return {"card": card, "finishes": finishes, "scans": [dict(s) for s in scans_of], "printings": printings,
            "links": links(row), "aud_per_usd": float(rate) if rate else None}


def binder_sets(q):
    """Every set of a game with how many of its base slots you own through any printing."""
    scope = GAME_SCOPE.get(q.get("game", [""])[0], "1")
    owned = dict(db().execute(
        """SELECT c.set_id, COUNT(DISTINCT c.slot) FROM items i JOIN c.cards c ON c.id = i.card_id
           WHERE c.slot IS NOT NULL AND i.qty > 0 GROUP BY c.set_id""").fetchall())
    copies = dict(db().execute(
        "SELECT c.set_id, SUM(i.qty) FROM items i JOIN c.cards c ON c.id = i.card_id GROUP BY c.set_id").fetchall())
    rows = db().execute(f"SELECT * FROM c.sets WHERE {scope} ORDER BY released DESC, name").fetchall()
    return [{**dict(r), "owned": owned.get(r["id"], 0), "copies": copies.get(r["id"], 0)} for r in rows]


def binder(q):
    """One set's printings in number order with the copies owned, lowest TCGplayer price and binder slot."""
    set_id = q["id"][0]
    info = db().execute("SELECT * FROM c.sets WHERE id = ?", (set_id,)).fetchone()
    if info is None:
        raise ValueError("no such set")
    rows = db().execute(
        f"SELECT {CARD_COLS}, slot FROM c.cards WHERE set_id = ? ORDER BY number_sort, number", (set_id,)
    ).fetchall()
    ids = [r["id"] for r in rows]
    held, lowest, captures = {}, {}, {}
    if ids:
        marks = ",".join("?" * len(ids))
        held = dict(db().execute(
            f"SELECT card_id, SUM(qty) FROM items WHERE card_id IN ({marks}) GROUP BY card_id", ids).fetchall())
        captures = dict(db().execute(
            f"""SELECT card_id, MAX(id) FROM scans WHERE card_id IN ({marks}) AND status = 'added'
                GROUP BY card_id""", ids).fetchall())
        for (cid, _), v in price_map(ids).items():
            if v.get("tcg") is not None:
                lowest[cid] = min(lowest.get(cid, v["tcg"]), v["tcg"])
    rate = collection.get_meta(db(), "aud_per_usd")
    return {"set": dict(info), "aud_per_usd": float(rate) if rate else None,
            "cards": [{**card_json(r), "qty": held.get(r["id"], 0), "usd": lowest.get(r["id"]),
                       "capture_id": captures.get(r["id"])} for r in rows]}


def post_prices():
    lines = []
    prices.refresh(db(), lines.append)
    return {"log": lines}


def rate(_q):
    r = collection.get_meta(db(), "aud_per_usd")
    return {"aud_per_usd": float(r) if r else None}


GET = {"/api/rate": rate, "/api/sessions": sessions, "/api/scans": scans, "/api/options": options, "/api/search": search,
       "/api/finishes": finishes, "/api/items": items, "/api/card": card_detail, "/api/sets": binder_sets,
       "/api/binder": binder, "/api/imports": lambda _q: importer.history(db())}


def post_scan(scan_id, body):
    changes = {k: body[k] for k in ("card_id", "finish", "condition", "status") if k in body}
    if "card_id" in changes and "finish" not in changes:
        changes["finish"] = None
    if "card_id" in changes:
        changes["confidence"] = "manual"
    collection.update_scan(db(), scan_id, **changes)
    return {"ok": True}


def post_item(body):
    """{card_id, finish, condition, delta}, or `to_finish`/`to_condition` to move one copy."""
    card, finish, cond = body["card_id"], body["finish"], body.get("condition", "NM")
    if "to_finish" in body or "to_condition" in body:
        with db():
            collection._bump(db(), card, finish, cond, -1)
            collection._bump(db(), card, body.get("to_finish", finish), body.get("to_condition", cond), 1)
    else:
        collection.adjust(db(), card, finish, cond, int(body["delta"]))
    return {"ok": True}


def post_import(body):
    """{text, name, apply}: the plan for a CSV, or with `apply` the import itself. The per-card lines stay out of the
    reply; an export of a big collection has thousands."""
    p = importer.apply(db(), body["text"], body.get("name") or "import") if body.get("apply") else importer.plan(
        db(), body["text"])
    p.pop("lines")
    return p


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        # the app's page has its own origin, so JSON POSTs are preflighted
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _live(self):
        """Each new preview frame as one part of a multipart/x-mixed-replace stream, which an <img> plays."""
        path = Path(LIVE["path"]) if LIVE["path"] else None
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        seen = None
        try:
            self.wfile.write(b"--frame\r\n")
            while True:
                try:
                    stamp = path.stat().st_mtime_ns if path else None
                except FileNotFoundError:
                    stamp = None
                if stamp and stamp != seen:
                    try:
                        data = path.read_bytes()
                    except OSError:
                        # Windows: the scanner is swapping in the next frame
                        continue
                    seen = stamp
                    # the closing boundary goes out with the frame, or browsers hold it until the next one
                    self.wfile.write(b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode()
                                     + b"\r\n\r\n" + data + b"\r\n--frame\r\n")
                    self.wfile.flush()
                time.sleep(0.01)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/live":
                return self._live()
            if url.path.startswith("/capture/"):
                return self._capture(int(url.path.rsplit("/", 1)[1]))
            handler = GET.get(url.path)
            if not handler:
                return self._send(404, {"error": "not found"})
            return self._send(200, handler(parse_qs(url.query)))
        except Exception as e:  # the page shows the message instead of a dead request
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if url.path.startswith("/api/sessions/") and url.path.endswith("/delete"):
                collection.delete_session(db(), int(url.path.split("/")[3]))
                return self._send(200, {"ok": True})
            if url.path.startswith("/api/scans/"):
                return self._send(200, post_scan(int(url.path.rsplit("/", 1)[1]), body))
            if url.path == "/api/items":
                return self._send(200, post_item(body))
            if url.path == "/api/import":
                return self._send(200, post_import(body))
            if url.path.startswith("/api/imports/") and url.path.endswith("/undo"):
                importer.undo(db(), int(url.path.split("/")[3]))
                return self._send(200, {"ok": True})
            if url.path == "/api/prices":
                return self._send(200, post_prices())
            if url.path == "/api/export":
                return self._send(200, exporter.write(db()))
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(400, {"error": str(e)})

    def _capture(self, scan_id):
        row = db().execute(
            "SELECT s.dir, x.file FROM scans x JOIN sessions s ON s.id = x.session_id WHERE x.id = ?", (scan_id,)
        ).fetchone()
        path = Path(row["dir"]) / row["file"] if row else None
        if not path or not path.exists():
            return self._send(404, {"error": "no image"})
        return self._send(200, path.read_bytes(), "image/jpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--live", help="the scanner's preview JPEG, served as /live")
    ap.add_argument("--with-parent", action="store_true", help="exit when the process that started this one does")
    args = ap.parse_args()
    LIVE["path"] = args.live
    if args.with_parent:
        parent = os.getppid()

        def watch_parent():
            # a killed or crashed app never gets to stop its server
            if sys.platform == "win32":
                # Windows never reparents an orphan, so wait on the parent's process handle
                import ctypes
                synchronize, infinite = 0x00100000, 0xFFFFFFFF
                handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent)
                if handle:
                    ctypes.windll.kernel32.WaitForSingleObject(handle, infinite)
            else:
                # reparenting means it's gone
                while os.getppid() == parent:
                    time.sleep(2)
            os._exit(0)

        threading.Thread(target=watch_parent, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
