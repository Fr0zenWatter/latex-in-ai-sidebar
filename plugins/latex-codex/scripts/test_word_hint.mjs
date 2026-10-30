// Run: node test_word_hint.mjs (stdlib and bundled assets; no browser or network).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {englishHint} from './vendor/latex-word-hint.mjs';
import {englishWords} from './vendor/english-words.mjs';

const CodeMirror = {};
const relocatedRequire = Object.assign(() => CodeMirror, {resolve: name => name, cache: {}});
vm.runInNewContext(fs.readFileSync(new URL('./vendor/runmode.node.js', import.meta.url), 'utf8'),
  {exports: CodeMirror, module: {exports: CodeMirror}, require: relocatedRequire});
for (const file of ['stex.js', 'xml.js', 'markdown.js', 'multiplex.js', 'latex-markdown.js']) {
  vm.runInNewContext(fs.readFileSync(new URL('./vendor/' + file, import.meta.url), 'utf8'), {CodeMirror});
}
globalThis.fetch = () => { throw new Error('Dictionary completion must stay offline.'); };
function complete(text, mode = 'text/x-stex', projectWords = []) {
  const index = text.indexOf('|'), before = text.slice(0, index), value = text.replace('|', '');
  const cursor = {line: before.split('\n').length - 1, ch: before.split('\n').at(-1).length};
  let type = '';
  CodeMirror.runMode(before, mode, (token, style, line, ch) => {
    if (line === cursor.line && ch + token.length >= cursor.ch) type = style || '';
  });
  return englishHint({getCursor: () => cursor, getLine: line => value.split('\n')[line],
    getTokenTypeAt: () => type, getValue: () => value,
    indexFromPos: pos => value.split('\n').slice(0, pos.line).reduce((n, line) => n + line.length + 1, 0) + pos.ch,
    state: {englishProjectWords: projectWords}});
}
assert.equal(englishWords.length, 9800);
assert.equal(new Set(englishWords).size, englishWords.length);
assert(englishWords.every(word => /^[a-z]+$/.test(word)));
assert.equal(complete('analy|').list[0], 'analysis', 'Common prose words precede obscure dictionary entries.');
for (const word of ['pap', 'pape', 'paper']) {
  assert.equal(complete(word + '|').list[0], 'paper', word);
}
assert.equal(complete('paper|', 'text/x-stex', ['papers', 'paperwork']).list[0], 'paper', 'Exact words precede longer project words.');
assert.equal(complete('Paper|').list[0], 'Paper');
assert.equal(complete('PAPER|').list[0], 'PAPER');
const finished = complete('A paper|, already typed.');
const finishedSource = 'A paper, already typed.';
assert.equal(finishedSource.slice(0, finished.from.ch) + finished.list[0] + finishedSource.slice(finished.to.ch), finishedSource,
  'Accepting an exact word replaces its range once without duplicating text or punctuation.');
assert.equal(complete('hyperconvergence|', 'text/x-stex', ['hyperconvergence']).list[0], 'hyperconvergence');
assert.equal(complete('Hyperconvergence appears. hyperconvergence|').list[0], 'hyperconvergence');
assert(!complete('unregisteredword|')?.list.includes('unregisteredword'), 'The current unconfirmed token cannot become its own candidate.');
const stableSource = 'customalpha custombeta', stableWords = [];
let stableCursor = 11;
const stableEditor = {getValue: () => stableSource, getLine: () => stableSource,
  getCursor: () => ({line: 0, ch: stableCursor}), indexFromPos: pos => pos.ch,
  getTokenTypeAt: () => '', state: {englishProjectWords: stableWords}};
assert(!englishHint(stableEditor)?.list.includes('customalpha'));
stableCursor = stableSource.length;
assert(!englishHint(stableEditor)?.list.includes('custombeta'), 'Moving the cursor refreshes which token is excluded.');
assert(complete('theo|').list.includes('theorem'));
assert(complete('mathemati|').list.includes('mathematics'));
assert(complete('Ana|').list.includes('Analysis'));
assert(complete('ANA|').list.includes('ANALYSIS'));
assert.equal(complete('rsrch|').list[0], 'research');
assert.equal(complete('RSRCH|').list[0], 'RESEARCH');
assert(complete('reserch|').list.includes('research'), 'Omitted letters match without changing their order.');
assert(complete('srch|').list.includes('research'), 'A compact match may start inside a word.');
assert.equal(complete('hycnv|', 'text/x-stex', ['hyperconvergence']).list[0], 'hyperconvergence');
assert.equal(complete('hycnv|', 'text/x-stex', [])?.list.includes('hyperconvergence') || false, false, 'Switching projects clears their vocabulary.');
assert.equal(complete('yhcnv|', 'text/x-stex', ['hyperconvergence'])?.list.includes('hyperconvergence') || false, false, 'Letters must stay in order.');
assert.equal(complete('rese|', 'text/x-stex', ['unrelatedresection']).list[0], 'research', 'Whole prefixes precede fuzzy project matches.');
const middle = complete('The ana|lysis, continues.');
assert(middle.list.includes('analysis'));
assert.deepEqual(middle.from, {line: 0, ch: 4});
assert.deepEqual(middle.to, {line: 0, ch: 12}, 'Accepting replaces the entire word without eating punctuation.');
const terms = complete('We prove superconvergence and Supercontinuity.\nThe supercon|');
assert.equal(terms.list[0], 'superconvergence', 'Document terminology is preferred even when absent from the dictionary.');
assert.equal(new Set(terms.list).size, terms.list.length);
assert(terms.list.length <= 30);
assert(complete('\\section{analy|}').list.includes('analysis'));
assert(complete('\\textbf{analy|}').list.includes('analysis'));
for (const text of ['an|', '\\analy|', '% analy|', '$analy|', '\\[analy|', '\\begin{align}\nanaly|',
  '\\ref{analy|', '\\label{analy|', '\\cite{analy|', '\\begin{analy|', '\\usepackage{analy|',
  'name_analy|', 'analy|123', 'https://exam|', 'paper.tex|', 'me@exam|']) {
  assert.equal(complete(text), null, text);
}
assert(complete('# Analy|', 'obsidian-md').list.includes('Analysis'));
for (const text of ['`analy|`', '```\nanaly|', '$analy|', '[link](https://exam|)']) {
  assert.equal(complete(text, 'obsidian-md'), null, text);
}
console.log('PASS: 9,800 offline words, project/draft terms, ordered-letter abbreviations, prefix ranking, casing, exact replacement and TeX/Markdown isolation');
