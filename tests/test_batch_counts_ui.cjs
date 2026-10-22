// Run explicitly: node --test tests/test_batch_counts_ui.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8').split('/* ---------- init ---------- */')[0];

function make(copy) {
  const nodes = {
    '#copy': {value: copy},
    '#batchExtra': {innerHTML: ''},
    '#batchInfo': {textContent: ''},
  };
  const ctx = vm.createContext({document: {querySelector: (s) => nodes[s] || null}, window: {}, console, setTimeout: (fn) => { fn(); return 0; }, clearTimeout, AbortController, fetch: () => { throw Error('unexpected fetch'); }});
  vm.runInContext(source, ctx);
  vm.runInContext("state.batchMode='multi'", ctx);
  return {ctx, nodes};
}

test('multi mode renders one count input per script', () => {
  const f = make('第一条文案\n---\n第二条文案');
  vm.runInContext('renderBatchBox()', f.ctx);
  const html = f.nodes['#batchExtra'].innerHTML;
  assert.equal((html.match(/class="scriptCount"/g) || []).length, 2);
  assert.match(html, /第 1 条/);
  assert.match(html, /第 2 条/);
  assert.match(f.nodes['#batchInfo'].textContent, /共 2 条视频/);
});

test('per-script counts survive a re-render and feed the total', () => {
  const f = make('甲\n---\n乙');
  vm.runInContext('state.batchCounts=[2,3];renderBatchBox()', f.ctx);
  const html = f.nodes['#batchExtra'].innerHTML;
  assert.match(html, /data-i="0"[^>]*value="2"/);
  assert.match(html, /data-i="1"[^>]*value="3"/);
  assert.match(f.nodes['#batchInfo'].textContent, /共 5 条视频/);
});

test('count input is clamped to 1..500', () => {
  const f = make('甲');
  vm.runInContext('onScriptCount(0,"999")', f.ctx);
  assert.equal(vm.runInContext('state.batchCounts[0]', f.ctx), 500);
  vm.runInContext('onScriptCount(0,"0")', f.ctx);
  assert.equal(vm.runInContext('state.batchCounts[0]', f.ctx), 1);
});
