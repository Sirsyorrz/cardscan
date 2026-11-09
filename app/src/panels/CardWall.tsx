import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { cardLine, money, url, type Item } from "../api";

export const ZOOM_MIN = 48;
export const ZOOM_MAX = 320;
export const clampZoom = (px: number) => Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, px)));

const GAP = 8;
const RATIO = 88 / 63;
/** Hovered cards grow to about this many pixels wide, however far out the wall is zoomed. */
const HOVER_WIDTH = 300;
const EDGE_SCALE = 0.55;

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const smooth = (t: number) => t * t * (3 - 2 * t);

function WallImage({ item, large }: { item: Item; large: boolean }) {
  const capture = item.capture_id ? url(`/capture/${item.capture_id}`) : null;
  const [src, setSrc] = useState((large ? item.img_large : item.img) ?? capture);
  if (!src) return <div className="noimg" />;
  return (
    <img
      loading="lazy"
      draggable={false}
      src={src}
      alt=""
      onError={(e) => (capture && src !== capture ? setSrc(capture) : (e.currentTarget.style.visibility = "hidden"))}
    />
  );
}

/** Swapped in over the small image once it has loaded, so a magnified card isn't a blurry upscale. */
function HiRes({ src }: { src: string }) {
  const [loaded, setLoaded] = useState(false);
  return (
    <img
      className={`hires${loaded ? " loaded" : ""}`}
      src={src}
      alt=""
      draggable={false}
      onLoad={() => setLoaded(true)}
    />
  );
}

const WallCard = memo(function WallCard({
  item,
  cell,
  hires,
  onOpen,
}: {
  item: Item;
  cell: number;
  hires: boolean;
  onOpen: (cardId: string) => void;
}) {
  const large = cell > 200;
  return (
    <div className="wall-card" style={{ width: cell, height: cell * RATIO }} onClick={() => onOpen(item.card_id)}>
      <WallImage key={large ? "l" : "s"} item={item} large={large} />
      {hires && !large && item.img_large && <HiRes src={item.img_large} />}
      {item.qty > 1 && cell >= 64 && <span className="wall-qty">×{item.qty}</span>}
    </div>
  );
});

type Props = {
  items: Item[];
  rate: number | null;
  cell: number;
  onZoom: (update: (cell: number) => number) => void;
  onOpen: (cardId: string) => void;
};

/**
 * Every card as a wall of art, Apple Watch home screen style: cards shrink toward the top and bottom edges, and the
 * ones around the pointer swell and push their neighbours aside. Ctrl+wheel zooms around the pointer.
 */
