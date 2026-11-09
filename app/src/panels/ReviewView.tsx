import { useCallback, useEffect, useRef, useState } from "react";
import { api, cardLine, gameOf, url, type Card, type Finish, type Scan } from "../api";
import { useStore } from "../state";
import { loadSessions } from "../App";
import GameSelect from "./GameSelect";
import Lightbox from "./Lightbox";

type Filter = "review" | "added" | "all";

export default function ReviewView() {
  const session = useStore((s) => s.reviewSession);
  const say = useStore((s) => s.say);
  const [filter, setFilter] = useState<Filter>("review");
  const [scans, setScans] = useState<Scan[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const sig = useRef("");

  const load = useCallback(async () => {
    if (!session) return setScans([]);
    const data = await api<{ scans: Scan[] }>(`/api/scans?session=${session}`);
    // the poll only re-renders when something changed
    const next = JSON.stringify(data.scans.map((s) => [s.id, s.status, s.card_id, s.finish]));
    if (next !== sig.current) {
      sig.current = next;
      setScans(data.scans);
    }
  }, [session]);

  useEffect(() => {
    sig.current = "";
    setSelected(null);
    load();
    const t = setInterval(() => !document.hidden && load().catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [load]);

  const shown = scans.filter((s) => filter === "all" || s.status === filter);
  const current = scans.find((s) => s.id === selected) ?? null;

  const scanning = useStore((s) => s.scanning);
  const liveSession = useStore((s) => s.liveSession);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), 3000);
    return () => clearTimeout(t);
  }, [confirming]);

  const deleteSession = async () => {
    if (!confirming) return setConfirming(true);
    setConfirming(false);
    try {
      await api(`/api/sessions/${session}/delete`, {});
      say(`Deleted session ${session}`);
      loadSessions();
    } catch (e) {
      say(String(e));
    }
  };

  const columns = () => {
    const grid = document.querySelector(".grid");
    return grid ? getComputedStyle(grid).gridTemplateColumns.split(" ").length : 1;
  };

  const step = (dir: number) => {
    if (!shown.length) return;
    const i = shown.findIndex((s) => s.id === selected);
    const next = i < 0 ? shown[0] : shown[Math.min(shown.length - 1, Math.max(0, i + dir))];
    setSelected(next.id);
    document.querySelector(`.tile[data-id="${next.id}"]`)?.scrollIntoView({ block: "nearest" });
  };

  const update = async (id: number, body: Record<string, unknown>, msg: string) => {
    try {
      await api(`/api/scans/${id}`, body);
      say(msg);
      await load();
      loadSessions();
    } catch (e) {
      say(String(e));
    }
  };

  /** Add or reject a card, then select the one after it (before it, at the end of the list). */
  const decide = async (id: number, body: Record<string, unknown>, msg: string) => {
    const i = shown.findIndex((s) => s.id === id);
    const next = shown[i + 1] ?? shown[i - 1] ?? null;
    await update(id, body, msg);
    if (next) {
      setSelected(next.id);
      document.querySelector(`.tile[data-id="${next.id}"]`)?.scrollIntoView({ block: "nearest" });
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (document.activeElement as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "SELECT") return;
      const move = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns(), ArrowUp: -columns() }[e.key];
      if (move) {
        e.preventDefault();
        step(move);
      } else if (e.key === "Enter" && current?.card_id) decide(current.id, { status: "added" }, "Added");
      else if (e.key === "x" && current) decide(current.id, { status: "rejected" }, "Marked not a card");
      else if (e.key === "/") {
        e.preventDefault();
        document.getElementById("review-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const counts = {
    review: scans.filter((s) => s.status === "review").length,
    added: scans.filter((s) => s.status === "added").length,
    all: scans.length,
  };

  return (
    <div className="view">
      <div className="toolbar">
        <span className="title">{session ? `Session ${session}` : "No sessions"}</span>
        <div className="seg">
          {(["review", "added", "all"] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {f === "review" ? "Needs review" : f === "added" ? "Added" : "All"} <span className="dim">{counts[f]}</span>
            </button>
          ))}
        </div>
        <span className="spacer" />
        <span className="dim">
          <kbd>←↑↓→</kbd> move · <kbd>Enter</kbd> add · <kbd>X</kbd> not a card · <kbd>/</kbd> search
        </span>
        {session > 0 && (
          <button
            className="danger"
            disabled={scanning && liveSession === session}
            title={scanning && liveSession === session ? "Stop the scan first" : "Removes its captures and the cards they added"}
            onClick={deleteSession}
          >
            {confirming ? "Click again to delete" : "Delete session"}
          </button>
        )}
      </div>
      <div className="split">
        <div className="grid-wrap">
          {shown.length ? (
            <div className="grid">
              {shown.map((s) => (
                <div
                  key={s.id}
                  data-id={s.id}
                  className={`tile st-${s.status} ${selected === s.id ? "sel" : ""}`}
                  onClick={() => setSelected(s.id)}
                >
                  <span className={`chip st-${s.status}`}>{s.status}</span>
                  <img className="cap" loading="lazy" src={url(`/capture/${s.id}`)} alt="" />
                  <div className="meta">
                    <div className="ellipsis">{s.card?.name ?? "?"}</div>
                    <div className="dim ellipsis">
                      {s.n} · {cardLine(s.card)}
                      {s.finish_label && s.finish_label !== "Normal" ? ` · ${s.finish_label}` : ""}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty dim">{filter === "review" && scans.length ? "Nothing left to review." : "Nothing here."}</div>
          )}
        </div>
        {current && <Detail key={current.id} scan={current} update={update} decide={decide} />}
      </div>
    </div>
  );
}

function Detail({
  scan: s,
  update,
  decide,
}: {
  scan: Scan;
  update: (id: number, body: Record<string, unknown>, msg: string) => Promise<void>;
  /** like `update`, then moves on to the next card */
  decide: (id: number, body: Record<string, unknown>, msg: string) => Promise<void>;
}) {
  const say = useStore((st) => st.say);
  const [finishes, setFinishes] = useState<Finish[]>([]);
  const [options, setOptions] = useState<Card[] | null>(null);
  const [q, setQ] = useState("");
  const [game, setGame] = useState(gameOf(s.card));
  const [results, setResults] = useState<Card[]>([]);
  const [finish, setFinish] = useState(s.finish ?? "");
  const [enlarged, setEnlarged] = useState(false);

  useEffect(() => {
    if (s.card_id) api<Finish[]>(`/api/finishes?card=${encodeURIComponent(s.card_id)}`).then(setFinishes);
    api<Card[]>(`/api/options?scan=${s.id}`).then(setOptions);
  }, [s.id, s.card_id]);

  useEffect(() => {
    setFinish(s.finish ?? "");
  }, [s.finish]);

  useEffect(() => {
    if (q.trim().length < 2) return setResults([]);
    const t = setTimeout(
      () => api<Card[]>(`/api/search?q=${encodeURIComponent(q)}&game=${game}`).then(setResults),
      250,
    );
    return () => clearTimeout(t);
  }, [q, game]);

  const change = (f: string) => {
    setFinish(f);
    if (s.status === "added") update(s.id, { finish: f }, "Updated");
  };

  // picking only previews the printing; Enter or Add confirms it. An already added capture stays added.
  const choose = (id: string) => {
    (document.activeElement as HTMLElement | null)?.blur();
    if (id === s.card_id) return;
    update(s.id, s.status === "added" ? { card_id: id } : { card_id: id, status: "review" }, "Changed");
  };

  const extra = async () => {
    await api("/api/items", { card_id: s.card_id, finish: finish || s.finish, delta: 1 });
    say(`Added another ${s.card?.name}`);
  };

  return (
    <div className="detail">
      <div className="pair zoomable" title="Click to enlarge" onClick={() => setEnlarged(true)}>
        <img src={url(`/capture/${s.id}`)} alt="" />
        {s.card?.img_large ? <img src={s.card.img_large} alt="" onError={hide} /> : <div className="noimg dim">No image</div>}
      </div>
      {enlarged && (
        <Lightbox images={[url(`/capture/${s.id}`), s.card?.img_large ?? null]} onClose={() => setEnlarged(false)} />
      )}
      <h3>
        {s.card ? s.card.name : "Not identified"}{" "}
        <span className="dim">{s.card ? `${s.card.set_name} · ${cardLine(s.card)}` : ""}</span>
      </h3>
      <div className="dim">
        Capture {s.n} at {s.t}s · {s.status}
        {s.confidence ? ` · ${s.confidence}` : ""}
        {s.rough ? " · rough outline" : ""}
      </div>
      {finishes.length > 0 && (
        <div className="row">
          <select value={finish} onChange={(e) => change(e.target.value)}>
            {finishes.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="row">
        <button
          className="primary"
          disabled={!s.card_id || s.status === "added"}
          onClick={() => decide(s.id, { status: "added", finish: finish || undefined }, "Added")}
        >
          {s.status === "added" ? "Added" : "Add"}
        </button>
        <button disabled={!s.card_id} onClick={extra}>
          +1 copy
        </button>
        {s.status === "added" ? (
          <button onClick={() => update(s.id, { status: "review" }, "Removed from the collection")}>Remove</button>
        ) : (
          <button
            className="danger"
            disabled={s.status === "rejected"}
            onClick={() => decide(s.id, { status: "rejected" }, "Marked not a card")}
          >
            Not a card
          </button>
        )}
      </div>
      <div className="sidebar-h">Other printings</div>
      <div className="opts">
        {options === null ? (
          <span className="dim">Loading</span>
        ) : options.length ? (
          options.map((c) => <Option key={c.id} card={c} current={c.id === s.card_id} onPick={choose} />)
        ) : (
          <span className="dim">None</span>
        )}
      </div>
      <div className="sidebar-h">Search</div>
      <div className="row">
        <input
          id="review-search"
          className="grow"
          placeholder="Card name"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <GameSelect value={game} onChange={setGame} allLabel="Any game" />
      </div>
      <div className="opts">
        {results.map((c) => (
          <Option key={c.id} card={c} current={c.id === s.card_id} onPick={choose} />
        ))}
      </div>
    </div>
  );
}

function Option({ card, current, onPick }: { card: Card; current: boolean; onPick: (id: string) => void }) {
  return (
    <div className={`opt ${current ? "cur" : ""}`} onClick={() => onPick(card.id)} title={card.name}>
      {card.img ? <img loading="lazy" src={card.img} alt="" onError={hide} /> : <div className="noimg" />}
      <div>{cardLine(card)}</div>
      <div className="dim ellipsis">{card.name}</div>
    </div>
  );
}

function hide(e: React.SyntheticEvent<HTMLImageElement>) {
  e.currentTarget.style.visibility = "hidden";
}
