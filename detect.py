"""Card outline detection, ported from the old Android app's CardDetector.kt (in git history).

Hough lines on colour gradients, then the best-supported quadrilateral of two near-vertical and two
near-horizontal lines with a card's proportions (portrait or landscape), refined by fitting each side to
the edge pixels along it. Pixel constants were tuned at 240 px wide there and are scaled by
`width / 240` here.
"""

import math

import cv2
import numpy as np
from numba import njit

CARD_ASPECT = 63 / 88
THETAS = 180
PEAKS = 25
MIN_SUPPORT = 0.5
# a side this well supported counts as fully supported, so the outer card edge beats inner frame lines
SATURATE = 0.85
ASPECT_SIGMA = 0.05
MAX_SKEW_DEG = 12
# how far past a side to look for the real card edge, as a fraction of the card's size across that side
EXPAND_RANGE = 0.12
EXPAND_CONSISTENCY = 0.6
EXPAND_TILT = 0.1
BEYOND_EDGE_MAX = 0.4


def gradients(img):
    """Per channel Sobel on a 3x3 box blur, keeping the strongest channel: a border can match the
    background in brightness but not in colour."""
    h, w = img.shape[:2]
    best = np.zeros((h, w), np.float32)
    gx = np.zeros((h, w), np.float32)
    gy = np.zeros((h, w), np.float32)
    for c in range(3):
        ch = cv2.blur(img[:, :, c].astype(np.float32), (3, 3), borderType=cv2.BORDER_REPLICATE)
        cx = cv2.Sobel(ch, cv2.CV_32F, 1, 0, ksize=3)
        cy = cv2.Sobel(ch, cv2.CV_32F, 0, 1, ksize=3)
        m = cx * cx + cy * cy
        take = m > best
        best[take] = m[take]
        gx[take] = cx[take]
        gy[take] = cy[take]
    for a in (gx, gy, best):
        a[0, :] = a[-1, :] = a[:, 0] = a[:, -1] = 0
    return gx, gy, np.sqrt(best)


@njit(cache=True)
def _across(gx, gy, mag, x, y, nx, ny):
    """Gradient across the edge at (x, y), counted only when the gradient actually points across it."""
    h, w = mag.shape
    if x <= 0 or y <= 0 or x >= w - 1 or y >= h - 1:
        return 0.0
    a = abs(gx[y, x] * nx + gy[y, x] * ny)
    return a if a > 0.8 * mag[y, x] else 0.0


@njit(cache=True)
def _hough(gx, gy, mag, strong, diag):
    """Votes only near each pixel's own gradient direction, which keeps texture from flooding it."""
    h, w = mag.shape
    acc = np.zeros((THETAS, 2 * diag + 1), np.float32)
    cos_t = np.cos(np.arange(THETAS) * math.pi / 180)
    sin_t = np.sin(np.arange(THETAS) * math.pi / 180)
    for y in range(h):
        for x in range(w):
            m = mag[y, x]
            if m <= strong:
                continue
            phi = math.atan2(gy[y, x], gx[y, x]) * 180 / math.pi
            phi = ((phi % 180) + 180) % 180
            t0 = int(round(phi))
            for d in range(-1, 2):
                t = ((t0 + d) % THETAS + THETAS) % THETAS
                rho = int(round(x * cos_t[t] + y * sin_t[t])) + diag
                acc[t, rho] += m * (1.0 if d == 0 else 0.5)
    return acc


@njit(cache=True)
def _peaks(acc, diag, thetas, nms_rho):
    a = np.zeros_like(acc)
    for t in thetas:
        a[t] = acc[t]
    out_t = np.empty(PEAKS, np.int64)
    out_r = np.empty(PEAKS, np.float64)
    n = 0
    for _ in range(PEAKS):
        bt = -1
        br = -1
        bv = 0.0
        for t in thetas:
            for r in range(a.shape[1]):
                if a[t, r] > bv:
                    bv = a[t, r]
                    bt = t
                    br = r
        if bt < 0:
            break
        out_t[n] = bt
        out_r[n] = br - diag
        n += 1
        for dt in range(-3, 4):
            raw = bt + dt
            t = (raw % THETAS + THETAS) % THETAS
            # crossing 0/180 degrees flips the sign of rho
            r = br if 0 <= raw < THETAS else 2 * diag - br
            for rr in range(max(0, r - nms_rho), min(a.shape[1] - 1, r + nms_rho) + 1):
                a[t, rr] = 0
    return out_t[:n], out_r[:n]


