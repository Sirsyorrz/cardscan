import { useState } from "react";
import type { Card } from "../api";
import FlipButton from "./FlipButton";

/** A search or binder tile's card image, turnable when it has a back, falling back to your own scan of it while
 *  there's no image online yet (new sets, before TCGplayer has photos). */
export default function TileArt({ card, capture = null }: { card: Card; capture?: string | null }) {
  const [turned, setTurned] = useState(false);
  const [src, setSrc] = useState(card.img ?? capture);
  const shown = turned && card.img_back ? card.img_back : src;
  return (
    <div className="tile-art">
      {shown ? (
        <img
          key={shown}
          loading="lazy"
          src={shown}
          alt=""
          onError={(e) =>
            !turned && capture && src !== capture ? setSrc(capture) : (e.currentTarget.style.visibility = "hidden")
          }
        />
      ) : (
        <div className="noimg" />
      )}
      {card.img_back && <FlipButton className="small" onFlip={() => setTurned((t) => !t)} />}
    </div>
  );
}
