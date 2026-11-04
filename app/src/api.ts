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
  condition: string;
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
  usd: number | null;
};

export type Items = { items: Item[]; aud_per_usd: number | null; prices_at: string | null };

export type Finish = { key: string; label: string };

export const CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"];

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

export function cardLine(c: { set_code: string; number: string; lang?: string } | null) {
  return c ? `${c.set_code} #${c.number}${c.lang === "ja" ? " JP" : ""}` : "";
}

export function gameOf(c: Card | null) {
  if (!c) return "";
  return c.game === "mtg" ? "mtg" : c.lang === "ja" ? "pkj" : "pkm";
}

export function money(usd: number | null | undefined, rate: number | null) {
  if (usd == null) return "";
  return rate ? `A$${(usd * rate).toFixed(2)}` : `US$${usd.toFixed(2)}`;
}