@njit(cache=True)
def _intersect(t1d, r1, t2d, r2):
    t1 = t1d * math.pi / 180
    t2 = t2d * math.pi / 180
    det = math.cos(t1) * math.sin(t2) - math.sin(t1) * math.cos(t2)
    if abs(det) < 1e-6:
        return False, 0.0, 0.0
    x = (r1 * math.sin(t2) - r2 * math.sin(t1)) / det
    y = (math.cos(t1) * r2 - math.cos(t2) * r1) / det
    return True, x, y


@njit(cache=True)
def _dist(q, a, b):
    return math.hypot(q[a, 0] - q[b, 0], q[a, 1] - q[b, 1])


@njit(cache=True)
def _support(gx, gy, mag, ref, q, a, b, f0, f1, n):
    """Mean edge strength (0..1) across the segment from corner a to b, sampled between f0 and f1."""
    x0 = q[a, 0]
    y0 = q[a, 1]
    dx = q[b, 0] - x0
    dy = q[b, 1] - y0
    ln = math.hypot(dx, dy)
    if ln < 1:
        return 0.0
    nx = -dy / ln
    ny = dx / ln
    total = 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * (i + 0.5) / n
        x = x0 + dx * f
        y = y0 + dy * f
        best = 0.0
        for o in range(-1, 2):
            v = _across(gx, gy, mag, int(round(x + nx * o)), int(round(y + ny * o)), nx, ny)
            best = max(best, min(1.0, v / ref) * (1 - 0.25 * abs(o)))
        total += best
    return total / n


@njit(cache=True)
def _search(gx, gy, mag, ref, vt, vr, ht, hr, min_frac):
    h, w = mag.shape
    best = np.zeros((4, 2))
    best_score = 0.0
    q = np.zeros((4, 2))
    s = np.zeros(4)
    lengths = np.zeros(4)
    mx = 0.04 * w
    my = 0.04 * h
    nv = vt.shape[0]
    nh = ht.shape[0]
    for i in range(nv):
        for j in range(i + 1, nv):
            if vr[i] < vr[j]:
                lt, lr, rt, rr = vt[i], vr[i], vt[j], vr[j]
            else:
                lt, lr, rt, rr = vt[j], vr[j], vt[i], vr[i]
            if abs(lt - rt) > MAX_SKEW_DEG or rr - lr < min_frac * w:
                continue
            for k in range(nh):
                for m in range(k + 1, nh):
                    if hr[k] < hr[m]:
                        tt, tr, bt, br = ht[k], hr[k], ht[m], hr[m]
                    else:
                        tt, tr, bt, br = ht[m], hr[m], ht[k], hr[k]
                    if abs(tt - bt) > MAX_SKEW_DEG or br - tr < min_frac * h:
                        continue
                    ok0, q[0, 0], q[0, 1] = _intersect(tt, tr, lt, lr)
                    ok1, q[1, 0], q[1, 1] = _intersect(tt, tr, rt, rr)
                    ok2, q[2, 0], q[2, 1] = _intersect(bt, br, rt, rr)
                    ok3, q[3, 0], q[3, 1] = _intersect(bt, br, lt, lr)
                    if not (ok0 and ok1 and ok2 and ok3):
                        continue
                    inside = True
                    for c in range(4):
                        if q[c, 0] < -mx or q[c, 0] > w + mx or q[c, 1] < -my or q[c, 1] > h + my:
                            inside = False
                    if not inside:
                        continue
                    top = _dist(q, 0, 1)
                    right = _dist(q, 1, 2)
                    bottom = _dist(q, 2, 3)
                    left = _dist(q, 3, 0)
                    if not (0.8 <= top / bottom <= 1.25 and 0.8 <= left / right <= 1.25):
                        continue
                    aspect = (top + bottom) / (left + right)
                    if aspect > 1:
                        aspect = 1 / aspect
                    if not (0.6 <= aspect <= 0.85):
                        continue
                    for e in range(4):
                        s[e] = _support(gx, gy, mag, ref, q, e, (e + 1) % 4, 0.1, 0.9, 32)
                    if s.min() < MIN_SUPPORT:
                        continue
                    # Lines running on past a corner mean the quad borrows sides from something else.
                    overrun = 0.0
                    for e in range(4):
                        overrun += _support(gx, gy, mag, ref, q, e, (e + 1) % 4, -0.08, -0.01, 8)
                        overrun += _support(gx, gy, mag, ref, q, e, (e + 1) % 4, 1.01, 1.08, 8)
                    overrun /= 8
                    lengths[0] = top
                    lengths[1] = right
                    lengths[2] = bottom
                    lengths[3] = left
                    score = 0.0
                    for e in range(4):
                        score += min(1.0, s[e] / SATURATE) * lengths[e]
                    da = (aspect - CARD_ASPECT) / ASPECT_SIGMA
                    score *= math.exp(-da * da) * (1 - 0.6 * overrun)
                    if score > best_score:
                        best_score = score
                        best[:] = q
    return best, best_score


