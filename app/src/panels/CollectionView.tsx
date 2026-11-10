import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { api, cardLine, gameOf, money, url, type Item, type Items } from "../api";
import { useStore } from "../state";
import CardModal from "./CardModal";
import CardWall, { clampZoom, ZOOM_MAX, ZOOM_MIN } from "./CardWall";
import { matcher, sortRarities } from "../filters";
import FilterPanel, { FilterButton, useGameFilters } from "./FilterPanel";
import GameSelect from "./GameSelect";
import ImportModal from "./ImportModal";

type SortKey = "name" | "set" | "finish" | "price" | "qty" | "added";

const text = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });

const COMPARE: Record<SortKey, (a: Item, b: Item) => number> = {
  name: (a, b) => text(a.name, b.name) || text(a.set_name, b.set_name),
  set: (a, b) =>
    (a.set_released ?? "").localeCompare(b.set_released ?? "") ||
    text(a.set_code, b.set_code) || (a.number_sort ?? 0) - (b.number_sort ?? 0) || text(a.number, b.number),
  finish: (a, b) => text(a.finish_label, b.finish_label) || text(a.name, b.name),
  price: (a, b) => (a.usd ?? -1) - (b.usd ?? -1),
  qty: (a, b) => a.qty - b.qty || text(a.name, b.name),
  added: (a, b) => a.added_at - b.added_at,
};

// Clicking a new column starts high-to-low for numbers and dates, A-Z for text.
const DESC_FIRST: SortKey[] = ["price", "qty", "added"];

const SORTS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Card" },
  { key: "set", label: "Set" },
  { key: "finish", label: "Finish" },
  { key: "price", label: "Price" },
  { key: "qty", label: "Qty" },
  { key: "added", label: "Added" },
];

/** The card image, or your own scan of it when there's none online yet (new sets, before TCGplayer has photos). */
function Thumb({ item }: { item: Item }) {
  const capture = item.capture_id ? url(`/capture/${item.capture_id}`) : null;
  const [src, setSrc] = useState(item.img ?? capture);
  if (!src) return null;
  return (
    <img
      loading="lazy"
      src={src}
      alt=""
      onError={(e) => (capture && src !== capture ? setSrc(capture) : (e.currentTarget.style.visibility = "hidden"))}
    />
  );
}

function added(ms: number) {
  const d = new Date(ms);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString();
}

