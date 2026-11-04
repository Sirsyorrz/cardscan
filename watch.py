"""Turns a camera feed into one capture per card dropped on the pile.

The camera is fixed, so once the picture settles, a change since the last settled picture means a new card.
On an empty tray (or when the card lands off to one side) the changed area is the card itself and gives its
rough place and angle. When it lands squarely on the last card only the artwork differs, so the search falls
back to where the last card was. Either way the edge fitter in detect.py finds the exact outline on an
upright crop around that spot.
"""

from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from detect import CARD_ASPECT, detect, portrait, warp

WORK = 480
MOTION_DIFF = 18
CHANGE_DIFF = 30
# share of the picture that has to change between settled scenes to count as a new card
MIN_CHANGE = 0.01
# still frames to keep retrying the edge fitter before settling for the rough outline
ROUGH_AFTER = 6


@dataclass
class Capture:
    frame_no: int
    card: np.ndarray
    quad: np.ndarray
    rough: bool
    # kept for rough captures only, so the identifier can look again for the card inside the rough region
    frame: np.ndarray | None = None


@dataclass
class Status:
    moving: bool
    quad: np.ndarray | None = None
    capture: Capture | None = None


def _small(frame):
    h, w = frame.shape[:2]
    return cv2.resize(frame, (WORK, round(h * WORK / w)), interpolation=cv2.INTER_AREA)


def _gray(small):
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)


def match_exposure(ref, cur):
    """`ref` scaled per channel to `cur`'s exposure and white balance. The phone readjusts both when a card lands,
    which otherwise shows up as the whole picture changing. Measured on the edges of the picture, which the pile
    doesn't reach."""
    r = ref.astype(np.float32) + 1
    ratio = (cur.astype(np.float32) + 1) / r
    h, w = ratio.shape[:2]
    by, bx = h // 12, w // 12
    ring = np.concatenate([ratio[:by].reshape(-1, 3), ratio[-by:].reshape(-1, 3),
                           ratio[by:-by, :bx].reshape(-1, 3), ratio[by:-by, -bx:].reshape(-1, 3)])
    gain = np.median(ring, axis=0)
    return np.clip(r * gain - 1, 0, 255).astype(np.uint8)


