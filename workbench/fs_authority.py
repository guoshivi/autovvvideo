"""Small fail-closed filesystem authority helpers."""
from pathlib import Path
import os, stat, threading, contextlib, ctypes, sys

class AuthorityError(ValueError): pass
class UnsupportedNative(OSError): pass
class CommitEffectError(IOError):
    """A publication syscall happened, but its postcondition was not proven."""
    def __init__(self, message, *, receipt=None):
        super().__init__(message); self.after_replace=True; self.receipt=receipt or {}

_tx = threading.local()
@contextlib.contextmanager
def commit_scope(kind):
    n=getattr(_tx,'depth',0); _tx.depth=n+1
    try: yield
    finally: _tx.depth=n
def assert_mutation_allowed():
    if getattr(_tx,'depth',0):
        raise AuthorityError('mutation is forbidden during final commit validation')

def lexical_under(path, root):
    p, r = Path(path).absolute(), Path(root).absolute()
    try: p.relative_to(r)
    except ValueError: raise AuthorityError('path outside authorized root')
    cur = r
    for part in p.relative_to(r).parts:
        cur = cur / part
        try: st = cur.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(st.st_mode) or (getattr(st, 'st_file_attributes', 0) & 0x400):
            raise AuthorityError('reparse/symlink ancestor rejected')
    return p

def identity_chain(path):
    """Return the lexical ancestor identities used by a publication lease."""
    p=Path(path).absolute()
    rows=[]
    for cur in (p,*p.parents):
        try: st=cur.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(st.st_mode) or (getattr(st,'st_file_attributes',0)&0x400):
            raise AuthorityError('reparse/symlink ancestor rejected')
        rows.append((str(cur),int(st.st_dev),int(st.st_ino),int(st.st_mode)))
    return tuple(rows)

def same_identity_chain(path, expected):
    try: return identity_chain(path)==tuple(expected)
    except (OSError,ValueError): return False

def capture_lease(path):
    p=Path(path).absolute()
    return {'path':str(p),'chain':[list(x) for x in identity_chain(p)]}

def exclusive_stage(root, name):
    root = lexical_under(root, root)
    p = root / name
    p.mkdir(exist_ok=False)
    return p

def authorized_ancestors(path, root):
    """Validate lexical ancestors and return an immutable absolute root/path pair."""
    p=Path(path).absolute(); r=Path(root).absolute()
    identity_chain(r)
    try: p.relative_to(r)
    except ValueError: raise AuthorityError('path outside authorized root')
    cur=r
    for part in p.relative_to(r).parts:
        cur=cur/part
        try:
            st=cur.lstat()
            if stat.S_ISLNK(st.st_mode) or (getattr(st,'st_file_attributes',0)&0x400): raise AuthorityError('ancestor changed to reparse point')
        except FileNotFoundError: pass
    return p

def verify_lease(path, expected):
    actual=identity_chain(path)
    if tuple(tuple(x) for x in expected or ()) and actual!=tuple(tuple(x) for x in expected):
        raise AuthorityError('ancestor identity lease changed')
    return actual

def _validate_windows_path(path):
    """Permit ordinary absolute drive/UNC paths, not ADS or device namespaces."""
    from pathlib import PureWindowsPath
    text=str(path); p=PureWindowsPath(text)
    if text.startswith(('\\\\?\\','\\\\.\\','\\??\\')) or not p.is_absolute():
        raise AuthorityError('device or relative Windows path rejected')
    devices={'CON','PRN','AUX','NUL'}|{'COM'+str(i) for i in range(1,10)}|{'LPT'+str(i) for i in range(1,10)}
    for part in p.parts[1:]:
        if ':' in part or part.endswith((' ','.')) or part.split('.')[0].upper() in devices:
            raise AuthorityError('ADS/device component rejected')
    return p

