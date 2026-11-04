import { norm } from './normalize.ts';

export type Line = { text: string; x: number; y: number; w: number; h: number };
/** What the scanner looks for. 'pkj' is Japanese Pokemon: game 'pkm' with lang 'ja' in the card database. */
export type Game = 'mtg' | 'pkm' | 'pkj';

export function cardGame(game: Game): 'mtg' | 'pkm' {
  return game === 'pkj' ? 'pkm' : game;
}

/** SQL condition limiting `cards` (optionally aliased) to the scanned game and language. */
export function gameScope(game: Game, alias = ''): { sql: string; params: string[] } {
  return { sql: `${alias}game = ? AND ${alias}lang = ?`, params: [cardGame(game), game === 'pkj' ? 'ja' : 'en'] };
}

/** Japanese names are matched on the printed name, the rest on the English one. */
function normColumn(game: Game) {
  return game === 'pkj' ? 'local_norm' : 'name_norm';
}

const isJapanese = (n: string) => /[^\x00-\x7f]/.test(n);
export type Query = <T>(sql: string, params: (string | number)[]) => Promise<T[]>;

export type NameIndex = {
  exact: Map<string, string[]>;
  byLength: Map<number, string[]>;
};

export type Clues = {
  /** Normalized longer lines, used to tell apart cards whose names OCR mangled (e.g. a dropped "ex"). */
  body: string[];
  names: { norm: string; name: string; y: number; exact: boolean }[];
  numbers: { number: string; total: string | null }[];
  setCodes: string[];
};

/** high: printing confirmed by number or set code; medium: fuzzy name or partial clues; low: name only, printing guessed. */
export type Match = {
  id: string;
  name: string;
  set_code: string;
  number: string;
  confidence: 'high' | 'medium' | 'low';
  /** For low-confidence matches: the rules/attack text the read agreed with, to narrow a manual pick. */
  textId?: string | null;
  /** For low-confidence matches: every plausible printing, newest first, for the artwork check or a manual pick. */
  candidates?: string[];
  /** Matched from the collector number alone; `name` is just the first candidate's. */
  nameUnread?: boolean;
  /** The collector number was read and agrees: the printing is settled, whatever the name confidence. */
  byNumber?: boolean;
};

export const MAX_CANDIDATES = 80;

/** Rows must already be limited to the game being scanned. */
export function buildNameIndex(rows: { norm: string; name: string }[]): NameIndex {
  const exact = new Map<string, string[]>();
  const byLength = new Map<number, string[]>();
  for (const r of rows) {
    // Japanese packs a name into fewer characters ("ルギア")
    if (r.norm.length < (isJapanese(r.norm) ? 2 : 3)) continue;
    const list = exact.get(r.norm);
    if (list) {
      if (!list.includes(r.name)) list.push(r.name);
      continue;
    }
    exact.set(r.norm, [r.name]);
    const bucket = byLength.get(r.norm.length) ?? [];
    bucket.push(r.norm);
    byLength.set(r.norm.length, bucket);
  }
  // Several cards can share a normalized name (DFC back faces, "Charizard ☆ δ"); try the plainest first.
  for (const [key, names] of exact) {
    if (names.length > 1) names.sort((a, b) => Number(norm(b) === key) - Number(norm(a) === key) || a.length - b.length);
  }
  return { exact, byLength };
}

function levenshtein(a: string, b: string, max: number) {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      const v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      cur.push(v);
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > max) return max + 1;
    prev = cur;
  }
  return prev[b.length];
}

const PKM_NOISE = /\b(basic|stage\s*[12]?|v?star|vmax|break|level\s*up|evolves\s+from.*|put\s+.*\s+on\s+the.*|trainer|item|supporter|stadium|tool|energy)\b/gi;
// "Energy" is part of the name on energy cards ("Fire Energy", "Jet Energy") and some trainers ("Energy Switch")
const PKM_NOISE_KEEP_ENERGY = /\b(basic|stage\s*[12]?|v?star|vmax|break|level\s*up|evolves\s+from.*|put\s+.*\s+on\s+the.*|trainer|item|supporter|stadium|tool|special\s+energy)\b/gi;
// Stage and card-type labels; "リザードから進化" (evolves from Charmeleon) names another card.
const PKJ_NOISE = /\S*から進化|たね|[12１２]\s*進化|進化|グッズ|サポート|スタジアム|ポケモンのどうぐ|トレーナーズ/g;

