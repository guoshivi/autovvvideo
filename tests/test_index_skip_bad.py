"""A single unreadable/corrupt source file must not abort the whole folder.

Field case (2026-10-13): an 8.9MB truncated mp4 ("moov atom not found") in a
791-file folder killed the whole index job at 723/791 -- every other usable
file had to be re-processed from scratch.  Such files are now skipped and
reported on the record instead.

The partial-success contract (F3):
  * the record and the job/API exposure say exactly how many were skipped and
    why -- a partial index must never present itself as a full success;
  * skipped files are NOT journalled, so the next index run retries them;
  * a folder where nothing could be indexed still fails hard;
  * the public (API/UI) view carries no private paths.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workbench.indexer import Indexer, is_source_defect


class _StubIndexer(Indexer):
    """Per-file work stubbed out: no ffprobe, no ffmpeg, no model."""

    def __init__(self, root, bad, error='index/probe: {name}: moov atom not found'):
        super().__init__(root, None)
        self.bad = set(bad)
        self.error = error
        self.visited = []

    def _slice_file(self, p, n, tmp, fp):
        self.visited.append(p.name)
        clip = tmp / f'clip-{n}-0.mp4'
        clip.write_bytes(b'x' * 16)
        if p.name in self.bad:
            # mirror the real slicer: a failed file cleans up after itself
            self._discard_clips(tmp, n)
            raise ValueError(self.error.format(name=p.name))
        return 10.0, [(0.0, 10.0, {'video': [str(clip)]}, clip)]

    def _encode(self, payloads):
        return [[1.0, 0.0] for _ in payloads]


class SourceDefectTests(unittest.TestCase):
    def test_prefixes_are_classified(self):
        self.assertTrue(is_source_defect(ValueError('index/probe: a.mp4: moov atom not found')))
        self.assertTrue(is_source_defect(ValueError('index/window: a.mp4 0.0: rc=1 size=0')))
        for msg in ('index/vector: invalid numeric vector', 'index/scan: 没有视频',
                    'index/source changed during build', 'source changed while fingerprinting'):
            self.assertFalse(is_source_defect(ValueError(msg)), msg)


class SkipBadFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='idx-skip-'))
        self.src = self.tmp / 'src'
        self.idx = self.tmp / 'idx'
        self.src.mkdir()
        for name in ('a.mp4', 'b.mp4', 'c.mp4'):
            (self.src / name).write_bytes(b'fake-' + name.encode())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(self, bad, error='index/probe: {name}: moov atom not found'):
        stub = _StubIndexer(self.idx, bad, error)
        rec = stub.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1')
        return stub, rec

    def _published(self):
        dirs = [d for d in self.idx.iterdir() if d.is_dir()]
        self.assertEqual(len(dirs), 1, dirs)
        return dirs[0]

    def _staging(self):
        """Staging directories. A `resume-*.jsonl` journal may legitimately
        survive a failed build (that is what makes it resumable)."""
        return [d.name for d in self.idx.iterdir() if d.is_dir()]

    def test_bad_file_is_skipped_and_the_rest_are_indexed(self):
        stub, rec = self._build({'b.mp4'})
        self.assertEqual(rec['count'], 2)
        self.assertEqual([e['fingerprint']['path'].split(os.sep)[-1] for e in rec['files']],
                         ['a.mp4', 'c.mp4'])
        self.assertEqual([s['name'] for s in rec['skipped']], ['b.mp4'])
        self.assertIn('moov atom not found', rec['skipped'][0]['reason'])
        self.assertEqual(rec['skipped'][0]['bytes'], len(b'fake-b.mp4'))
        # every file is still visited: skipping must not abort the loop
        self.assertEqual(sorted(stub.visited), ['a.mp4', 'b.mp4', 'c.mp4'])
        # the partial result is explicitly recorded, never a silent full success
        self.assertEqual(rec['count'] + len(rec['skipped']), 3)

    def test_skipped_file_leaves_no_staged_clips_behind(self):
        self._build({'b.mp4'})
        published = self._published()
        leftovers = [p.name for p in published.glob('clip-*.mp4')]
        self.assertEqual(leftovers, [], leftovers)
        # the manifest itself must be there and reflect the skip
        rec = json_load(published / 'manifest.json')
        self.assertEqual(rec['count'], 2)
        self.assertEqual(len(rec['skipped']), 1)

    def test_skipped_files_are_retried_on_the_next_run(self):
        # the published index becomes the next baseline; the skipped file is not
        # part of it, so a later run recomputes exactly that file
        _, rec = self._build({'b.mp4'})
        stub = _StubIndexer(self.idx, set())
        rec2 = stub.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1', baseline=rec)
        self.assertEqual(stub.visited, ['b.mp4'], 'only the previously skipped file is recomputed')
        self.assertEqual(rec2['reused'], 2)
        self.assertEqual(rec2['skipped'], [])
        self.assertEqual(rec2['count'], 3)

    def test_window_defect_is_also_skipped(self):
        _, rec = self._build({'a.mp4'}, 'index/window: {name} 0.0: rc=1 size=0 ')
        self.assertEqual(rec['count'], 2)
        self.assertEqual([s['name'] for s in rec['skipped']], ['a.mp4'])

    def test_all_bad_files_raise_and_publish_nothing(self):
        with self.assertRaises(ValueError) as cm:
            self._build({'a.mp4', 'b.mp4', 'c.mp4'})
        self.assertIn('所有素材都无法解析', str(cm.exception))
        self.assertEqual(self._staging(), [], 'staging dir must be cleaned up')

    def test_unrelated_error_still_propagates(self):
        with self.assertRaises(ValueError) as cm:
            self._build({'b.mp4'}, 'index/vector: invalid numeric vector')
        self.assertIn('invalid numeric vector', str(cm.exception))
        self.assertEqual(self._staging(), [], 'staging dir must be cleaned up')

    def test_skipped_files_do_not_count_as_reused(self):
        _, rec = self._build({'b.mp4'})
        self.assertEqual(rec['reused'], 0)
        self.assertEqual(len(rec['files']), rec['count'])


def json_load(p):
    return json.loads(Path(p).read_text(encoding='utf8'))


class PartialIndexExposureTests(unittest.TestCase):
    """The API/UI boundary must show the partial result, without private paths."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='idx-partial-api-'))
        self.material = self.tmp / 'material'
        self.material.mkdir()
        for name in ('ok.mp4', 'bad.mp4'):
            (self.material / name).write_bytes(b'x' + name.encode())
        from workbench.service import WorkbenchService
        from workbench.queue import JobQueue
        from workbench.store import Store
        from workbench.indexer import collect_source_fingerprints

        class _Cfg:
            data_root = str(self.tmp / 'data')
            nas_roots = [str(self.material)]
            model_dir = ''
            gpu_lock_path = ''
            ffmpeg = 'ffmpeg'
            ffprobe = 'ffprobe'
            project_src = str(self.tmp)
            max_stage_seconds = 60
            tts_model = ''
            tts_python = ''
            comfy_ports = []
            port = 8765

        self.store = Store(self.tmp / 'data' / 'records.json')
        self.q = JobQueue(self.tmp / 'data' / 'jobs.json')
        self.svc = WorkbenchService(_Cfg(), self.store, self.q, embedder_factory=lambda: None, start=False)
        self.product = self.store.create_product('p')
        self.store.select(self.product['id'], 'source', {'path': str(self.material.resolve())})
        fps = collect_source_fingerprints(str(self.material))
        self.by_name = {Path(f['path']).name: f for f in fps}
        rev = self.store.get_product(self.product['id'])['selection_revision']
        snap = {'kind': 'index', 'source': {'path': str(self.material.resolve())},
                'source_fingerprints': fps, 'selection_revision': rev,
                'model_identity': 'model:v1', 'settings': {'sample_seconds': 10}}
        self.job = self.q.enqueue(self.product['id'], snap, 1)
        self.rec = {'id': uuid.uuid4().hex, 'product_id': self.product['id'],
                    'source': str(self.material.resolve()), 'settings': {'sample_seconds': 10},
                    'model_identity': 'model:v1', 'synthetic': True, 'count': 1, 'reused': 0,
                    'created_at': time.time(),
                    'skipped': [{'path': str(self.material / 'bad.mp4'), 'name': 'bad.mp4', 'bytes': 8,
                                 'reason': 'index/probe: bad.mp4: moov atom not found'}],
                    'files': [{'fingerprint': self.by_name['ok.mp4'], 'duration': 10.0,
                               'blocks': [{'start': 0.0, 'seconds': 10.0, 'vector': [1.0, 0.0]}],
                               'vector': [1.0, 0.0]}]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_job_and_state_expose_the_partial_result(self):
        class _Stub:
            def __init__(self, rec):
                self.rec = rec

            def is_obsolete(self, rec):
                return False

            def build(self, *a, **kw):
                return self.rec

        self.svc._indexer = lambda emb: _Stub(self.rec)
        self.svc._running_job_id = self.job['id']
        self.svc._run_index(self.job, lambda: False)
        job = self.svc.public_job(self.q.get(self.job['id']))
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['skipped'], 1)
        self.assertEqual(job['skipped_files'],
                         [{'name': 'bad.mp4', 'reason': 'unreadable', 'reason_cn': '无法解析（文件损坏或不完整）'}])
        self.assertEqual((job['indexed'], job['total']), (1, 2), 'the job must not claim 2/2 success')
        blob = json.dumps(job, ensure_ascii=False)
        self.assertNotIn('moov', blob)
        self.assertNotIn(str(self.material), blob)
        state = self.svc.index_state(self.product['id'])
        self.assertTrue(state['ready'])
        self.assertEqual(state['index']['skipped'],
                         [{'name': 'bad.mp4', 'reason': 'unreadable', 'reason_cn': '无法解析（文件损坏或不完整）'}])
        self.assertNotIn(str(self.material), json.dumps(state, ensure_ascii=False))
        # the internal record still keeps the full diagnostic for the operator
        stored = self.store.get_index(self.rec['id'])
        self.assertIn('moov atom not found', stored['skipped'][0]['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
