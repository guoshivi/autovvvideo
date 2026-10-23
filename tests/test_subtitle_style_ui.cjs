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
    '#hlOn': {checked: true}, '#stickerOn': {checked: true}, '#sfxOn': {checked: true}, '#flowerOn': {checked: true}, '#flowerStyle': {value: 'burst_red'}, '#flowerDensity': {value: 'max'},
    '#secParams': {innerHTML: ''}, '#secAdvanced': {innerHTML: ''}, '#secBatch': {innerHTML: ''},
  };
  const ctx = vm.createContext({document: {querySelector: (s) => nodes[s] || null}, window: {}, console, setTimeout: (fn) => { fn(); return 0; }, clearTimeout, AbortController, fetch: () => { throw Error('unexpected fetch'); }});
  vm.runInContext(source, ctx);
  ctx.product = {id: 'a', name: 'A', settings: {}};
  vm.runInContext('state.product=product;state.products=[product]', ctx);
  return {ctx, nodes};
}

function htmlOf(f){return ['#secScript','#secParams','#secAdvanced','#secBatch'].map(k=>f.nodes[k]?.innerHTML||'').join('\n')}

test('subtitle style picker exposes every advertised preset', () => {
  const f = make('classic');
  vm.runInContext('renderScript()', f.ctx);
  const html = htmlOf(f);
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
  const html = htmlOf(f);
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


test('flower text has its own on/off checkbox and defaults on', () => {
  const f = make('classic');
  vm.runInContext('renderScript()', f.ctx);
  assert.match(htmlOf(f), /id="flowerOn"/);
  assert.match(htmlOf(f), /花字/);
  assert.equal(vm.runInContext('collectSettings().flower_enabled', f.ctx), true);
  f.nodes['#flowerOn'].checked = false;
  assert.equal(vm.runInContext('collectSettings().flower_enabled', f.ctx), false);
});


test('flower style picker lists auto plus every style and is sent through', () => {
  const f = make('classic');
  vm.runInContext('renderScript()', f.ctx);
  const html = htmlOf(f);
  assert.match(html, /id="flowerStyle"/);
  for (const value of ['auto', 'burst_red', 'burst_orange', 'glow_title', 'bottom_glow', 'badge', 'tape', 'bubble', 'neon', 'brush', 'shadow3d']) {
    assert.match(html, new RegExp(`value="${value}"`));
  }
  assert.equal(vm.runInContext('collectSettings().flower_style', f.ctx), 'burst_red');
});


test('flower density picker offers four levels and is sent through', () => {
  const f = make('classic');
  vm.runInContext('renderScript()', f.ctx);
  const html = htmlOf(f);
  assert.match(html, /id="flowerDensity"/);
  for (const value of ['low', 'normal', 'high', 'max']) assert.match(html, new RegExp(`value="${value}"`));
  assert.equal(vm.runInContext('collectSettings().flower_density', f.ctx), 'max');
});
