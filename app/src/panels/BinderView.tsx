import { useCallback, useEffect, useMemo, useState } from "react";
import { api, GAME_LABEL, gameOf as scopeOf, money, url, type Binder, type BinderCard, type BinderSet } from "../api";
import { useStore } from "../state";
import CardModal from "./CardModal";
import GameSelect from "./GameSelect";
import TileArt from "./TileArt";

const text = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });
const progressOf = (s: BinderSet) => (s.base ? s.owned / s.base : 0);

type SetKey = "code" | "name" | "game" | "released" | "progress" | "slots" | "copies";

// ties fall back to release order, newest first, the order the sets arrive in
const SET_COMPARE: Record<SetKey, (a: BinderSet, b: BinderSet) => number> = {
  code: (a, b) => text(a.code, b.code),
  name: (a, b) => text(a.name, b.name),
  game: (a, b) => text(GAME_LABEL[scopeOf(a)], GAME_LABEL[scopeOf(b)]),
  released: (a, b) => (a.released ?? "").localeCompare(b.released ?? ""),
  progress: (a, b) => progressOf(a) - progressOf(b) || a.owned - b.owned,
  slots: (a, b) => a.owned - b.owned || a.base - b.base,
  copies: (a, b) => a.copies - b.copies,
};

// Clicking a new column starts high-to-low for numbers and dates, A-Z for text.
const SET_DESC_FIRST: SetKey[] = ["released", "progress", "slots", "copies"];

type CardKey = "number" | "name" | "price" | "qty";
const CARD_SORTS: { key: CardKey; label: string }[] = [
  { key: "number", label: "Number" },
  { key: "name", label: "Name" },
  { key: "price", label: "Price" },
  { key: "qty", label: "Owned" },
];
const CARD_DESC_FIRST: CardKey[] = ["price", "qty"];

type Slot = {
  base: BinderCard;
  /** Owned printings that fill this slot, the base card first. */
  owned: BinderCard[];
  /** The cheapest printing that would fill it. */
  usd: number | null;
};

/** Base slots in number order, plus the secret rares and alternates that sit outside the base set. */
function buildBinder(cards: BinderCard[]) {
  const slots = new Map<string, Slot>();
  for (const c of cards) if (c.slot === c.number) slots.set(c.number, { base: c, owned: [], usd: null });
  const extras: BinderCard[] = [];
  for (const c of cards) {
    const slot = c.slot != null ? slots.get(c.slot) : undefined;
    if (slot) {
      if (c.qty > 0) {
        if (c === slot.base) slot.owned.unshift(c);
        else slot.owned.push(c);
      }
      if (c.usd != null) slot.usd = slot.usd == null ? c.usd : Math.min(slot.usd, c.usd);
    }
    if (c.slot !== c.number) extras.push(c);
  }
  return { slots: [...slots.values()], extras };
}

function CardImage({ card }: { card: BinderCard }) {
  return <TileArt card={card} capture={card.capture_id ? url(`/capture/${card.capture_id}`) : null} />;
}

function Progress({ value, done }: { value: number; done: boolean }) {
  return (
    <div className="progress">
      <div className={done ? "done" : ""} style={{ width: `${Math.min(1, value) * 100}%` }} />
    </div>
  );
}

