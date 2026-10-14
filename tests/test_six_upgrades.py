import json
from pathlib import Path

import pytest

from workbench.copy_tools import clean_punctuation, parse_script_variants
from workbench.filters import ffmpeg_filter
from workbench.queue import JobQueue
from workbench.settings import ProductionSettings
from workbench.selection import diversity_rank


def _cand(i):
    return {'path':f'/m/{i}.mp4','start':float(i),'seconds':2.0,'source_sha256':f'{i:064x}','index_id':'idx','score':1-i/10}


def test_allocation_is_persistent_and_multi_count_one_rotates(tmp_path):
    q=JobQueue(tmp_path/'jobs.json')
    mapping=[[ _cand(0),_cand(1),_cand(2) ]]
    base={'kind':'render','mapping':mapping,'mapping_sha256':'old','lines':['a'],'settings':{'voice':False}}
    a=q.enqueue('p',base,1); b=q.enqueue('p',base,1)
    assert a['variant']==b['variant']==0
    assert a['input']['allocation_seq']==0 and b['input']['allocation_seq']==1
    assert a['input']['selection_context']['prefix_seconds']==15
    assert a['input']['mapping'][0][0]['source_sha256'] != b['input']['mapping'][0][0]['source_sha256']
    assert q.get(a['id'])['input_sha256'] == a['input_sha256']


def test_diversity_keeps_provenance_and_marks_overlap_identity():
    rows=[[dict(_cand(0),start=10),dict(_cand(0),start=10.5),_cand(1)]]
    out=diversity_rank(rows,1,[(rows[0][0]['source_sha256'],10.0)])
    assert all('source_sha256' in x and 'index_id' in x for x in out[0])
    assert out[0][0]['source_sha256'] != rows[0][0]['source_sha256'] or out[0][0]['start'] != 10


def test_clean_copy_preserves_numbers_units_and_separator():
    src='-3.5°C!! 1.25L,,, 50% USB-C ABC2 mg/ml\n第二句？？\n---\n第三句。'
    got=clean_punctuation(src)
    assert '-3.5°C' in got and '1.25L' in got and '50%' in got and 'USB-C' in got
    assert got.count('---')==1 and got.count('\n')>=3 and '第二句' in got
    assert clean_punctuation(got)==got


def test_fission_response_is_structured_unique_and_rejects_duplicates():
    rows=parse_script_variants({'variants':['开头一\n卖点','开头二\n卖点']})
    assert len(rows)==2
    with pytest.raises(ValueError):parse_script_variants({'variants':['同稿','同稿']})


def test_settings_filters_are_independent_and_nan_rejected():
    s=ProductionSettings.from_dict({'beauty':True,'beauty_strength':0,'visual_filter':'warm','visual_filter_strength':0.4})
    assert s.beauty and s.beauty_strength==0 and s.visual_filter=='warm'
    with pytest.raises(ValueError):ProductionSettings.from_dict({'visual_filter':'bad'})
    assert 'hflip' not in ffmpeg_filter('cinema',.4)
