"""GPU preflight: fail-closed parsing, unchanged busy thresholds, retry policy.

Two regressions are guarded here:
  * an nvidia-smi output that cannot be parsed must REFUSE the run.  The current
    line raises; a refactor that maps unparseable output to an empty row list
    would silently read as "GPU idle" and let a job through (F4).
  * the busy thresholds stay the existing `mem>1500 or util>5` values, and the
    4x3s retry wrapper + ComfyUI queue check are preserved.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workbench.service import (ServiceError, _parse_smi_rows, _gpu_busy_reason,
                               _gpu_preflight_once, gpu_preflight,
                               _GPU_BUSY_MEM_MB, _GPU_BUSY_UTIL)


class _Cfg:
    comfy_ports = []
    port = 8765


def _smi(stdout, returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr='')


class ParseTests(unittest.TestCase):
    def test_parses_one_row(self):
        self.assertEqual(_parse_smi_rows('0, 2263, 7'), [(0, 2263, 7)])

    def test_parses_multiple_rows_and_blank_lines(self):
        self.assertEqual(_parse_smi_rows('\n0, 2263, 7\n1, 100, 0\n'), [(0, 2263, 7), (1, 100, 0)])

    def test_unparseable_output_is_refused(self):
        for bad in ('not,a,row', '0, 2263', '0, 2263, seven', 'garbage', ',,,', '0, -1', '0, 1e3, 4'):
            self.assertIsNone(_parse_smi_rows(bad), bad)

    def test_empty_output_is_refused(self):
        for empty in ('', '   \n\n'):
            self.assertIsNone(_parse_smi_rows(empty))


class BusyThresholdTests(unittest.TestCase):
    def test_idle_card_is_allowed(self):
        self.assertIsNone(_gpu_busy_reason([(0, 200, 1)]))

    def test_exactly_at_thresholds_is_allowed(self):
        self.assertIsNone(_gpu_busy_reason([(0, _GPU_BUSY_MEM_MB, _GPU_BUSY_UTIL)]))

    def test_high_utilization_is_busy(self):
        reason = _gpu_busy_reason([(0, 200, _GPU_BUSY_UTIL + 1)])
        self.assertIsNotNone(reason)
        self.assertIn('显卡正忙', reason)

    def test_high_memory_use_is_busy(self):
        reason = _gpu_busy_reason([(0, _GPU_BUSY_MEM_MB + 1, 0)])
        self.assertIsNotNone(reason)
        self.assertIn(f'{_GPU_BUSY_MEM_MB + 1}MB', reason)

    def test_any_gpu_busy_blocks(self):
        self.assertIsNotNone(_gpu_busy_reason([(0, 10, 0), (1, 10, 95)]))


class PreflightOnceTests(unittest.TestCase):
    def test_valid_idle_output_is_accepted(self):
        with mock.patch('workbench.service.subprocess.run', return_value=_smi('0, 10, 0')):
            _gpu_preflight_once(_Cfg())

    def test_malformed_nvidia_smi_is_refused(self):
        with mock.patch('workbench.service.subprocess.run', return_value=_smi('not,a,row')):
            with self.assertRaises(ServiceError) as cm:
                _gpu_preflight_once(_Cfg())
        self.assertIn('malformed', str(cm.exception))

    def test_unavailable_nvidia_smi_is_refused(self):
        with mock.patch('workbench.service.subprocess.run', return_value=_smi('', returncode=9)):
            with self.assertRaises(ServiceError) as cm:
                _gpu_preflight_once(_Cfg())
        self.assertIn('unavailable', str(cm.exception))

    def test_busy_output_is_refused(self):
        with mock.patch('workbench.service.subprocess.run', return_value=_smi('0, 9000, 49')):
            with self.assertRaises(ServiceError) as cm:
                _gpu_preflight_once(_Cfg())
        self.assertIn('显卡正忙', str(cm.exception))

    def test_comfy_queue_is_still_checked(self):
        cfg = _Cfg()
        cfg.comfy_ports = [8188]
        with mock.patch('workbench.service.urllib.request.build_opener') as opener, \
             mock.patch('workbench.service.subprocess.run', return_value=_smi('0, 10, 0')):
            opener.return_value.open.return_value.__enter__.return_value = _Queue({'queue_running': [1], 'queue_pending': []})
            with self.assertRaises(ServiceError) as cm:
                _gpu_preflight_once(cfg)
        self.assertIn('ComfyUI', str(cm.exception))


class _Queue:
    def __init__(self, payload):
        self.payload = payload

    def read(self, *_a):
        import json
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RetryTests(unittest.TestCase):
    def test_four_attempts_with_three_waits(self):
        calls = {'n': 0}

        def flaky(cfg):
            calls['n'] += 1
            raise ServiceError('显卡正忙')

        with mock.patch('workbench.service._gpu_preflight_once', side_effect=flaky), \
             mock.patch('workbench.service.time.sleep') as sleeper:
            with self.assertRaises(ServiceError):
                gpu_preflight(_Cfg())
        self.assertEqual(calls['n'], 4)
        self.assertEqual(sleeper.call_count, 3)

    def test_first_success_returns_without_waiting(self):
        with mock.patch('workbench.service._gpu_preflight_once', return_value=None) as once, \
             mock.patch('workbench.service.time.sleep') as sleeper:
            gpu_preflight(_Cfg())
        self.assertEqual(once.call_count, 1)
        sleeper.assert_not_called()


if __name__ == '__main__':
    unittest.main()
