// match.ts behind a JSON-lines pipe for identify.py. Request: {"reads": [{"key": "up", "lines": [...]}, ...],
// "games": ["mtg", "pkm", "pkj"]}, lines in card-relative 0..1 coordinates. Reply: the best match over every read and
// game, or {"match": null}.
import { DatabaseSync } from 'node:sqlite';
import { createInterface } from 'node:readline';

import { buildNameIndex, extractClues, resolveCard, type Game, type Line, type Match } from './match.ts';

const db = new DatabaseSync(new URL('../data/cards.db', import.meta.url).pathname, { readOnly: true });
const query = async <T,>(sql: string, params: (string | number)[]) => db.prepare(sql).all(...params) as T[];

const indexes = new Map<Game, ReturnType<typeof buildNameIndex>>();
function index(game: Game) {
  let i = indexes.get(game);
  if (!i) {
    i = buildNameIndex(db.prepare('SELECT norm, name FROM ocr_names WHERE game = ?').all(game) as { norm: string; name: string }[]);
    indexes.set(game, i);
  }
  return i;
}

const RANK = { high: 3, medium: 2, low: 1 } as const;

type Request = { reads: { key: string; lines: Line[] }[]; games: Game[] };

async function handle(req: Request) {
  let best: { match: Match; key: string; game: Game } | null = null;
  for (const game of req.games) {
    for (const read of req.reads) {
      const match = await resolveCard(extractClues(read.lines, index(game), game), query, game);
      if (match && (!best || RANK[match.confidence] > RANK[best.match.confidence])) best = { match, key: read.key, game };
    }
  }
  if (!best) return { match: null };
  const card = db
    .prepare('SELECT set_name, rarity, image, lang FROM cards WHERE id = ?')
    .get(best.match.id) as { set_name: string; rarity: string | null; image: string | null; lang: string };
  return { ...best, card };
}

const rl = createInterface({ input: process.stdin });
for await (const raw of rl) {
  if (!raw.trim()) continue;
  process.stdout.write(`${JSON.stringify(await handle(JSON.parse(raw) as Request))}\n`);
}
