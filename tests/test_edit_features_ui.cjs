const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static', 'index.html'), 'utf8');

test('voice volume and gender filter exist in the voice tab', () => {
  assert.match(js, /id="voiceVol"/);
  assert.match(js, /filterSpeakers/);
  assert.match(js, /data-g="女声"/);
  assert.match(js, /data-g="方言"/);
});

test('pronunciation editor is in the subtitle tab and keeps original subtitles', () => {
  assert.match(js, /pronBox/);
  assert.match(js, /renderPronunciationBox/);
  assert.match(js, /只改配音，不改字幕/);
});

test('per-line subtitle editor is its own left panel', () => {
  assert.match(html, /id="secSubtitleEditor"/);
  assert.match(js, /renderSubtitleEditor/);
  assert.match(js, /updateSubOverride/);
  assert.match(js, /只改显示，不改配音/);
});
