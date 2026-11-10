/** The card popup's 3D card: tilts toward the pointer, spins when dragged (showing the other face or the game's card
 *  back), and lights a foil layer for the finish. Ported to fakehams' site/collections/cards.js (holoCard); keep the
 *  two in step. */

export type Fx = "" | "foil" | "etched" | "holo" | "reverse" | "full";

// Pokémon printings with the holo across the whole card rather than in the art window
const FULL_ART =
  /ultra|double|illustration|secret|hyper|shiny|special|\bv(max|star)?\b|triple|character|radiant|legend|amazing|full art|black white|futuristic|rgb|gold|rainbow/i;

/** Which foil layer a finish gets. Pokémon finish keys start with normal/holo/reverse ("reverse:pokeball"). */
export function effectFor(game: string, finish: string | null | undefined, rarity: string | null | undefined): Fx {
  const base = (finish ?? "").split(":")[0];
  if (game === "mtg") return base === "foil" ? "foil" : base === "etched" ? "etched" : "";
  if (game === "lor") return base === "foil" ? "foil" : "";
  if (base === "reverse") return "reverse";
  if (base === "holo") return FULL_ART.test(rarity ?? "") ? "full" : "holo";
  return "";
}

/** The back of a one-faced card, a file under the page's backs/ folder. */
export function backFile(game: string, lang: string) {
  if (game === "pkm") return lang === "ja" ? "pkj.webp" : "pkm.webp";
  return game === "lor" ? "lor.webp" : "mtg.webp";
}

/** A Pokémon card's art window as a CSS mask position and size, measured off real cards: Wizards-era cards have a
 *  smaller, lower window than everything since EX Ruby & Sapphire (2003). */
export function artWindow(released: string | null | undefined) {
  const [x, y, w, h] = (released ?? "9999") < "2003-07" ? [9.5, 10.5, 81, 42.5] : [7.5, 8.5, 85, 40];
  // a mask-position percentage places the box's own edge at that fraction of the leftover space
  return `${(x / (100 - w)) * 100}% ${(y / (100 - h)) * 100}% / ${w}% ${h}%`;
}

const TILT = 35;
const DRAG = 0.55;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const rad = (deg: number) => (deg * Math.PI) / 180;

