"""Pause / resume for queued jobs.

The queue is the single source of truth: `pause_requested` is the request.
A job that has not started yet parks immediately; a RUNNING job is only
reported `paused` after its worker acknowledges at a file/step boundary
(`pause_ack`) -- claiming 'paused' while work is still in flight was F7.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workbench.queue import JobQueue
from workbench.store import Store
from workbench.indexer import collect_source_fingerprints


class _Cfg:
    nas_roots = []
    model_dir = ''
    ffmpeg = 'ffmpeg'
    ffprobe = 'ffprobe'
    gpu_lock_path = ''
    project_src = '.'
    max_stage_seconds = 60
    tts_model = ''
    tts_python = ''
    comfy_ports = []
    data_root = ''


class QueuePauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='queue-pause-'))
        self.path = self.tmp / 'jobs.json'
        self.q = JobQueue(self.path)
        self.jid = self.q.enqueue('pid', {'kind': 'index'})['id']

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pausing_a_queued_job_keeps_it_out_of_the_worker(self):
        self.assertTrue(self.q.pause(self.jid))
        j = self.q.get(self.jid)
        self.assertEqual((j['status'], j['stage']), ('paused', 'paused'))
        self.assertTrue(j['pause_requested'])
        self.assertIsNone(self.q.next(), 'a paused job must not be picked up')

    def test_pausing_a_running_job_waits_for_the_worker_ack(self):
        self.q.next()                                   # worker took it
        self.assertTrue(self.q.pause(self.jid))
        j = self.q.get(self.jid)
        self.assertEqual(j['status'], 'running', 'a live job must not claim paused before its worker confirms')
        self.assertTrue(j['pause_requested'])
        self.assertEqual(j['stage'], 'starting', 'the stage must not be rewritten by the request alone')
        self.assertTrue(self.q.pause_ack(self.jid))
        j = self.q.get(self.jid)
        self.assertEqual((j['status'], j['stage']), ('paused', 'paused'))

    def test_pause_ack_is_refused_when_nothing_is_in_flight(self):
        self.assertFalse(self.q.pause_ack(self.jid), 'a queued job has no worker to ack')
        self.q.next()
        self.assertFalse(self.q.pause_ack(self.jid), 'no pause was requested')
        self.q.pause(self.jid)
        self.assertTrue(self.q.pause_ack(self.jid))

    def test_resuming_an_unstarted_job_puts_it_back_in_the_queue(self):
        self.q.pause(self.jid)
        self.assertTrue(self.q.resume(self.jid, live=False))
        j = self.q.get(self.jid)
        self.assertEqual((j['status'], j['stage']), ('queued', 'queued'))
        self.assertFalse(j['pause_requested'])
        self.assertEqual(self.q.next()['id'], self.jid)

    def test_resuming_a_live_job_leaves_it_running(self):
        self.q.next()                                  # worker took it
        self.q.pause(self.jid)
        self.q.pause_ack(self.jid)
        self.assertEqual(self.q.get(self.jid)['status'], 'paused')
        self.assertTrue(self.q.resume(self.jid, live=True))
        j = self.q.get(self.jid)
        self.assertEqual(j['status'], 'running')
        self.assertEqual(j['stage'], 'resuming')
        self.assertFalse(j['pause_requested'])
        self.assertIsNone(self.q.next(), 'a job its worker still holds is not re-queued')

    def test_progress_updates_do_not_unpause_a_job(self):
        self.q.next()
        self.q.pause(self.jid)
        self.q.pause_ack(self.jid)
        self.q.update(self.jid, stage='embedding', progress=40)
        j = self.q.get(self.jid)
        self.assertEqual(j['status'], 'paused')
        self.assertEqual((j['stage'], j['progress']), ('embedding', 40))

    def test_cancelling_a_paused_job_is_terminal(self):
        self.q.pause(self.jid)
        self.assertTrue(self.q.cancel(self.jid))
        self.assertEqual(self.q.get(self.jid)['status'], 'cancelled')
        self.assertFalse(self.q.pause(self.jid))
        self.assertFalse(self.q.resume(self.jid, live=False))

    def test_cancelling_a_live_paused_job_sets_the_cancel_flag(self):
        self.q.next()
        self.q.pause(self.jid)
        self.q.pause_ack(self.jid)
        self.assertTrue(self.q.cancel(self.jid))
        j = self.q.get(self.jid)
        self.assertTrue(j['cancel_requested'], 'the worker must be able to observe the cancel')
        self.assertEqual(j['status'], 'cancelled')

    def test_resume_is_a_noop_unless_the_job_is_paused(self):
        self.assertFalse(self.q.resume(self.jid, live=False))

    def test_paused_job_survives_a_restart_and_running_becomes_interrupted(self):
        running = self.q.next()['id']
        other = self.q.enqueue('pid', {'kind': 'index'})['id']
        self.q.pause(other)
        again = JobQueue(self.path)
        self.assertEqual(again.get(running)['status'], 'interrupted')
        self.assertEqual(again.get(other)['status'], 'paused')
        self.assertIsNone(again.next(), 'a restart must not silently run a paused job')
        self.assertTrue(again.resume(other, live=False))
        self.assertEqual(again.get(other)['status'], 'queued')

    def test_a_running_job_with_a_pending_pause_request_is_interrupted_on_restart(self):
        self.q.next()
        self.q.pause(self.jid)                     # requested, never acked
        again = JobQueue(self.path)
        j = again.get(self.jid)
        self.assertEqual(j['status'], 'interrupted')
        self.assertTrue(j['pause_requested'])


class _StubIndexer:
    """Minimal stand-in for Indexer: parks at the pause callback like the real one."""

    def __init__(self, rec, acked, events):
        self.rec = rec
        self.acked = acked
        self.events = events

    def is_obsolete(self, rec):
        return False

    def build(self, pid, src, settings, model, progress=None, cancel=None, pause=None,
              on_pause=None, on_resume=None, baseline=None):
        if progress:
            progress(0, 1, 0, 0)
        while not (pause and pause()):
            time.sleep(0.02)
        self.events.append('paused_at_boundary')
        if on_pause:
            on_pause()
        self.acked.set()
        while pause():
            if cancel and cancel():
                raise InterruptedError('index/cancel')
            time.sleep(0.02)
        self.events.append('resumed')
        if on_resume:
            on_resume()
        if progress:
            progress(1, 1, 0, 0)
        return self.rec


class ServicePauseTests(unittest.TestCase):
    """The live/re-queue split lives in the service, because only it knows
    whether a worker is still inside the job."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='svc-pause-'))
        from workbench.service import WorkbenchService
        cfg = _Cfg()
        cfg.data_root = str(self.tmp / 'data')
        self.q = JobQueue(self.tmp / 'jobs.json')
        self.svc = WorkbenchService(cfg, Store(self.tmp / 'records.json'), self.q, embedder_factory=lambda: None, start=False)
        self.jid = self.q.enqueue('pid', {'kind': 'index'})['id']

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pause_and_resume_are_exported_and_scoped_to_the_product(self):
        self.assertEqual(self.svc.pause('pid', self.jid)['status'], 'paused')
        self.assertEqual(self.svc.resume('pid', self.jid)['status'], 'queued')
        from workbench.service import ServiceError
        with self.assertRaises(KeyError):
            self.svc.pause('other-product', self.jid)
        with self.assertRaises(ServiceError):
            self.svc.resume('pid', self.jid)          # not paused any more

    def test_pausing_a_queued_job_does_not_touch_a_live_job(self):
        self.q.next()
        self.assertEqual(self.svc.pause('pid', self.jid)['status'], 'running',
                         'a running job only carries the request until its worker acks')

    def test_resume_requeues_when_no_worker_holds_the_job(self):
        self.q.next()                                  # pretended worker, then it died
        self.q.pause(self.jid)
        self.q.pause_ack(self.jid)                     # ... after it reached a boundary
        self.svc._running_job_id = None
        self.assertEqual(self.svc.resume('pid', self.jid)['status'], 'queued')

    def test_resume_resumes_in_place_while_the_worker_still_holds_it(self):
        self.q.next()
        self.q.pause(self.jid)
        self.q.pause_ack(self.jid)
        self.svc._running_job_id = self.jid
        self.assertEqual(self.svc.resume('pid', self.jid)['status'], 'running')

    def test_resume_refuses_while_the_pause_is_still_only_requested(self):
        from workbench.service import ServiceError
        self.q.next()
        self.q.pause(self.jid)
        self.svc._running_job_id = self.jid
        with self.assertRaises(ServiceError):
            self.svc.resume('pid', self.jid)           # the worker has not parked yet
        self.assertEqual(self.q.get(self.jid)['status'], 'running')


    def test_cancel_bookkeeping_keeps_a_state_someone_else_already_recorded(self):
        # review L2: cancel() finalises a paused job while the worker is still
        # reporting during shutdown -- the handler must keep the recorded state
        # and never raise into the worker thread.
        self.q.pause(self.jid)
        self.assertTrue(self.q.cancel(self.jid))
        self.svc.stop_flag.set()
        try:
            self.svc._finish_cancelled(self.jid, InterruptedError('index/cancel'))
        finally:
            self.svc.stop_flag.clear()
        self.assertEqual(self.q.get(self.jid)['status'], 'cancelled')

    def test_cancel_bookkeeping_records_cancelled_for_a_live_job(self):
        self.q.next()
        self.svc._finish_cancelled(self.jid, InterruptedError('index/cancel'))
        self.assertEqual(self.q.get(self.jid)['status'], 'cancelled')

    def test_cancel_bookkeeping_marks_interrupted_when_the_service_is_exiting(self):
        self.q.next()
        self.svc.stop_flag.set()
        try:
            self.svc._finish_cancelled(self.jid, InterruptedError('index/cancel'))
        finally:
            self.svc.stop_flag.clear()
        self.assertEqual(self.q.get(self.jid)['status'], 'interrupted')


