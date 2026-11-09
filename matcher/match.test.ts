import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { test } from 'node:test';

import { buildNameIndex, extractClues, resolveCard, type Game, type Line } from './match.ts';

const db = new DatabaseSync(new URL('../data/cards.db', import.meta.url).pathname, { readOnly: true });
const query = async <T,>(sql: string, params: (string | number)[]) => db.prepare(sql).all(...params) as T[];

function index(game: Game) {
  return buildNameIndex(db.prepare('SELECT norm, name FROM ocr_names WHERE game = ?').all(game) as { norm: string; name: string }[]);
}
const indexes = { mtg: index('mtg'), pkm: index('pkm'), pkj: index('pkj'), lor: index('lor') };

function lines(...rows: [string, number][]): Line[] {
  return rows.map(([text, y]) => ({ text, x: 0.05, y, w: 0.5, h: 0.03 }));
}

async function scan(game: Game, l: Line[]) {
  return resolveCard(extractClues(l, indexes[game], game), query, game);
}

test('mtg: name + set code + number pins the printing', async () => {
  const m = await scan('mtg', lines(['Sheoldred, the Apocalypse 2', 0.05], ['Legendary Creature — Phyrexian Praetor', 0.58], ['Deathtouch', 0.65], ['107/281 M', 0.93], ['DMU • EN Chris Rahn', 0.95]));
  assert.equal(m?.name, 'Sheoldred, the Apocalypse');
  assert.equal(m?.set_code, 'DMU');
  assert.equal(m?.number, '107');
  assert.equal(m?.confidence, 'high');
});

test('mtg: OCR typo in the name still matches', async () => {
  const m = await scan('mtg', lines(['Sheo1dred, the Apocalypse', 0.05], ['107/281 M', 0.93], ['DMU EN', 0.95]));
  assert.equal(m?.set_code, 'DMU');
});

test('mtg: an emblem named after its planeswalker is the emblem, not the card', async () => {
  const m = await scan('mtg', lines(['CHANDRA, CHILL OF COMPLIANCE', 0.05], ['Emblem', 0.68], ['Whenever you cast a spell, draw a card.', 0.8], ['E 0017', 0.92], ['FRA • EN KIERAN YANNER', 0.95]));
  assert.equal(m?.name, 'Chandra, Chill of Compliance Emblem');
  assert.equal(m?.set_code, 'TFRA');
});

test('pokemon: stage label with its first letter lost', async () => {
  const m = await scan('pkm', lines(['TAGE2 Talonflame', 0.03], ['Evolves from Fletchinder', 0.06], ['PORIN', 0.93], ['014/088', 0.95]));
  assert.equal(m?.name, 'Talonflame');
  assert.equal(m?.set_code, 'POR');
});

test('pokemon: a misread number with the wrong total falls back to the total', async () => {
  const m = await scan('pkm', lines(['Lampent', 0.03], ['90 0', 0.04], ['PBLI 0.32/084', 0.95]));
  assert.equal(m?.set_code, 'PBL');
  assert.equal(m?.number, '037');
});

test('mtg: a name none of whose printings have the number/total read is marked contradicted', async () => {
  const m = await scan('mtg', lines(['ande   ria', 0.03], ['056/084 PMB1 0', 0.1]));
  assert.equal(m?.contradicted, true);
  assert.equal(m?.confidence, 'low');
});

test('pokemon: a total cut off at the edge is no evidence', async () => {
  const m = await scan('pkm', lines(['BASIC Goldeen', 0.03], ['70', 0.04], ['013/0', 0.95]));
  assert.notEqual(m?.set_code, 'TK9S');
});

test('pokemon: set code + number/total with the name cropped beats a Magic set with the same code', async () => {
  const read = lines(['Evolves Irom Doublade', 0.03], ['POR058/088', 0.95]);
  const pkm = await scan('pkm', read);
  assert.equal(pkm?.name, 'Aegislash');
  assert.equal(pkm?.totalAgrees, true);
  const mtg = await scan('mtg', read);
  assert.ok(!mtg || (!mtg.totalAgrees && mtg.confidence === pkm?.confidence));
});

test('pokemon: a Nidoran is told apart by its number', async () => {
  const m = await scan('pkm', lines(['BASIC Nidoran♀ HP60', 0.05], ['Find a Friend', 0.6], ['DLR EN 055/103', 0.95]));
  assert.equal(m?.name, 'Nidoran♀');
  assert.equal(m?.set_code, 'DLR');
});

