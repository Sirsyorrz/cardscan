/** Per-game card filters, Scryfall advanced search style. Must match filters.py (search) and the port in fakehams'
 *  site/collections/cards.js. */

export type ColorMode = "any" | "all" | "exact" | "within" | "identity";
export type RangeKey = "cost" | "power" | "toughness" | "hp" | "price";
export type Range = { min: string; max: string };

export type Filters = {
  colors: string[];
  colorMode: ColorMode;
  type: string;
  text: string;
  rarity: string[];
  set: string;
  format: string;
  /** Collection only: search results are printings, not owned copies. */
  finish: string;
  ranges: Partial<Record<RangeKey, Range>>;
};

export const NO_FILTERS: Filters = {
  colors: [],
  colorMode: "any",
  type: "",
  text: "",
  rarity: [],
  set: "",
  format: "",
  finish: "",
  ranges: {},
};

/** What a card needs for filtering: a collection item or a search result with its filter fields. */
export type Filterable = {
  game: string;
  colors?: string | null;
  identity?: string | null;
  type_line?: string | null;
  text?: string | null;
  cmc?: number | null;
  power?: string | null;
  toughness?: string | null;
  hp?: number | null;
  rarity?: string | null;
  set_code: string;
  set_name: string;
  legal?: number | null;
  finish?: string;
  usd?: number | null;
};

export type Colour = { key: string; label: string; swatch: string };

export type GameFilters = {
  colourLabel: string;
  colours: Colour[];
  /** Magic's commander colour identity as an extra colour mode. */
  identity?: boolean;
  /** cards.legal bits in bit order; must match FORMATS in filters.py. */
  formats: string[];
  ranges: { key: RangeKey; label: string }[];
  typeHint: string;
  textHint: string;
  /** Rarities in a sensible order; any others found in the cards go after, A-Z. */
  rarityOrder: string[];
};

const POKEMON: GameFilters = {
  colourLabel: "Energy",
  colours: [
    { key: "Grass", label: "Grass", swatch: "#5fb35a" },
    { key: "Fire", label: "Fire", swatch: "#e8603c" },
    { key: "Water", label: "Water", swatch: "#4a90e2" },
    { key: "Lightning", label: "Lightning", swatch: "#f2c93a" },
    { key: "Psychic", label: "Psychic", swatch: "#a35ec4" },
    { key: "Fighting", label: "Fighting", swatch: "#b8693c" },
    { key: "Darkness", label: "Darkness", swatch: "#3d4a57" },
    { key: "Metal", label: "Metal", swatch: "#9aa5b1" },
    { key: "Fairy", label: "Fairy", swatch: "#e889b8" },
    { key: "Dragon", label: "Dragon", swatch: "#b39b3c" },
    { key: "Colorless", label: "Colorless", swatch: "#d9d6cf" },
  ],
  formats: ["standard", "expanded"],
  ranges: [
    { key: "hp", label: "HP" },
    { key: "price", label: "Price" },
  ],
  typeHint: "Trainer, Stage1, ex, Supporter",
  textHint: "Attack or ability text",
  rarityOrder: ["Common", "Uncommon", "Rare", "Holo Rare", "Double rare", "Ultra Rare", "Illustration rare",
    "Special illustration rare", "Hyper rare", "Secret Rare", "Promo"],
};

export const FILTER_GAMES: Record<string, GameFilters> = {
  mtg: {
    colourLabel: "Colours",
    colours: [
      { key: "W", label: "White", swatch: "#f3eccd" },
      { key: "U", label: "Blue", swatch: "#3a86c8" },
      { key: "B", label: "Black", swatch: "#4b4040" },
      { key: "R", label: "Red", swatch: "#d9533e" },
      { key: "G", label: "Green", swatch: "#2f9a5c" },
      { key: "C", label: "Colourless", swatch: "#b9b3ad" },
    ],
    identity: true,
    formats: ["standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander"],
    ranges: [
      { key: "cost", label: "Mana value" },
      { key: "power", label: "Power" },
      { key: "toughness", label: "Toughness" },
      { key: "price", label: "Price" },
    ],
    typeHint: "Legendary creature, elf, -token",
    textHint: "Rules text, \"draw a card\"",
    rarityOrder: ["common", "uncommon", "rare", "mythic", "special", "bonus"],
  },
  lor: {
    colourLabel: "Ink",
    colours: [
      { key: "Amber", label: "Amber", swatch: "#f2b33d" },
      { key: "Amethyst", label: "Amethyst", swatch: "#8a4ea0" },
      { key: "Emerald", label: "Emerald", swatch: "#3c9a50" },
      { key: "Ruby", label: "Ruby", swatch: "#d23a4c" },
      { key: "Sapphire", label: "Sapphire", swatch: "#2f8fd0" },
      { key: "Steel", label: "Steel", swatch: "#9aa3ad" },
    ],
    formats: ["core", "infinity"],
    ranges: [
      { key: "cost", label: "Cost" },
      { key: "power", label: "Strength" },
      { key: "toughness", label: "Willpower" },
      { key: "hp", label: "Lore" },
      { key: "price", label: "Price" },
    ],
    typeHint: "Character, song, hero",
    textHint: "Ability text",
    rarityOrder: ["Common", "Uncommon", "Rare", "Super rare", "Legendary", "Epic", "Enchanted", "Iconic", "Promo"],
  },
  pkm: POKEMON,
  pkj: POKEMON,
};

