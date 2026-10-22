import numpy as np

from workbench import renderer


def test_sticker_windows_anchor_start_and_end():
    timeline = [{'start': 0.0, 'end': 3.0}, {'start': 3.0, 'end': 6.0}]
    rows = renderer._sticker_windows([
        {'line': 1, 'icon': 'check', 'at': 'start', 'side': 'right'},
        {'line': 2, 'icon': 'money', 'at': 'end', 'side': 'left'},
        {'line': 9, 'icon': 'box'},
    ], timeline)
    assert rows == [('check', 'right', 0.0, 1.0), ('money', 'left', 5.0, 6.0)]


def test_sticker_window_skips_too_short_sentences():
    assert renderer._sticker_windows([{'line': 1, 'icon': 'box'}], [{'start': 0.0, 'end': 0.1}]) == []


def test_mix_sfx_places_sound_at_sentence_start_and_respects_gain():
    master = np.zeros(24000 * 2)
    master.setflags(write=False)
    quiet = renderer._mix_sfx(master, [{'line': 1, 'sound': 'ding', 'gain': -12}], [{'start': 0.5, 'end': 2.0}], [])
    loud = renderer._mix_sfx(master, [{'line': 1, 'sound': 'ding', 'gain': -3}], [{'start': 0.5, 'end': 2.0}], [])
    assert abs(quiet).max() > 0 and abs(loud).max() > abs(quiet).max()
    assert abs(quiet[:12000]).max() == 0  # nothing before the cue


def test_mix_sfx_keeps_headroom_below_qa_ceiling():
    master = np.full(24000 * 2, 0.80)
    master.setflags(write=False)
    out = renderer._mix_sfx(master, [{'line': 1, 'sound': 'ding', 'gain': -3}], [{'start': 0.0, 'end': 2.0}], [])
    assert float(np.max(np.abs(out))) <= 0.93 + 1e-9
    assert float(np.max(np.abs(out))) > 0.80


def test_every_icon_and_sound_asset_exists():
    from workbench import packaging
    from pathlib import Path
    root = Path(renderer.__file__).parent / 'assets'
    for icon in packaging.ICONS:
        assert (root / 'stickers' / f'{icon}.png').is_file(), icon
    for sound in packaging.SOUNDS:
        assert (root / 'sfx' / f'{sound}.wav').is_file(), sound


def test_duplicate_icon_uses_one_input_per_window(monkeypatch):
    captured = {}

    def fake_command(args, cwd, timeout, cancel):
        captured['args'] = args

    monkeypatch.setattr(renderer, '_command', fake_command)
    monkeypatch.setattr(renderer, '_probe_duration', lambda ffprobe, path: 6.0)
    timeline = [{'start': 0.0, 'end': 3.0}, {'start': 3.0, 'end': 6.0}]
    stickers = [
        {'line': 1, 'icon': 'check', 'at': 'start', 'side': 'right'},
        {'line': 2, 'icon': 'check', 'at': 'start', 'side': 'left'},
    ]
    from pathlib import Path
    renderer._apply_stickers(type('C', (), {'ffmpeg': 'ff', 'ffprobe': 'fp', 'max_stage_seconds': 10})(),
                             'draft.mp4', stickers, timeline, 380, Path('/tmp'), None)
    args = captured['args']
    assert args.count('-loop') == 2
    graph = args[args.index('-filter_complex') + 1]
    assert graph.count('[ic1]overlay') == 1 and graph.count('[ic2]overlay') == 1
    assert 'gte(t,0.000)*lt(t,1.000)' in graph


def test_missing_sfx_asset_fails_loudly():
    import pytest
    master = np.zeros(24000)
    master.setflags(write=False)
    with pytest.raises(ValueError, match='sfx missing'):
        renderer._mix_sfx(master, [{'line': 1, 'sound': 'not-a-sound'}], [{'start': 0.0, 'end': 1.0}], [])
