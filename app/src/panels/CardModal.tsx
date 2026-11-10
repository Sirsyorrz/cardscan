import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { api, LANG_LABEL, money, setLabel, url, type CardDetail } from "../api";
import { useStore } from "../state";

const hide = (e: React.SyntheticEvent<HTMLImageElement>) => (e.currentTarget.style.visibility = "hidden");

export default function CardModal({
  id,
  onClose,
  onChanged,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const say = useStore((s) => s.say);
  // printings visited from here, so Back returns to the one opened from the list
  const [trail, setTrail] = useState([id]);
  const current = trail[trail.length - 1];
  const [d, setD] = useState<CardDetail | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);

  const load = useCallback(
    () =>
      api<CardDetail>(`/api/card?id=${encodeURIComponent(current)}`)
        .then(setD)
        .catch((e) => say(String(e))),
    [current],
  );

  useEffect(() => {
    setShown(null);
    setBroken(false);
    load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const adjust = async (finish: string, delta: number) => {
    await api("/api/items", { card_id: current, finish, delta });
    await load();
    onChanged();
  };

  const open = (link: string) => invoke("open_url", { url: link }).catch((e) => say(String(e)));

  const c = d?.card;
  const rate = d?.aud_per_usd ?? null;
  const owned = d ? d.finishes.reduce((n, f) => n + f.qty, 0) : 0;
  const value = d ? d.finishes.reduce((n, f) => n + (f.tcg ?? 0) * f.qty, 0) : 0;
  const hasCk = d?.finishes.some((f) => f.ck != null) ?? false;
  const capture = d?.scans.length ? url(`/capture/${d.scans[0].id}`) : null;
  // new sets often have no card image yet; the scan is the next best picture
  const art = shown ?? (broken ? capture : c?.img_large) ?? null;
  const back = !shown && !broken ? c?.img_back_large : null;
  type Stat = [string, string | number | null | undefined];
  // cards.db keeps Lorcana's lore in `hp` and strength / willpower in power / toughness
  const gameStats: Stat[] = !c
    ? []
    : c.game === "lor"
      ? [
          ["Strength", c.power],
          ["Willpower", c.toughness],
          ["Lore", c.hp],
          ["Ink", c.colors],
        ]
      : [
          ["HP", c.hp],
          ["P / T", c.power != null ? `${c.power} / ${c.toughness}` : null],
          ["Colours", c.colors],
        ];
  const stats: Stat[] = c
    ? [
        ["Type", c.type_line],
        ["Cost", c.mana_cost],
        ...gameStats,
        ["Rarity", c.rarity],
        ["Number", c.printed_total ? `${c.number} / ${c.printed_total}` : c.number],
        ["Set", [setLabel(c), c.series].filter(Boolean).join(" · ")],
        ["Released", c.released],
        ["Artist", c.artist],
      ]
    : [];

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal card-modal" onClick={(e) => e.stopPropagation()}>
        {!c ? (
          <div className="pad dim">Loading</div>
        ) : (
          <>
            <div className="cm-head">
              {trail.length > 1 && <button onClick={() => setTrail((t) => t.slice(0, -1))}>Back</button>}
              <div className="grow">
                <div className="cm-title">
                  {c.name}
                  {c.local_name && <span className="dim"> {c.local_name}</span>}
                </div>
                <div className="dim">
                  {c.set_name} · {c.set_code} #{c.number}
                  {c.lang !== "en" ? ` · ${LANG_LABEL[c.lang] ?? c.lang}` : ""}
                </div>
              </div>
              <button onClick={onClose}>Close</button>
            </div>
            <div className="cm-body">
              <div className="cm-art">
                {art ? (
                  <div className="cm-faces">
                    <img src={art} alt="" onError={(e) => (shown || broken ? hide(e) : setBroken(true))} />
                    {back && <img src={back} alt="" onError={hide} />}
                  </div>
                ) : (
                  <div className="noimg dim">No image</div>
                )}
                {d.scans.length > 0 && !broken && (
                  <div className="cm-scans">
                    <button className={shown ? "" : "on"} onClick={() => setShown(null)}>
                      Card
                    </button>
                    {d.scans.slice(0, 4).map((s, i) => (
                      <button
                        key={s.id}
                        className={shown === url(`/capture/${s.id}`) ? "on" : ""}
                        onClick={() => setShown(url(`/capture/${s.id}`))}
                        title={new Date(s.created_at).toLocaleString()}
                      >
                        {i ? `Scan ${i + 1}` : "Your scan"}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="cm-info">
                <table className="cm-prices">
                  <thead>
                    <tr>
                      <th>Finish</th>
                      <th className="num">TCGplayer</th>
                      {hasCk && <th className="num">Card Kingdom</th>}
                      <th className="num">Owned</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.finishes.map((f) => (
                      <tr key={f.key}>
                        <td>{f.label}</td>
                        <td className="num">{money(f.tcg, rate) || <span className="dim">-</span>}</td>
                        {hasCk && <td className="num">{money(f.ck, rate) || <span className="dim">-</span>}</td>}
                        <td className="num qty">
                          <button disabled={!f.qty} onClick={() => adjust(f.key, -1)}>
                            −
                          </button>
                          <span>{f.qty}</span>
                          <button onClick={() => adjust(f.key, 1)}>+</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="cm-owned dim">
                  {owned ? (
                    <>
                      You own <b>{owned}</b>
                      {value ? (
                        <>
                          , worth <b className="accent">{money(value, rate)}</b>
                        </>
                      ) : null}
                      {d.scans.length ? ` · scanned ${new Date(d.scans[0].created_at).toLocaleString()}` : ""}
                    </>
                  ) : (
                    "Not in the collection"
                  )}
                </div>
                <dl className="cm-stats">
                  {stats
                    .filter(([, v]) => v != null && v !== "")
                    .map(([k, v]) => (
                      <div key={k}>
                        <dt>{k}</dt>
                        <dd>{v}</dd>
                      </div>
                    ))}
                </dl>
                {c.text && <div className="cm-text">{c.text}</div>}
                <div className="cm-legal">
                  {c.legal.map((l) => (
                    <span key={l.format} className={`chip ${l.legal ? "legal" : "dim"}`}>
                      {l.format}
                      {l.restricted ? " (R)" : ""}
                    </span>
                  ))}
                </div>
                {d.links.length > 0 && (
                  <div className="row">
                    {d.links.map((l) => (
                      <button key={l.url} onClick={() => open(l.url)}>
                        {l.label} ↗
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {d.printings.length > 1 && (
              <>
                <div className="sidebar-h">Other printings ({d.printings.length - 1})</div>
                <div className="cm-printings">
                  {d.printings.map((p) => (
                    <div
                      key={p.id}
                      className={`opt${p.id === current ? " cur" : ""}`}
                      title={`${setLabel(p)} #${p.number}`}
                      onClick={() => p.id !== current && setTrail((t) => [...t, p.id])}
                    >
                      {p.img ? <img loading="lazy" src={p.img} alt="" onError={hide} /> : <div className="noimg" />}
                      <div className="ellipsis">
                        {p.set_code} #{p.number}
                      </div>
                      <div className="dim">
                        {money(p.usd, rate) || "-"}
                        {p.qty > 0 && <span className="ok"> · ×{p.qty}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
