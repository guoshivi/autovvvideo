"""Local smoke: silent render with intro/outro cards + cover + selector (feature 8/10).

Run: python smoke_v3_render.py
"""
import subprocess, tempfile, json, sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workbench.media import sha256
from workbench.renderer import render_job
from workbench.selection import shot_usage

def main():
    tmp = Path(tempfile.mkdtemp(prefix='wbk-smoke-', dir=Path(__file__).resolve().parent/'reports'))
    src = tmp / 'src'; src.mkdir()
    for name, color in (('a.mp4', 'blue'), ('b.mp4', 'red')):
        subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={color}:s=1080x1920:r=30','-t','8','-an','-c:v','libx264','-pix_fmt','yuv420p',str(src/name)], check=True)
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=green:s=1080x1920','-frames:v','1',str(src/'card.png')], check=True)
    out = tmp / 'job'; out.mkdir()
    cfg = SimpleNamespace(ffmpeg='ffmpeg', ffprobe='ffprobe', fdk_encoder='fdkaac', font_path='', max_stage_seconds=120)
    candidates = [{'path': str(src/'a.mp4'), 'source_sha256': sha256(src/'a.mp4'), 'start': 0, 'seconds': 8},
                  {'path': str(src/'b.mp4'), 'source_sha256': sha256(src/'b.mp4'), 'start': 0, 'seconds': 8},
                  {'path': str(src/'a.mp4'), 'source_sha256': sha256(src/'a.mp4'), 'start': 4, 'seconds': 4}]
    # sliver regression fixture: slot 60f, only candidate floor(1.94*30)=58f ->
    # a 2-frame tail that must be DROPPED (not cut as a fragment that would
    # destabilize concat frame bookkeeping).
    sliver = [{'path': str(src/'b.mp4'), 'source_sha256': sha256(src/'b.mp4'), 'start': 6, 'seconds': 1.94}]
    job = {'id': 'smoke', 'variant': 1, 'lines': ['第一句测试', '第二句测试', '第三句尾巴'], 'mapping': [candidates, candidates, sliver],
           'settings': {'voice': False, 'subtitles': False, 'bgm': False, 'dedup': 'off', 'shot_reuse': 1,
                        'cover': {'mode': 'auto'},
                        'intro': {'path': str(src/'card.png'), 'kind': 'image', 'seconds': 2, 'sha256': sha256(src/'card.png')},
                        'outro': {'path': str(src/'card.png'), 'kind': 'image', 'seconds': 1, 'sha256': sha256(src/'card.png')}},
           'index_id': 'i', 'input_sha256': 'x', 'bgm_sha256': None}
    final, report = render_job(job, cfg, out)
    tl = report['timeline']
    assert final.is_file() and report['ok'], report
    assert (out/'cover.jpg').is_file() and report['cover']['sha256'] == sha256(out/'cover.jpg')
    usage = report['shot_usage']
    total = tl[-1]['end']
    picks = [(c['path'].split('/')[-1], c['start']) for c in tl[0]['sources']] + [(c['path'].split('/')[-1], c['start']) for c in tl[1]['sources']]
    print('OK duration=%.2fs intro=%.2f outro_tail=%.2f' % (total, tl[0]['start'], total - tl[1]['end'] + 0))
    print('timeline:', json.dumps(tl, ensure_ascii=False))
    print('picks:', picks)
    print('usage:', json.dumps(usage, ensure_ascii=False))
    print('cover bytes:', report['cover']['bytes'])
    # expectations: intro shifts first sentence to 2.0s; both sentences pick different shots when possible
    assert abs(tl[0]['start'] - 2.0) < 1/30 + .01, tl
    keys = [(c['path'], c['start']) for c in tl[0]['sources']] + [(c['path'], c['start']) for c in tl[1]['sources']]
    assert len(set(keys)) >= 2, keys
    # sliver regression: sentence 2's slot (60f) minus its only candidate (58f)
    # leaves a 2-frame tail that must be dropped, not cut; draft timing must
    # stay exactly CFR 30 (no phantom frame slots from tiny fragments).
    cuts3 = sorted(out.glob('cut_002_*.mp4'))
    assert len(cuts3) == 1 and cuts3[0].name == 'cut_002_000.mp4', cuts3
    assert tl[2]['segments'] == ['cut_002_000.mp4'], tl[2]
    fr = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=avg_frame_rate', '-of', 'csv=p=0', str(out/'draft.mp4')], capture_output=True, text=True).stdout.strip()
    assert fr == '30/1', 'draft avg_frame_rate=%r' % fr
    print('sliver case ok: frames=%d draft=%s' % (round((tl[2]['end']-tl[2]['start'])*30), fr))
    print('ALL CHECKS PASSED')

if __name__ == '__main__':
    main()
