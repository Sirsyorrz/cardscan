import { useEffect, useState } from "react";

type Zoom = { i: number; x: number; y: number; scale: number } | null;

/** Full-height view of a capture next to the matched card. Click zooms in where you click, the mouse pans, the
 *  wheel changes the zoom, a second click or Esc steps back out. */
export default function Lightbox({ images, onClose }: { images: (string | null)[]; onClose: () => void }) {
  const [zoom, setZoom] = useState<Zoom>(null);

  useEffect(() => {
    // capture phase, so the review page's arrow keys and shortcuts don't fire underneath
    const onKey = (e: KeyboardEvent) => {
      e.stopImmediatePropagation();
      if (e.key !== "Escape") return;
      if (zoom) setZoom(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [zoom, onClose]);

  const at = (e: React.MouseEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 };
  };

  return (
    <div className="lightbox" onClick={onClose}>
      {images.map((src, i) =>
        src ? (
          <div
            key={i}
            className={`lb-frame${zoom?.i === i ? " zoomed" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setZoom(zoom?.i === i ? null : { i, ...at(e), scale: 3 });
            }}
            onMouseMove={(e) => zoom?.i === i && setZoom({ ...zoom, ...at(e) })}
            onWheel={(e) =>
              zoom?.i === i &&
              setZoom({ ...zoom, scale: Math.min(8, Math.max(1.5, zoom.scale * (e.deltaY < 0 ? 1.2 : 1 / 1.2))) })
            }
          >
            <img
              src={src}
              alt=""
              draggable={false}
              style={
                zoom?.i === i
                  ? { transform: `scale(${zoom.scale})`, transformOrigin: `${zoom.x}% ${zoom.y}%` }
                  : undefined
              }
            />
          </div>
        ) : null,
      )}
      <div className="lb-hint dim">Click to zoom · scroll to change zoom · Esc to close</div>
    </div>
  );
}