export default function CardWall({ items, rate, cell, onZoom, onOpen }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  /** Card centres in grid coordinates, untransformed. Grid order means y never decreases. */
  const centers = useRef<{ x: number; y: number }[]>([]);
  const pointer = useRef<{ x: number; y: number } | null>(null);
  const touched = useRef(new Set<number>());
  const frame = useRef(0);
  const shownCell = useRef(cell);
  const [hover, setHover] = useState<number | null>(null);

  const measure = () => {
    const kids = grid.current?.children as HTMLCollectionOf<HTMLElement> | undefined;
    centers.current = kids
      ? Array.from(kids, (el) => ({ x: el.offsetLeft + el.offsetWidth / 2, y: el.offsetTop + el.offsetHeight / 2 }))
      : [];
  };

  /** The visible strip of the grid and the pointer, both in grid coordinates. */
  const viewport = () => {
    const s = scroller.current!;
    const sr = s.getBoundingClientRect();
    const gr = grid.current!.getBoundingClientRect();
    const top = sr.top - gr.top;
    const left = sr.left - gr.left;
    const p = pointer.current;
    return {
      top,
      bottom: top + s.clientHeight,
      left,
      right: left + s.clientWidth,
      pointer: p && { x: p.x - gr.left, y: p.y - gr.top },
    };
  };

  const nearest = (x: number, y: number) => {
    let best = -1;
    let bestD = Infinity;
    centers.current.forEach((c, i) => {
      const d = (c.x - x) ** 2 + (c.y - y) ** 2;
      if (d < bestD) [best, bestD] = [i, d];
    });
    return best;
  };

  const paint = () => {
    frame.current = 0;
    const g = grid.current;
    if (!scroller.current || !g) return;
    const { top, bottom, left, right, pointer: p } = viewport();
    const cs = centers.current;
    const h = cell * RATIO;
    const peak = Math.min(4, Math.max(1.35, HOVER_WIDTH / cell));
    const sigma = (cell + GAP) * 0.75;
    const band = Math.min(160, (bottom - top) * 0.25);
    const kids = g.children as HTMLCollectionOf<HTMLElement>;

    let lo = 0;
    let hi = cs.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (cs[m].y + h / 2 < top) lo = m + 1;
      else hi = m;
    }

    const seen = new Set<number>();
    let near: number | null = null;
    let nearD = Infinity;
    for (let i = lo; i < cs.length && cs[i].y - h / 2 <= bottom; i++) {
      const c = cs[i];
      const el = kids[i];
      if (!el) break;
      const edge = EDGE_SCALE + (1 - EDGE_SCALE) * smooth(clamp01(Math.min(c.y - top, bottom - c.y) / band));
      let scale = edge;
      let dx = 0;
      let dy = 0;
      let boost = 0;
      if (p) {
        const vx = c.x - p.x;
        const vy = c.y - p.y;
        const r2 = vx * vx + vy * vy;
        if (r2 < nearD) [near, nearD] = [i, r2];
        const w = Math.exp(-r2 / (2 * sigma * sigma));
        boost = (peak - 1) * w;
        // the lens overrides the edge shrink, so a hovered card at the edge still reads
        scale = (edge + (1 - edge) * w) * (1 + boost);
        dx = vx * boost * 0.4;
        dy = vy * boost * 0.4;
        if (boost > 0.05) {
          // keep the magnified card inside the viewport instead of clipping it
          const hw = (cell * scale) / 2 + 4;
          const hh = (h * scale) / 2 + 4;
          const fx = c.x + dx;
          const fy = c.y + dy;
          if (hw * 2 < right - left) dx += Math.max(0, left + hw - fx) - Math.max(0, fx + hw - right);
          if (hh * 2 < bottom - top) dy += Math.max(0, top + hh - fy) - Math.max(0, fy + hh - bottom);
        }
      }
      el.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) scale(${scale.toFixed(3)})`;
      el.style.zIndex = boost > 0.01 ? String(1 + Math.round(boost * 100)) : "";
      seen.add(i);
    }
    for (const i of touched.current) {
      if (seen.has(i) || !kids[i]) continue;
      kids[i].style.transform = "";
      kids[i].style.zIndex = "";
    }
    touched.current = seen;
    setHover(near);
  };

  // the resize and wheel listeners outlive renders, so they go through this to see the current cell size
  const paintRef = useRef(paint);
  paintRef.current = paint;
  const schedule = () => {
    if (!frame.current) frame.current = requestAnimationFrame(() => paintRef.current());
  };

  useLayoutEffect(() => {
    const s = scroller.current;
    if (s && shownCell.current !== cell && centers.current.length) {
      // centers still hold the old layout here: find the card under the pointer (or mid-view) before re-measuring,
      // then scroll so it stays put
      const v = viewport();
      const at = v.pointer ?? { x: (v.left + v.right) / 2, y: (v.top + v.bottom) / 2 };
      const i = nearest(at.x, at.y);
      const offset = i >= 0 ? centers.current[i].y - v.top : 0;
      measure();
      if (i >= 0 && centers.current[i]) s.scrollTop += centers.current[i].y - viewport().top - offset;
    } else {
      measure();
    }
    shownCell.current = cell;
    paint();
  }, [cell, items]);

  useEffect(() => {
    const s = scroller.current!;
    const ro = new ResizeObserver(() => {
      measure();
      schedule();
    });
    ro.observe(s);
    // React's wheel listener is passive, so it can't stop the webview's own ctrl+wheel zoom
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      onZoom((c) => clampZoom(c * Math.exp(-e.deltaY * 0.0015)));
    };
    s.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      ro.disconnect();
      s.removeEventListener("wheel", onWheel);
      cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [onZoom]);

  const hovered = hover != null ? items[hover] : null;
  const magnified = cell * Math.max(1.35, HOVER_WIDTH / cell) > 200;

  return (
    <div className="wall-wrap">
      <div
        ref={scroller}
        className="wall"
        onScroll={schedule}
        onPointerMove={(e) => {
          pointer.current = { x: e.clientX, y: e.clientY };
          schedule();
        }}
        onPointerLeave={() => {
          pointer.current = null;
          schedule();
        }}
      >
        <div ref={grid} className="wall-grid" style={{ gridTemplateColumns: `repeat(auto-fill, ${cell}px)`, gap: GAP }}>
          {items.map((r, i) => (
            <WallCard
              key={`${r.card_id}|${r.finish}|${r.condition}`}
              item={r}
              cell={cell}
              hires={i === hover && magnified}
              onOpen={onOpen}
            />
          ))}
        </div>
      </div>
      {hovered && (
        <div className="wall-caption">
          <b>{hovered.name}</b>
          <span className="dim">
            {" "}
            {hovered.set_name} {cardLine(hovered)}
          </span>
          {" · "}
          {hovered.finish_label}
          {hovered.condition !== "NM" && <span className="dim"> · {hovered.condition}</span>}
          {hovered.usd != null && <span className="accent"> · {money(hovered.usd, rate)}</span>}
          {hovered.qty > 1 && <span className="ok"> · ×{hovered.qty}</span>}
        </div>
      )}
    </div>
  );
}