def windows_noreplace(stage, destination, root, expected_chain=None):
    """HANDLE-bound Windows directory rename with ReplaceIfExists=FALSE.

    No pathlib/os.replace fallback is permitted.  The opened root handle does
    not share delete, so a competing rename/delete request is rejected by the
    kernel while this operation is in flight. Ordinary UNC roots are allowed;
    ADS/device paths and reparse ancestors are rejected before opening.
    This helper alone does not freeze directory members or the outer transaction.
    """
    if os.name != 'nt': raise UnsupportedNative('Windows HANDLE publication unavailable on this host')
    s=Path(stage).absolute(); d=Path(destination).absolute(); r=Path(root).absolute()
    for p in (s,d,r): _validate_windows_path(p)
    lexical_under(s,r); lexical_under(d,r)
    if s.parent!=d.parent: raise AuthorityError('staging and destination must be siblings')
    verify_lease(r, expected_chain)
    k=ctypes.WinDLL('kernel32', use_last_error=True)
    HANDLE=ctypes.c_void_p; INVALID=HANDLE(-1).value
    CreateFileW=k.CreateFileW; CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,HANDLE,ctypes.c_uint32,ctypes.c_uint32,HANDLE];CreateFileW.restype=HANDLE
    CloseHandle=k.CloseHandle; CloseHandle.argtypes=[HANDLE]; CloseHandle.restype=ctypes.c_int
    FILE_FLAG_BACKUP_SEMANTICS=0x02000000; FILE_FLAG_OPEN_REPARSE_POINT=0x00200000; OPEN_EXISTING=3; GENERIC_READ=0x80000000; FILE_SHARE_READ=1|2
    h=CreateFileW(str(r),GENERIC_READ,FILE_SHARE_READ,None,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,None)
    if h is None or h==INVALID: raise OSError(ctypes.get_last_error(),'CreateFileW(root) failed')
    try:
        verify_lease(r, expected_chain)
        nt=ctypes.WinDLL('ntdll', use_last_error=True)
        class IO_STATUS_UNION(ctypes.Union):
            _fields_=[('Status',ctypes.c_int32),('Pointer',HANDLE)]
        class IO_STATUS_BLOCK(ctypes.Structure):
            _anonymous_=('u',)
            _fields_=[('u',IO_STATUS_UNION),('Information',ctypes.c_size_t)]
        NtSetInformationFile=nt.NtSetInformationFile
        NtSetInformationFile.argtypes=[HANDLE,ctypes.POINTER(IO_STATUS_BLOCK),ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int32]
        NtSetInformationFile.restype=ctypes.c_int32
        RtlNtStatusToDosError=nt.RtlNtStatusToDosError;RtlNtStatusToDosError.argtypes=[ctypes.c_int32];RtlNtStatusToDosError.restype=ctypes.c_uint32
        FILE_RENAME_INFORMATION_CLASS=10
        # Open the staging directory itself and rename it relative to its parent.
        hs=CreateFileW(str(s),GENERIC_READ|0x00010000,FILE_SHARE_READ,None,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,None)
        if hs is None or hs==INVALID: raise OSError(ctypes.get_last_error(),'CreateFileW(stage) failed')
        try:
            # Rebuild with the actual parent handle and basename.
            parent=CreateFileW(str(s.parent),GENERIC_READ,FILE_SHARE_READ,None,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,None)
            if parent is None or parent==INVALID: raise OSError(ctypes.get_last_error(),'CreateFileW(parent) failed')
            try:
                name=os.path.basename(str(d)).encode('utf-16-le')
                class RENAME_FLAGS(ctypes.Union):
                    _fields_=[('ReplaceIfExists',ctypes.c_ubyte),('Flags',ctypes.c_uint32)]
                class FILE_RENAME_INFO(ctypes.Structure):
                    _anonymous_=('u',)
                    _fields_=[('u',RENAME_FLAGS),('RootDirectory',HANDLE),('FileNameLength',ctypes.c_uint32),('FileName',ctypes.c_ubyte*len(name))]
                nbuf=FILE_RENAME_INFO(); nbuf.Flags=0; nbuf.RootDirectory=parent; nbuf.FileNameLength=len(name)
                for i,b in enumerate(name): nbuf.FileName[i]=b
                storage=ctypes.create_string_buffer(ctypes.sizeof(nbuf)+len(name))
                ctypes.memmove(storage,ctypes.byref(nbuf),ctypes.sizeof(nbuf))
                iosb=IO_STATUS_BLOCK()
                status=NtSetInformationFile(hs,ctypes.byref(iosb),storage,ctypes.sizeof(storage),FILE_RENAME_INFORMATION_CLASS)
                if status!=0:
                    error=RtlNtStatusToDosError(status)
                    raise OSError(error,'NtSetInformationFile(FileRenameInformation) failed: NTSTATUS='+hex(status&0xffffffff))
            finally: CloseHandle(parent)
        finally: CloseHandle(hs)
    finally: CloseHandle(h)