export const COLOUR_MODES: { key: ColorMode; label: string }[] = [
  { key: "any", label: "Any of these" },
  { key: "all", label: "Including these" },
  { key: "exact", label: "Exactly these" },
  { key: "within", label: "At most these" },
  { key: "identity", label: "Fits a commander of these" },
];

const RANGE_FIELD: Record<Exclude<RangeKey, "price">, keyof Filterable> = {
  cost: "cmc",
  power: "power",
  toughness: "toughness",
  hp: "hp",
};

const fold = (s: string | null | undefined) =>
  (s ?? "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();

/** Words and "quoted phrases" that must appear, and -excluded ones. */
export function terms(q: string) {
  const inc: string[] = [];
  const exc: string[] = [];
  for (const m of q.matchAll(/(-?)(?:"([^"]*)"|(\S+))/g)) {
    const t = fold(m[2] ?? m[3]);
    if (t) (m[1] ? exc : inc).push(t);
  }
  return { inc, exc };
}

function hasTerms(value: string | null | undefined, { inc, exc }: { inc: string[]; exc: string[] }) {
  const v = fold(value);
  return inc.every((t) => v.includes(t)) && !exc.some((t) => v.includes(t));
}

/** Magic colours are letters ("RUW") and colourless counts as C; the others are comma lists. */
function colours(game: string, value: string | null | undefined) {
  if (game === "mtg") return new Set(value ? [...value] : ["C"]);
  return new Set((value ?? "").split(",").filter(Boolean));
}

const subset = (a: Set<string>, b: Set<string>) => [...a].every((x) => b.has(x));

function colourMatch(have: Set<string>, want: Set<string>, mode: ColorMode) {
  if (mode === "all") return subset(want, have);
  if (mode === "exact") return subset(want, have) && subset(have, want);
  if (mode === "within") return subset(have, want);
  return [...want].some((x) => have.has(x));
}

const num = (v: unknown) => {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const isRangeSet = (r?: Range) => !!r && (num(r.min) != null || num(r.max) != null);

export function activeCount(f: Filters) {
  return (
    (f.colors.length ? 1 : 0) +
    (terms(f.type).inc.length || terms(f.type).exc.length ? 1 : 0) +
    (terms(f.text).inc.length || terms(f.text).exc.length ? 1 : 0) +
    (f.rarity.length ? 1 : 0) +
    (f.set.trim() ? 1 : 0) +
    (f.format ? 1 : 0) +
    (f.finish ? 1 : 0) +
    Object.values(f.ranges).filter(isRangeSet).length
  );
}

/** A filter for one game's cards. Prices are compared in the shown currency: AUD at `rate`. */
export function matcher(f: Filters, rate: number | null) {
  const want = new Set(f.colors);
  const type = terms(f.type);
  const text = terms(f.text);
  const rarity = new Set(f.rarity);
  const set = fold(f.set).trim();
  const ranges = Object.entries(f.ranges).filter(([, r]) => isRangeSet(r)) as [RangeKey, Range][];
  return (c: Filterable) => {
    if (want.size) {
      if (f.colorMode === "identity") {
        if (![...(c.identity ?? "")].every((x) => want.has(x) && x !== "C")) return false;
      } else if (!colourMatch(colours(c.game, c.colors), want, f.colorMode)) return false;
    }
    if ((type.inc.length || type.exc.length) && !hasTerms(c.type_line, type)) return false;
    if ((text.inc.length || text.exc.length) && !hasTerms(c.text, text)) return false;
    if (rarity.size && !rarity.has(c.rarity ?? "")) return false;
    if (set && fold(c.set_code) !== set && !fold(c.set_name).includes(set)) return false;
    if (f.format) {
      const i = FILTER_GAMES[c.game]?.formats.indexOf(f.format) ?? -1;
      if (i < 0 || !(((c.legal ?? 0) >> i) & 1)) return false;
    }
    if (f.finish && c.finish !== f.finish) return false;
    for (const [key, r] of ranges) {
      let v: number | null;
      if (key === "price") v = c.usd == null ? null : rate ? c.usd * rate : c.usd;
      else v = num(c[RANGE_FIELD[key]]);
      const lo = num(r.min);
      const hi = num(r.max);
      if (v == null || (lo != null && v < lo) || (hi != null && v > hi)) return false;
    }
    return true;
  };
}

export function sortRarities(game: string, found: Iterable<string>) {
  const order = FILTER_GAMES[game]?.rarityOrder ?? [];
  const rank = (r: string) => {
    const i = order.findIndex((o) => o.toLowerCase() === r.toLowerCase());
    return i < 0 ? order.length : i;
  };
  return [...new Set(found)].filter((r) => r && r !== "None").sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}