/** Strip the furniture OCR picks up around the name line (HP, stage labels, mana symbols). */
function cleanNameLine(text: string, keepEnergy = false) {
  return text
    // no leading \b: on Japanese cards HP often runs straight on from the name ("リザードンexHP330")
    .replace(/HP\s*\d+\b/gi, ' ')
    .replace(/\b\d+\s*HP\b/gi, ' ')
    .replace(keepEnergy ? PKM_NOISE_KEEP_ENERGY : PKM_NOISE, ' ')
    .replace(PKJ_NOISE, ' ')
    .replace(/[{}()®©@*•·|]/g, ' ')
    .replace(/\s\d+$/, '')
    .trim();
}

function lookupName(index: NameIndex, n: string): { key: string; exact: boolean } | null {
  if (index.exact.has(n)) return { key: n, exact: true };
  // Fuzzy matching short strings is how "2 life." turns into "Life // Death"; only correct longer reads.
  // Each Japanese character carries far more of the name, so the bar is lower there.
  const ja = isJapanese(n);
  if (n.length < (ja ? 4 : 7)) return null;
  const max = n.length >= (ja ? 8 : 12) ? 2 : 1;
  const minCand = ja ? 3 : 6;
  let best: { key: string; d: number } | null = null;
  for (let len = n.length - max; len <= n.length + max; len++) {
    for (const cand of index.byLength.get(len) ?? []) {
      if (cand.length < minCand) continue;
      const d = levenshtein(n, cand, max);
      if (d <= max && (!best || d < best.d)) best = { key: cand, d };
    }
  }
  return best ? { key: best.key, exact: false } : null;
}

const SET_CODE_RE = /\b([A-Z0-9]{2,5})\s*[•·*.\-+»]?\s*EN\b/;
// Japanese promos print the set where the total goes: "001/SV-P".
const SLASH_NUMBER_RE = /\b([A-Z]{0,4}\d{1,4}[a-z]?)\s*\/\s*([A-Z]{0,4}\d{1,4}|[A-Z]{1,3}-P)\b/;
// Japanese cards print the bare set code bottom left ("SV2a", "S12a", "SM12a"), with no language tag.
const PKJ_SET_RE = /\b((?:SV|SM|XY|BW|CP|CS|S|M)\d{1,2}(?:\.\d)?[a-zA-Z+]{0,2})(?![\w/])/g;
// Modern Magic frames print "U 0163" / "0163 U": a rarity letter or zero padding marks it as a collector number.
// OCR often loses the gap after the letter ("U0163"); the zero padding still marks it.
const MTG_NUMBER_RE = /^(?:[CURMSLT]\s+(\d{1,4})|[CURMSLT]?(0\d{3})(?:\s+[CURMSLT])?|(\d{1,4})\s+[CURMSLT])$/;
// Basic energy sets (SVE, MEE) print no total and no type in words: just "SVE EN 002" bottom left.
const PKM_BARE_NUMBER_RE = /(?:^|EN\s*)(0\d{2})$/;

