"""A build that dies mid-run must be resumable instead of losing all progress.

Field case (2026-10-13): a 791-file index aborted at 723/791 (87%). The manifest
is only published after the very last file, so those 723 already-embedded files
had to be recomputed from zero.  Progress is now journalled as it goes.

F6: a same-fingerprint journal entry is not automatically trustworthy.  Its
windows must lie inside the recorded duration and its vectors must be finite
with a consistent dimension; anything else is REBUILT, never republished
(probe: a journal entry with start=-999 / seconds=-10 used to publish as-is).
"""
import json
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workbench.indexer import Indexer, entry_valid, fingerprint_file

NAMES = ('a.mp4', 'b.mp4', 'c.mp4', 'd.mp4')


class _StubIndexer(Indexer):
    """Per-file work stubbed out: no ffprobe, no ffmpeg, no model.

    ENCODE_BATCH=1 keeps one encode call per file, so a mid-run failure lands
    exactly where the test wants it (and mirrors the pre-batching behaviour)."""

    ENCODE_BATCH = 1
    SLICE_WORKERS = 1

    def __init__(self, root, fail_on=None, error='index/vector: invalid numeric vector'):
        super().__init__(root, None)
        self.fail_on = fail_on
        self.error = error
        self.visited = []

    def _slice_file(self, p, n, tmp, fp):
        self.visited.append(p.name)
        return 10.0, [(0.0, 10.0, {'video': [str(p)], 'name': p.name}, None)]

    def _encode(self, payloads):
        for pl in payloads:
            if pl.get('name') == self.fail_on:
                raise ValueError(self.error)
        return [[1.0, 0.0] for _ in payloads]


def _good_entry(path, name, sha, start=0.0, seconds=10.0, duration=10.0, vector=(1.0, 0.0)):
    return {'fingerprint': {'path': str(path), 'bytes': 9, 'mtime_ns': 1, 'sha256': sha},
            'duration': duration,
            'blocks': [{'start': start, 'seconds': seconds, 'vector': list(vector)}],
            'vector': list(vector), 'name': name}


