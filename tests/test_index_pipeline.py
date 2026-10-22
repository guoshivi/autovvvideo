"""The indexer is a pipeline: slicer threads run ahead of the embedder and
clips are embedded in batches.

Measured before this change: the GPU sat idle in bursts (5%,5%,5%,16%,22%,93%)
because every window was strictly `slice -> embed -> unlink`.

Also covered here (fixes over the friend build):
  * cancel is checked at every file/window boundary on the normal path -- not
    only while paused (F2);
  * a pause is acknowledged at a file boundary via on_pause(), and the resume
    path calls on_resume() before any further GPU work.
"""
import os
import sys
import time
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workbench.indexer import Indexer, encode_many, wait_while_paused

NAMES = ('a.mp4', 'b.mp4', 'c.mp4', 'd.mp4')


class _Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.clip_depth = 0
        self.max_clip_depth = 0
        self.clip_depth_at_first_encode = None
        self.encodes = []
        self.slow = set()

    def clip_enter(self):
        with self.lock:
            self.clip_depth += 1
            self.max_clip_depth = max(self.max_clip_depth, self.clip_depth)
            return self.clip_depth

    def clip_exit(self):
        with self.lock:
            self.clip_depth -= 1

    def encode(self, payloads):
        with self.lock:
            if self.clip_depth_at_first_encode is None:
                self.clip_depth_at_first_encode = self.clip_depth
            self.encodes.append([p.get('name') for p in payloads])


class _PipeIndexer(Indexer):
    """No ffmpeg and no model: the seams under test are slicing and encoding."""

    ENCODE_BATCH = 1
    SLICE_WORKERS = 3

    def __init__(self, root, rec, fail=None, error='index/vector: invalid numeric vector',
                 windows=1, batch_encoders=True):
        super().__init__(root, None)
        self.rec = rec
        self.fail = set(fail or ())
        self.error = error
        self.windows = windows
        self.batch_encoders = batch_encoders
        self.single_calls = []
        if batch_encoders:
            self.encode_media_many = self._many

    def _many(self, payloads):
        return self._do(payloads)

    def _encode(self, payloads):
        return encode_many(self, payloads)   # the indexer plays the embedder here

    def encode_media(self, payload):
        self.single_calls.append(payload)
        return self._do([payload])[0]

    def _do(self, payloads):
        for p in payloads:
            if p.get('name') in self.fail:
                raise ValueError(self.error)
        self.rec.encode(payloads)
        return [[1.0, 0.0] for _ in payloads]

    def _slice_file(self, p, n, tmp, fp):
        self.rec.clip_enter()
        try:
            # a real slice is an ffmpeg process taking hundreds of ms; the stub
            # holds long enough for overlap to be observable deterministically
            time.sleep(0.05 + (0.5 if p.name in self.rec.slow else 0))
            return 10.0, [(float(i), 10.0, {'video': [str(p)], 'name': p.name}, None)
                          for i in range(self.windows)]
        finally:
            self.rec.clip_exit()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='idx-pipe-'))
        self.src = self.tmp / 'src'
        self.idx = self.tmp / 'idx'
        self.src.mkdir()
        for name in NAMES:
            (self.src / name).write_bytes(b'fake-' + name.encode())
        self.rec = _Recorder()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(self, rec=None, **kw):
        rec = rec or self.rec
        idx = _PipeIndexer(self.idx, rec, **kw)
        return idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1')

    def test_record_keeps_user_source_spelling(self):
        # A mapped drive must stay "Z:\..." in the published record: the store
        # compares it byte-for-byte with the product's selected path. Resolving
        # it to the UNC target made every NAS index fail with
        # "索引来源与商品不符".
        rec = self._build()
        self.assertEqual(rec['source'], str(self.src))

    def test_source_match_ignores_resolved_spelling(self):
        from workbench.indexer import _same_source
        self.assertTrue(_same_source(str(self.src), str(self.src.resolve())))

    def test_slices_run_in_parallel(self):
        self._build()
        self.assertGreaterEqual(self.rec.max_clip_depth, 2,
                                'slicers must overlap, not run one after another')

    def test_encoding_overlaps_slicing(self):
        self.rec.slow = {'b.mp4'}
        self._build()
        self.assertIsNotNone(self.rec.clip_depth_at_first_encode)
        self.assertGreaterEqual(self.rec.clip_depth_at_first_encode, 1,
                                'the first encode must happen while a later file is still being cut')

    def test_clips_are_batched_into_one_call(self):
        rec = _Recorder()
        idx = _PipeIndexer(self.idx, rec, windows=3)
        idx.ENCODE_BATCH = 4
        idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1')
        self.assertEqual(len(rec.encodes), 3, rec.encodes)   # 4 files x 3 windows / 4 per call
        self.assertEqual(sum(len(b) for b in rec.encodes), 12)
        self.assertGreater(max(len(b) for b in rec.encodes), 1, 'clips must share a forward pass')

    def test_entries_keep_file_order_even_when_slicing_completes_out_of_order(self):
        self.rec.slow = {'a.mp4'}
        rec = self.rec
        idx = _PipeIndexer(self.idx, rec)
        out = idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1')
        self.assertEqual([e['fingerprint']['path'].split(os.sep)[-1] for e in out['files']],
                         list(NAMES))

    def test_falls_back_to_single_calls_without_a_batch_method(self):
        rec = _Recorder()
        idx = _PipeIndexer(self.idx, rec, windows=2, batch_encoders=False)
        idx.ENCODE_BATCH = 8
        out = idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1')
        self.assertEqual(len(idx.single_calls), 8)      # 4 files x 2 windows, one call each
        self.assertEqual(out['count'], 4)
        self.assertTrue(all(set(p) == {'video', 'name'} for p in idx.single_calls))


