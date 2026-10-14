"""fix2 regression tests (parent-repaired copy of the writer's broken draft).

The writer's original file never executed (WorkbenchService.__new__ skipped the
attrs the timing path needs: store lock, timing cache, cfg ffmpeg/ffprobe).
This repaired version keeps the same contract intents, exercises the real
Store/JobQueue/Service objects, and proves the timing path never touches the
embedder unless retrieve=True is requested.  Parent-owned edit, recorded in
parent-fix2-review evidence; business assertions unchanged in intent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.config import RuntimeConfig
from workbench.indexer import collect_source_fingerprints
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.store import Store


def _svc(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a1.mp4").write_bytes(b"A" * 64)
    (src / "a2.mp4").write_bytes(b"A" * 64)  # same content alias
    (src / "b1.mp4").write_bytes(b"B" * 64)
    fps = collect_source_fingerprints(str(src))
    by_path = {f["path"]: f for f in fps}
    files = []
    for p in sorted(by_path):
        e = {"fingerprint": by_path[p]}
        if p.endswith("a1.mp4"):
            e["blocks"] = [{"start": 0, "seconds": 3}, {"start": 2, "seconds": 3}]
        elif p.endswith("a2.mp4"):
            e["blocks"] = [{"start": 4, "seconds": 2}]
        else:
            e["blocks"] = [{"start": 0, "seconds": 4}]
        files.append(e)
    cfg = RuntimeConfig(
        data_root=str(tmp_path / "data"),
        nas_roots=[str(src)],
        project_src=str(Path(__file__).resolve().parents[1]),
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        font_path="/System/Library/Fonts/Helvetica.ttc",
        ai_api_key="SYNTHETIC",
        max_stage_seconds=30,
    )
    st = Store(tmp_path / "store.json")
    q = JobQueue(tmp_path / "queue.json")
    calls = []

    def factory():
        calls.append(1)
        raise RuntimeError("no GPU for timing test")

    s = WorkbenchService(cfg, st, q, embedder_factory=factory, start=False)
    p = st.create_product("测试商品")
    pid = p["id"]
    st.select(pid, "source", {"path": str(src)})
    st.put_index(
        pid,
        {
            "id": "idx",
            "product_id": pid,
            "source": str(src),
            "files": files,
            "settings": {"sample_seconds": 10},
        },
    )
    return s, pid, calls


def test_timing_without_retrieve_uses_deduplicated_index_capacity(tmp_path):
    s, pid, calls = _svc(tmp_path)
    out = s.timing(pid, "甲")
    assert out["estimate"]["available_seconds"] == 10.0
    assert "未按当前文案匹配" in out["estimate"]["warning"]
    assert calls == []  # the default timing path must not touch the embedder


def test_timing_retrieve_cache_reused_without_second_embedder(tmp_path):
    s, pid, calls = _svc(tmp_path)
    seen = []

    def fake(p, rec, text):
        seen.append(text)
        return [
            {
                "sentence_id": 0,
                "line": text,
                "candidates": [
                    {"path": "x.mp4", "source_sha256": "a" * 64, "index_id": "idx", "start": 0.0, "seconds": 2.0}
                ],
            }
        ]

    s._retrieve_snapshot = fake
    assert s.timing(pid, "甲", retrieve=True)["estimate"]["available_seconds"] == 2.0
    assert s.timing(pid, "甲")["estimate"]["available_seconds"] == 2.0
    assert len(seen) == 1
    assert calls == []