class IndexPauseIntegrationTests(unittest.TestCase):
    """A real `_run_index` pause: worker ack at a boundary, GPU/model released,
    resume continues, and the job finishes normally."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='svc-idxpause-'))
        self.material = self.tmp / 'material'
        self.material.mkdir()
        (self.material / 'a.mp4').write_bytes(b'fake-a.mp4')
        from workbench.service import WorkbenchService

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

        self.store = Store(self.tmp / 'data' / 'records.json')
        self.q = JobQueue(self.tmp / 'data' / 'jobs.json')
        self.closed = []
        self.created = []

        class _Emb:
            synthetic = True

            def close(self):
                self.closed_ref.append(1)

        def factory():
            emb = _Emb()
            emb.closed_ref = self.closed
            self.created.append(1)
            return emb

        self.svc = WorkbenchService(_Cfg(), self.store, self.q, embedder_factory=factory, start=False)
        self.product = self.store.create_product('p')
        self.store.select(self.product['id'], 'source', {'path': str(self.material.resolve())})
        self.fps = collect_source_fingerprints(str(self.material))
        rev = self.store.get_product(self.product['id'])['selection_revision']
        snap = {'kind': 'index', 'source': {'path': str(self.material.resolve())},
                'source_fingerprints': self.fps, 'selection_revision': rev,
                'model_identity': 'model:v1', 'settings': {'sample_seconds': 10}}
        self.job = self.q.enqueue(self.product['id'], snap, 1)
        self.rec = {'id': uuid.uuid4().hex, 'product_id': self.product['id'],
                    'source': str(self.material.resolve()), 'settings': {'sample_seconds': 10},
                    'model_identity': 'model:v1', 'synthetic': True, 'count': 1, 'reused': 0,
                    'skipped': [], 'created_at': time.time(),
                    'files': [{'fingerprint': self.fps[0], 'duration': 10.0,
                               'blocks': [{'start': 0.0, 'seconds': 10.0, 'vector': [1.0, 0.0]}],
                               'vector': [1.0, 0.0]}]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_pause_acks_at_a_boundary_releases_gpu_and_resumes(self):
        acked = threading.Event()
        events = []
        stub = _StubIndexer(self.rec, acked, events)
        self.svc._indexer = lambda emb: stub
        outcome = {}

        def run():
            try:
                self.svc._run_index(self.job, lambda: False)
                outcome['ok'] = True
            except Exception as e:                     # pragma: no cover - failure path
                outcome['error'] = repr(e)

        self.svc._running_job_id = self.job['id']
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(self.q.pause(self.job['id']))
            self.assertTrue(acked.wait(5), 'the worker never reached a pause boundary')
            j = self.q.get(self.job['id'])
            self.assertEqual(j['status'], 'paused', 'the queue reports paused only after the worker ack')
            self.assertTrue(self.closed, 'the embedder/model must be released at the pause point')
            self.assertIn('paused_at_boundary', events)
            self.assertNotIn('resumed', events)
            self.assertTrue(self.q.resume(self.job['id'], live=True))
            worker.join(10)
        finally:
            if worker.is_alive():
                self.q.cancel(self.job['id'])
                worker.join(5)
        self.assertIsNone(outcome.get('error'), outcome.get('error'))
        self.assertTrue(outcome.get('ok'), outcome)
        self.assertIn('resumed', events)
        self.assertEqual(self.q.get(self.job['id'])['status'], 'done')
        self.assertEqual(self.created and len(self.created), 2, 'resume must re-acquire an embedder')
        self.assertEqual(len(self.store.get_index(self.rec['id'])['files']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
