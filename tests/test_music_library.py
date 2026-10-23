from pathlib import Path

import pytest

from workbench.music import MUSIC_DIR, MusicError, find, list_tracks, resolve
from workbench.settings import ProductionSettings

QWEN = ["Serena", "Vivian", "Uncle_Fu", "Ryan", "Aiden", "Ono_Anna", "Sohee", "Eric", "Dylan"]


@pytest.mark.parametrize("speaker", QWEN)
def test_every_model_speaker_is_accepted(speaker):
    s = ProductionSettings.from_dict({"speaker": speaker})
    s.validate()
    assert s.speaker == speaker


def test_unknown_speaker_is_rejected():
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"speaker": "Cherry"}).validate()


def test_music_library_lists_tracks_with_unique_ids():
    rows = list_tracks()
    assert len(rows) >= 5
    assert len({r["id"] for r in rows}) == len(rows)
    assert {"兴奋", "愉悦", "平静", "悲伤", "动感"} <= {r["mood"] for r in rows}
    for row in rows:
        p = Path(row["path"])
        assert p.is_file() and p.stat().st_size > 100_000
        assert row["license"]


def test_music_resolution_stays_inside_the_library():
    row = list_tracks()[0]
    p = resolve(row["id"])
    assert p.parent == MUSIC_DIR.resolve()
    with pytest.raises(MusicError):
        resolve("../../etc/passwd")
    with pytest.raises(MusicError):
        find("missing-id")
