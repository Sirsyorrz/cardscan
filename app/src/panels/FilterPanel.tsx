import { useState } from "react";
import {
  activeCount,
  COLOUR_MODES,
  FILTER_GAMES,
  NO_FILTERS,
  type Filters,
  type Range,
  type RangeKey,
} from "../filters";
import { useStore } from "../state";

/** Filters kept per game, so switching games and back keeps what was set. */
export function useGameFilters(game: string) {
  const [byGame, setByGame] = useState<Record<string, Filters>>({});
  const open = useStore((s) => s.prefs.filtersOpen);
  const setPrefs = useStore((s) => s.setPrefs);
  const filters = (game && byGame[game]) || NO_FILTERS;
  return {
    /** Only a picked game has filters. */
    filters: game && FILTER_GAMES[game] ? filters : null,
    setFilters: (f: Filters) => setByGame((m) => ({ ...m, [game]: f })),
    open,
    toggle: () => setPrefs({ filtersOpen: !open }),
    count: activeCount(filters),
  };
}

export function FilterButton({ open, count, onClick }: { open: boolean; count: number; onClick: () => void }) {
  return (
    <button className={open || count ? "on" : ""} onClick={onClick} title="Colours, type, text, stats, rarity, set">
      Filters{count ? ` (${count})` : ""}
    </button>
  );
}

type Props = {
  game: string;
  value: Filters;
  onChange: (f: Filters) => void;
  rarities: string[];
  sets: { code: string; name: string }[];
  /** Collection only. */
  finishes?: { key: string; label: string }[];
  currency: string;
};

export default function FilterPanel({ game, value: f, onChange, rarities, sets, finishes, currency }: Props) {
  const g = FILTER_GAMES[game];
  if (!g) return null;
  const set = (patch: Partial<Filters>) => onChange({ ...f, ...patch });
  const toggle = (list: string[], key: string) => (list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);
  const setRange = (key: RangeKey, patch: Partial<Range>) =>
    set({ ranges: { ...f.ranges, [key]: { min: "", max: "", ...f.ranges[key], ...patch } } });
  const modes = COLOUR_MODES.filter((m) => m.key !== "identity" || g.identity);
  const listId = `sets-${game}`;

  return (
    <div className="filters">
      <div className="f-row">
        <span className="f-label">{g.colourLabel}</span>
        <div className="f-chips">
          {g.colours.map((c) => (
            <button
              key={c.key}
              className={`f-colour${f.colors.includes(c.key) ? " on" : ""}`}
              onClick={() => set({ colors: toggle(f.colors, c.key) })}
            >
              <i style={{ background: c.swatch }} />
              {c.label}
            </button>
          ))}
        </div>
        <select value={f.colorMode} onChange={(e) => set({ colorMode: e.target.value as Filters["colorMode"] })}>
          {modes.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
      </div>
      <div className="f-row">
        <label className="f-field grow">
          <span className="f-label">Type</span>
          <input value={f.type} placeholder={g.typeHint} onChange={(e) => set({ type: e.target.value })} />
        </label>
        <label className="f-field grow">
          <span className="f-label">Text</span>
          <input value={f.text} placeholder={g.textHint} onChange={(e) => set({ text: e.target.value })} />
        </label>
        <label className="f-field">
          <span className="f-label">Set</span>
          <input list={listId} value={f.set} placeholder="Name or code" onChange={(e) => set({ set: e.target.value })} />
          <datalist id={listId}>
            {sets.map((s) => (
              <option key={s.code} value={s.name}>
                {s.code}
              </option>
            ))}
          </datalist>
        </label>
        <label className="f-field">
          <span className="f-label">Legal in</span>
          <select value={f.format} onChange={(e) => set({ format: e.target.value })}>
            <option value="">Any format</option>
            {g.formats.map((k) => (
              <option key={k} value={k}>
                {k[0].toUpperCase() + k.slice(1)}
              </option>
            ))}
          </select>
        </label>
        {finishes && (
          <label className="f-field">
            <span className="f-label">Finish</span>
            <select value={f.finish} onChange={(e) => set({ finish: e.target.value })}>
              <option value="">Any finish</option>
              {finishes.map((x) => (
                <option key={x.key} value={x.key}>
                  {x.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="f-row">
        {g.ranges.map((r) => (
          <div key={r.key} className="f-range">
            <span className="f-label">{r.key === "price" ? `${r.label} (${currency})` : r.label}</span>
            <input
              inputMode="decimal"
              placeholder="min"
              value={f.ranges[r.key]?.min ?? ""}
              onChange={(e) => setRange(r.key, { min: e.target.value })}
            />
            <span className="dim">to</span>
            <input
              inputMode="decimal"
              placeholder="max"
              value={f.ranges[r.key]?.max ?? ""}
              onChange={(e) => setRange(r.key, { max: e.target.value })}
            />
          </div>
        ))}
      </div>
      <div className="f-row">
        {rarities.length > 0 && (
          <>
            <span className="f-label">Rarity</span>
            <div className="f-chips">
              {rarities.map((r) => (
                <button
                  key={r}
                  className={f.rarity.includes(r) ? "on" : ""}
                  onClick={() => set({ rarity: toggle(f.rarity, r) })}
                >
                  {r[0].toUpperCase() + r.slice(1)}
                </button>
              ))}
            </div>
          </>
        )}
        <div className="grow" />
        <button disabled={!activeCount(f)} onClick={() => onChange(NO_FILTERS)}>
          Clear filters
        </button>
      </div>
    </div>
  );
}
