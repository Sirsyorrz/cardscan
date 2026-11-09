import { useStore } from "./state";

export type Card = {
  id: string;
  game: string;
  lang: string;
  name: string;
  local_name: string | null;
  set_code: string;
  set_name: string;
  number: string;
  rarity: string | null;
  img: string | null;
  img_large: string | null;
};

/** A search result: a printing with its lowest TCGplayer price and the copies owned. */
export type Found = Card & { usd: number | null; qty: number };

export type Session = {
  id: number;
  started_at: number;
  source: string;
  scans: number;
  added: number | null;
  review: number | null;
};

export type ScanStatus = "added" | "review" | "repeat" | "rejected";

export type Scan = {
  id: number;
  n: number;
  t: number;
  card_id: string | null;
  card: Card | null;
  finish: string | null;
  finish_label: string | null;
  status: ScanStatus;
  confidence: string | null;
  rough: number;
};

export type Item = Card & {
  card_id: string;
  finish: string;
  finish_label: string;
  condition: string;
  qty: number;
  added_at: number;
  number_sort: number | null;
  capture_id: number | null;
  usd: number | null;
};

export type Items = { items: Item[]; aud_per_usd: number | null; prices_at: string | null };

export type Finish = { key: string; label: string };

export type BinderSet = {
  id: string;
  game: string;
  lang: string;
  code: string;
  name: string;
  released: string | null;
  kind: "main" | "extra";
  series: string | null;
  /** Numbered cards up to the printed total: the binder slots. */
  base: number;
  total: number;
  /** Base slots filled by any printing. */
  owned: number;
  copies: number;
};

export type BinderCard = Card & { slot: string | null; qty: number; usd: number | null; capture_id: number | null };

export type Binder = { set: Omit<BinderSet, "owned" | "copies">; cards: BinderCard[]; aud_per_usd: number | null };

export type CardDetail = {
  card: Card & {
    rarity: string | null;
    type_line: string | null;
    mana_cost: string | null;
    colors: string | null;
    power: string | null;
    toughness: string | null;
    hp: number | null;
    artist: string | null;
    released: string | null;
    printed_total: string | null;
    set_total: number | null;
    series: string | null;
    text: string | null;
    legal: { format: string; legal: boolean; restricted: boolean }[];
  };
  finishes: (Finish & { tcg?: number; ck?: number; qty: number; added_at: number | null })[];
  scans: { id: number; session_id: number; status: string; finish: string | null; created_at: number }[];
  printings: (Card & { qty: number; usd: number | null })[];
  links: { label: string; url: string }[];
  aud_per_usd: number | null;
};

export const MODES = [
  { key: "normal", label: "Normal" },
  { key: "foil", label: "Foil / Reverse" },
  { key: "holo", label: "Holo" },
] as const;

export function url(path: string) {
  return useStore.getState().base + path;
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(
    url(path),
    body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data as T;
}

/** Scryfall's language codes, for the Magic printings that exist only in one language, plus Japanese Pokémon. */
export const LANG_LABEL: Record<string, string> = {
  ja: "Japanese",
  ph: "Phyrexian",
  es: "Spanish",
  fr: "French",
  it: "Italian",
  de: "German",
  pt: "Portuguese",
  ru: "Russian",
  ko: "Korean",
  zhs: "Simplified Chinese",
  zht: "Traditional Chinese",
  he: "Hebrew",
  ar: "Arabic",
  la: "Latin",
  grc: "Ancient Greek",
  sa: "Sanskrit",
  qya: "Quenya",
  tlh: "Klingon",
  dw: "Dwarvish",
};

const langTag = (lang?: string) => (!lang || lang === "en" ? "" : ` ${lang === "ja" ? "JP" : lang.toUpperCase()}`);

export function cardLine(c: { set_code: string; number: string; lang?: string } | null) {
  return c ? `${c.set_code} #${c.number}${langTag(c.lang)}` : "";
}

/** Game filter scopes; keys must match GAME_SCOPE in review.py. */
export const GAMES = [
  { key: "lor", label: "Lorcana" },
  { key: "mtg", label: "Magic" },
  { key: "pkm", label: "Pokémon" },
  { key: "pkj", label: "Pokémon JP" },
].sort((a, b) => a.label.localeCompare(b.label));

export const GAME_LABEL: Record<string, string> = Object.fromEntries(GAMES.map((g) => [g.key, g.label]));

/** Japanese Pokémon is its own scope since it's a separate card pool. */
export function gameOf(c: { game: string; lang: string } | null) {
  if (!c) return "";
  return c.game === "pkm" && c.lang === "ja" ? "pkj" : c.game;
}

export function money(usd: number | null | undefined, rate: number | null) {
  if (usd == null) return "";
  return rate ? `A$${(usd * rate).toFixed(2)}` : `US$${usd.toFixed(2)}`;
}
