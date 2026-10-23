const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const js = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'static', 'app.js'), 'utf8');

test('brand controls cover logo upload and watermark', () => {
  for (const id of ['logoOn', 'logoFile', 'logoPos', 'logoScale', 'logoOpacity', 'wmOn', 'wmText', 'wmPos', 'wmSize', 'wmOpacity']) {
    assert.match(js, new RegExp(`id="${id}"`));
  }
  assert.match(js, /overlay-file/);
  assert.match(js, /renderBrandBox/);
});

test('brand settings survive collectSettings', () => {
  for (const key of ['logo_enabled', 'logo_path', 'logo_sha256', 'watermark_enabled', 'watermark_text']) {
    assert.match(js, new RegExp(`${key}:`));
  }
});
