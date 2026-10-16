from workbench.cleanup import cleanup_job_dir

def test_cleanup_allowlist_and_bytes(tmp_path):
    (tmp_path/'cut_1.mp4').write_bytes(b'12');(tmp_path/'draft.mp4').write_bytes(b'3');(tmp_path/'font.ttf').write_bytes(b'456')
    (tmp_path/'voice_000.wav').write_bytes(b'keep');(tmp_path/'final.mp4').write_bytes(b'keep')
    (tmp_path/'voice-intermediate').mkdir();(tmp_path/'voice-intermediate'/'x').write_bytes(b'1234')
    r=cleanup_job_dir(tmp_path,'font.ttf')
    assert r['files']==4 and r['bytes']==10
    assert (tmp_path/'voice_000.wav').exists() and (tmp_path/'final.mp4').exists() and not (tmp_path/'voice-intermediate').exists()

def test_final_codec_is_completely_preserved(tmp_path):
    d=tmp_path/'final-codec';d.mkdir();(d/'final.mp4').write_bytes(b'12345');(d/'other.f32').write_bytes(b'keep')
    before=sum(p.stat().st_size for p in d.iterdir());r=cleanup_job_dir(tmp_path,'font.ttf')
    assert r['files']==0 and r['bytes']==0 and sum(p.stat().st_size for p in d.iterdir())==before

def test_symlink_directory_is_not_followed(tmp_path):
    outside=tmp_path.parent/'cleanup-outside';outside.mkdir(exist_ok=True);secret=outside/'secret.f32';secret.write_bytes(b'secret')
    link=tmp_path/'linked';
    try:link.symlink_to(outside,target_is_directory=True)
    except (OSError,NotImplementedError):return
    cleanup_job_dir(tmp_path,'font.ttf')
    assert secret.exists() and link.is_symlink()

def test_monkeypatched_reparse_boundary_is_not_entered(tmp_path,monkeypatch):
    protected=tmp_path/'protected';protected.mkdir();secret=protected/'secret.f32';secret.write_bytes(b'secret')
    import workbench.cleanup as mod
    original=mod.no_follow;inode=protected.stat().st_ino
    monkeypatch.setattr(mod,'no_follow',lambda st: st.st_ino==inode or original(st))
    cleanup_job_dir(tmp_path,'font.ttf')
    assert secret.exists() and protected.exists()

def test_nested_voice_intermediate_is_removed_and_counted(tmp_path):
    vi=tmp_path/'voice-intermediate'/'a'/'b';vi.mkdir(parents=True);(vi/'one').write_bytes(b'123');(vi/'two').write_bytes(b'4567')
    r=cleanup_job_dir(tmp_path,'font.ttf')
    assert r['files']==2 and r['bytes']==7 and not (tmp_path/'voice-intermediate').exists()