test('mtg: name only falls back to a printing of that card', async () => {
  const m = await scan('mtg', lines(['Lightning Bolt', 0.05], ['Instant', 0.58]));
  assert.equal(m?.name, 'Lightning Bolt');
});

test('pokemon: name with HP and number', async () => {
  const m = await scan('pkm', lines(['BASIC', 0.03], ['Pikachu ex HP200', 0.05], ['Resolute Heart', 0.55], ['Topaz Bolt 300', 0.7], ['GIDORA', 0.93], ['H SSP EN 238/191', 0.95]));
  assert.equal(m?.name, 'Pikachu ex');
  assert.equal(m?.number, '238');
  assert.equal(m?.set_code, 'SSP');
});

test('pokemon: stage label whose digit the OCR dropped', async () => {
  const m = await scan('pkm', lines(['STAGE Kilowattrel 120', 0.03], ['H DLR EN 041/103', 0.95]));
  assert.equal(m?.name, 'Kilowattrel');
  assert.equal(m?.set_code, 'DLR');
});

test('pokemon: trainer name under its label, name strip read on its own', async () => {
  const m = await scan('pkm', lines(['Pokémon Tool TRAINER', 0.04], ['Custom Vest', 0.1]));
  assert.equal(m?.name, 'Custom Vest');
});

test('pokemon: set code read glued to the number', async () => {
  const m = await scan('pkm', lines(['Custom Vest', 0.05], ['Illus. Toyste Beach', 0.9], ['DLR091/103', 0.95]));
  assert.equal(m?.number, '091');
  assert.equal(m?.confidence, 'high');
});

test('pokemon: vintage number with total', async () => {
  const m = await scan('pkm', lines(['Charizard', 0.05], ['120 HP', 0.05], ['Fire Spin', 0.7], ['4/102', 0.95]));
  assert.equal(m?.name, 'Charizard');
  assert.equal(m?.number, '4');
});

test('pokemon: basic energy keeps "Energy" in the name', async () => {
  const m = await scan('pkm', lines(['Basic Fire Energy', 0.05], ['153/159', 0.95]));
  assert.equal(m?.name, 'Fire Energy');
  assert.equal(m?.set_code, 'CRZ');
});

test('pokemon: SVE/MEE basic energy from the set code and bare number', async () => {
  for (const [bottom, set, number] of [['SVE EN 002', 'SVE', '002'], ['SVEEN 010', 'SVE', '010'], ['MEE EN 002', 'MEE', '002']]) {
    const m = await scan('pkm', lines(['Basic Energy ENERGY', 0.03], ['Basic & Energy', 0.08], ['©2023 Pokémon', 0.97], [bottom, 0.94]));
    assert.equal(m?.name, 'Fire Energy', bottom);
    assert.equal(m?.set_code, set);
    assert.equal(m?.number, number);
  }
  const split = await scan('pkm', lines(['Basic Energy', 0.03], ['SVE EN', 0.94], ['018', 0.94]));
  assert.equal(split?.id, 'sve-018');
});

test('pokemon: special energy and energy trainers', async () => {
  assert.equal((await scan('pkm', lines(['Special Energy', 0.03], ['Jet Energy', 0.05], ['190/193', 0.95])))?.name, 'Jet Energy');
  assert.equal((await scan('pkm', lines(['Energy Switch', 0.05], ['Item', 0.08], ['Trainer', 0.95])))?.name, 'Energy Switch');
});

test('nothing recognisable returns null', async () => {
  assert.equal(await scan('mtg', lines(['the quick brown fox', 0.3])), null);
});

test('pokemon: name-only read with many printings is low confidence', async () => {
  const m = await scan('pkm', lines(['Pikachu', 0.28], ['Resolute Heart', 0.51], ['Topaz Bolt', 0.6]));
  assert.equal(m?.confidence, 'low');
});

test('pokemon: dropped suffix is recovered from the number', async () => {
  const m = await scan('pkm', lines(['Pikachu', 0.28], ['Topaz Bolt', 0.6], ['238/191', 0.95]));
  assert.equal(m?.name, 'Pikachu ex');
  assert.equal(m?.number, '238');
});

test('mtg: power/toughness is not mistaken for the collector number', async () => {
  const m = await scan('mtg', lines(['Sheoldred, the Apocalypse 2', 0.25], ['4/5', 0.66], ['107/28 M', 0.66], ['DMUEN SGHs RANN', 0.67]));
  assert.equal(m?.set_code, 'DMU');
  assert.equal(m?.number, '107');
});

