import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { api, cardLine, gameOf, money, url, type Item, type Items } from "../api";
import { useStore } from "../state";
import CardModal from "./CardModal";
import GameSelect from "./GameSelect";

type SortKey = "name" | "set" | "finish" | "price" | "qty" | "added";

const text = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });

const COMPARE: Record<SortKey, (a: Item, b: Item) => number> = {
  name: (a, b) => text(a.name, b.name) || text(a.set_name, b.set_name),
  set: (a, b) =>
    text(a.set_name, b.set_name) || (a.number_sort ?? 0) - (b.number_sort ?? 0) || text(a.number, b.number),
  finish: (a, b) => text(a.finish_label, b.finish_label) || text(a.name, b.name),
  price: (a, b) => (a.usd ?? -1) - (b.usd ?? -1),
  qty: (a, b) => a.qty - b.qty || text(a.name, b.name),
  added: (a, b) => a.added_at - b.added_at,
};

// Clicking a new column starts high-to-low for numbers and dates, A-Z for text.
const DESC_FIRST: SortKey[] = ["price", "qty", "added"];

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
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "added", desc: true });
  const [data, setData] = useState<Items | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [opened, setOpened] = useState<string | null>(null);
  const [game, setGame] = useState("");

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

  const adjust = async (i: number, delta: number) => {
    const r = rows[i];
    await api("/api/items", { card_id: r.card_id, finish: r.finish, condition: r.condition, delta });
    load();
  };

  const rows = useMemo(() => {
    const cmp = COMPARE[sort.key];
    return (data?.items ?? [])
      .filter((r) => !game || gameOf(r) === game)
      .sort((a, b) => (sort.desc ? cmp(b, a) : cmp(a, b)));
  }, [data, sort, game]);

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
        <button disabled={refreshing} onClick={refresh}>
          {refreshing ? "Refreshing" : "Refresh prices"}
        </button>
      </div>
      <div className="summary">
        <b>{copies}</b> cards · {rows.length} entries · <b className="accent">{money(value, rate) || "no prices"}</b>
        <span className="dim">
          {" "}
          · {data?.prices_at ? `prices from ${new Date(Number(data.prices_at)).toLocaleDateString()}` : "snapshot prices"}
        </span>
      </div>
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
              <tr key={`${r.card_id}|${r.finish}|${r.condition}`} className="clickable" onClick={() => setOpened(r.card_id)}>
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
                <td>{r.finish_label}</td>
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
        {data && !rows.length && <div className="empty dim">{q || game ? "No matches." : "Nothing in the collection yet."}</div>}
      </div>
      {opened && <CardModal id={opened} onClose={() => setOpened(null)} onChanged={load} />}
    </div>
  );
}
