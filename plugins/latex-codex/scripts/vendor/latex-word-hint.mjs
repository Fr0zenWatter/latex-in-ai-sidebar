import {englishWords} from './english-words.mjs';

let cachedSource, cachedWords, cachedFrom, cachedTo, projectWords = [];
function documentWords(editor, from, to) {
  const source = editor.getValue(), words = editor.state?.englishProjectWords;
  const start = editor.indexFromPos(from), end = editor.indexFromPos(to);
  if (source !== cachedSource || words !== cachedWords || start !== cachedFrom || end !== cachedTo) {
    const terms = new Set([...source.matchAll(/(?<![A-Za-z\\])[A-Za-z]{3,64}\b/g)]
      .filter(match => match.index >= end || match.index + match[0].length <= start)
      .map(match => match[0].toLowerCase()));
    if (Array.isArray(words)) for (const word of words) {
      if (typeof word === 'string' && /^[A-Za-z]{3,64}$/.test(word)) terms.add(word.toLowerCase());
    }
    cachedSource = source; cachedWords = words; cachedFrom = start; cachedTo = end; projectWords = [...terms];
  }
  return projectWords;
}

function matchWord(word, query) {
  if (word.length < query.length) return null;
  let position = -1, start = -1;
  for (const letter of query) {
    position = word.indexOf(letter, position + 1);
    if (position < 0) return null;
    if (start < 0) start = position;
  }
  return {exact: word === query, prefix: word.startsWith(query), start, gaps: position - start + 1 - query.length};
}

export function englishHint(editor) {
  const cursor = editor.getCursor(), line = editor.getLine(cursor.line), before = line.slice(0, cursor.ch);
  const match = before.match(/[A-Za-z]+$/);
  if (!match || match[0].length < 3 || /[\w\\@.:/]/.test(before[match.index - 1] || '')) return null;
  const types = (editor.getTokenTypeAt(cursor) || '').split(' ');
  if (types.some(type => ['comment', 'math', 'latex-math', 'atom', 'number', 'tag', 'operator',
    'reference', 'environment', 'string', 'string-2', 'link', 'url'].includes(type))) return null;
  const rest = line.slice(cursor.ch).match(/^[A-Za-z]*/)[0];
  if (/[\w]/.test(line[cursor.ch + rest.length] || '')) return null;
  const query = match[0], lower = query.toLowerCase(), candidates = [], seen = new Set();
  const from = {line: cursor.line, ch: match.index}, to = {line: cursor.line, ch: cursor.ch + rest.length};
  for (const [words, project] of [[documentWords(editor, from, to), true], [englishWords, false]]) {
    for (const [rank, word] of words.entries()) {
      if (seen.has(word)) continue;
      seen.add(word);
      const score = matchWord(word, lower);
      if (score) candidates.push({word, project, rank, ...score});
    }
  }
  if (!candidates.length) return null;
  candidates.sort((a, b) => Number(b.exact) - Number(a.exact) || Number(b.prefix) - Number(a.prefix) || a.start - b.start
    || Number(b.project) - Number(a.project) || a.gaps - b.gaps
    || (a.prefix ? 0 : a.word.length - b.word.length) || a.rank - b.rank);
  return {
    list: candidates.slice(0, 30).map(({word}) => query === query.toUpperCase() ? word.toUpperCase()
      : query[0] === query[0].toUpperCase() ? word[0].toUpperCase() + word.slice(1) : word),
    from, to,
  };
}