class CancelTests(unittest.TestCase):
    """F2: the normal (non-paused) path must honour a cancel request promptly."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='idx-cancel-'))
        self.src = self.tmp / 'src'
        self.idx = self.tmp / 'idx'
        self.src.mkdir()
        for name in NAMES:
            (self.src / name).write_bytes(b'fake-' + name.encode())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dirs(self):
        return [d.name for d in self.idx.iterdir() if d.is_dir()]

    def test_cancel_already_requested_refuses_before_any_work(self):
        calls = []
        idx = _PipeIndexer(self.idx, _Recorder())
        t0 = time.monotonic()
        with self.assertRaises(InterruptedError):
            idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1',
                      cancel=lambda: (calls.append(1), True)[1])
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertGreaterEqual(len(calls), 1, 'cancel must be consulted on the normal path')
        self.assertEqual(self._dirs(), [], 'a cancelled build must not publish or stage anything')

    def test_cancel_mid_run_stops_at_the_next_boundary(self):
        rec = _Recorder()
        idx = _PipeIndexer(self.idx, rec)
        state = {'cancel': False}

        # flip cancel after the first encode call completes
        def encode(payloads):
            rec.encode(payloads)
            state['cancel'] = True
            return [[1.0, 0.0] for _ in payloads]

        idx._encode = encode
        t0 = time.monotonic()
        with self.assertRaises(InterruptedError):
            idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1',
                      cancel=lambda: state['cancel'])
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertLessEqual(len(rec.encodes), 2, 'cancel must abort instead of indexing the whole folder')
        self.assertEqual(self._dirs(), [])


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='idx-pause-'))
        self.src = self.tmp / 'src'
        self.idx = self.tmp / 'idx'
        self.src.mkdir()
        for name in NAMES[:2]:
            (self.src / name).write_bytes(b'fake-' + name.encode())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_blocks_while_paused_and_then_finishes(self):
        state = {'paused': True}
        rec = _Recorder()
        idx = _PipeIndexer(self.idx, rec)
        threading.Timer(0.4, lambda: state.update(paused=False)).start()
        t0 = time.monotonic()
        out = idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1',
                        pause=lambda: state['paused'])
        self.assertGreaterEqual(time.monotonic() - t0, 0.35, 'a paused build must not charge ahead')
        self.assertEqual(out['count'], 2)
        self.assertTrue(rec.encodes, 'work must resume after the pause')

    def test_pause_ack_happens_on_a_boundary_and_resume_is_signalled_once(self):
        state = {'paused': True}
        events = []
        rec = _Recorder()
        idx = _PipeIndexer(self.idx, rec)
        threading.Timer(0.3, lambda: state.update(paused=False)).start()
        out = idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1',
                        pause=lambda: state['paused'],
                        on_pause=lambda: events.append('pause'),
                        on_resume=lambda: events.append('resume'))
        self.assertEqual(events, ['pause', 'resume'])
        self.assertEqual(out['count'], 2)

    def test_cancel_while_paused_aborts_promptly(self):
        state = {'paused': True}
        idx = _PipeIndexer(self.idx, _Recorder())
        t0 = time.monotonic()
        with self.assertRaises(InterruptedError):
            idx.build('pid', str(self.src), {'sample_seconds': 10}, 'model:v1',
                      cancel=lambda: True, pause=lambda: state['paused'])
        self.assertLess(time.monotonic() - t0, 2.0)
        # a cancelled build must not publish anything
        self.assertEqual([d for d in self.idx.iterdir() if d.is_dir()], [])

    def test_wait_while_paused_is_a_noop_without_a_pause_callback(self):
        wait_while_paused(None)
        wait_while_paused(lambda: False)


class EncodeManyTests(unittest.TestCase):
    def test_batch_method_is_used_and_its_result_length_is_trusted(self):
        calls = []

        class E:
            def encode_media_many(self, ps):
                calls.append(len(ps))
                return [[1.0]] * len(ps)

            def encode_media(self, p):
                raise AssertionError('must not fall back')

        self.assertEqual(encode_many(E(), [{'a': 1}, {'a': 2}]), [[1.0], [1.0]])
        self.assertEqual(calls, [2])

    def test_a_broken_batch_degrades_to_single_calls(self):
        seen = []

        class E:
            def encode_media_many(self, ps):
                raise RuntimeError('CUDA out of memory')

            def encode_media(self, p):
                seen.append(p)
                return [0.5]

        self.assertEqual(encode_many(E(), [{'a': 1}, {'a': 2}]), [[0.5], [0.5]])
        self.assertEqual(len(seen), 2)

    def test_a_mismatched_batch_length_degrades_to_single_calls(self):
        class E:
            def encode_media_many(self, ps):
                return [[1.0]]

            def encode_media(self, p):
                return [2.0]

        self.assertEqual(encode_many(E(), [{'a': 1}, {'a': 2}]), [[2.0], [2.0]])

    def test_empty_input_is_empty(self):
        self.assertEqual(encode_many(object(), []), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
