"""Safe, allow-listed cleanup helpers for completed jobs.

The walker is deliberately based on ``os.scandir`` and lstat-like stats. On
Windows a junction/reparse directory is not necessarily a symlink, so a
``Path.rglob`` or ``os.walk(followlinks=False)`` is not a sufficient boundary.
"""
from pathlib import Path
import fnmatch, os, stat

def no_follow(st):
    """Return True for symlinks and Windows reparse points."""
    return bool(stat.S_ISLNK(st.st_mode) or (getattr(st,'st_file_attributes',0) & 0x400))

def _stat_entry(entry):
    try:return entry.stat(follow_symlinks=False)
    except OSError:return None

def iter_tree(root):
    """Yield ``(Path, stat_result)`` for safe, non-link tree entries."""
    root=Path(root)
    try:
        rst=root.lstat()
        if no_follow(rst) or not stat.S_ISDIR(rst.st_mode):return
    except OSError:return
    def walk(folder):
        try:entries=os.scandir(folder)
        except OSError:return
        with entries:
            for entry in entries:
                st=_stat_entry(entry)
                if st is None or no_follow(st):continue
                p=Path(entry.path);yield p,st
                if stat.S_ISDIR(st.st_mode):yield from walk(p)
    yield from walk(root)

def _safe_root(root):
    try:
        # A link in an ancestor would make every lexical child point outside
        # the job boundary.  Inspect the chain without resolving it.
        for cur in (root,*root.parents):
            st=cur.lstat()
            if no_follow(st):return None
        st=root.lstat()
        return st if not no_follow(st) and root.is_dir() else None
    except OSError:return None

def _link_only_dir(path):
    try:
        found=False
        with os.scandir(path) as entries:
            for entry in entries:
                found=True;st=_stat_entry(entry)
                if st is None or not no_follow(st):return False
        return found
    except OSError:return False

def _rmdir_quiet(path,errors):
    try:path.rmdir()
    except OSError as e:
        if not _link_only_dir(path):errors.append(f'{path}: {e}')

def cleanup_job_dir(jobdir,font_basename,*,verified_export=False):
    """Remove allow-listed intermediates.

    ``verified_export`` is deliberately opt-in.  The service may set it only
    after a completed job's persisted receipt and external output have been
    independently verified; legacy callers continue to protect all finals.
    """
    root=Path(jobdir);out={'files':0,'bytes':0,'errors':[]}
    if _safe_root(root) is None:return out
    def remove_file(p,st=None):
        if st is None:
            try:st=p.stat(follow_symlinks=False)
            except OSError:return
        if no_follow(st):return
        try:p.unlink();out['files']+=1;out['bytes']+=st.st_size
        except OSError as e:out['errors'].append(f'{p.name}: {e}')
    for p,st in iter_tree(root):
        try:
            rel=p.relative_to(root)
            if any(part=='final-codec' for part in rel.parts):continue
            if not stat.S_ISREG(st.st_mode):continue
            name=p.name
            target=(name==font_basename or fnmatch.fnmatch(name,'cut_*.mp4') or name=='draft.mp4' or fnmatch.fnmatch(name,'beauty-*') or fnmatch.fnmatch(name,'captioned-*') or fnmatch.fnmatch(name,'card_*.mp4') or fnmatch.fnmatch(name,'*.f32') or fnmatch.fnmatch(name,'*.f32.tmp'))
            if target:remove_file(p,st)
        except OSError as e:out['errors'].append(f'{p}: {e}')
    if verified_export:
        # Silent renders leave a root-level final.mp4.  Codec retries keep
        # their encoder/QA media under final-codec/codec-*/; delete only the
        # concrete names produced by the renderer, never an arbitrary subtree.
        p=root/'final.mp4'
        try:
            st=p.lstat()
            if stat.S_ISREG(st.st_mode) and not no_follow(st):remove_file(p,st)
        except FileNotFoundError:pass
        except OSError as e:out['errors'].append(f'{p.name}: {e}')
        codec=root/'final-codec'
        try:
            cst=codec.lstat()
            if not no_follow(cst) and stat.S_ISDIR(cst.st_mode):
                known={'master.wav','audio.m4a','final.mp4','final-decoded.f32','final-decoded.f32.tmp'}
                for p,st in iter_tree(codec):
                    rel=p.relative_to(codec)
                    if (stat.S_ISREG(st.st_mode) and p.name in known and len(rel.parts)==2
                            and fnmatch.fnmatch(rel.parts[0],'codec-*')):
                        remove_file(p,st)
        except FileNotFoundError:pass
        except OSError as e:out['errors'].append(f'{codec}: {e}')
    vi=root/'voice-intermediate';errors=out['errors']
    try:
        vst=vi.lstat()
        if no_follow(vst):return out
        if stat.S_ISDIR(vst.st_mode):
            rows=list(iter_tree(vi))
            for p,st in sorted(rows,key=lambda x:len(x[0].parts),reverse=True):
                if stat.S_ISREG(st.st_mode):remove_file(p,st)
                elif stat.S_ISDIR(st.st_mode):_rmdir_quiet(p,errors)
            _rmdir_quiet(vi,errors)
    except FileNotFoundError:pass
    except OSError as e:errors.append(f'{vi}: {e}')
    return out

def remove_job_dir(jobdir):
    """Delete a job directory without traversing links; return (bytes, errors)."""
    root=Path(jobdir)
    if _safe_root(root) is None:return 0,[]
    total=0;errors=[]
    rows=list(iter_tree(root))
    for p,st in sorted(rows,key=lambda x:len(x[0].parts),reverse=True):
        try:
            if stat.S_ISREG(st.st_mode):
                try:p.unlink();total+=st.st_size
                except OSError as e:errors.append(f'{p}: {e}')
            elif stat.S_ISDIR(st.st_mode):_rmdir_quiet(p,errors)
        except OSError as e:errors.append(f'{p}: {e}')
    _rmdir_quiet(root,errors)
    return total,errors
