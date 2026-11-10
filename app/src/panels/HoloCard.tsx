import { useEffect, useRef } from "react";
import { attachHolo, type Fx } from "../holo";
import FlipButton from "./FlipButton";

const hide = (e: React.SyntheticEvent<HTMLImageElement>) => (e.currentTarget.style.visibility = "hidden");

/** The popup's card: tilts toward the pointer and spins when dragged, lit with the foil of `fx`. A double-faced card
 *  turns to its other face, anything else to the game's card back. */
export default function HoloCard({
  front,
  back,
  cardBack,
  fx,
  win,
  onFrontError,
}: {
  front: string;
  back?: string | null;
  cardBack: string;
  fx: Fx;
  /** The Pokémon art window, for holo and reverse holo. */
  win: string;
  onFrontError?: React.ReactEventHandler<HTMLImageElement>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const holo = useRef<ReturnType<typeof attachHolo>>(null);

  useEffect(() => {
    holo.current = attachHolo(ref.current!);
    return () => holo.current?.destroy();
  }, []);

  useEffect(() => holo.current?.reset(), [front]);

  return (
    <div className="cm-holo">
      <div
        ref={ref}
        className={back ? "holo" : "holo plain-back"}
        data-fx={fx}
        style={{ "--win": win } as React.CSSProperties}
      >
        <div className="holo-tilt">
          <div className="holo-face">
            <img className="front" src={front} alt="" draggable={false} onError={onFrontError ?? hide} />
            <img className="back" src={back ?? cardBack} alt="" draggable={false} onError={hide} />
            <div className="holo-shine" />
            <div className="holo-glare" />
          </div>
        </div>
        {back && <FlipButton onFlip={() => holo.current?.turnOver()} />}
      </div>
      <div className="holo-hint dim">Drag to turn it over</div>
    </div>
  );
}
