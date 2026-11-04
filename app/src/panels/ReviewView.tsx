import { useCallback, useEffect, useRef, useState } from "react";
import { api, cardLine, CONDITIONS, gameOf, url, type Card, type Finish, type Scan } from "../api";
import { useStore } from "../state";
import { loadSessions } from "../App";

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
    const next = JSON.stringify(data.scans.map((s) => [s.id, s.status, s.card_id, s.finish, s.condition]));
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

  const step = (dir: number) => {
    if (!shown.length) return;
    const i = shown.findIndex((s) => s.id === selected);
    const next = shown[Math.min(shown.length - 1, Math.max(0, i + dir))] ?? shown[0];
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (document.activeElement as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "SELECT") return;
      if (e.key === "j") step(1);
      else if (e.key === "k") step(-1);
      else if (e.key === "Enter" && current?.card_id) update(current.id, { status: "added" }, "Added");
      else if (e.key === "x" && current) update(current.id, { status: "rejected" }, "Marked not a card");
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
          <kbd>J</kbd>/<kbd>K</kbd> move · <kbd>Enter</kbd> add · <kbd>X</kbd> not a card · <kbd>/</kbd> search
        </span>
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
        {current && <Detail key={current.id} scan={current} update={update} />}
      </div>
    </div>
  );
}

function Detail({
  scan: s,
  update,
}: {
  scan: Scan;
  update: (id: number, body: Record<string, unknown>, msg: string) => Promise<void>;
}) {
  const say = useStore((st) => st.say);
  const [finishes, setFinishes] = useState<Finish[]>([]);
  const [options, setOptions] = useState<Card[] | null>(null);
  const [q, setQ] = useState("");
  const [game, setGame] = useState(gameOf(s.card));
  const [results, setResults] = useState<Card[]>([]);
  const [finish, setFinish] = useState(s.finish ?? "");
  const [condition, setCondition] = useState(s.condition || "NM");

  useEffect(() => {
    if (s.card_id) api<Finish[]>(`/api/finishes?card=${encodeURIComponent(s.card_id)}`).then(setFinishes);
    api<Card[]>(`/api/options?scan=${s.id}`).then(setOptions);
  }, [s.id, s.card_id]);

  useEffect(() => {
    setFinish(s.finish ?? "");
    setCondition(s.condition || "NM");
  }, [s.finish, s.condition]);

  useEffect(() => {
    if (q.trim().length < 2) return setResults([]);
    const t = setTimeout(
      () => api<Card[]>(`/api/search?q=${encodeURIComponent(q)}&game=${game}`).then(setResults),
      250,
    );
    return () => clearTimeout(t);
  }, [q, game]);

  const change = (f: string, c: string) => {
    setFinish(f);
    setCondition(c);
    if (s.status === "added") update(s.id, { finish: f, condition: c }, "Updated");
  };

  const choose = (id: string) => update(s.id, { card_id: id, status: "added" }, "Changed and added");

  const extra = async () => {
    await api("/api/items", { card_id: s.card_id, finish: finish || s.finish, condition, delta: 1 });
    say(`Added another ${s.card?.name}`);
  };

  return (
    <div className="detail">
      <div className="pair">
        <img src={url(`/capture/${s.id}`)} alt="" />
        {s.card?.img_large ? <img src={s.card.img_large} alt="" onError={hide} /> : <div className="noimg dim">No image</div>}
      </div>
      <h3>
        {s.card ? s.card.name : "Not identified"}{" "}
        <span className="dim">{s.card ? `${s.card.set_name} · ${cardLine(s.card)}` : ""}</span>
      </h3>
      <div className="dim">
        Capture {s.n} at {s.t}s · {s.status}
        {s.confidence ? ` · ${s.confidence}` : ""}
        {s.rough ? " · rough outline" : ""}
      </div>
      <div className="row">
        {finishes.length > 0 && (
          <select value={finish} onChange={(e) => change(e.target.value, condition)}>
            {finishes.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        )}
        <select value={condition} onChange={(e) => change(finish, e.target.value)}>
          {CONDITIONS.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </div>
      <div className="row">
        <button
          className="primary"
          disabled={!s.card_id || s.status === "added"}
          onClick={() => update(s.id, { status: "added", finish: finish || undefined, condition }, "Added")}
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
            onClick={() => update(s.id, { status: "rejected" }, "Marked not a card")}
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
        <select value={game} onChange={(e) => setGame(e.target.value)}>
          <option value="">Any game</option>
          <option value="mtg">Magic</option>
          <option value="pkm">Pokémon</option>
          <option value="pkj">Pokémon JP</option>
        </select>
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