def changed(ref, cur):
    """Mask (WORK pixels) of what differs between two settled scenes, after undoing any small camera shift."""
    shift, _ = cv2.phaseCorrelate(_gray(ref).astype(np.float32), _gray(cur).astype(np.float32))
    m = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
    ref = cv2.warpAffine(ref, m, (ref.shape[1], ref.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    ref = match_exposure(ref, cur)
    diff = cv2.absdiff(cv2.GaussianBlur(ref, (5, 5), 0), cv2.GaussianBlur(cur, (5, 5), 0)).max(axis=2)
    mask = (diff > CHANGE_DIFF).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    k = max(3, WORK // 60)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))


def largest_change(mask):
    """Rotated rect of the biggest changed region whatever its shape (a card with a moved shadow attached), or None."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    big = max(contours, key=cv2.contourArea, default=None)
    if big is None or cv2.contourArea(big) < 0.03 * mask.size:
        return None
    return cv2.minAreaRect(cv2.convexHull(big))


def card_blob(mask):
    """Rotated rect of the most card-like changed region, or None."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = mask.shape[0] * mask.shape[1]
    best = None
    best_score = 0.0
    for c in contours:
        if cv2.contourArea(c) < 0.02 * area:
            continue
        hull = cv2.convexHull(c)
        rect = cv2.minAreaRect(hull)
        rw, rh = rect[1]
        if min(rw, rh) < 1:
            continue
        aspect = min(rw, rh) / max(rw, rh)
        fill = cv2.contourArea(hull) / (rw * rh)
        if not 0.6 <= aspect <= 0.85 or fill < 0.85:
            continue
        score = rw * rh * fill * np.exp(-((aspect - CARD_ASPECT) / 0.08) ** 2)
        if score > best_score:
            best, best_score = rect, score
    return best


def top_left_first(pts):
    order = np.argsort(pts[:, 1])
    top = pts[order[:2]][np.argsort(pts[order[:2], 0])]
    bottom = pts[order[2:]][np.argsort(pts[order[2:], 0])]
    return np.array([top[0], top[1], bottom[1], bottom[0]], np.float64)


def refine(frame, rect, pad):
    """Card corners in full-frame pixels from the edge fitter on an upright crop around `rect` (full-frame
    pixels), or the rect itself flagged rough when the fitter finds nothing that agrees with it."""
    (cx, cy), (rw, rh), angle = rect
    if rw > rh:
        rw, rh, angle = rh, rw, angle - 90
    cw = 300
    ch = round(cw * rh / rw)
    src = portrait(top_left_first(cv2.boxPoints(((cx, cy), (rw * pad, rh * pad), angle)))).astype(np.float32)
    dst = np.float32([[0, 0], [cw - 1, 0], [cw - 1, ch - 1], [0, ch - 1]])
    m = cv2.getPerspectiveTransform(src, dst)
    crop = cv2.warpPerspective(frame, m, (cw, ch), flags=cv2.INTER_AREA)
    rough = top_left_first(cv2.boxPoints(((cx, cy), (rw * 1.04, rh * 1.04), angle)))
    # A Magic card's black border has an edge on both sides and either can win. The collector line is printed on
    # the border, so push out to the outer one, but only about a border's width: the detector's default reach
    # jumps on to the edge of the tray floor.
    quad = detect(crop, min_frac=0.7 / pad, expand_range=0.05)
    if quad is None:
        return rough, True
    # the crop is upright and about `pad` times the card, so anything else is a box inside the card
    width = (np.hypot(*(quad[1] - quad[0])) + np.hypot(*(quad[2] - quad[3]))) / 2
    height = (np.hypot(*(quad[3] - quad[0])) + np.hypot(*(quad[2] - quad[1]))) / 2
    if width > height or not 0.8 <= width * pad / cw <= 1.25:
        return rough, True
    full = cv2.perspectiveTransform(quad.reshape(-1, 1, 2).astype(np.float32), np.linalg.inv(m)).reshape(4, 2)
    ratio = cv2.contourArea(full) / cv2.contourArea(rough.astype(np.float32))
    if not 0.75 <= ratio <= 1.3:
        return rough, True
    return full.astype(np.float64), False


def _changed_share(mask, quad, frame_w):
    poly = np.zeros(mask.shape, np.uint8)
    cv2.fillConvexPoly(poly, (quad * mask.shape[1] / frame_w).astype(np.int32), 1)
    return float(mask[poly > 0].mean()) if poly.any() else 0.0


def inner_card(frame, quad, mask):
    """A card lying inside `quad` that holds the change, or None. The tray floor is card-shaped too, and once its
    outline is taken for a card the size check keeps accepting it; the card that just landed is what changed,
    while most of the floor around it didn't. An inner outline that merely sits in a card (the art box) changed
    no more than the card around it."""
    x0, y0 = np.floor(quad.min(axis=0)).astype(int).clip(0)
    x1, y1 = np.ceil(quad.max(axis=0)).astype(int)
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    inner = detect(crop, min_frac=0.2, expand_range=0.05)
    if inner is None:
        return None
    inner = portrait(inner).astype(np.float64) + (x0, y0)
    if cv2.contourArea(inner.astype(np.float32)) > 0.6 * cv2.contourArea(quad.astype(np.float32)):
        return None
    w = frame.shape[1]
    return inner if _changed_share(mask, inner, w) >= _changed_share(mask, quad, w) + 0.15 else None


def has_content(card):
    """A card is full of edges (text, art, frame); an empty tray or table is nearly flat even under shadows."""
    g = cv2.cvtColor(cv2.resize(card, (315, 440), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return float((cv2.Canny(g, 60, 150)[44:396, 31:284] > 0).mean()) > 0.03


def _short_side(quad):
    return min(np.hypot(*(quad[1] - quad[0])), np.hypot(*(quad[3] - quad[0])))


class Watcher:
    def __init__(self, still_frames=2, move_frac=0.03):
        self.still_frames = still_frames
        self.move_frac = move_frac
        self.prev = None
        self.still = 0
        self.ref = None
        self.armed = False
        self.tries = 0
        self.last_rect = None
        # short sides of recent clean outlines; the card's size in the picture barely changes as the pile grows
        self.sizes = deque(maxlen=9)
        # first clean outline of this still spell, as (quad, card, frame_no, small); confirmed by a matching one
        # on a later frame, or kept anyway if the next card arrives first
        self.pending = None
        self.frame_no = -1
        self.frame_w = 1

    def _fits(self, quad):
        if len(self.sizes) < 3:
            return True
        return 0.85 <= _short_side(quad) / float(np.median(self.sizes)) <= 1.18

    def _motion(self, g):
        """Share of moving pixels around the pile, so a hand reaching in at the edge of the picture for the
        next card doesn't hold back this one."""
        moved = cv2.absdiff(g, self.prev) > MOTION_DIFF
        if self.last_rect is None:
            return float(moved.mean())
        (cx, cy), (rw, rh), a = self.last_rect
        k = g.shape[1] / self.frame_w
        roi = np.zeros(g.shape, np.uint8)
        cv2.fillConvexPoly(roi, cv2.boxPoints(((cx * k, cy * k), (rw * k * 1.4, rh * k * 1.4), a)).astype(np.int32), 1)
        return float(moved[roi > 0].mean()) if roi.any() else float(moved.mean())

    def _commit(self, quad, card, frame_no, small, rough, frame=None):
        self.ref, self.armed, self.pending = small, False, None
        if not rough:
            self.sizes.append(_short_side(quad))
        self.last_rect = cv2.minAreaRect(quad.astype(np.float32))
        return Capture(frame_no, card, quad, rough, frame if rough else None)

    def feed(self, frame):
        self.frame_w = frame.shape[1]
        self.frame_no += 1
        small = _small(frame)
        g = _gray(small)
        moving = self.prev is not None and self._motion(g) > self.move_frac
        self.prev = g
        if moving:
            capture = self._commit(*self.pending, rough=False) if self.armed and self.pending else None
            self.still = 0
            self.tries = 0
            self.armed = self.ref is not None
            return Status(moving=True, capture=capture)
        self.still += 1
        if self.still < self.still_frames:
            return Status(moving=False)
        if self.ref is None:
            self.ref = small
            return Status(moving=False)
        if not self.armed:
            return Status(moving=False)

        mask = changed(self.ref, small)
        if mask.mean() < MIN_CHANGE:
            self.armed = False
            return Status(moving=False)
        scale = frame.shape[1] / WORK
        candidates = []
        blob = card_blob(mask)
        if blob is not None:
            (bx, by), (bw, bh), ba = blob
            candidates.append((((bx * scale, by * scale), (bw * scale, bh * scale), ba), 1.3, True))
        if self.last_rect is not None:
            candidates.append((self.last_rect, 1.5, True))
        elif blob is None and (region := largest_change(mask)) is not None:
            # First card, but the change isn't card-shaped (a moved shadow attached): let the edge fitter look
            # inside the changed region. Only a clean outline counts; a rough guess here would be the hand or the
            # shadow, and the next cards would be searched for where it was.
            (bx, by), (bw, bh), ba = region
            candidates.append((((bx * scale, by * scale), (bw * scale, bh * scale), ba), 1.1, False))
        if not candidates:
            return Status(moving=False)

        self.tries += 1
        clean, unfit, rough = [], [], []
        for rect, pad, rough_ok in candidates:
            quad, is_rough = refine(frame, rect, pad)
            if not rough_ok and (is_rough or _changed_share(mask, quad, frame.shape[1]) < 0.6):
                # a fallback outline that's mostly unchanged is the tray's pocket around the card, not the card
                continue
            if not is_rough:
                inner = inner_card(frame, quad, mask)
                if inner is not None:
                    quad = inner
            card = warp(frame, quad)
            if not has_content(card):
                continue
            if is_rough:
                rough.append((quad, card))
            elif self._fits(quad):
                clean.append((quad, card))
                break
            else:
                unfit.append((quad, card))
        if clean:
            quad, card = clean[0]
            if self.pending is None or np.abs(quad - self.pending[0]).max() > 0.015 * _short_side(quad):
                self.pending = (quad, card, self.frame_no, small)
                return Status(moving=False, quad=quad)
            return Status(moving=False, quad=quad, capture=self._commit(quad, card, self.frame_no, small, False))
        if self.tries < ROUGH_AFTER:
            return Status(moving=False)
        # after a few tries a clean outline of a new size wins over a rough one: the camera may have moved
        if unfit:
            return Status(moving=False, capture=self._commit(*unfit[0], self.frame_no, small, False))
        if rough:
            return Status(moving=False, capture=self._commit(*rough[0], self.frame_no, small, True, frame))
        self.ref, self.armed = small, False
        return Status(moving=False)
