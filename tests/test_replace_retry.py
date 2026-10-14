"""Atomic-replace resilience tests (Windows sharing-violation retry).

Field incident: a job failed at 27% with
    PermissionError [WinError 5] '...jobs.json.tmp-xxx' -> '...jobs.json'
because another process merely had jobs.json open for reading. On Windows
MoveFileExW(MOVEFILE_REPLACE_EXISTING) fails with ERROR_ACCESS_DENIED while the
destination is open -- even when the reader shared delete. The staging file is
already fsynced, so the rename is retried.

The retry must not widen the authority window (F8): the final commit condition
is re-evaluated immediately before EVERY actual replace attempt, so a condition
that drifts to False while an attempt is failing aborts the publication instead
of riding along with the retry.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import workbench.fs_authority as fa
from workbench.fs_authority import replace_with_retry

_HOLDER = r'''
import sys, time
p, hold = sys.argv[1], float(sys.argv[2])
f = open(p, 'r', encoding='utf8')      # plain reader: blocks replace on Windows
print('OPENED', flush=True)
time.sleep(hold)
'''


def _sharing_error():
    err = OSError(5, 'sharing violation')
    err.winerror = 5
    return err


class ReplaceRetryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='wbrep-')
        self.dst = os.path.join(self.dir, 'jobs.json')
        Path(self.dst).write_text('{"v": 1}', encoding='utf8')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _tmp(self, text='{"v": 2}'):
        tmp = os.path.join(self.dir, 'jobs.json.tmp-x')
        Path(tmp).write_text(text, encoding='utf8')
        return tmp

    def test_plain_replace_when_destination_is_free(self):
        replace_with_retry(self._tmp(), self.dst)
        self.assertEqual(Path(self.dst).read_text(encoding='utf8'), '{"v": 2}')

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics only')
    def test_succeeds_once_reader_lets_go(self):
        holder = os.path.join(self.dir, 'holder.py')
        Path(holder).write_text(_HOLDER, encoding='utf8')
        p = subprocess.Popen([sys.executable, holder, self.dst, '1.0'],
                             stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), 'OPENED')
            # The destination is open right now: a bare replace would raise
            # WinError 5, but the retry must wait it out and succeed.
            replace_with_retry(self._tmp('{"v": 3}'), self.dst, attempts=100, delay=0.05)
        finally:
            p.wait(timeout=10)
        self.assertEqual(Path(self.dst).read_text(encoding='utf8'), '{"v": 3}')

    def test_missing_source_raises_without_masking(self):
        missing = os.path.join(self.dir, 'nope.tmp')
        with self.assertRaises(OSError):
            replace_with_retry(missing, self.dst, attempts=5, delay=0.01)


class GuardRecheckTests(unittest.TestCase):
    """A sharing failure must re-check the final condition before trying again."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='wbguard-')
        self.dst = os.path.join(self.dir, 'jobs.json')
        Path(self.dst).write_text('{"v": 1}', encoding='utf8')
        self.tmp = os.path.join(self.dir, 'jobs.json.tmp-x')
        Path(self.tmp).write_text('{"v": 2}', encoding='utf8')
        self.real = fa._os_replace

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _flaky(self, state, retryable=True):
        real = self.real

        def fake(src, dst):
            state['attempts'] += 1
            if state['attempts'] == 1:
                if retryable:
                    increase = state.get('drift')
                    if increase is not None:
                        increase()
                    raise _sharing_error()
                raise OSError(2, 'no such file')
            return real(src, dst)
        return fake

    def test_condition_drift_aborts_the_retried_replace(self):
        state = {'attempts': 0, 'ok': True, 'drift': None}
        state['drift'] = lambda: state.__setitem__('ok', False)
        guarded = []
        with mock.patch.object(fa, '_os_replace', side_effect=self._flaky(state)):
            with self.assertRaises(ValueError) as cm:
                replace_with_retry(self.tmp, self.dst, attempts=5, delay=0.01,
                                   guard=lambda: guarded.append(1) or state['ok'])
        self.assertIn('rejected before replace', str(cm.exception))
        self.assertEqual(state['attempts'], 1, 'no replace may happen while the condition is False')
        self.assertEqual(guarded, [1, 1], 'the guard must be re-checked before every attempt')
        self.assertEqual(Path(self.dst).read_text(encoding='utf8'), '{"v": 1}')

    def test_condition_still_true_lets_the_retry_publish(self):
        state = {'attempts': 0}
        guarded = []
        with mock.patch.object(fa, '_os_replace', side_effect=self._flaky(state)):
            replace_with_retry(self.tmp, self.dst, attempts=5, delay=0.01,
                               guard=lambda: guarded.append(1) or True)
        self.assertEqual(state['attempts'], 2)
        self.assertEqual(guarded, [1, 1])
        self.assertEqual(Path(self.dst).read_text(encoding='utf8'), '{"v": 2}')

    def test_guard_false_never_replaces(self):
        state = {'attempts': 0}
        with mock.patch.object(fa, '_os_replace', side_effect=self._flaky(state)) as patched:
            with self.assertRaises(ValueError):
                replace_with_retry(self.tmp, self.dst, attempts=5, delay=0.01, guard=lambda: False)
        patched.assert_not_called()
        self.assertEqual(Path(self.dst).read_text(encoding='utf8'), '{"v": 1}')

    def test_non_sharing_error_is_not_retried(self):
        state = {'attempts': 0}
        with mock.patch.object(fa, '_os_replace', side_effect=self._flaky(state, retryable=False)):
            with self.assertRaises(OSError):
                replace_with_retry(self.tmp, self.dst, attempts=5, delay=0.01)
        self.assertEqual(state['attempts'], 1)


