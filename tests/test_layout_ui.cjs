const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static', 'style.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');

test('three-column shell exists and keeps left/center/right', () => {
  for (const id of ['secMaterials', 'secScript', 'wbPreview', 'secQueue', 'secResults', 'secParams']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /class="shell3"/);
  assert.match(css, /grid-template-columns:390px/);
});

test('right panel exposes params/advanced/batch tabs', () => {
  for (const tab of ['params', 'advanced', 'batch']) {
    assert.match(html, new RegExp(`data-wb-tab="${tab}"`));
  }
  assert.match(js, /function setWbTab/);
  assert.match(js, /html\.indexOf\('<div class="settings-row">'\)/);
});

test('existing control ids survive the restyle', () => {
  for (const id of ['copy', 'subStyle', 'flowerStyle', 'flowerDensity', 'btnGo']) {
    assert.match(js, new RegExp(`id="${id}"`));
  }
});
