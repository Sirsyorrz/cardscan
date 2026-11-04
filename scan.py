"""Watch a video file or camera device, identify each card dropped and list the session.

    python scan.py /dev/video2 --show
    python scan.py clip.mp4 --out captures

Writes the straightened cards, sheet.jpg and session.json (one entry per capture) to --out. A capture that names the
same card as the one before is a repeat (the card slid after landing), never a second copy.
"""

import argparse
import json
import os
import queue
import shutil
import threading
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

# OpenCV's bundled Qt has no Wayland plugin and no fonts; run it under XWayland with the system's fonts. Set after
# importing cv2, which points the font directory at its own empty folder; Qt only reads it when a window opens.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ["QT_QPA_FONTDIR"] = "/usr/share/fonts/TTF"

from detect import detect, portrait, warp
from identify import RANK, Identifier, upright
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

    def __init__(self, out, fps):
        self.out = out
        self.fps = fps
        self.entries = []
        self.last = None
        self.latest = ""
        self.jobs = queue.Queue()
        self.identifier = Identifier()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def add(self, capture):
        self.jobs.put(capture)

    def close(self):
        self.jobs.put(None)
        self.thread.join()
        self.identifier.close()
        (self.out / "session.json").write_text(json.dumps(self.entries, indent=1, ensure_ascii=False))
        contact_sheet(self.entries, self.out)

    def _run(self):
        while (c := self.jobs.get()) is not None:
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
            # The same name twice in a row is the same card read again (it slid after landing); a second copy is
            # never dropped straight after the first. The surer read decides the printing.
            prev = self.last
            if r.id and prev and prev["name"] == r.name:
                status = "repeat"
                if RANK[r.confidence] > RANK[prev["confidence"]] and not c.rough:
                    keep = {k: prev[k] for k in ("n", "t", "file", "status")}
                    prev.update(entry, **keep)
                    if prev["status"] == "review" and r.confidence != "low":
                        prev["status"] = "added"
                    cv2.imwrite(str(self.out / prev["file"]), upright(c.card, r))
            elif not r.id or r.confidence == "low" or (c.rough and r.confidence != "high"):
                status = "review"
            else:
                status = "added"
            entry["status"] = status
            self.entries.append(entry)
            if r.id and status != "repeat":
                self.last = entry
            if status != "repeat":
                self.latest = f"{r.name or '?'} [{r.set_code} {r.number}]" if r.id else "? (review)"
                print(f"{n:4d} {status:6s} {r.confidence or '':6s} {r.name or '?'}" + (f"  [{r.set_code} {r.number}]" if r.id else ""), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="video file, /dev/videoN or camera index")
    ap.add_argument("--out", default="captures")
    ap.add_argument("--show", action="store_true", help="preview window with the detected outline")
    ap.add_argument("--size", default="1920x1080", help="camera resolution")
    ap.add_argument("--record", help="also save the raw feed to this .mkv, for replaying a session")
    args = ap.parse_args()

    out = Path(args.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cap = open_source(args.source, args.size)
    if not cap.isOpened():
        raise SystemExit(f"can't open {args.source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    watcher = Watcher()
    session = Session(out, fps)
    started = time.perf_counter()
    held = None
    recorder = None
    try:
        while True:
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
                session.add(status.capture)
                held = (status.capture.quad, watcher.frame_no + round(fps * 0.7))
            if args.show:
                view = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])))
                k = 960 / frame.shape[1]
                if held and watcher.frame_no <= held[1]:
                    cv2.polylines(view, [(held[0] * k).astype(np.int32)], True, (0, 255, 0), 3)
                elif status.quad is not None:
                    cv2.polylines(view, [(status.quad * k).astype(np.int32)], True, (0, 200, 255), 2)
                added = sum(e["status"] == "added" for e in session.entries)
                cv2.putText(view, f"{added} cards  {session.latest}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 5)
                cv2.putText(view, f"{added} cards  {session.latest}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.imshow("pcscan", view)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
    except KeyboardInterrupt:
        pass
    if recorder is not None:
        recorder.release()
    session.close()
    counts = {s: sum(e["status"] == s for e in session.entries) for s in ("added", "review", "repeat")}
    print(f"{counts['added']} added, {counts['review']} to review, {counts['repeat']} repeats"
          f" ({watcher.frame_no + 1} frames in {time.perf_counter() - started:.1f}s)")


if __name__ == "__main__":
    main()
