const SMALL_KANA = 'ァィゥェォッャュョヮヵヶ';
const LARGE_KANA = 'アイウエオツヤユヨワカケ';
const KANJI_LOOKALIKE = '力口工二夕卜八';
const KATAKANA_LOOKALIKE = 'カロエニタトハ';

/**
 * OCR slips on Japanese: hiragana/katakana twins (へ/ヘ), small vs full-size kana and kanji that look like katakana.
 * Fold them all to one katakana form.
 */
function foldKana(ch: string) {
  const cp = ch.codePointAt(0)!;
  if (cp >= 0x3041 && cp <= 0x3096) return String.fromCodePoint(cp + 0x60);
  let i = SMALL_KANA.indexOf(ch);
  if (i >= 0) return LARGE_KANA[i];
  i = KANJI_LOOKALIKE.indexOf(ch);
  return i >= 0 ? KATAKANA_LOOKALIKE[i] : ch;
}

/**
 * Must match norm() in tools/build_db.py. Latin text keeps only a-z0-9; Japanese keeps katakana and kanji but drops
 * dakuten and the long vowel mark, which OCR misses or swaps too often.
 */
export function norm(s: string) {
  return s
    .normalize('NFKD')
    .toLowerCase()
    .replace(/[\u3041-\u3096ァィゥェォッャュョヮヵヶ力口工二夕卜八]/g, foldKana)
    .replace(/[^a-z0-9\u30a1-\u30fa\u3400-\u4dbf\u4e00-\u9fff]|一/g, '');
}
