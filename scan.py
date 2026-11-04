"""Watch a video file or camera device, identify each card dropped and add it to the collection.

    python scan.py /dev/video2 --show
    python scan.py /dev/video2 --show --foil        # start on foil; F in the window cycles normal / foil / holo
    python scan.py clip.mkv --no-db --out /tmp/caps   # replay a recording without touching the collection

With --control (used by the desktop app) it reads commands from stdin ("mode foil", "quit") and reports on stdout
as JSON lines; --preview writes the annotated feed to a JPEG a few times a second instead of opening a window.

Every capture goes into data/collection.db (see collection.py) and its image into captures/<session>/, with
sheet.jpg and session.json beside them. A capture that names the same card as the one before is a repeat (the card
slid after landing), never a second copy.
"""

import argparse
import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

# OpenCV's bundled Qt has no Wayland plugin and no fonts; run it under XWayland with the system's fonts. Set after
# importing cv2, which points the font directory at its own empty folder; Qt only reads it when a window opens.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ["QT_QPA_FONTDIR"] = "/usr/share/fonts/TTF"

import collection
from detect import detect, portrait, warp
from identify import RANK, Identifier, Result, upright
from watch import Watcher


def open_source(src, size):
    # OpenCV's V4L2 backend only opens devices by index
    index = src.removeprefix("/dev/video")
    if not index.isdigit():
        return cv2.VideoCapture(src)
    cap = cv2.VideoCapture(int(index), cv2.CAP_V4L2)
    # without asking, UVC cameras (the Pixel's webcam mode included) come up as 640x480 raw
    w, h = (int(v) for v in size.split("x"))
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    # keep only the newest frame so a slow detection pass doesn't build up lag
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def contact_sheet(entries, out, cols=8, width=200):
    if not entries:
        return
    colours = {"added": (0, 255, 0), "review": (0, 160, 255), "repeat": (160, 160, 160)}
    tiles = []
    for e in entries:
        t = cv2.resize(cv2.imread(str(out / e["file"])), (width, round(width * 88 / 63)), interpolation=cv2.INTER_AREA)
        if e["status"] == "repeat":
            t = (t * 0.4).astype(np.uint8)
        label = [f"{e['n']} {e['status']}", (e["name"] or "?")[:22], f"{e['set_code']} {e['number']} {e['confidence']}" if e["id"] else ""]
        for i, text in enumerate(label):
            y = 20 + i * 18
            cv2.putText(t, text, (5, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 4)
            cv2.putText(t, text, (5, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colours[e["status"]], 1)
        tiles.append(t)
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(out / "sheet.jpg"), np.vstack(rows))


def outlined(frame, held, quad):
    """The feed at 960 wide with the card outline: green just after a capture, amber while one is being looked for."""
    view = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])))
    k = 960 / frame.shape[1]
    if held:
        cv2.polylines(view, [(held[0] * k).astype(np.int32)], True, (0, 255, 0), 3)
    elif quad is not None:
        cv2.polylines(view, [(quad * k).astype(np.int32)], True, (0, 200, 255), 2)
    return view


MODE_BOX = (250, 48)
MODE_COLOUR = {"normal": (90, 90, 90), "foil": (200, 120, 0), "holo": (180, 0, 200)}