test('pokemon: regulation mark glued to set code', async () => {
  const m = await scan('pkm', lines(['Pikachu ex', 0.03], ['HSSPEN 238/191', 0.94]));
  assert.equal(m?.set_code, 'SSP');
  assert.equal(m?.confidence, 'high');
});

test('mtg: bare modern collector number when the card sits mid-photo', async () => {
  const m = await scan('mtg', lines(['Lightning Bolt', 0.3], ['Instant', 0.5], ['Lightning Bolt deals 3 damage to any target.', 0.55], ['U 0163', 0.68], ['FDC • EN CHRISTOPHER MOELLER', 0.7]));
  assert.equal(m?.set_code, 'FDC');
  assert.equal(m?.number, '163');
});

test('mtg: rarity letter glued to the collector number', async () => {
  const m = await scan('mtg', lines(['Thorin Oakenshield', 0.05], ['3/2', 0.9], ['U0165', 0.92], ['HOB· EN FRANCISCO MIYARA', 0.94]));
  assert.equal(m?.number, '165');
  assert.equal(m?.confidence, 'high');
});

test('mtg: set code without a number picks the main-set printing, not a variant', async () => {
  const m = await scan('mtg', lines(['Thorin Oakenshield', 0.05], ['HOB·EN FRANCISCO MIYARA', 0.94]));
  assert.equal(m?.set_code, 'HOB');
  assert.equal(m?.number, '165');
  assert.equal(m?.confidence, 'medium');
});

test('pokemon: stray leading digit and mangled suffix from a real OCR read', async () => {
  const m = await scan('pkm', lines(['BASIG', 0.13], ['Pikachux', 0.11], ['Topaz Bolt', 0.72], ['MOORA', 0.9], ['9238/191', 0.92]));
  assert.equal(m?.name, 'Pikachu ex');
  assert.equal(m?.number, '238');
});

test('pokemon: rules text numbers are not collector numbers', async () => {
  const m = await scan('pkm', lines(['Pikachu', 0.11], ['is Knocked Out, your opponent takes 2 Prize cards.', 0.9], ['224 Pokmon /Nintendo', 0.94]));
  assert.equal(m?.confidence, 'low');
});

test('pokemon: attack text recovers the card when the number is unreadable', async () => {
  const m = await scan('pkm', lines(['BASIG', 0.13], ['Pikachu', 0.12], ['Ability Resolute Heart', 0.56], ['remaining HP becomes 10.', 0.65], ['Topaz Bolt', 0.72], ['Discard 3 Energy from this Pokémon.', 0.75]));
  assert.equal(m?.name, 'Pikachu ex');
  assert.ok(m?.textId);
});

test('mtg: rules text is not read as a short card name', async () => {
  const m = await scan('mtg', lines(['Deathtouch', 0.1], ['Whenever you draw a card, you gain', 0.2], ['2 life.', 0.3], ['they lose 2 life.', 0.85]));
  assert.equal(m, null);
});

test('pokemon variants: patterned reverses get their own key, label and price', () => {
  const rows = db
    .prepare("SELECT v.key, v.label, b.usd FROM variants v LEFT JOIN base_prices b ON b.card_id = v.card_id AND b.finish = v.key WHERE v.card_id = 'sv08.5-001' ORDER BY v.sort")
    .all() as { key: string; label: string; usd: number | null }[];
  const keys = rows.map((r) => r.key);
  assert.deepEqual(keys.slice(0, 2), ['normal', 'reverse']);
  assert.ok(keys.includes('reverse:pokeball') && keys.includes('reverse:masterball'));
  assert.equal(rows.find((r) => r.key === 'reverse:masterball')?.label, 'Master Ball reverse');
  assert.ok(rows.every((r) => r.usd != null));
});

test('pokemon variants: 1st edition and shadowless are separate', () => {
  const keys = (db.prepare("SELECT key FROM variants WHERE card_id = 'base1-4'").all() as { key: string }[]).map((r) => r.key);
  assert.ok(keys.includes('holo:shadowless:1st-edition') && keys.includes('holo:shadowless') && keys.includes('holo:unlimited'));
});

test('pokemon: unreadable name with number/total gives candidates for the artwork check', async () => {
  const m = await scan('pkm', lines(['Ch@r!z4rd', 0.05], ['Fire Spin', 0.7], ['4/102', 0.95]));
  assert.equal(m?.confidence, 'low');
  assert.equal(m?.nameUnread, true);
  assert.ok(m?.candidates?.includes('base1-4'));
  assert.ok(m!.candidates!.length <= 5);
});