export function extractClues(lines: Line[], index: NameIndex, game: Game): Clues {
  const sorted = [...lines].sort((a, b) => a.y - b.y);
  const clues: Clues = { body: [], names: [], numbers: [], setCodes: [] };
  // The card rarely fills the photo, so judge "bottom of the card" against the text block, not the image.
  const top = sorted[0]?.y ?? 0;
  // at least a third of the card tall, so a name strip read on its own keeps its lower lines as name candidates
  const bottom = Math.max(...sorted.map((l) => l.y + l.h), top + 0.3);
  const relY = (l: Line) => (l.y - top) / (bottom - top);

  for (const line of sorted) {
    const text = line.text.trim();

    const set = SET_CODE_RE.exec(text);
    if (set && !/^\d+$/.test(set[1])) {
      clues.setCodes.push(set[1]);
      // Pokemon prints a regulation mark right before the set code ("H SSP EN"), often read as one word
      if (set[1].length === 4 && /^[A-Z]/.test(set[1])) clues.setCodes.push(set[1].slice(1));
    }

    if (game === 'pkj' && relY(line) > 0.75) {
      for (const m of text.matchAll(PKJ_SET_RE)) clues.setCodes.push(m[1]);
    }

    const slash = SLASH_NUMBER_RE.exec(text);
    if (slash) {
      clues.numbers.push({ number: slash[1], total: slash[2] });
      if (/-P$/.test(slash[2])) clues.setCodes.push(slash[2]);
      // the set code read glued to the number: "DLR091/103" (prefixed numbers carry the prefix on the total too)
      const glued = /^([A-Z]{2,4})(\d+)$/.exec(slash[1]);
      if (glued && /^\d+$/.test(slash[2])) {
        clues.numbers.push({ number: glued[2], total: slash[2] });
        clues.setCodes.push(glued[1]);
      }
      // a symbol just before the number (regulation mark, rarity star) is often read as an extra digit: "9238/191"
      if (/^\d+$/.test(slash[1]) && slash[1].length > slash[2].length) {
        clues.numbers.push({ number: slash[1].slice(slash[1].length - slash[2].length), total: slash[2] });
      }
    } else if (game === 'mtg' && relY(line) > 0.8) {
      const bare = MTG_NUMBER_RE.exec(text);
      if (bare) clues.numbers.push({ number: bare[1] ?? bare[2] ?? bare[3], total: null });
    } else if (game === 'pkm' && relY(line) > 0.8) {
      const bare = PKM_BARE_NUMBER_RE.exec(text);
      if (bare) clues.numbers.push({ number: bare[1], total: null });
    }

    const bodyNorm = norm(text);
    if (bodyNorm.length >= 8) clues.body.push(bodyNorm);

    // Both games print the name at the top; lower lines are rules text that can contain other card names.
    if (relY(line) > 0.4) continue;

    // Try the name with "Energy" kept first, so "Energy Switch" doesn't fall back to "Switch"
    const variants = /energy/i.test(text) ? [cleanNameLine(text, true), cleanNameLine(text)] : [cleanNameLine(text)];
    let hit: ReturnType<typeof lookupName> = null;
    for (const cleaned of variants) {
      const n = norm(cleaned);
      if (n.length < 3 || /^\d+$/.test(n)) continue;
      hit = lookupName(index, n);
      if (hit) break;
    }
    if (!hit) continue;
    for (const name of index.exact.get(hit.key) ?? []) {
      if (!clues.names.some((x) => x.name === name)) clues.names.push({ norm: hit.key, name, y: line.y, exact: hit.exact });
    }
  }
  return clues;
}

function stripZeros(n: string) {
  return n.replace(/^([A-Z]*)0+(?=\d)/i, '$1').toUpperCase();
}

type Row = {
  id: string;
  name: string;
  set_code: string;
  number: string;
  printed_total: string | null;
  game: string;
  released: string | null;
  text_id: string | null;
};

const ROW_COLS = 'id, name, set_code, number, printed_total, game, released, text_id';

export async function resolveCard(clues: Clues, query: Query, game: Game): Promise<Match | null> {
  const nums = clues.numbers.map((n) => ({ ...n, number: stripZeros(n.number) }));
  const scope = gameScope(game);
  const codes = clues.setCodes.map((c) => c.toUpperCase());
  const inReadSet = (p: Row) => codes.includes(p.set_code.toUpperCase());

  // Set code + collector number pins an exact printing even when the name OCR is poor.
  for (const code of clues.setCodes) {
    for (const n of nums) {
      const rows = await query<Row>(
        `SELECT ${ROW_COLS} FROM cards WHERE set_code = ? COLLATE NOCASE AND (upper(ltrim(number, '0')) = ? OR number = ?) AND ${scope.sql} LIMIT 5`,
        [code, n.number, n.number, ...scope.params],
      );
      const named = rows.find((r) => clues.names.some((c) => c.name === r.name));
      if (named) return { ...named, confidence: 'high', byNumber: true };
      if (rows.length === 1 && !clues.names.length) return { ...rows[0], confidence: 'medium', byNumber: true };
    }
  }

  const best = [...clues.names].sort((a, b) => Number(b.exact) - Number(a.exact) || a.y - b.y || b.norm.length - a.norm.length)[0];
  if (!best) return numberOnly(nums, query, game);

  // Every name that normalizes the same is a candidate; the collector number decides between them.
  const sameName = clues.names.filter((c) => c.norm === best.norm).map((c) => c.name);
  const printings = await query<Row>(
    `SELECT ${ROW_COLS} FROM cards WHERE name IN (${sameName.map(() => '?').join(',')}) AND ${scope.sql}
     ORDER BY name = ? DESC, released DESC`,
    [...sameName, ...scope.params, best.name],
  );
  if (!printings.length) return null;

  // OCR often drops suffixes ("Pikachu ex" read as "Pikachu"); with a number we can afford a prefix search.
  if (nums.length && !printings.some((p) => nums.some((n) => stripZeros(p.number) === n.number))) {
    printings.push(
      ...(await query<Row>(
        `SELECT ${ROW_COLS} FROM cards WHERE ${normColumn(game)} LIKE ? AND ${scope.sql} AND number_sort IN (${nums.map(() => '?').join(',')})
         ORDER BY released DESC LIMIT 20`,
        [`${best.norm}%`, ...scope.params, ...nums.map((n) => Number.parseInt(n.number.replace(/^\D+/, ''), 10) || -1)],
      )),
    );
  }

  for (const n of nums) {
    const hits = printings.filter((p) => stripZeros(p.number) === n.number);
    const withTotal = n.total ? hits.filter((p) => !p.printed_total || stripZeros(p.printed_total) === stripZeros(n.total!)) : hits;
    const pick = (withTotal.length ? withTotal : hits).find((p) => !codes.length || inReadSet(p)) ?? (withTotal[0] ?? hits[0]);
    if (pick) return { ...pick, confidence: best.exact ? 'high' : 'medium', byNumber: true };
  }

  if (codes.length) {
    // Without a number, the main-set printing: showcase, borderless and other variants are numbered after it.
    const bySet = printings
      .filter(inReadSet)
      .sort((a, b) => (Number.parseInt(a.number, 10) || Infinity) - (Number.parseInt(b.number, 10) || Infinity))[0];
    if (bySet) return { ...bySet, confidence: 'medium' };
  }
  const byText = await narrowByText(clues, query, game, best.norm);
  if (byText.length) {
    return { ...byText[0], confidence: byText.length === 1 ? 'medium' : 'low', textId: byText[0].text_id, candidates: idsOf(byText) };
  }
  return { ...printings[0], confidence: printings.length === 1 ? 'medium' : 'low', candidates: idsOf(printings) };
}