def draw_mode(view, mode):
    """The finish toggle, top right; F or a click on it switches."""
    w, h = MODE_BOX
    x = view.shape[1] - w
    cv2.rectangle(view, (x, 0), (view.shape[1], h), MODE_COLOUR[mode], -1)
    cv2.putText(view, f"{collection.MODES[mode]} [F]", (x + 10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)


def recrop(c):
    """The card inside a rough capture's region, at the frame's full resolution. A rough region can cover the
    whole tray floor when the change mask spreads (a shadow, a nudged tray), leaving the card small in the warp."""
    x0, y0 = np.floor(c.quad.min(axis=0)).astype(int).clip(0)
    x1, y1 = np.ceil(c.quad.max(axis=0)).astype(int)
    crop = c.frame[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    quad = detect(crop, min_frac=0.2, expand_range=0.05)
    if quad is None:
        return None
    return warp(c.frame, portrait(quad + (x0, y0)))


class Session:
    """Identifies captures on a worker thread, in order, so the camera loop never waits on OCR."""

    def __init__(self, out, fps, db=None, session_id=None):
        self.out = out
        self.fps = fps
        self.db = db
        self.session_id = session_id
        self.entries = []
        self.last = None
        self.latest = ""
        self.report = None
        self.jobs = queue.Queue()
        self.identifier = Identifier()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def add(self, capture, mode):
        """`mode`: the finish toggle as the card landed, not when its turn in the queue comes."""
        self.jobs.put((capture, mode))

    def close(self):
        self.jobs.put(None)
        self.thread.join()
        self.identifier.close()
        (self.out / "session.json").write_text(json.dumps(self.entries, indent=1, ensure_ascii=False))
        contact_sheet(self.entries, self.out)

    def _run(self):
        while (job := self.jobs.get()) is not None:
            c, mode = job
            try:
                self._process(c, mode)
            except Exception:
                # one bad capture must not stop the session; keep its image so it can be reviewed
                traceback.print_exc()
                n = len(self.entries) + 1
                cv2.imwrite(str(self.out / f"{n:04d}.jpg"), c.card)
                entry = {"n": n, "t": round(c.frame_no / self.fps, 1), "file": f"{n:04d}.jpg", "rough": c.rough,
                         **asdict(Result(None)), "finish": None, "status": "review"}
                del entry["upside_down"]
                if self.db:
                    entry["scan_id"] = collection.add_scan(self.db, self.session_id, entry)
                self.entries.append(entry)
                if self.report:
                    self.report({"event": "card", "status": "review", "n": n, "t": entry["t"], "scan_id": entry.get("scan_id"),
                                 "name": None, "pending": self.jobs.qsize()})

    def _process(self, c, mode):
        r = self.identifier(c.card)
        if not r.id and c.frame is not None:
            found = recrop(c)
            if found is not None:
                retry = self.identifier(found)
                if retry.id:
                    r, c.card = retry, found
        n = len(self.entries) + 1
        file = f"{n:04d}.jpg"
        cv2.imwrite(str(self.out / file), upright(c.card, r))
        entry = {"n": n, "t": round(c.frame_no / self.fps, 1), "file": file, "rough": c.rough, **asdict(r)}
        del entry["upside_down"]
        entry["finish"] = collection.pick_finish(self.db, r.id, mode) if self.db and r.id else None
        # The same name twice in a row is the same card read again (it slid after landing); a second copy is
        # never dropped straight after the first. The surer read decides the printing.
        prev = self.last
        if r.id and prev and prev["name"] == r.name:
            status = "repeat"
            if RANK[r.confidence] > RANK[prev["confidence"]] and not c.rough:
                keep = {k: prev[k] for k in ("n", "t", "file", "status", "scan_id") if k in prev}
                prev.update(entry, **keep)
                if prev["status"] == "review" and r.confidence != "low":
                    prev["status"] = "added"
                cv2.imwrite(str(self.out / prev["file"]), upright(c.card, r))
                if self.db:
                    collection.update_scan(self.db, prev["scan_id"], card_id=r.id, finish=prev["finish"],
                                           status=prev["status"], confidence=r.confidence)
        elif not r.id or r.confidence == "low" or (c.rough and r.confidence != "high"):
            status = "review"
        else:
            status = "added"
        entry["status"] = status
        if self.db:
            entry["scan_id"] = collection.add_scan(self.db, self.session_id, entry)
        self.entries.append(entry)
        if r.id and status != "repeat":
            self.last = entry
        if status != "repeat":
            self.latest = f"{r.name or '?'} [{r.set_code} {r.number}]" if r.id else "? (review)"
            if not self.report:
                print(f"{n:4d} {status:6s} {r.confidence or '':6s} {r.name or '?'}" + (f"  [{r.set_code} {r.number}]" if r.id else ""), flush=True)
        if self.report:
            # repeats are reported too: a surer re-read can upgrade the entry shown before
            shown = self.last if status == "repeat" and self.last else entry
            self.report({"event": "card", "status": status, "entry_status": shown["status"], "repeat_of": self.last["n"] if status == "repeat" and self.last else None,
                         **{k: shown.get(k) for k in ("n", "t", "scan_id", "name", "set_code", "set_name", "number",
                                                      "confidence", "finish", "game")},
                         "pending": self.jobs.qsize()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="video file, /dev/videoN or camera index")
    ap.add_argument("--out", help="where captures go (default captures/<session>)")
    ap.add_argument("--foil", action="store_true", help="start with the finish toggle on foil / reverse holo")
    ap.add_argument("--no-db", action="store_true", help="don't record anything in the collection")
    ap.add_argument("--show", action="store_true", help="preview window with the detected outline")
    ap.add_argument("--size", default="1920x1080", help="camera resolution")
    ap.add_argument("--record", help="also save the raw feed to this .mkv, for replaying a session")
    ap.add_argument("--mode", choices=list(collection.MODES), help="starting finish (overrides --foil)")
    ap.add_argument("--control", action="store_true", help="JSON lines on stdout, commands on stdin (for the app)")
    ap.add_argument("--preview", help="write the annotated feed to this JPEG a few times a second")
    args = ap.parse_args()

    out_lock = threading.Lock()

    def report(event):
        if args.control:
            with out_lock:
                sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
                sys.stdout.flush()

    cap = open_source(args.source, args.size)
    if not cap.isOpened():
        report({"event": "error", "message": f"can't open {args.source}"})
        raise SystemExit(f"can't open {args.source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    mode = args.mode or ("foil" if args.foil else "normal")
    db = session_id = None
    if args.no_db:
        if not args.out:
            raise SystemExit("--no-db needs --out")
        out = Path(args.out)
    else:
        db = collection.connect()
        session_id, out = collection.start_session(db, args.source, mode, args.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    watcher = Watcher()
    session = Session(out, fps, db, session_id)
    if args.control:
        session.report = report
    toggle = {"mode": mode, "quit": False}
    modes = list(collection.MODES)

    def set_mode(m):
        toggle["mode"] = m
        if not args.control:
            print(f"finish: {collection.MODES[m]}", flush=True)
        report({"event": "mode", "mode": m})

    def next_mode():
        set_mode(modes[(modes.index(toggle["mode"]) + 1) % len(modes)])

    def read_commands():
        for line in sys.stdin:
            cmd, _, arg = line.strip().partition(" ")
            if cmd == "mode" and arg in collection.MODES:
                set_mode(arg)
            elif cmd == "quit":
                break
        # stdin closing (the app went away) stops the scan too
        toggle["quit"] = True

    if args.control:
        threading.Thread(target=read_commands, daemon=True).start()

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN and x >= 960 - MODE_BOX[0] and y <= MODE_BOX[1]:
            next_mode()

    if session_id and not args.control:
        print(f"session {session_id}, captures in {out}", flush=True)
    report({"event": "started", "session": session_id, "dir": str(out), "mode": mode})
    started = time.perf_counter()
    last_preview = 0.0
    held = None
    recorder = None
    mouse_set = False
    try:
        while not toggle["quit"]:
            ok, frame = cap.read()
            if not ok:
                break
            if args.record:
                if recorder is None:
                    h, w = frame.shape[:2]
                    recorder = cv2.VideoWriter(args.record, cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
                recorder.write(frame)
            status = watcher.feed(frame)
            if status.capture:
                session.add(status.capture, toggle["mode"])
                held = (status.capture.quad, watcher.frame_no + round(fps * 0.7))
            now = time.perf_counter()
            if args.preview and now - last_preview > 0.12:
                last_preview = now
                view = outlined(frame, held if held and watcher.frame_no <= held[1] else None, status.quad)
                tmp = args.preview + ".tmp.jpg"
                cv2.imwrite(tmp, view, [cv2.IMWRITE_JPEG_QUALITY, 75])
                os.replace(tmp, args.preview)
            if args.show:
                view = outlined(frame, held if held and watcher.frame_no <= held[1] else None, status.quad)
                added = sum(e["status"] == "added" for e in session.entries)
                cv2.putText(view, f"{added} cards  {session.latest}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 5)
                cv2.putText(view, f"{added} cards  {session.latest}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                draw_mode(view, toggle["mode"])
                cv2.imshow("pcscan", view)
                if not mouse_set:
                    cv2.setMouseCallback("pcscan", on_mouse)
                    mouse_set = True
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
                if key == ord("f"):
                    next_mode()
    except KeyboardInterrupt:
        pass
    if recorder is not None:
        recorder.release()
    session.close()
    counts = {s: sum(e["status"] == s for e in session.entries) for s in ("added", "review", "repeat")}
    report({"event": "done", **counts, "session": session_id})
    if args.control:
        return
    print(f"{counts['added']} added, {counts['review']} to review, {counts['repeat']} repeats"
          f" ({watcher.frame_no + 1} frames in {time.perf_counter() - started:.1f}s)")


if __name__ == "__main__":
    main()
