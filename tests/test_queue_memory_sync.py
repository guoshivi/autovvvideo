import copy
import hashlib
import json

import pytest

from workbench import queue as queue_module
from workbench.queue import JobQueue


def _job(jid, status='queued'):
    return {'id': jid, 'product_id': 'p', 'input_sha256': hashlib.sha256(b'{}').hexdigest(),
            'input': {}, 'variant': 0, 'selection_rank': 0, 'status': status,
            'stage': status, 'progress': 0, 'created_at': 1.0, 'error': None, 'revision': 0}


def test_memory_follows_published_disk_when_rollback_fails(tmp_path, monkeypatch):
    q = JobQueue(tmp_path / 'jobs.json')
    jid = 'a' * 32
    d = q.root / jid
    d.mkdir()
    (d / 'input.json').write_bytes(b'{}')
    q.jobs = {jid: _job(jid)}
    q._save()

    real = queue_module.replace_with_retry

    def flaky(src, dst, guard=None):
        if 'reconcile' in str(src):
            raise OSError('share is gone')
        return real(src, dst, guard=guard)

    monkeypatch.setattr(queue_module, 'replace_with_retry', flaky)
    candidate = copy.deepcopy(q.jobs)
    candidate[jid].update(status='done', stage='done', progress=100)
    calls = {'n': 0}

    def condition(_value):
        calls['n'] += 1
        return calls['n'] < 3  # the post-publish re-check fails

    with pytest.raises(queue_module.CommitError) as exc:
        q._save(candidate, condition)
    assert exc.value.after_replace is True
    # The rollback could not restore, so disk already holds the published row;
    # memory must agree with disk instead of staying one revision behind.
    assert q.jobs[jid]['status'] == 'done'
