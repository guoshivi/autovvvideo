"""Server-side NAS directory authority. API paths are root IDs and component tokens."""
from pathlib import Path
import os, re, stat

class BrowserError(ValueError): pass
VIDEO_EXT={'.mp4','.mov','.m4v','.avi','.mkv','.webm','.wmv','.ts','.mts'}
AUDIO_EXT={'.mp3','.wav','.m4a','.aac','.flac'}
IMAGE_EXT={'.jpg','.jpeg','.png','.webp','.bmp'}
_RESERVED={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
_RESERVED |= {f'COM{x}' for x in '¹²³'} | {f'LPT{x}' for x in '¹²³'}

def component(name):
    if not isinstance(name,str) or not name or name in ('.','..') or name[-1:] in (' ','.') or '/' in name or '\\' in name or ':' in name or '\0' in name or any(ord(c)<32 for c in name) or name.split('.')[0].upper() in _RESERVED:
        raise BrowserError('非法目录名/Windows 保留名/ADS')
    return name

def reject_links(path, floor=None):
    p=Path(path).absolute()
    floor=Path(floor).absolute() if floor is not None else None
    # Inspect the complete lexical chain before resolve.  A link in a
    # configured root's parent must be rejected even when the final root
    # component itself is a normal directory.
    items=[p,*p.parents]
    if floor is not None:
        try:
            rel=p.relative_to(floor); items += [floor.joinpath(*rel.parts[:i]) for i in range(len(rel.parts))]
        except ValueError: pass
    for item in dict.fromkeys(items):
        try: s=item.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:
            raise BrowserError('拒绝符号链接/重解析点')
    return p

def overlaps(a,b):
    a=os.path.normcase(os.path.abspath(a)); b=os.path.normcase(os.path.abspath(b))
    try: return os.path.commonpath([a,b]) in (a,b)
    except ValueError: return False

class FolderBrowser:
    def __init__(self,roots):
        self.roots=[]
        for r in roots:
            raw=Path(r).absolute(); reject_links(raw); p=raw.resolve()
            reject_links(p); self.roots.append(p)
    def _parts(self,token):
        if token in ('',None): return []
        if not isinstance(token,str) or token.startswith(('/', '\\')): raise BrowserError('仅接受相对目录 token')
        return [component(x) for x in token.split('/')]
    def at(self,root_id,token=''):
        if type(root_id) is not int or root_id<0 or root_id>=len(self.roots): raise BrowserError('未知 NAS 根目录')
        p=self.roots[root_id].joinpath(*self._parts(token)); reject_links(p, self.roots[root_id])
        if not p.is_dir(): raise BrowserError('NAS 目录不可访问；请检查当前登录用户权限')
        return p
    def at_path(self,path):
        raw=str(path or '').strip()
        if not raw:raise BrowserError('空路径')
        if raw.startswith(('\\\\?\\','\\\\.\\')):raise BrowserError('拒绝设备路径')
        if '..' in Path(raw).parts:raise BrowserError('拒绝路径穿越')
        p=Path(raw)
        if not p.is_absolute():raise BrowserError('需要完整路径，例如 D:\\素材 或 \\\\server\\共享')
        p=p.absolute()
        s=str(p);body=s[2:] if (len(s)>1 and s[1]==':') else s
        if ':' in body:raise BrowserError('拒绝 ADS/非法字符')
        reject_links(p)
        if not p.is_dir():raise BrowserError('目录不存在或不可访问: '+raw)
        return p
    def list_free(self,path):
        q=self.at_path(path)
        try:
            return [{'name':x.name,'path':str(x)} for x in sorted(q.iterdir(),key=lambda z:z.name.casefold()) if x.is_dir() and not x.is_symlink() and not (getattr(x.lstat(),'st_file_attributes',0)&0x400) and _valid(x.name)]
        except OSError as e:raise BrowserError(f'目录读取失败: {e}') from e
    def browse_path(self,path='',pick='audio'):
        exts={'audio':AUDIO_EXT,'image':IMAGE_EXT,'video':VIDEO_EXT,'media':IMAGE_EXT|VIDEO_EXT}.get(pick) or AUDIO_EXT
        if not path:
            entries=[{'name':d,'path':d} for d in self.drives()]
            for r in self.roots:entries.append({'name':str(r),'path':str(r)})
            return {'path':'','parent':None,'directories':entries,'audio_files':[],'files':[]}
        q=self.at_path(path);entries=self.list_free(str(q))
        parent=str(q.parent) if q.parent!=q else ''
        files=[]
        try:
            for x in sorted(q.iterdir(),key=lambda z:z.name.casefold()):
                if len(files)>=500:break
                try:
                    if x.is_file() and not x.is_symlink() and not (getattr(x.lstat(),'st_file_attributes',0)&0x400) and _valid(x.name) and x.suffix.lower() in exts:
                        files.append({'name':x.name,'path':str(x)})
                except OSError:continue
        except OSError:pass
        return {'path':str(q),'parent':parent,'directories':entries,'audio_files':files if pick=='audio' else [],'files':files}
    @staticmethod
    def drives():
        out=[]
        for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            try:
                if os.path.exists(f'{c}:/'):out.append(f'{c}:/')
            except OSError:pass
        return out
    def resolve(self,path):
        raw=str(path)
        # Legacy absolute interface retained for the original tests; public API uses at().
        parts=Path(raw).parts
        if '..' in parts or raw.startswith(('\\\\?\\','\\\\.\\')): raise BrowserError('拒绝路径穿越或设备路径')
        rawp=Path(raw).absolute()
        # Inspect the lexical path before resolution so a link cannot disappear
        # by resolving into an authorized target. /private is the macOS test
        # alias; Windows reparse ancestors are rejected by lstat below.
        for item in [rawp,*rawp.parents]:
            try:
                if item.is_symlink(): raise BrowserError('拒绝符号链接/重解析点')
            except OSError as e: raise BrowserError(f'NAS 权限/运行身份错误: {e}')
        p=rawp.resolve(strict=False)
        for root in self.roots:
            try: rel=p.relative_to(root)
            except ValueError: continue
            for x in rel.parts: component(x)
            reject_links(p, root)
            return p
        raise BrowserError('路径不在授权 NAS 根目录内')
    def list(self,path):
        q=self.resolve(path)
        if not q.is_dir(): raise BrowserError('不是目录')
        try:
            return [{'name':x.name,'path':str(x),'is_dir':True} for x in sorted(q.iterdir(),key=lambda z:z.name.casefold()) if x.is_dir() and not x.is_symlink() and not (getattr(x.lstat(),'st_file_attributes',0)&0x400) and _valid(x.name)]
        except OSError as e: raise BrowserError(f'NAS 权限/运行身份错误: {e}') from e
    def browse(self,root_id,token=''):
        p=self.at(root_id,token)
        children=[]
        for x in self.list(p): children.append({'name':x['name'],'token':token+'/'+x['name'] if token else x['name']})
        return {'root_id':root_id,'token':token,'parent':'/'.join(token.split('/')[:-1]) if token else None,'directories':children}
    def files(self,path,extensions=VIDEO_EXT):
        q=self.at_path(path)
        if not q.is_dir(): raise BrowserError('不是目录')
        out=[]
        for root,dirs,files in os.walk(q,followlinks=False):
            dirs[:]=[d for d in dirs if _valid(d) and not (Path(root)/d).is_symlink() and not (getattr((Path(root)/d).lstat(),'st_file_attributes',0)&0x400)]
            for f in files:
                x=Path(root)/f
                if _valid(f) and x.suffix.lower() in extensions and not x.is_symlink() and not (getattr(x.lstat(),'st_file_attributes',0)&0x400):out.append(x)
        return sorted(out)

def _valid(s):
    try: component(s); return True
    except BrowserError: return False
