"""The engine with few rights: low integrity, inside a job object.

A PDF that takes over the engine through a fault in MuPDF then cannot write into the
user's folders (low integrity may write only where it is allowed: the engine's own folder
under %LOCALAPPDATA%Low), and dies with the window (the job closes with it). The engine still
reads the files it is given; see HANDOFF.md, step F, for what would close that too.

multiprocessing's spawn is used as it is, with one change: the process is created with a
low-integrity copy of our token. Such a process cannot open ours, so the pipe that carries
the start data is inherited instead of being taken over from us.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as w

from multiprocessing import context, reduction, spawn, util

LOW = "S-1-16-4096"
FOLDERID_LOCAL_LOW = "{A520A1A4-1780-4FF6-BD18-167343C5AF16}"
MEMORY_LIMIT = 6 * 1024 ** 3  # per process: far above what a document needs

failed = ""    # why the last engine could not be started with few rights ("" when it was)
weakened = ""  # what of the job could not be had; the engine still runs with few rights

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", w.DWORD)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD),
                ("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD),
                ("dwThreadId", w.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _check(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return ok


# -- the folder a low-integrity engine may write to ------------------------------------------
def local_low() -> str:
    """%USERPROFILE%\\AppData\\LocalLow: marked so that low integrity may write there."""
    guid = ctypes.create_string_buffer(16)
    ctypes.oledll.ole32.CLSIDFromString(FOLDERID_LOCAL_LOW, guid)  # raises on failure
    path = ctypes.c_wchar_p()
    ctypes.oledll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path))
    try:
        return path.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


def engine_base() -> str:
    """Where the engine folders of all windows live."""
    base = os.environ.get("FURCELI_ENGINE_BASE") or os.path.join(local_low(), "ETRATI")
    os.makedirs(base, exist_ok=True)
    return base


# -- the job: the engines die with the window ------------------------------------------------
_job = None


def job():
    """One job for the engines of this window, or None when Windows gives none. It is never
    closed on purpose: Windows closes it when this process ends, however it ends, and every
    engine in it goes too."""
    global _job, weakened
    if _job is None:
        try:
            _job = _make_job()
        except OSError as e:
            weakened = f"no job: {e}"
            _job = 0
    return _job or None


def _make_job():
    global weakened
    _k32.CreateJobObjectW.restype = w.HANDLE
    handle = _check(_k32.CreateJobObjectW(None, None))
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = (
        0x2000      # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | 0x0400    # JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION: no error box that waits
        | 0x0100)   # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.ProcessMemoryLimit = MEMORY_LIMIT
    _check(_k32.SetInformationJobObject(w.HANDLE(handle), 9, ctypes.byref(info),
                                        ctypes.sizeof(info)))
    ui = w.DWORD(0x02 | 0x04 | 0x08 | 0x10 | 0x40 | 0x80)  # clipboard, system settings, desktop
    try:
        _check(_k32.SetInformationJobObject(w.HANDLE(handle), 4, ctypes.byref(ui), 4))
    except OSError as e:  # a job around us may forbid it: the rest still holds
        weakened = f"no UI limits: {e}"
    return handle


# -- a low-integrity token --------------------------------------------------------------------
def _low_token():
    token = w.HANDLE()
    _check(_adv.OpenProcessToken(w.HANDLE(_k32.GetCurrentProcess()),
                                 0x0002 | 0x0008 | 0x0080 | 0x0001, ctypes.byref(token)))
    low = w.HANDLE()
    try:
        _check(_adv.DuplicateTokenEx(token, 0x02000000, None, 2, 1, ctypes.byref(low)))
    finally:
        _k32.CloseHandle(token)
    sid = ctypes.c_void_p()
    _check(_adv.ConvertStringSidToSidW(LOW, ctypes.byref(sid)))
    try:
        label = _SidAndAttributes(sid, 0x20)  # SE_GROUP_INTEGRITY
        size = ctypes.sizeof(label) + _adv.GetLengthSid(sid)
        _check(_adv.SetTokenInformation(low, 25, ctypes.byref(label), size))  # TokenIntegrityLevel
    except BaseException:
        _k32.CloseHandle(low)
        raise
    finally:
        _k32.LocalFree(sid)
    return low


def _environment(extra):
    env = dict(os.environ, **extra)
    block = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda i: i[0].upper()))
    return ctypes.create_unicode_buffer(block + "\0")


def create_low(exe, cmd, inherit, env):
    """(process handle, pid) of exe started at low integrity in the job, with one handle given
    to it and the environment env added."""
    _k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD,
                                                       ctypes.POINTER(ctypes.c_size_t)]
    _k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t,
                                               ctypes.c_void_p, ctypes.c_size_t,
                                               ctypes.c_void_p, ctypes.c_void_p]
    size = ctypes.c_size_t()
    _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attrs = ctypes.create_string_buffer(size.value)
    _check(_k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)))
    log = None
    if os.environ.get("FURCELI_SANDBOX_LOG"):  # what the engine says while it starts, for a bug hunt
        import msvcrt
        log_file = open(os.environ["FURCELI_SANDBOX_LOG"], "ab")
        log = msvcrt.get_osfhandle(log_file.fileno())
    handles = (w.HANDLE * (2 if log else 1))(*([inherit, log] if log else [inherit]))
    token = None
    try:
        _check(_k32.SetHandleInformation(w.HANDLE(inherit), 1, 1))  # HANDLE_FLAG_INHERIT
        if log:
            _check(_k32.SetHandleInformation(w.HANDLE(log), 1, 1))
        _check(_k32.UpdateProcThreadAttribute(attrs, 0, 0x20002, handles,  # HANDLE_LIST
                                              ctypes.sizeof(handles), None, None))
        si = _StartupInfoEx()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
        if log:
            si.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            si.StartupInfo.hStdOutput = si.StartupInfo.hStdError = log
        pi = _ProcessInfo()
        token = _low_token()
        line = ctypes.create_unicode_buffer(cmd)
        flags = 0x00000004 | 0x00080000 | 0x00000400 | 0x08000000  # suspended, extended
        _check(_adv.CreateProcessAsUserW(token, exe, line, None, None, True, flags,  # info,
                                         _environment(env), None, ctypes.byref(si),  # unicode,
                                         ctypes.byref(pi)))                           # no window
        try:
            if job() is not None:
                _check(_k32.AssignProcessToJobObject(w.HANDLE(job()), pi.hProcess))
        except OSError as e:  # still low integrity, only without the job's limits
            global weakened
            weakened = f"not in the job: {e}"
        finally:
            if pi.hProcess:
                _k32.ResumeThread(pi.hThread)
            _k32.CloseHandle(pi.hThread)
        return pi.hProcess, pi.dwProcessId
    finally:
        _k32.SetHandleInformation(w.HANDLE(inherit), 1, 0)
        _k32.DeleteProcThreadAttributeList(attrs)
        if log:
            log_file.close()
        if token is not None:
            _k32.CloseHandle(token)


class LowPopen(object):
    """multiprocessing's Windows spawn (popen_spawn_win32.Popen), started with few rights."""
    method = "spawn"

    def __init__(self, process_obj):
        import _winapi
        import msvcrt
        prep_data = spawn.get_preparation_data(process_obj._name)
        rhandle, whandle = _winapi.CreatePipe(None, 0)
        wfd = msvcrt.open_osfhandle(whandle, 0)
        # no parent_pid: the child takes the pipe it inherited, it cannot open us
        cmd = spawn.get_command_line(pipe_handle=rhandle)
        exe, env = spawn.get_executable(), {"TEMP": process_obj._tmp, "TMP": process_obj._tmp}
        if sys.platform == "win32" and getattr(sys, "_base_executable", exe) != exe \
                and os.path.normcase(exe) == os.path.normcase(sys.executable):
            cmd[0] = exe = sys._base_executable  # a venv: as popen_spawn_win32 does
            env["__PYVENV_LAUNCHER__"] = sys.executable
        cmd = " ".join('"%s"' % x for x in cmd)
        with open(wfd, "wb", closefd=True) as to_child:
            try:
                hp, pid = create_low(exe, cmd, rhandle, env)
            finally:
                _winapi.CloseHandle(rhandle)
            self.pid, self.returncode = pid, None
            self._handle = self.sentinel = int(hp)
            self.finalizer = util.Finalize(self, _winapi.CloseHandle, (self.sentinel,))
            context.set_spawning_popen(self)
            try:
                reduction.dump(prep_data, to_child)
                self._pickler(to_child).dump(process_obj)
            finally:
                context.set_spawning_popen(None)

    def _pickler(self, file):
        """A pickler that hands pipe ends over to the child itself. multiprocessing leaves the
        child to take them out of this process, which low integrity may not open."""
        import _winapi
        from multiprocessing import connection
        pickler = reduction.ForkingPickler(file, 2)

        def handover(conn):
            access = ((_winapi.FILE_GENERIC_READ if conn.readable else 0)
                      | (_winapi.FILE_GENERIC_WRITE if conn.writable else 0))
            dh = reduction.DupHandle(conn.fileno(), access, self.pid)
            return connection.rebuild_pipe_connection, (dh, conn.readable, conn.writable)
        pickler.dispatch_table[connection.PipeConnection] = handover
        return pickler

    def duplicate_for_child(self, handle):
        assert self is context.get_spawning_popen()
        return reduction.duplicate(handle, self.sentinel)

    def wait(self, timeout=None):
        import _winapi
        if self.returncode is not None:
            return self.returncode
        msecs = _winapi.INFINITE if timeout is None else max(0, int(timeout * 1000 + 0.5))
        if _winapi.WaitForSingleObject(int(self._handle), msecs) == _winapi.WAIT_OBJECT_0:
            self.returncode = _winapi.GetExitCodeProcess(self._handle)
        return self.returncode

    def poll(self):
        return self.wait(timeout=0)

    def terminate(self):
        import _winapi
        if self.returncode is not None:
            return
        try:
            _winapi.TerminateProcess(int(self._handle), 1)
        except PermissionError:
            if _winapi.GetExitCodeProcess(int(self._handle)) == _winapi.STILL_ACTIVE:
                raise

    kill = terminate

    def close(self):
        self.finalizer()