class SkipPublicViewTests(unittest.TestCase):
    def test_public_skipped_keeps_only_name_and_reason_category(self):
        from workbench.service import public_skipped
        rows = [{'path': '/private/materials/broken.mp4', 'name': 'broken.mp4', 'bytes': 9,
                 'reason': 'index/probe: broken.mp4: moov atom not found'},
                {'path': 'D:\\素材\\trunc.mp4', 'name': 'trunc.mp4', 'bytes': 8,
                 'reason': 'index/window: trunc.mp4 0.0: rc=1 size=0 '}]
        out = public_skipped(rows)
        self.assertEqual([x['name'] for x in out], ['broken.mp4', 'trunc.mp4'])
        self.assertEqual([x['reason'] for x in out], ['unreadable', 'slice_failed'])
        for row in out:
            self.assertEqual(set(row), {'name', 'reason', 'reason_cn'})
            self.assertNotIn('path', row)
            self.assertNotIn('moov', json.dumps(row))
            self.assertNotIn('D:', json.dumps(row))

    def test_public_skipped_maps_unknown_reasons_to_a_safe_label(self):
        from workbench.service import public_skipped
        out = public_skipped([{'name': 'x.mp4', 'reason': 'something/internal'}])
        self.assertEqual(out, [{'name': 'x.mp4', 'reason': 'unknown', 'reason_cn': '读取失败'}])


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='idx-resume-'))
        self.src = self.tmp / 'src'
        self.idx = self.tmp / 'idx'
        self.src.mkdir()
        for name in NAMES:
            (self.src / name).write_bytes(b'fake-' + name.encode())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub(self, fail_on=None):
        return _StubIndexer(self.idx, fail_on)

    def _run(self, stub, settings=None, model='model:v1', source=None, baseline=None):
        return stub.build('pid', str(source or self.src), settings or {'sample_seconds': 10},
                          model, progress=lambda *a: None, baseline=baseline)

    def _checkpoints(self):
        return sorted(p.name for p in self.idx.glob('resume-*.jsonl'))

    def _write_journal(self, fp, blocks, duration=10.0, source=None, settings=None):
        path = self._stub()._checkpoint_path(Path(source or self.src).resolve(), 'model:v1',
                                             settings or {'sample_seconds': 10})
        header = {'source': str(Path(source or self.src).resolve()), 'model_identity': 'model:v1',
                  'settings': {'sample_seconds': (settings or {'sample_seconds': 10}).get('sample_seconds')}}
        entry = {'fingerprint': fp, 'duration': duration, 'blocks': blocks,
                 'vector': [1.0, 0.0]}
        with path.open('w', encoding='utf8') as f:
            f.write(json.dumps(header) + '\n')
            f.write(json.dumps(entry) + '\n')
        return path

    def test_failed_run_journals_progress(self):
        with self.assertRaises(ValueError):
            self._run(self._stub(fail_on='c.mp4'))
        self.assertEqual(len(self._checkpoints()), 1)
        lines = (self.idx / self._checkpoints()[0]).read_text(encoding='utf8').strip().splitlines()
        self.assertEqual(len(lines), 3, 'header + the two completed files')
        head = json.loads(lines[0])
        self.assertEqual(head['model_identity'], 'model:v1')
        self.assertEqual(head['settings']['sample_seconds'], 10)
        self.assertEqual([json.loads(x)['fingerprint']['path'].split(os.sep)[-1] for x in lines[1:]],
                         ['a.mp4', 'b.mp4'])

    def test_second_run_resumes_and_only_recomputes_the_rest(self):
        with self.assertRaises(ValueError):
            self._run(self._stub(fail_on='c.mp4'))
        stub = self._stub()
        rec = self._run(stub)
        self.assertEqual(stub.visited, ['c.mp4', 'd.mp4'], 'journalled files must not be recomputed')
        self.assertEqual(rec['reused'], 2)
        self.assertEqual(rec['count'], 4)

    def test_checkpoint_is_dropped_after_a_successful_publish(self):
        self._run(self._stub())
        self.assertEqual(self._checkpoints(), [])
        self.assertEqual(len([d for d in self.idx.iterdir() if d.is_dir()]), 1)

    def test_checkpoint_from_another_source_or_sampling_is_ignored(self):
        with self.assertRaises(ValueError):
            self._run(self._stub(fail_on='c.mp4'))
        other = self.tmp / 'other'
        other.mkdir()
        for name in NAMES:
            (other / name).write_bytes(b'other-' + name.encode())
        stub = _StubIndexer(self.idx, None)
        rec = self._run(stub, source=other)
        self.assertEqual(sorted(stub.visited), sorted(NAMES))
        self.assertEqual(rec['reused'], 0)

        stub2 = self._stub()
        rec2 = self._run(stub2, settings={'sample_seconds': 5})
        self.assertEqual(sorted(stub2.visited), sorted(NAMES))
        self.assertEqual(rec2['reused'], 0)

    def test_torn_trailing_line_is_tolerated(self):
        # journal written by a process that was hard-killed mid-flush: the last
        # line is truncated JSON and must be discarded, not fatal
        path = self._stub()._checkpoint_path(Path(self.src).resolve(), 'model:v1', {'sample_seconds': 10})
        fp = fingerprint_file(self.src / 'a.mp4')
        with path.open('w', encoding='utf8') as f:
            f.write(json.dumps({'source': str(Path(self.src).resolve()), 'model_identity': 'model:v1',
                                'settings': {'sample_seconds': 10}}) + '\n')
            f.write(json.dumps({'fingerprint': fp,
                                'blocks': [{'start': 0.0, 'seconds': 10.0, 'vector': [1.0, 0.0]}],
                                'duration': 10.0, 'vector': [1.0, 0.0]}) + '\n')
            f.write('{"fingerprint": {"path": "D:\\\\broken')  # torn
        stub = self._stub()
        rec = self._run(stub)
        self.assertEqual(rec['reused'], 1)
        self.assertEqual(stub.visited, ['b.mp4', 'c.mp4', 'd.mp4'])

    def test_malformed_journal_shapes_are_ignored_never_fatal(self):
        # review L1: shapes that are valid JSON but structurally wrong must
        # cost a rebuild, never crash the build.
        resolved = str(Path(self.src).resolve())
        fp = fingerprint_file(self.src / 'a.mp4')
        entry = json.dumps({'fingerprint': fp, 'duration': 10.0,
                            'blocks': [{'start': 0.0, 'seconds': 10.0, 'vector': [1.0, 0.0]}],
                            'vector': [1.0, 0.0]})
        for head_text in ('[]', '42', '"str"',
                          json.dumps({'source': resolved, 'model_identity': 'model:v1', 'settings': []})):
            path = self._stub()._checkpoint_path(Path(self.src).resolve(), 'model:v1', {'sample_seconds': 10})
            with path.open('w', encoding='utf8') as f:
                f.write(head_text + '\n')
                f.write(entry + '\n')
            stub = self._stub()
            rec = self._run(stub)
            self.assertEqual(rec['reused'], 0, head_text)
            self.assertEqual(sorted(stub.visited), sorted(NAMES), head_text)

    def test_journal_entry_without_a_path_is_skipped_not_fatal(self):
        # review L1: a fingerprint dict without 'path' must not KeyError the
        # loader; that entry is skipped and valid entries still reuse.
        path = self._stub()._checkpoint_path(Path(self.src).resolve(), 'model:v1', {'sample_seconds': 10})
        resolved = str(Path(self.src).resolve())
        header = json.dumps({'source': resolved, 'model_identity': 'model:v1',
                             'settings': {'sample_seconds': 10}})
        bad_entry = json.dumps({'fingerprint': {'bytes': 9, 'mtime_ns': 1, 'sha256': 'zz'},
                                'duration': 10.0,
                                'blocks': [{'start': 0.0, 'seconds': 10.0, 'vector': [1.0, 0.0]}],
                                'vector': [1.0, 0.0]})
        good_entry = json.dumps({'fingerprint': fingerprint_file(self.src / 'a.mp4'), 'duration': 10.0,
                                 'blocks': [{'start': 0.0, 'seconds': 10.0, 'vector': [1.0, 0.0]}],
                                 'vector': [1.0, 0.0]})
        with path.open('w', encoding='utf8') as f:
            f.write(header + '\n')
            f.write(bad_entry + '\n')
            f.write(good_entry + '\n')
        stub = self._stub()
        rec = self._run(stub)
        self.assertEqual(rec['reused'], 1, 'the valid entry is still reused')
        self.assertEqual(stub.visited, ['b.mp4', 'c.mp4', 'd.mp4'])

    def test_invalid_window_in_journal_is_rebuilt_not_reused(self):
        # the review probe: start=-999 / seconds=-10 in the same-fingerprint journal
        fp = fingerprint_file(self.src / 'a.mp4')
        self._write_journal(fp, [{'start': -999, 'seconds': -10, 'vector': [1.0, 0.0]}])
        stub = self._stub()
        rec = self._run(stub)
        self.assertEqual(rec['reused'], 0, 'an out-of-range window must never be republished')
        self.assertIn('a.mp4', stub.visited, 'the offending file must be rebuilt')
        published = [d for d in self.idx.iterdir() if d.is_dir()][0]
        manifest = json.loads((published / 'manifest.json').read_text(encoding='utf8'))
        block = manifest['files'][0]['blocks'][0]
        self.assertEqual((block['start'], block['seconds']), (0.0, 10.0))

    def test_window_beyond_duration_is_rejected(self):
        fp = fingerprint_file(self.src / 'b.mp4')
        self._write_journal(fp, [{'start': 5.0, 'seconds': 40.0, 'vector': [1.0, 0.0]}])
        stub = self._stub()
        rec = self._run(stub)
        self.assertEqual(rec['reused'], 0)
        self.assertIn('b.mp4', stub.visited)

    def test_non_finite_vector_is_rejected(self):
        fp = fingerprint_file(self.src / 'c.mp4')
        path = self._stub()._checkpoint_path(Path(self.src).resolve(), 'model:v1', {'sample_seconds': 10})
        header = {'source': str(Path(self.src).resolve()), 'model_identity': 'model:v1',
                  'settings': {'sample_seconds': 10}}
        body = ('{"fingerprint": %s, "duration": 10.0, "vector": [1.0, 0.0], '
                '"blocks": [{"start": 0.0, "seconds": 10.0, "vector": [Infinity, 0.5]}]}'
                % json.dumps(fp))
        with path.open('w', encoding='utf8') as f:
            f.write(json.dumps(header) + '\n')
            f.write(body + '\n')
        stub = self._stub()
        rec = self._run(stub)
        self.assertEqual(rec['reused'], 0)
        self.assertIn('c.mp4', stub.visited)

    def test_invalid_entry_does_not_abort_the_run(self):
        fp = fingerprint_file(self.src / 'a.mp4')
        self._write_journal(fp, [{'start': -999, 'seconds': -10, 'vector': [1.0, 0.0]}])
        stub = self._stub()
        rec = self._run(stub)
        self.assertEqual(rec['count'], 4)
        self.assertEqual(sorted(stub.visited), sorted(NAMES))

    def test_doctored_baseline_with_invalid_blocks_is_rebuilt(self):
        # same contract applies to the older published baseline, not just the journal
        first = self._run(self._stub())
        baseline = json.loads(json.dumps(first))
        baseline['files'][0]['blocks'][0].update({'start': -999, 'seconds': -10})
        stub = self._stub()
        rec = self._run(stub, baseline=baseline)
        self.assertEqual(rec['reused'], len(NAMES) - 1)
        self.assertIn('a.mp4', stub.visited)

    def test_entry_valid_contract(self):
        fp = fingerprint_file(self.src / 'a.mp4')
        good = {'fingerprint': fp, 'duration': 10.0,
                'blocks': [{'start': 0.0, 'seconds': 10.0, 'vector': [1.0, 0.0]}],
                'vector': [1.0, 0.0]}
        self.assertTrue(entry_valid(good))
        for mutate in (lambda e: e['blocks'][0].update(seconds=-1),
                       lambda e: e['blocks'][0].update(start=-999),
                       lambda e: e['blocks'][0].update(start=9.0, seconds=5.0),
                       lambda e: e['blocks'][0].update(vector=[1.0]),
                       lambda e: e.update(duration=0),
                       lambda e: e['fingerprint'].update(sha256='zz'),
                       lambda e: e.update(vector=[]),
                       lambda e: e.update(blocks=[])):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            self.assertFalse(entry_valid(bad), bad)


if __name__ == '__main__':
    unittest.main(verbosity=2)
