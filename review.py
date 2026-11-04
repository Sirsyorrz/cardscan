"""Local web page for checking scans and editing the collection: http://localhost:8765

    python review.py [--port 8765]

Run it beside scan.py; the page refreshes as cards come in.
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import collection
import prices
from collection import norm

PAGE = Path(__file__).resolve().parent / "static" / "review.html"
CARD_COLS = "id, game, lang, name, local_name, set_code, set_name, number, rarity, image, released"
_local = threading.local()


def db():
    if not hasattr(_local, "db"):
        _local.db = collection.connect()
    return _local.db


def image_url(card, size="small"):
    """Scryfall's id-based layout for Magic; Pokemon keeps the TCGdex asset path or `tcgplayer/<productId>`."""
    if card["game"] == "mtg":
        i = card["id"]
        return f"https://cards.scryfall.io/{'small' if size == 'small' else 'normal'}/front/{i[0]}/{i[1]}/{i}.jpg"
    image = card["image"]
    if not image:
        return None
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
        cur = db().execute("SELECT name, game, lang FROM c.cards WHERE id = ?", (scan["card_id"],)).fetchone()
        rows = db().execute(
            f"SELECT {CARD_COLS} FROM c.cards WHERE name = ? AND game = ? AND lang = ? ORDER BY released DESC LIMIT 120",
            (cur["name"], cur["game"], cur["lang"]),
        ).fetchall()
    seen = {r["id"] for r in rows}
    extra = [i for i in ids if i not in seen]
    if extra:
        rows += db().execute(
            f"SELECT {CARD_COLS} FROM c.cards WHERE id IN ({','.join('?' * len(extra))})", extra
        ).fetchall()
    return [card_json(r) for r in rows]


def search(q):
    text = q.get("q", [""])[0].strip()
    key = norm(text)
    if len(key) < 2:
        return []
    game = q.get("game", [""])[0]
    scope = {"mtg": "game = 'mtg'", "pkm": "game = 'pkm' AND lang = 'en'", "pkj": "game = 'pkm' AND lang = 'ja'"}.get(game, "1")
    rows = db().execute(
        f"""SELECT {CARD_COLS} FROM c.cards
            WHERE {scope} AND (name_norm LIKE ? OR local_norm LIKE ?)
            ORDER BY name_norm = ? DESC, name_norm LIKE ? DESC, released DESC LIMIT 80""",
        (f"%{key}%", f"%{key}%", key, f"{key}%"),
    ).fetchall()
    return [card_json(r) for r in rows]


def finishes(q):
    return [{"key": k, "label": l} for k, l in collection.finishes(db(), q["card"][0])]


def items(q):
    text = norm(q.get("q", [""])[0])
    sort = {"name": "c.name, c.set_code", "recent": "i.added_at DESC", "set": "c.set_code, c.number_sort"}.get(
        q.get("sort", ["recent"])[0], "i.added_at DESC")
    rows = db().execute(
        f"""SELECT i.*, {', '.join('c.' + col for col in CARD_COLS.split(', '))} FROM items i
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


def post_prices():
    lines = []
    prices.refresh(db(), lines.append)
    return {"log": lines}


GET = {"/api/sessions": sessions, "/api/scans": scans, "/api/options": options, "/api/search": search,
       "/api/finishes": finishes, "/api/items": items}


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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
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
            if url.path.startswith("/api/scans/"):
                return self._send(200, post_scan(int(url.path.rsplit("/", 1)[1]), body))
            if url.path == "/api/items":
                return self._send(200, post_item(body))
            if url.path == "/api/prices":
                return self._send(200, post_prices())
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
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