@njit(cache=True)
def _centre(q):
    return (q[0, 0] + q[1, 0] + q[2, 0] + q[3, 0]) / 4, (q[0, 1] + q[1, 1] + q[2, 1] + q[3, 1]) / 4


@njit(cache=True)
def _continuity(gx, gy, mag, x0, y0, x1, y1, edge_min):
    """Fraction of points along the segment that sit on an edge running parallel to it."""
    dx = x1 - x0
    dy = y1 - y0
    ln = max(1e-3, math.hypot(dx, dy))
    nx = -dy / ln
    ny = dx / ln
    n = 32
    hits = 0
    for i in range(n):
        f = 0.1 + 0.8 * (i + 0.5) / n
        x = x0 + dx * f
        y = y0 + dy * f
        for o in range(-2, 3):
            if _across(gx, gy, mag, int(round(x + nx * o)), int(round(y + ny * o)), nx, ny) >= edge_min:
                hits += 1
                break
    return hits / n


@njit(cache=True)
def _fit_line(xs, ys, keep):
    n = 0
    cx = 0.0
    cy = 0.0
    for i in range(keep.shape[0]):
        if keep[i]:
            cx += xs[i]
            cy += ys[i]
            n += 1
    cx /= n
    cy /= n
    sxx = 0.0
    syy = 0.0
    sxy = 0.0
    for i in range(keep.shape[0]):
        if keep[i]:
            ex = xs[i] - cx
            ey = ys[i] - cy
            sxx += ex * ex
            syy += ey * ey
            sxy += ex * ey
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return np.array([cx, cy, math.cos(angle), math.sin(angle)])


