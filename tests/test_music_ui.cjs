const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');

test('all nine model speakers are exposed in the picker', () => {
  for (const v of ['Serena', 'Vivian', 'Ono_Anna', 'Sohee', 'Uncle_Fu', 'Ryan', 'Aiden', 'Eric', 'Dylan']) {
    assert.match(js, new RegExp(`value="${v}"`));
  }
  assert.match(js, /四川话/);
  assert.match(js, /北京话/);
});

test('music tab renders the local library and can set BGM', () => {
  assert.match(js, /renderMusicLibrary/);
  assert.match(js, /\/api\/music/);
  assert.match(js, /function useMusic\(/);
  assert.match(js, /function previewMusic\(/);
  assert.match(js, /用作 BGM/);
  assert.match(js, /musicMood/);
});
