import { useEffect, useRef, useState } from "react";
import { api, GAME_LABEL } from "../api";
import { useStore } from "../state";

type Unmatched = { line: number; name: string; set: string; number: string; qty: number; reason: string };

type Plan = {
  source: string;
  rows: number;
  copies: number;
  matched: number;
  unmatched: Unmatched[];
  games: Record<string, number>;
};

type Past = { id: number; at: number; name: string; source: string; copies: number; undone: number };

/** Imports a collection CSV from ManaBox, Moxfield, Deckbox, TCGplayer and the like: pick a file, see what
 * matches, then add it. Past imports can be undone. */
export default function ImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const say = useStore((s) => s.say);
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [past, setPast] = useState<Past[]>([]);
  const [confirming, setConfirming] = useState<number | null>(null);

  const loadPast = () => api<Past[]>("/api/imports").then(setPast).catch(() => {});

  useEffect(() => {
    loadPast();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (confirming === null) return;
    const t = setTimeout(() => setConfirming(null), 3000);
    return () => clearTimeout(t);
  }, [confirming]);

  const choose = async (f: File | undefined) => {
    if (!f) return;
    setPlan(null);
    setError("");
    setBusy(true);
    try {
      const text = await f.text();
      setFile({ name: f.name, text });
      setPlan(await api<Plan>("/api/import", { text }));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const done = await api<Plan>("/api/import", { text: file.text, name: file.name, apply: true });
      say(`Imported ${done.matched} cards from ${file.name}`);
      onImported();
      onClose();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      setBusy(false);
    }
  };

  const undo = async (p: Past) => {
    if (confirming !== p.id) return setConfirming(p.id);
    setConfirming(null);
    try {
      await api(`/api/imports/${p.id}/undo`, {});
      say(`Undid the import of ${p.name}`);
      loadPast();
      onImported();
    } catch (e) {
      say(String(e instanceof Error ? e.message : e));
    }
  };

  const games = plan
    ? Object.entries(plan.games)
        .map(([g, n]) => `${GAME_LABEL[g] ?? g} ${n}`)
        .join(", ")
    : "";

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal import" onClick={(e) => e.stopPropagation()}>
        <div className="sidebar-h">Import a collection</div>
        <div className="setrow">
          <div>
            {file ? file.name : "CSV export"}
            <div className="dim">
              ManaBox, Moxfield, Deckbox, TCGplayer or any CSV with name, set and collector number columns.
            </div>
          </div>
          <button disabled={busy} onClick={() => input.current?.click()}>
            {file ? "Choose another" : "Choose file"}
          </button>
          <input
            ref={input}
            type="file"
            accept=".csv,.txt,text/csv"
            hidden
            onChange={(e) => {
              void choose(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>
        {busy && !plan && <div className="pad dim">Reading</div>}
        {error && <div className="pad bad">{error}</div>}
        {plan && (
          <>
            <div className="pad">
              {plan.source}: {plan.rows} rows, {plan.copies} cards. <b className="ok">{plan.matched}</b> match
              {games && <span className="dim"> ({games})</span>}
              {plan.unmatched.length > 0 && (
                <>
                  , <b className="warn">{plan.copies - plan.matched}</b> don't and are skipped:
                </>
              )}
            </div>
            {plan.unmatched.length > 0 && (
              <div className="import-unmatched">
                <table>
                  <thead>
                    <tr>
                      <th className="num">Line</th>
                      <th>Card</th>
                      <th>Set</th>
                      <th className="num">Qty</th>
                      <th>Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.unmatched.map((u) => (
                      <tr key={u.line}>
                        <td className="num dim">{u.line}</td>
                        <td>{u.name}</td>
                        <td>
                          {u.set} <span className="dim">{u.number && `#${u.number}`}</span>
                        </td>
                        <td className="num">{u.qty}</td>
                        <td className="dim">{u.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
        {past.length > 0 && (
          <>
            <div className="sidebar-h">Past imports</div>
            {past.map((p) => (
              <div className="setrow" key={p.id}>
                <div>
                  {p.name}
                  <div className="dim">
                    {p.source} · {p.copies} cards · {new Date(p.at).toLocaleString()}
                  </div>
                </div>
                {p.undone ? (
                  <span className="dim">undone</span>
                ) : (
                  <button className="danger" onClick={() => undo(p)}>
                    {confirming === p.id ? "Click again to undo" : "Undo"}
                  </button>
                )}
              </div>
            ))}
          </>
        )}
        <div className="modal-foot">
          <button onClick={onClose}>Cancel</button>
          <button disabled={!plan || !plan.matched || busy} onClick={run}>
            {plan ? `Add ${plan.matched} cards to the collection` : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}
