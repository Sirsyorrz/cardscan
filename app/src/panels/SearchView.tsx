import { useEffect, useState } from "react";
import { api, cardLine, money, type Found } from "../api";
import { activeCount, sortRarities } from "../filters";
import CardModal from "./CardModal";
import FilterPanel, { FilterButton, useGameFilters } from "./FilterPanel";
import GameSelect from "./GameSelect";
import TileArt from "./TileArt";

type Facets = { rarities: string[]; sets: { code: string; name: string }[] };

export default function SearchView() {
  const [q, setQ] = useState("");
  const [game, setGame] = useState("");
  const [results, setResults] = useState<Found[] | null>(null);
  const [rate, setRate] = useState<number | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [facets, setFacets] = useState<Facets>({ rarities: [], sets: [] });
  const { filters, setFilters, open, toggle, count } = useGameFilters(game);
  const filtering = !!filters && activeCount(filters) > 0;

  useEffect(() => {
    api<{ aud_per_usd: number | null }>("/api/rate").then((r) => setRate(r.aud_per_usd));
  }, []);

  useEffect(() => {
    setFacets({ rarities: [], sets: [] });
    if (game) api<Facets>(`/api/facets?game=${game}`).then(setFacets);
  }, [game]);

  const run = () =>
    api<Found[]>(
      `/api/search?q=${encodeURIComponent(q.trim())}&game=${game}` +
        (filtering ? `&f=${encodeURIComponent(JSON.stringify(filters))}` : ""),
    ).then(setResults);

  useEffect(() => {
    if (q.trim().length < 2 && !filtering) return setResults(null);
    const t = setTimeout(run, 250);
    return () => clearTimeout(t);
  }, [q, game, filters]);

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
        {filters && <FilterButton open={open} count={count} onClick={toggle} />}
      </div>
      {filters && open && (
        <FilterPanel
          game={game}
          value={filters}
          onChange={setFilters}
          rarities={sortRarities(game, facets.rarities)}
          sets={facets.sets}
          currency={rate ? "A$" : "US$"}
        />
      )}
      <div className="search-results">
        {results === null ? (
          <div className="empty dim">
            Search any card to see its prices and add it to the collection.
            {!game && " Pick a game for colour, type, stat and rarity filters."}
          </div>
        ) : !results.length ? (
          <div className="empty dim">No cards found.</div>
        ) : (
          <div className="found-grid">
            {results.map((c) => (
              <div key={c.id} className="found" onClick={() => setOpened(c.id)} title={`${c.name} · ${c.set_name}`}>
                <TileArt card={c} />
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
        {results && results.length >= 150 && <div className="empty dim">Showing the first 150. Narrow it down to see the rest.</div>}
      </div>
      {opened && <CardModal id={opened} onClose={() => setOpened(null)} onChanged={run} />}
    </div>
  );
}