function idsOf(rows: Row[]) {
  return rows.slice(0, MAX_CANDIDATES).map((r) => r.id);
}

/**
 * Name unreadable (glare, foil) but a Pokemon "number/total" came through: the cards printed with it are only
 * candidates, since one misread digit names a different card. The artwork check has to confirm.
 * Magic prints no total on most frames, so a bare number would match half the game.
 */
async function numberOnly(nums: Clues['numbers'], query: Query, game: Game): Promise<Match | null> {
  if (cardGame(game) !== 'pkm') return null;
  const scope = gameScope(game);
  const seen = new Map<string, Row>();
  for (const n of nums) {
    if (!n.total) continue;
    const rows = await query<Row>(
      `SELECT ${ROW_COLS} FROM cards WHERE ${scope.sql} AND (upper(ltrim(number, '0')) = ? OR number = ?)
       AND upper(ltrim(printed_total, '0')) = ? ORDER BY released DESC LIMIT ?`,
      [...scope.params, n.number, n.number, stripZeros(n.total), MAX_CANDIDATES],
    );
    for (const r of rows) seen.set(r.id, r);
  }
  const rows = [...seen.values()];
  if (!rows.length) return null;
  return { ...rows[0], confidence: 'low', candidates: idsOf(rows), nameUnread: true };
}

/** Among cards whose name starts like the read name, keep those whose rules/attack text matches the most OCR lines. */
async function narrowByText(clues: Clues, query: Query, game: Game, nameNorm: string): Promise<Row[]> {
  if (clues.body.length < 2) return [];
  const pool = await query<Row & { body: string | null }>(
    `SELECT ${ROW_COLS.split(', ').map((c) => `c.${c}`).join(', ')}, t.body FROM cards c LEFT JOIN texts t ON t.id = c.text_id
     WHERE c.${normColumn(game)} LIKE ? AND ${gameScope(game, 'c.').sql} ORDER BY c.released DESC LIMIT 500`,
    [`${nameNorm}%`, ...gameScope(game).params],
  );
  // Pokemon reprints carry identical text under different ids, so compare bodies rather than text ids.
  const scores = new Map<string, number>();
  const bodyOf = new Map<string, string>();
  for (const row of pool) {
    if (!row.body) continue;
    const body = norm(row.body);
    bodyOf.set(row.id, body);
    if (!scores.has(body)) scores.set(body, clues.body.filter((line) => body.includes(line)).length);
  }
  const top = Math.max(0, ...scores.values());
  if (top < 2) return [];
  const winners = [...scores].filter(([, n]) => n === top).map(([body]) => body);
  if (winners.length !== 1) return [];
  return pool.filter((r) => bodyOf.get(r.id) === winners[0]);
}