test('mtg: a bare number without name or set code is not guessed', async () => {
  assert.equal(await scan('mtg', lines(['xqzt', 0.05], ['107/281 M', 0.93])), null);
});

test('japanese: printed name, set code and number pin the printing', async () => {
  const m = await scan('pkj', lines(['2進化', 0.03], ['リザードンexHP330', 0.05], ['リザードから進化', 0.08], ['ブレイブウイング', 0.6], ['G SV2a 006/165 RR', 0.95]));
  assert.equal(m?.id, 'ja-SV2a-006');
  assert.equal(m?.name, 'Charizard ex');
  assert.equal(m?.confidence, 'high');
});

test('japanese: evolves-from line does not name the card', async () => {
  const m = await scan('pkj', lines(['リザードから進化', 0.03], ['リザードンex', 0.05], ['201/165', 0.95]));
  assert.equal(m?.id, 'ja-SV2a-201');
});

test('japanese: kana OCR slips still match', async () => {
  // missing dakuten and the kanji "one" read for the long vowel mark
  const m = await scan('pkj', lines(['リサ一ドンex', 0.05], ['SV2a 006/165', 0.95]));
  assert.equal(m?.id, 'ja-SV2a-006');
});

test('japanese: promo number with the set in place of the total', async () => {
  const m = await scan('pkj', lines(['ピカチュウ', 0.05], ['001/SV-P', 0.95]));
  assert.equal(m?.id, 'ja-SV-P-001');
});

test('japanese: english scan mode never returns japanese cards and vice versa', async () => {
  const en = await scan('pkm', lines(['Charizard ex', 0.05], ['006/165', 0.95]));
  assert.ok(en && !en.id.startsWith('ja-'));
  const ja = await scan('pkj', lines(['リザードンex', 0.05], ['006/165', 0.95]));
  assert.ok(ja?.id.startsWith('ja-'));
});

test('pokemon jp: rarity read glued onto the total', async () => {
  const m = await scan('pkj', lines(['たね ヒトデマン', 0.03], ['HP 60', 0.05], ['120/165c', 0.95]));
  assert.equal(m?.id, 'ja-SV2a-120');
});

test('pokemon jp: lowercase set code and furigana after the name', async () => {
  const m = await scan('pkj', lines(['推理セット すい', 0.03], ['グッズ', 0.05], ['sv8', 0.94], ['094/106 U', 0.95]));
  assert.equal(m?.id, 'ja-SV8-094');
  assert.equal(m?.confidence, 'high');
});

test('pokemon jp: deck code without digits tells two decks apart', async () => {
  const m = await scan('pkj', lines(['サポート', 0.02], ['ナンペジャモ', 0.04], ['svLN', 0.94], ['019/022', 0.95]));
  assert.equal(m?.id, 'ja-SVLN-019');
});

test('lorcana: name, version and collector line pin the printing', async () => {
  const m = await scan('lor', lines(['3', 0.02], ['OWL', 0.55], ['3', 0.555], ['Pirate Lookout', 0.59], ['Storyborn • Ally • Pirate', 0.64], ['Omar Lozano', 0.955], ['1/204 • EN • 6', 0.97]));
  assert.equal(m?.name, 'Owl - Pirate Lookout');
  assert.equal(m?.set_code, '6');
  assert.equal(m?.number, '1');
  assert.equal(m?.confidence, 'high');
});

test('lorcana: version unread, the number picks it', async () => {
  const m = await scan('lor', lines(['TIANA', 0.62], ['Restau#nt 0wn', 0.655], ['16/204·EN·6', 0.97]));
  assert.equal(m?.name, 'Tiana - Restaurant Owner');
  assert.equal(m?.set_code, '6');
});

test('lorcana: an action has no version line', async () => {
  const m = await scan('lor', lines(['GOOD JOB!', 0.57], ['Action', 0.64], ['Chosen character gets +1 this turn.', 0.75], ['27/204 EN 6', 0.97]));
  assert.equal(m?.name, 'Good Job!');
  assert.equal(m?.number, '27');
});

test('lorcana: collector line alone settles it', async () => {
  const m = await scan('lor', lines(['205/204 • EN • 6', 0.97]));
  assert.equal(m?.name, "Chip 'n' Dale - Recovery Rangers");
  assert.equal(m?.confidence, 'medium');
});

test('lorcana: name without a number still finds the card', async () => {
  const m = await scan('lor', lines(['NAVEENS UKULELE', 0.57], ['Item', 0.64]));
  assert.equal(m?.name, "Naveen's Ukulele");
});
