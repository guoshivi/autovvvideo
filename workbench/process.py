"""Owned process trees with Windows Job Object and cross-platform diagnostics."""
import ctypes
import errno
import os
import signal
import subprocess
import time


class Cancelled(InterruptedError):
    pass


class StageError(RuntimeError):
    pass


def _tail(path, limit=1000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-limit:]
    except OSError as exc:
        return f"<log unavailable: {exc}>"


if os.name == "nt":
    import _winapi
    import msvcrt
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_uint64), ("WriteOperationCount", ctypes.c_uint64), ("OtherOperationCount", ctypes.c_uint64), ("ReadTransferCount", ctypes.c_uint64), ("WriteTransferCount", ctypes.c_uint64), ("OtherTransferCount", ctypes.c_uint64)]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTime", ctypes.c_int64), ("PerJobUserTime", ctypes.c_int64), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _k.CreateJobObjectW.restype = wintypes.HANDLE
    _k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k.CloseHandle.argtypes = [wintypes.HANDLE]
    # _winapi does not expose ResumeThread on all supported Python builds.
    # Bind the real kernel32 HANDLE/DWORD ABI and preserve its failure sentinel.
    _k.ResumeThread.argtypes = [wintypes.HANDLE]
    _k.ResumeThread.restype = wintypes.DWORD

    class WindowsOwned:
        def __init__(self, cmd, cwd=None, stdout_path=None, stderr_path=None):
            self.job = _k.CreateJobObjectW(None, None)
            self.hp = None
            self.ht = None
            self.pid = None
            self._logs = []
            if not self.job:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _k.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                self.close()
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
            si = subprocess.STARTUPINFO()
            inherit = False
            if stdout_path and stderr_path:
                so = open(stdout_path, "wb")
                se = open(stderr_path, "wb")
                self._logs = [so, se]
                for f in self._logs:
                    os.set_handle_inheritable(msvcrt.get_osfhandle(f.fileno()), True)
                si.dwFlags |= subprocess.STARTF_USESTDHANDLES
                si.hStdOutput = msvcrt.get_osfhandle(so.fileno())
                si.hStdError = msvcrt.get_osfhandle(se.fileno())
                si.hStdInput = _winapi.GetStdHandle(_winapi.STD_INPUT_HANDLE)
                inherit = True
            try:
                hp, ht, pid, tid = _winapi.CreateProcess(str(cmd[0]), subprocess.list2cmdline([str(x) for x in cmd]), None, None, inherit, 0x00000004 | 0x08000000, None, None if cwd is None else str(cwd), si)
                self.hp, self.ht, self.pid = hp, ht, pid
                if not _k.AssignProcessToJobObject(self.job, hp):
                    raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
                resumed = _k.ResumeThread(ht)
                if resumed == 0xFFFFFFFF:
                    error = ctypes.get_last_error()
                    raise OSError(error, f"ResumeThread failed (GetLastError={error})")
                _winapi.CloseHandle(ht)
                self.ht = None
            except BaseException:
                self.kill()
                self.close()
                raise

        def poll(self):
            if _winapi.WaitForSingleObject(self.hp, 0) == _winapi.WAIT_TIMEOUT:
                return None
            return _winapi.GetExitCodeProcess(self.hp)

        def kill(self):
            if self.job:
                _k.TerminateJobObject(self.job, 1)

        def close(self):
            if getattr(self, "ht", None):
                _winapi.CloseHandle(self.ht)
                self.ht = None
            if getattr(self, "hp", None):
                _winapi.CloseHandle(self.hp)
                self.hp = None
            if getattr(self, "job", None):
                _k.CloseHandle(self.job)
                self.job = None
            for f in getattr(self, "_logs", []):
                try:
                    f.close()
                except OSError:
                    pass
            self._logs = []
else:
    class WindowsOwned:
        pass


def _kill_unix_group(proc):
    """Kill only the process group created by this invocation."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise


def _group_exists(proc):
    try:
        os.killpg(proc.pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run_owned(cmd, *, cwd=None, timeout=900, cancel=None, log_dir=None):
    if not isinstance(cmd, (list, tuple)) or not cmd or not all(isinstance(x, str) for x in cmd):
        raise ValueError("command must be string list")
    log_dir = os.fspath(log_dir or cwd or os.getcwd())
    os.makedirs(log_dir, exist_ok=True)
    name = f"stage-{time.time_ns()}"
    out = os.path.join(log_dir, name + ".stdout.log")
    err = os.path.join(log_dir, name + ".stderr.log")
    if os.name == "nt":
        proc = WindowsOwned(list(cmd), cwd, out, err)
    else:
        with open(out, "xb") as so, open(err, "xb") as se:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=so, stderr=se, start_new_session=True)
    start = time.monotonic()
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                # A leader can exit while an owned descendant remains.  The
                # process group/Job Object is still ours, so close it now.
                if os.name != "nt" and _group_exists(proc):
                    _kill_unix_group(proc)
                if rc:
                    raise StageError(f"stage/exit: {cmd[0]} returncode={rc}; stdout={out}; stderr={err}; stderr_tail={_tail(err)}")
                return {"returncode": rc, "stdout": out, "stderr": err, "seconds": time.monotonic() - start}
            if cancel and cancel():
                raise Cancelled("stage/cancel: owned process terminated")
            if time.monotonic() - start > timeout:
                raise StageError(f"stage/timeout: {cmd[0]} elapsed>{timeout}s; stdout={out}; stderr={err}")
            time.sleep(0.1)
    except BaseException:
        if os.name == "nt":
            proc.kill()
        else:
            _kill_unix_group(proc)
        for _ in range(100):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        raise
    finally:
        if os.name == "nt":
            proc.close()
