import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { api, cardLine, money, type Items } from "../api";
import { useStore } from "../state";

export default function CollectionView() {
  const say = useStore((s) => s.say);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("recent");
  const [data, setData] = useState<Items | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(
    () => api<Items>(`/api/items?q=${encodeURIComponent(q)}&sort=${sort}`).then(setData),
    [q, sort],
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
    const r = data!.items[i];
    await api("/api/items", { card_id: r.card_id, finish: r.finish, condition: r.condition, delta });
    load();
  };

  const rows = data?.items ?? [];
  const rate = data?.aud_per_usd ?? null;
  const copies = rows.reduce((n, r) => n + r.qty, 0);
  const value = rows.reduce((n, r) => n + (r.usd || 0) * r.qty, 0);

  return (
    <div className="view">
      <div className="toolbar">
        <input className="grow" placeholder="Filter by name" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="recent">Recent</option>
          <option value="name">Name</option>
          <option value="set">Set</option>
          <option value="value">Value</option>
        </select>
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
              <th>Card</th>
              <th>Set</th>
              <th>Finish</th>
              <th>Cond.</th>
              <th className="num">Price</th>
              <th className="num">Qty</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.card_id}|${r.finish}|${r.condition}`}>
                <td>
                  {r.img && (
                    <img
                      loading="lazy"
                      src={r.img}
                      alt=""
                      onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                    />
                  )}
                </td>
                <td>
                  {r.name}
                  {r.local_name && <span className="dim"> {r.local_name}</span>}
                </td>
                <td>
                  {r.set_name} <span className="dim">{cardLine(r)}</span>
                </td>
                <td>{r.finish_label}</td>
                <td>{r.condition}</td>
                <td className="num">{money(r.usd, rate)}</td>
                <td className="num qty">
                  <button onClick={() => adjust(i, -1)}>−</button>
                  <span>{r.qty}</span>
                  <button onClick={() => adjust(i, 1)}>+</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && !rows.length && <div className="empty dim">{q ? "No matches." : "Nothing in the collection yet."}</div>}
      </div>
    </div>
  );
}