@njit(cache=True)
def _fit_side(gx, gy, mag, q, a, b, edge_min, outermost, reach):
    """Total-least-squares line through the edge points along one side, as (cx, cy, dx, dy);
    an empty array when the side has too few edge points."""
    x0 = q[a, 0]
    y0 = q[a, 1]
    dx = q[b, 0] - x0
    dy = q[b, 1] - y0
    ln = math.hypot(dx, dy)
    if ln < 1:
        return np.empty(0)
    nx = -dy / ln
    ny = dx / ln
    cx, cy = _centre(q)
    if (x0 + dx / 2 - cx) * nx + (y0 + dy / 2 - cy) * ny < 0:
        nx = -nx
        ny = -ny
    n = 40
    xs = np.zeros(n)
    ys = np.zeros(n)
    count = 0
    # vals runs from 4 px inside the side to `reach` px outside it
    vals = np.zeros(reach + 5)
    last = vals.shape[0] - 1
    for i in range(n):
        f = 0.08 + 0.84 * (i + 0.5) / n
        x = x0 + dx * f
        y = y0 + dy * f
        bo = -1
        for k in range(last + 1):
            o = k - 4
            vals[k] = _across(gx, gy, mag, int(round(x + nx * o)), int(round(y + ny * o)), nx, ny)
            if bo < 0 or vals[k] > vals[bo]:
                bo = k
        if outermost:
            for k in range(last, -1, -1):
                if vals[k] >= edge_min and (k == 0 or vals[k] >= vals[k - 1]):
                    bo = k
                    break
        if vals[bo] < edge_min:
            continue
        off = float(bo - 4)
        if 1 <= bo < last:
            den = vals[bo - 1] - 2 * vals[bo] + vals[bo + 1]
            if den < 0:
                off += 0.5 * (vals[bo - 1] - vals[bo + 1]) / den
        xs[count] = x + nx * off
        ys[count] = y + ny * off
        count += 1
    if count < n * 0.4:
        return np.empty(0)
    xs = xs[:count]
    ys = ys[:count]
    keep = np.ones(count, np.bool_)
    line = _fit_line(xs, ys, keep)
    for _ in range(3):
        res = np.abs((xs - line[0]) * -line[3] + (ys - line[1]) * line[2])
        median = np.sort(res)[count // 2]
        limit = max(1.0, 2.5 * median)
        nxt = res < limit
        if nxt.sum() < n * 0.3:
            return line
        keep = nxt
        line = _fit_line(xs, ys, keep)
    return line


@njit(cache=True)
def _corners(sides):
    out = np.zeros((4, 2))
    for c in range(4):
        p = sides[(c + 3) % 4]
        r = sides[c]
        det = p[2] * r[3] - p[3] * r[2]
        if abs(det) < 1e-6:
            return out, False
        t = ((r[0] - p[0]) * r[3] - (r[1] - p[1]) * r[2]) / det
        out[c, 0] = p[0] + p[2] * t
        out[c, 1] = p[1] + p[3] * t
    return out, True


@njit(cache=True)
def _expand(gx, gy, mag, q, edge_min, beyond0, beyond1, expand_range):
    """A silver or white border on a light background is a much weaker edge than the art box inside it,
    so the search can settle on the art box. Push each side out to the outermost parallel edge that runs
    its whole length; strength doesn't matter here, only that the edge is continuous."""
    h, w = mag.shape
    cx, cy = _centre(q)
    sides = np.zeros((4, 4))
    moved = np.zeros(4, np.bool_)
    for a in range(4):
        b = (a + 1) % 4
        sides[a, 0] = q[a, 0]
        sides[a, 1] = q[a, 1]
        sides[a, 2] = q[b, 0] - q[a, 0]
        sides[a, 3] = q[b, 1] - q[a, 1]
        ln = _dist(q, a, b)
        far = int(round(expand_range * _dist(q, b, (a + 2) % 4)))
        line = _fit_side(gx, gy, mag, q, a, b, edge_min, True, far)
        if line.shape[0] == 0:
            continue
        ox = -line[3]
        oy = line[2]
        if (line[0] - cx) * ox + (line[1] - cy) * oy < 0:
            ox = -ox
            oy = -oy
        shift_a = (line[0] - q[a, 0]) * ox + (line[1] - q[a, 1]) * oy
        shift_b = (line[0] - q[b, 0]) * ox + (line[1] - q[b, 1]) * oy
        if (shift_a + shift_b) / 2 < 1.5 or abs(shift_a - shift_b) > EXPAND_TILT * ln + 1:
            continue
        ax = q[a, 0] + ox * shift_a
        ay = q[a, 1] + oy * shift_a
        bx = q[b, 0] + ox * shift_b
        by = q[b, 1] + oy * shift_b
        if _continuity(gx, gy, mag, ax, ay, bx, by, edge_min) < EXPAND_CONSISTENCY:
            continue
        # Textured backgrounds are full of parallel edges; only trust an edge with plain background behind it.
        reach = beyond1 + 2
        if min(ax + ox * reach, bx + ox * reach) < 1 or max(ax + ox * reach, bx + ox * reach) > w - 2:
            continue
        if min(ay + oy * reach, by + oy * reach) < 1 or max(ay + oy * reach, by + oy * reach) > h - 2:
            continue
        busy = False
        for k in range(beyond0, beyond1 + 1):
            if _continuity(gx, gy, mag, ax + ox * k, ay + oy * k, bx + ox * k, by + oy * k, edge_min) >= BEYOND_EDGE_MAX:
                busy = True
                break
        if busy:
            continue
        sides[a, 0] = ax
        sides[a, 1] = ay
        sides[a, 2] = bx - ax
        sides[a, 3] = by - ay
        moved[a] = True
    if not moved.any():
        return q.copy(), moved
    out, ok = _corners(sides)
    if not ok:
        return q.copy(), np.zeros(4, np.bool_)
    return out, moved


@njit(cache=True)
def _refine(gx, gy, mag, q, edge_min, weak_min, outer, max_move):
    """Snap each side to the strongest edge within a few pixels and refit it as a line. Sides `_expand`
    moved out snap to the outermost edge instead, or they'd fall back onto the art box."""
    sides = np.zeros((4, 4))
    for a in range(4):
        line = _fit_side(gx, gy, mag, q, a, (a + 1) % 4, weak_min if outer[a] else edge_min, outer[a], 4)
        if line.shape[0] == 0:
            return q, False
        sides[a] = line
    out, ok = _corners(sides)
    if not ok:
        return q, False
    for c in range(4):
        if math.hypot(out[c, 0] - q[c, 0], out[c, 1] - q[c, 1]) > max_move:
            return q, False
    return out, True


def detect(img, min_frac=0.3, expand_range=EXPAND_RANGE):
    """Card outline in a small BGR image as TL, TR, BR, BL corners (4x2), or None.

    `min_frac` is the smallest card size accepted, as a fraction of the image's width and height; `expand_range` is
    how far (as a fraction of the card) a side may be pushed out to a fainter outer edge."""
    h, w = img.shape[:2]
    if w < 16 or h < 16:
        return None
    k = w / 240
    gx, gy, mag = gradients(img)
    flat = np.sort(mag, axis=None)
    strong = flat[int(flat.size * 0.85)]
    ref = max(1.0, float(flat[int(flat.size * 0.97)]))
    edge_min = max(strong * 0.5, 12.0)

    diag = int(math.hypot(w, h)) + 1
    acc = _hough(gx, gy, mag, strong, diag)
    nms_rho = max(4, round(4 * k))
    vt, vr = _peaks(acc, diag, np.array(list(range(0, 26)) + list(range(155, 180))), nms_rho)
    flip = vt >= 155
    vt = np.where(flip, vt - 180, vt)
    vr = np.where(flip, -vr, vr)
    ht, hr = _peaks(acc, diag, np.arange(65, 116), nms_rho)

    best, score = _search(gx, gy, mag, ref, vt, vr, ht, hr, min_frac)
    if score <= 0:
        return None
    weak_min = max(edge_min * 0.35, 8.0)
    quad, moved = _expand(gx, gy, mag, best, weak_min, round(6 * k), round(11 * k), expand_range)
    for _ in range(2):
        quad, _ok = _refine(gx, gy, mag, quad, edge_min, weak_min, moved, 8 * k)
    return quad


def portrait(quad):
    """Corners reordered so the warp comes out portrait (which way up is left to matching)."""
    top = np.hypot(*(quad[1] - quad[0]))
    left = np.hypot(*(quad[3] - quad[0]))
    if top > left:
        return quad[[3, 0, 1, 2]]
    return quad


def warp(frame, quad, width=630):
    height = round(width * 88 / 63)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32)
    m = cv2.getPerspectiveTransform(portrait(quad).astype(np.float32), dst)
    return cv2.warpPerspective(frame, m, (width, height), flags=cv2.INTER_AREA)
