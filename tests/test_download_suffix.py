"""Real HTTP coverage for the completed-result Range boundary."""

import hashlib
import threading
import urllib.error
import urllib.request

import pytest

from workbench.config import RuntimeConfig
from workbench.server import make_server
from workbench.store import Store


SYNTHETIC_BYTES = b"synthetic-result-0123456789"
JOB_ID = "job-synthetic-result"
PRODUCT_ID = "product-synthetic"


class SyntheticQueue:
    def __init__(self, job):
        self.job = job

    def get(self, job_id):
        if job_id != JOB_ID:
            raise KeyError(job_id)
        return dict(self.job)


class SyntheticService:
    def __init__(self, cfg, queue):
        self.cfg = cfg
        self.queue = queue


@pytest.fixture
def result_server(tmp_path):
    output = tmp_path / "synthetic-result.bin"
    output.write_bytes(SYNTHETIC_BYTES)
    job = {
        "id": JOB_ID,
        "product_id": PRODUCT_ID,
        "status": "done",
        "output": str(output),
        "artifact": {
            "sha256": hashlib.sha256(SYNTHETIC_BYTES).hexdigest(),
            "bytes": len(SYNTHETIC_BYTES),
        },
    }
    queue = SyntheticQueue(job)
    cfg = RuntimeConfig(
        host="localhost",
        port=0,
        data_root=str(tmp_path / "data"),
        nas_roots=[str(tmp_path / "materials")],
    )
    service = SyntheticService(cfg, queue)
    server = make_server(cfg, Store(tmp_path / "records.json"), queue, service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, queue, output
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request_result(server, range_value=None, product_id=PRODUCT_ID):
    url = (
        f"http://localhost:{server.server_port}/api/results/"
        f"{JOB_ID}?product_id={product_id}"
    )
    headers = {} if range_value is None else {"Range": range_value}
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read()


def assert_range_response(status, headers, body, start, end, total):
    assert status == 206
    assert body == SYNTHETIC_BYTES[start : end + 1]
    assert headers["Content-Range"] == f"bytes {start}-{end}/{total}"
    assert headers["Content-Length"] == str(end - start + 1)


def test_full_download_is_an_exact_http_response(result_server):
    server, _, _ = result_server
    status, headers, body = request_result(server)
    assert status == 200
    assert body == SYNTHETIC_BYTES
    assert headers["Content-Length"] == str(len(SYNTHETIC_BYTES))
    assert headers["Accept-Ranges"] == "bytes"


@pytest.mark.parametrize(
    ("range_value", "start", "end"),
    [
        ("bytes=1-4", 1, 4),
        ("bytes=1-", 1, len(SYNTHETIC_BYTES) - 1),
        ("bytes=-1", len(SYNTHETIC_BYTES) - 1, len(SYNTHETIC_BYTES) - 1),
        ("bytes=-4", len(SYNTHETIC_BYTES) - 4, len(SYNTHETIC_BYTES) - 1),
        ("bytes=-" + str(len(SYNTHETIC_BYTES)), 0, len(SYNTHETIC_BYTES) - 1),
        ("bytes=-999", 0, len(SYNTHETIC_BYTES) - 1),
    ],
)
def test_ranges_return_exact_body_and_headers(result_server, range_value, start, end):
    server, _, _ = result_server
    status, headers, body = request_result(server, range_value)
    assert_range_response(status, headers, body, start, end, len(SYNTHETIC_BYTES))


@pytest.mark.parametrize(
    ("range_value", "expected_status"),
    [
        ("bytes=-0", 416),
        ("bytes=999-", 416),
        ("bytes=1-999", 416),
        ("bytes=bad", 400),
        ("bytes=1-2,3-4", 400),
        ("bytes=-", 416),
    ],
)
def test_unsatisfiable_or_malformed_ranges_are_refused(result_server, range_value, expected_status):
    server, _, _ = result_server
    status, headers, body = request_result(server, range_value)
    assert status == expected_status
    if expected_status == 416:
        assert body == b""
        assert headers["Content-Range"] == f"bytes */{len(SYNTHETIC_BYTES)}"
    else:
        assert b"invalid Range" in body


def test_product_status_and_digest_guards_remain_refusals(result_server):
    server, queue, output = result_server
    status, _, _ = request_result(server, product_id="other-synthetic-product")
    assert status == 403

    queue.job["status"] = "running"
    status, _, _ = request_result(server)
    assert status == 403

    queue.job["status"] = "done"
    output.write_bytes(b"tampered-synthetic-result")
    status, _, _ = request_result(server)
    assert status == 403
