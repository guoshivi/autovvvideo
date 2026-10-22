// Run explicitly: node --test tests/test_subtitle_style_ui.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8').split('/* ---------- init ---------- */')[0];

function make(style) {
  const nodes = {
    '#secScript': {innerHTML: ''}, '#copy': {value: '甲'}, '#voice': {checked: true}, '#subs': {checked: true},
    '#voiceEngine': {value: 'qwen'}, '#voxVoice': {value: 'natural_female'}, '#speaker': {value: 'Serena'}, '#speed': {value: '1'},
    '#target': {value: ''}, '#subSize': {value: '54'}, '#subPos': {value: '380'}, '#subBorder': {value: '3'},
    '#subStyle': {value: style}, '#shotReuse': {value: '1'}, '#dedup': {value: 'off'}, '#beauty': {checked: false},
    '#beautyStrength': {value: '.25'}, '#visualFilter': {value: 'original'}, '#visualFilterStrength': {value: '.35'},
    '#sentenceGapMode': {value: 'auto'}, '#sentenceGap': {value: ''},
    '#hlOn': {checked: true}, '#stickerOn': {checked: true}, '#sfxOn': {checked: true},
  };
  const ctx = vm.createContext({document: {querySelector: (s) => nodes[s] || null}, window: {}, console, setTimeout: (fn) => { fn(); return 0; }, clearTimeout, AbortController, fetch: () => { throw Error('unexpected fetch'); }});
  vm.runInContext(source, ctx);
  ctx.product = {id: 'a', name: 'A', settings: {}};
  vm.runInContext('state.product=product;state.products=[product]', ctx);
  return {ctx, nodes};
}

test('subtitle style picker exposes every advertised preset', () => {
  const f = make('classic');
  vm.runInContext('renderScript()', f.ctx);
  const html = f.nodes['#secScript'].innerHTML;
  assert.match(html, /id="subStyle"/);
  for (const value of ['classic', 'neon', 'peach', 'mint', 'orange', 'minimal', 'legacy']) {
    assert.match(html, new RegExp(`value="${value}"`));
  }
});

test('collectSettings carries the selected subtitle style', () => {
  const f = make('mint');
  assert.equal(vm.runInContext('collectSettings().subtitle_style', f.ctx), 'mint');
});

test('missing picker falls back to the product setting and never sends an invented style', () => {
  const f = make('classic');
  f.ctx.next = {id: 'b', name: 'B', settings: {subtitle_style: 'peach'}};
  vm.runInContext('state.product=next;state.products=[next]', f.ctx);
  delete f.nodes['#subStyle'];
  assert.equal(vm.runInContext('collectSettings().subtitle_style', f.ctx), 'peach');
});


test('highlight, sticker and sfx each have an on/off checkbox', () => {
  const f = make('classic');
  vm.runInContext('renderScript()', f.ctx);
  const html = f.nodes['#secScript'].innerHTML;
  for (const id of ['hlOn', 'stickerOn', 'sfxOn']) assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /重点词高亮/);
  assert.match(html, /贴图/);
  assert.match(html, /音效/);
});

test('unchecked switches are sent as false', () => {
  const f = make('classic');
  f.nodes['#stickerOn'].checked = false;
  f.nodes['#sfxOn'].checked = false;
  const s = vm.runInContext('collectSettings()', f.ctx);
  assert.equal(s.sticker_enabled, false);
  assert.equal(s.sfx_enabled, false);
  assert.equal(s.subtitle_highlight, true);
});