class LowProcess(context.SpawnProcess):
    """A spawned process with few rights. _tmp: its temporary folder, where it may write."""
    _tmp = ""

    @staticmethod
    def _Popen(process_obj):
        return LowPopen(process_obj)


def integrity(pid) -> int:
    """The integrity level of a process (0x1000 low, 0x2000 medium); for tests and checks."""
    _k32.OpenProcess.restype = w.HANDLE
    proc = _check(_k32.OpenProcess(0x1000, False, pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    token = w.HANDLE()
    try:
        _check(_adv.OpenProcessToken(w.HANDLE(proc), 0x0008, ctypes.byref(token)))
        buf = ctypes.create_string_buffer(64)
        n = w.DWORD()
        _check(_adv.GetTokenInformation(token, 25, buf, 64, ctypes.byref(n)))
        sid = ctypes.cast(buf, ctypes.POINTER(_SidAndAttributes)).contents.Sid
        _adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        _adv.GetSidSubAuthority.restype = ctypes.POINTER(w.DWORD)
        count = _adv.GetSidSubAuthorityCount(ctypes.c_void_p(sid)).contents.value
        return _adv.GetSidSubAuthority(ctypes.c_void_p(sid), count - 1).contents.value
    finally:
        if token:
            _k32.CloseHandle(token)
        _k32.CloseHandle(w.HANDLE(proc))