/** Wires the tilt, spin and light to a `.holo` element. Returns the controls and the cleanup. */
export function attachHolo(el: HTMLElement) {
  const tilt = el.querySelector<HTMLElement>(".holo-tilt")!;
  // Firefox doesn't decode an image while it's hidden, so the far face would turn into view blank
  const decodeFaces = () => el.querySelectorAll("img").forEach((img) => img.decode().catch(() => {}));
  // rotation now, where it's heading, and how fast; y includes the turns
  const at = { x: 0, y: 0, vx: 0, vy: 0 };
  const to = { x: 0, y: 0 };
  let base = 0;
  let drag: { id: number; x: number; y: number; from: number; last: number; t: number; speed: number } | null = null;
  let frame = 0;
  let last = 0;
  let showingBack = false;

  const step = (now: number) => {
    frame = 0;
    const dt = Math.min(3, (now - (last || now)) / 16.67 || 1);
    last = now;
    // dragging tracks the finger tightly; letting go and the flip spring with a little overshoot
    const k = drag ? 0.35 : 0.075;
    const damp = drag ? 0.55 : 0.8;
    for (const axis of ["x", "y"] as const) {
      const v = axis === "x" ? "vx" : "vy";
      at[v] = (at[v] + (to[axis] - at[axis]) * k * dt) * damp ** dt;
      at[axis] += at[v] * dt;
    }
    // lifts toward the viewer while it's turning, more the further it still has to go
    const travel = Math.min(1, Math.abs(to.y - at.y) / 90);
    tilt.style.transform =
      `translateZ(${(travel * 50).toFixed(1)}px) scale(${(1 + travel * 0.05 + (el.matches(":hover") || drag ? 0.03 : 0)).toFixed(3)}) ` +
      `rotateY(${at.y.toFixed(2)}deg) rotateX(${at.x.toFixed(2)}deg)`;
    // the light comes from the card's angle, mirrored on the back face
    const sy = Math.sin(rad(at.y));
    const sx = Math.sin(rad(at.x));
    const mx = clamp(50 + sy * 110, 0, 100);
    const my = clamp(50 - sx * 110, 0, 100);
    el.style.setProperty("--mx", `${mx.toFixed(1)}%`);
    el.style.setProperty("--mxb", `${(100 - mx).toFixed(1)}%`);
    el.style.setProperty("--my", `${my.toFixed(1)}%`);
    el.style.setProperty("--o", clamp(Math.hypot(sy, sx) * 3.5, 0, 1).toFixed(3));
    // past edge-on the face shows the back image (see .holo.turned in the CSS)
    const back = Math.cos(rad(at.y)) * Math.cos(rad(at.x)) < 0;
    if (back !== showingBack) el.classList.toggle("turned", (showingBack = back));
    const moving = Math.abs(to.x - at.x) + Math.abs(to.y - at.y) + Math.abs(at.vx) + Math.abs(at.vy) > 0.02;
    if (moving || drag) frame = requestAnimationFrame(step);
    else last = 0;
  };
  const run = () => {
    if (!frame) frame = requestAnimationFrame(step);
  };

  const aim = (e: PointerEvent) => {
    const r = el.getBoundingClientRect();
    const px = clamp((e.clientX - r.left) / r.width, 0, 1);
    const py = clamp((e.clientY - r.top) / r.height, 0, 1);
    to.x = (0.5 - py) * TILT;
    to.y = base + (px - 0.5) * TILT;
  };

  const down = (e: PointerEvent) => {
    if ((e.target as HTMLElement).closest("button")) return;
    el.setPointerCapture(e.pointerId);
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, from: at.y, last: e.clientX, t: performance.now(), speed: 0 };
    el.classList.add("dragging");
    run();
  };
  const move = (e: PointerEvent) => {
    if (drag && e.pointerId === drag.id) {
      const now = performance.now();
      drag.speed = ((e.clientX - drag.last) / Math.max(1, now - drag.t)) * 16.67;
      drag.last = e.clientX;
      drag.t = now;
      to.y = drag.from + (e.clientX - drag.x) * DRAG;
      to.x = clamp(-(e.clientY - drag.y) * DRAG * 0.5, -TILT, TILT);
    } else if (e.pointerType === "mouse") aim(e);
    run();
  };
  const up = (e: PointerEvent) => {
    if (!drag || e.pointerId !== drag.id) return;
    // settle on whichever face it's nearest to, carried on by a flick
    // a flick carries it on, but not if the pointer stopped before letting go
    const flick = performance.now() - drag.t < 80 ? drag.speed : 0;
    const fling = clamp(flick * DRAG * 5, -70, 70);
    base = Math.round((to.y + fling) / 180) * 180;
    drag = null;
    el.classList.remove("dragging");
    to.x = 0;
    to.y = base;
    run();
  };
  const leave = () => {
    if (drag) return;
    to.x = 0;
    to.y = base;
    run();
  };
  const on: [string, (e: PointerEvent) => void][] = [
    ["pointerdown", down],
    ["pointermove", move],
    ["pointerup", up],
    ["pointercancel", up],
    ["pointerleave", leave],
    // otherwise the browser drags the image out instead of the card turning
    ["dragstart", (e) => e.preventDefault()],
  ];
  on.forEach(([type, fn]) => el.addEventListener(type, fn as EventListener));
  step(performance.now());
  decodeFaces();

  return {
    turnOver() {
      base += 180;
      to.y = base;
      to.x = 0;
      run();
    },
    reset() {
      base = to.x = to.y = at.x = at.y = at.vx = at.vy = 0;
      decodeFaces();
      step(performance.now());
    },
    destroy() {
      cancelAnimationFrame(frame);
      on.forEach(([type, fn]) => el.removeEventListener(type, fn as EventListener));
    },
  };
}