class StoreRetryGuardTests(unittest.TestCase):
    """The store's own final condition must be re-run before a retried replace."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='wbstore-'))
        from workbench.store import Store
        self.Store = Store
        self.real = fa._os_replace

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _flaky(self, state, drift=True):
        real = self.real

        def fake(src, dst):
            state['attempts'] += 1
            if state['attempts'] == 1:
                if drift:
                    state['ok'] = False      # the condition drifts during the failed attempt
                raise _sharing_error()
            return real(src, dst)
        return fake

    def _store(self):
        store = self.Store(self.dir / 'records.json')
        store._save({'products': {}, 'indexes': {}})     # a durable prior state to fall back on
        return store

    def test_store_aborts_when_the_condition_drifts_before_a_retry(self):
        store = self._store()
        state = {'attempts': 0, 'ok': True}
        with mock.patch.object(fa, '_os_replace', side_effect=self._flaky(state)):
            with self.assertRaises(ValueError):
                store._save({'products': {}, 'indexes': {'i1': {'x': 1}}},
                            before_replace=lambda value: state['ok'])
        self.assertEqual(state['attempts'], 1, 'the retry must not publish after the condition went False')
        self.assertEqual(json.loads((self.dir / 'records.json').read_text(encoding='utf8')), {'products': {}, 'indexes': {}})
        self.assertEqual(store.data, {'products': {}, 'indexes': {}})

    def test_store_retries_a_sharing_violation_when_the_condition_holds(self):
        store = self._store()
        state = {'attempts': 0, 'ok': True}
        with mock.patch.object(fa, '_os_replace', side_effect=self._flaky(state, drift=False)):
            store._save({'products': {}, 'indexes': {'i1': {'x': 1}}},
                        before_replace=lambda value: state['ok'])
        self.assertEqual(state['attempts'], 2)
        self.assertEqual(json.loads((self.dir / 'records.json').read_text(encoding='utf8'))['indexes']['i1'], {'x': 1})

    def test_store_condition_can_be_true_and_the_plain_path_still_works(self):
        store = self.Store(self.dir / 'records.json')
        store._save({'products': {}, 'indexes': {'i1': {'x': 1}}})
        self.assertEqual(json.loads((self.dir / 'records.json').read_text(encoding='utf8'))['indexes']['i1'], {'x': 1})


if __name__ == '__main__':
    unittest.main(verbosity=2)
