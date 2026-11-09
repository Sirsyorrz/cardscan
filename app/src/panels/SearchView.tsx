import { useEffect, useState } from "react";
import { api, cardLine, money, type Found } from "../api";
import CardModal from "./CardModal";
import GameSelect from "./GameSelect";

export default function SearchView() {
  const [q, setQ] = useState("");
  const [game, setGame] = useState("");
  const [results, setResults] = useState<Found[] | null>(null);
  const [rate, setRate] = useState<number | null>(null);
  const [opened, setOpened] = useState<string | null>(null);

  useEffect(() => {
    api<{ aud_per_usd: number | null }>("/api/rate").then((r) => setRate(r.aud_per_usd));
  }, []);

  const run = () =>
    api<Found[]>(`/api/search?q=${encodeURIComponent(q.trim())}&game=${game}`).then(setResults);

  useEffect(() => {
    if (q.trim().length < 2) return setResults(null);
    const t = setTimeout(run, 250);
    return () => clearTimeout(t);
  }, [q, game]);

  return (
    <div className="view">
      <div className="toolbar">
        <input
          autoFocus
          className="grow"
          placeholder="Card name, or set and number (DLR 44, 044/103)"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <GameSelect value={game} onChange={setGame} />
      </div>
      <div className="search-results">
        {results === null ? (
          <div className="empty dim">Search any card to see its prices and add it to the collection.</div>
        ) : !results.length ? (
          <div className="empty dim">No cards found.</div>
        ) : (
          <div className="found-grid">
            {results.map((c) => (
              <div key={c.id} className="found" onClick={() => setOpened(c.id)} title={`${c.name} · ${c.set_name}`}>
                {c.img ? (
                  <img
                    loading="lazy"
                    src={c.img}
                    alt=""
                    onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                  />
                ) : (
                  <div className="noimg" />
                )}
                <div className="ellipsis">{c.name}</div>
                <div className="dim ellipsis">
                  {c.set_name} · {cardLine(c)}
                </div>
                <div className="found-foot">
                  <span className="accent">{money(c.usd, rate) || <span className="dim">no price</span>}</span>
                  {c.qty > 0 && <span className="ok">×{c.qty} owned</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {opened && <CardModal id={opened} onClose={() => setOpened(null)} onChanged={run} />}
    </div>
  );
}