export default function BinderView() {
  const [game, setGame] = useState("");
  const [q, setQ] = useState("");
  const [started, setStarted] = useState(true);
  const [extras, setExtras] = useState(false);
  const [sets, setSets] = useState<BinderSet[] | null>(null);
  const [opened, setOpened] = useState<BinderSet | null>(null);
  const [sort, setSort] = useState<{ key: SetKey; desc: boolean } | null>(null);

  const load = useCallback(() => api<BinderSet[]>("/api/sets").then(setSets), []);

  useEffect(() => {
    load();
  }, [load]);

  const inGame = useMemo(() => (sets ?? []).filter((s) => !game || scopeOf(s) === game), [sets, game]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const shown = inGame.filter(
      (s) =>
        (extras || s.kind === "main") &&
        (!started || s.copies > 0) &&
        (!needle || s.name.toLowerCase().includes(needle) || s.code.toLowerCase() === needle),
    );
    if (!sort) return shown;
    const cmp = SET_COMPARE[sort.key];
    const order = new Map(shown.map((s, i) => [s, i]));
    return shown.sort((a, b) => (sort.desc ? cmp(b, a) : cmp(a, b)) || order.get(a)! - order.get(b)!);
  }, [inGame, q, started, extras, sort]);

  const sortBy = (key: SetKey) =>
    setSort((s) => (s?.key === key ? { key, desc: !s.desc } : { key, desc: SET_DESC_FIRST.includes(key) }));

  const header = (key: SetKey, label: string, className = "") => (
    <th className={`sortable ${className}`} onClick={() => sortBy(key)}>
      {label}
      {sort?.key === key && <span className="arrow">{sort.desc ? "▼" : "▲"}</span>}
    </th>
  );

  const perGame = (key: string) => (sets ?? []).filter((s) => s.copies > 0 && (!key || scopeOf(s) === key)).length;

  if (opened) {
    return (
      <SetBinder
        set={opened}
        onBack={() => {
          setOpened(null);
          load();
        }}
      />
    );
  }

  const done = rows.filter((s) => s.base > 0 && s.owned >= s.base).length;

  return (
    <div className="view">
      <div className="toolbar">
        <input className="grow" placeholder="Search sets" value={q} onChange={(e) => setQ(e.target.value)} />
        <GameSelect value={game} onChange={setGame} count={perGame} />
        <button className={started ? "on" : ""} onClick={() => setStarted((v) => !v)}>
          Started
        </button>
        <button className={extras ? "on" : ""} onClick={() => setExtras((v) => !v)}>
          {game === "mtg" ? "Tokens & promos" : game === "lor" ? "Promos" : game ? "Promos & kits" : "Promos, tokens & kits"}
        </button>
      </div>
      <div className="summary">
        <b>{rows.length}</b> sets · {rows.filter((s) => s.copies > 0).length} started · {done} complete
      </div>
      <div className="table-wrap">
        <table className="set-table">
          <thead>
            <tr>
              {header("code", "Code")}
              {header("name", "Set")}
              {!game && header("game", "Game")}
              {header("released", "Released")}
              {header("progress", "Progress", "progress-col")}
              {header("slots", "Slots", "num")}
              {header("copies", "Cards", "num")}
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const progress = progressOf(s);
              return (
                <tr key={s.id} className="clickable" onClick={() => setOpened(s)}>
                  <td className="set-code">{s.code}</td>
                  <td>
                    {s.name}
                    {s.series && <span className="dim"> · {s.series}</span>}
                  </td>
                  {!game && <td className="dim">{GAME_LABEL[scopeOf(s)]}</td>}
                  <td className="dim">{s.released?.slice(0, 4)}</td>
                  <td>
                    <Progress value={progress} done={s.base > 0 && s.owned >= s.base} />
                  </td>
                  <td className="num">
                    <span className={s.owned ? "" : "dim"}>
                      {s.owned}/{s.base}
                    </span>
                    <span className="dim pct">{s.owned ? `${Math.floor(progress * 100)}%` : ""}</span>
                  </td>
                  <td className="num dim">{s.copies || ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {sets && !rows.length && (
          <div className="empty dim">
            {started && !q ? "No started sets. Scan a card from a set to start its binder." : "No sets match."}
          </div>
        )}
      </div>
    </div>
  );
}

type Show = "all" | "missing" | "owned";

function SetBinder({ set, onBack }: { set: BinderSet; onBack: () => void }) {
  const say = useStore((s) => s.say);
  const [data, setData] = useState<Binder | null>(null);
  const [show, setShow] = useState<Show>("all");
  const [sort, setSort] = useState<{ key: CardKey; desc: boolean }>({ key: "number", desc: false });
  const [opened, setOpened] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api<Binder>(`/api/binder?id=${encodeURIComponent(set.id)}`)
        .then(setData)
        .catch((e) => say(String(e))),
    [set.id],
  );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !opened && onBack();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack, opened]);

  const binder = useMemo(() => buildBinder(data?.cards ?? []), [data]);
  const rate = data?.aud_per_usd ?? null;
  const owned = binder.slots.filter((s) => s.owned.length).length;
  const missing = binder.slots.filter((s) => !s.owned.length);
  const cost = missing.reduce((n, s) => n + (s.usd ?? 0), 0);
  const unpriced = missing.filter((s) => s.usd == null).length;
  const value = (data?.cards ?? []).reduce((n, c) => n + (c.usd ?? 0) * c.qty, 0);
  const sorted = <T,>(rows: T[], card: (r: T) => { name: string; usd: number | null; qty: number }) => {
    if (sort.key === "number") return sort.desc ? [...rows].reverse() : rows;
    const value = (r: T) => {
      const c = card(r);
      // unpriced cards sort last both ways
      return sort.key === "price" ? (c.usd ?? (sort.desc ? -Infinity : Infinity)) : c.qty;
    };
    const order = new Map(rows.map((r, i) => [r, i]));
    const cmp = (a: T, b: T) =>
      sort.key === "name" ? text(card(a).name, card(b).name) : value(a) - value(b);
    return [...rows].sort((a, b) => (sort.desc ? cmp(b, a) : cmp(a, b)) || order.get(a)! - order.get(b)!);
  };
  // a slot's price is what it's worth when filled, else what filling it costs; its count covers every printing in it
  const slotCard = (s: Slot) => ({
    name: s.base.name,
    usd: s.owned.length ? s.owned[0].usd : s.usd,
    qty: s.owned.reduce((n, c) => n + c.qty, 0),
  });
  const slots = sorted(
    binder.slots.filter((s) => show === "all" || (show === "owned") === s.owned.length > 0),
    slotCard,
  );
  const extras = sorted(
    binder.extras.filter((c) => show === "all" || (show === "owned") === c.qty > 0),
    (c) => c,
  );
  const sortBy = (key: CardKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: CARD_DESC_FIRST.includes(key) }));

  return (
    <div className="view">
      <div className="toolbar">
        <button onClick={onBack}>Back</button>
        <span className="set-code">{set.code}</span>
        <div className="title grow ellipsis">
          {set.name}
          {set.series && <span className="dim"> · {set.series}</span>}
        </div>
        <div className="seg">
          {CARD_SORTS.map(({ key, label }) => (
            <button key={key} className={sort.key === key ? "on" : ""} onClick={() => sortBy(key)} title="Sort; click again to reverse">
              {label}
              {sort.key === key && <span className="arrow">{sort.desc ? "▼" : "▲"}</span>}
            </button>
          ))}
        </div>
        <div className="seg">
          {(["all", "missing", "owned"] as Show[]).map((k) => (
            <button key={k} className={show === k ? "on" : ""} onClick={() => setShow(k)}>
              {k[0].toUpperCase() + k.slice(1)}
            </button>
          ))}
        </div>
      </div>
      <div className="summary">
        <b>
          {owned}/{binder.slots.length}
        </b>{" "}
        slots
        {value > 0 && (
          <>
            {" "}
            · owned worth <b className="accent">{money(value, rate)}</b>
          </>
        )}
        {missing.length > 0 && (
          <>
            {" "}
            · <b className="accent">{money(cost, rate)}</b> to complete
            {unpriced > 0 && <span className="dim"> ({unpriced} without a price)</span>}
          </>
        )}
        {data && binder.slots.length > 0 && !missing.length && <span className="ok"> · complete</span>}
      </div>
      <div className="search-results">
        {!data ? (
          <div className="empty dim">Loading</div>
        ) : (
          <>
            {slots.length > 0 && (
              <div className="found-grid binder-grid">
                {slots.map((s) => {
                  const shown = s.owned[0] ?? s.base;
                  const alt = s.owned.length > 0 && s.owned[0] !== s.base;
                  const qty = s.owned.reduce((n, c) => n + c.qty, 0);
                  return (
                    <div
                      key={s.base.id}
                      className={`found${s.owned.length ? "" : " missing"}`}
                      onClick={() => setOpened(shown.id)}
                      title={`${shown.name} #${shown.number}${alt ? ` (fills #${s.base.number})` : ""}`}
                    >
                      <CardImage key={shown.id} card={shown} />
                      <div className="ellipsis">
                        <span className="dim">{s.base.number}</span> {s.base.name}
                      </div>
                      <div className="found-foot">
                        <span className="accent">
                          {money(s.owned.length ? shown.usd : s.usd, rate) || <span className="dim">no price</span>}
                        </span>
                        {qty > 0 && (
                          <span className="ok">
                            ×{qty}
                            {alt ? ` #${shown.number}` : ""}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {extras.length > 0 && (
              <>
                <div className="sidebar-h binder-h">Secret rares and alternates ({binder.extras.length})</div>
                <div className="found-grid binder-grid">
                  {extras.map((c) => (
                    <div
                      key={c.id}
                      className={`found${c.qty ? "" : " missing"}`}
                      onClick={() => setOpened(c.id)}
                      title={`${c.name} #${c.number}${c.rarity ? ` · ${c.rarity}` : ""}`}
                    >
                      <CardImage card={c} />
                      <div className="ellipsis">
                        <span className="dim">{c.number}</span> {c.name}
                      </div>
                      <div className="found-foot">
                        <span className="accent">{money(c.usd, rate) || <span className="dim">no price</span>}</span>
                        {c.qty > 0 && <span className="ok">×{c.qty}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
            {!slots.length && !extras.length && (
              <div className="empty dim">{show === "owned" ? "Nothing owned from this set yet." : "Nothing missing."}</div>
            )}
          </>
        )}
      </div>
      {opened && <CardModal id={opened} onClose={() => setOpened(null)} onChanged={load} />}
    </div>
  );
}