export default function CollectionView() {
  const say = useStore((s) => s.say);
  const layout = useStore((s) => s.prefs.collectionView);
  const zoom = useStore((s) => s.prefs.wallZoom);
  const setPrefs = useStore((s) => s.setPrefs);
  const onZoom = useCallback(
    (update: (cell: number) => number) => setPrefs({ wallZoom: update(useStore.getState().prefs.wallZoom) }),
    [setPrefs],
  );
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "added", desc: true });
  const [data, setData] = useState<Items | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [opened, setOpened] = useState<{ id: string; finish: string } | null>(null);
  const [game, setGame] = useState("");
  const [importing, setImporting] = useState(false);
  const { filters, setFilters, open: filtersOpen, toggle: toggleFilters, count: filterCount } = useGameFilters(game);

  const load = useCallback(
    () => api<Items>(`/api/items?q=${encodeURIComponent(q)}`).then(setData),
    [q],
  );

  useEffect(() => {
    const t = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [load]);

  useEffect(() => {
    const off = [
      listen<{ job: string; line: string }>("job:line", (e) => e.payload.job === "prices" && say(e.payload.line)),
      listen<{ job: string; code: number }>("job:done", (e) => {
        if (e.payload.job !== "prices") return;
        setRefreshing(false);
        if (e.payload.code !== 0) say("Price refresh failed");
        load();
      }),
    ];
    return () => off.forEach((p) => p.then((f) => f()));
  }, [load]);

  const refresh = async () => {
    try {
      await invoke("job_run", { job: "prices" });
      setRefreshing(true);
      say("Refreshing prices");
    } catch (e) {
      say(String(e));
    }
  };

  const exportCsv = async () => {
    try {
      const r = await api<{ path: string; copies: number }>("/api/export", {});
      say(`Saved ${r.copies} cards to ${r.path}`);
    } catch (e) {
      say(String(e));
    }
  };

  const adjust = async (i: number, delta: number) => {
    const r = rows[i];
    await api("/api/items", { card_id: r.card_id, finish: r.finish, condition: r.condition, delta });
    load();
  };

  const ofGame = useMemo(() => (data?.items ?? []).filter((r) => !game || gameOf(r) === game), [data, game]);

  const choices = useMemo(() => {
    const sets = new Map<string, string>();
    const finishes = new Map<string, string>();
    for (const r of ofGame) {
      sets.set(r.set_code, r.set_name);
      finishes.set(r.finish, r.finish_label);
    }
    return {
      rarities: sortRarities(game, ofGame.map((r) => r.rarity ?? "")),
      sets: [...sets].map(([code, name]) => ({ code, name })).sort((a, b) => text(a.name, b.name)),
      finishes: [...finishes].map(([key, label]) => ({ key, label })),
    };
  }, [ofGame, game]);

  const rows = useMemo(() => {
    const cmp = COMPARE[sort.key];
    const keep = filters ? matcher(filters, data?.aud_per_usd ?? null) : () => true;
    return ofGame.filter(keep).sort((a, b) => (sort.desc ? cmp(b, a) : cmp(a, b)));
  }, [ofGame, sort, filters, data]);

  const perGame = (key: string) =>
    (data?.items ?? []).reduce((n, r) => (!key || gameOf(r) === key ? n + r.qty : n), 0);

  const sortBy = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: DESC_FIRST.includes(key) }));

  const header = (key: SortKey, label: string, num = false) => (
    <th className={`sortable${num ? " num" : ""}`} onClick={() => sortBy(key)}>
      {label}
      {sort.key === key && <span className="arrow">{sort.desc ? "▼" : "▲"}</span>}
    </th>
  );

  const rate = data?.aud_per_usd ?? null;
  const copies = rows.reduce((n, r) => n + r.qty, 0);
  const value = rows.reduce((n, r) => n + (r.usd || 0) * r.qty, 0);

  return (
    <div className="view">
      <div className="toolbar">
        <input className="grow" placeholder="Filter by name" value={q} onChange={(e) => setQ(e.target.value)} />
        <GameSelect value={game} onChange={setGame} count={perGame} />
        {filters && <FilterButton open={filtersOpen} count={filterCount} onClick={toggleFilters} />}
        <button disabled={refreshing} onClick={refresh}>
          {refreshing ? "Refreshing" : "Refresh prices"}
        </button>
        <button title="Add cards from another app's CSV export" onClick={() => setImporting(true)}>
          Import
        </button>
        <button title="Save the collection as a CSV in your Downloads folder" onClick={exportCsv}>
          Export
        </button>
        <div className="seg">
          <button className={layout === "list" ? "on" : ""} onClick={() => setPrefs({ collectionView: "list" })}>
            List
          </button>
          <button className={layout === "wall" ? "on" : ""} onClick={() => setPrefs({ collectionView: "wall" })}>
            View all
          </button>
        </div>
      </div>
      {filters && filtersOpen && (
        <FilterPanel
          game={game}
          value={filters}
          onChange={setFilters}
          rarities={choices.rarities}
          sets={choices.sets}
          finishes={choices.finishes}
          currency={data?.aud_per_usd ? "A$" : "US$"}
        />
      )}
      {layout === "wall" && (
        <div className="toolbar">
          <div className="seg">
            {SORTS.map(({ key, label }) => (
              <button
                key={key}
                className={sort.key === key ? "on" : ""}
                onClick={() => sortBy(key)}
                title="Sort; click again to reverse"
              >
                {label}
                {sort.key === key && <span className="arrow">{sort.desc ? "▼" : "▲"}</span>}
              </button>
            ))}
          </div>
          <div className="grow" />
          <span className="dim" title="Ctrl+scroll over the cards to zoom">
            Zoom
          </span>
          <input
            type="range"
            className="zoom"
            min={ZOOM_MIN}
            max={ZOOM_MAX}
            value={zoom}
            onChange={(e) => setPrefs({ wallZoom: clampZoom(Number(e.target.value)) })}
          />
        </div>
      )}
      <div className="summary">
        <b>{copies}</b> cards · {rows.length} entries · <b className="accent">{money(value, rate) || "no prices"}</b>
        <span className="dim">
          {" "}
          · {data?.prices_at ? `prices from ${new Date(Number(data.prices_at)).toLocaleDateString()}` : "snapshot prices"}
        </span>
      </div>
      {layout === "wall" ? (
        <>
          <CardWall items={rows} rate={rate} cell={zoom} onZoom={onZoom} onOpen={(id, finish) => setOpened({ id, finish })} />
          {data && !rows.length && (
            <div className="empty dim">{q || game ? "No matches." : "Nothing in the collection yet."}</div>
          )}
        </>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th></th>
                {header("name", "Card")}
                {header("set", "Set")}
                {header("finish", "Finish")}
                {header("price", "Price", true)}
                {header("qty", "Qty", true)}
                {header("added", "Added", true)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={`${r.card_id}|${r.finish}|${r.condition}`}
                  className="clickable"
                  onClick={() => setOpened({ id: r.card_id, finish: r.finish })}
                >
                  <td>
                    <Thumb item={r} />
                  </td>
                  <td>
                    {r.name}
                    {r.local_name && <span className="dim"> {r.local_name}</span>}
                  </td>
                  <td>
                    {r.set_name} <span className="dim">{cardLine(r)}</span>
                  </td>
                  <td>
                    {r.finish_label}
                    {r.condition !== "NM" && <span className="dim"> · {r.condition}</span>}
                  </td>
                  <td className="num">{money(r.usd, rate)}</td>
                  <td className="num qty" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => adjust(i, -1)}>−</button>
                    <span>{r.qty}</span>
                    <button onClick={() => adjust(i, 1)}>+</button>
                  </td>
                  <td className="num dim" title={new Date(r.added_at).toLocaleString()}>
                    {added(r.added_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && !rows.length && (
            <div className="empty dim">{q || game ? "No matches." : "Nothing in the collection yet."}</div>
          )}
        </div>
      )}
      {importing && <ImportModal onClose={() => setImporting(false)} onImported={load} />}
      {opened && <CardModal id={opened.id} finish={opened.finish} onClose={() => setOpened(null)} onChanged={load} />}
    </div>
  );
}
