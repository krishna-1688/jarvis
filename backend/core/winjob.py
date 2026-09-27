"""
core/winjob.py — a Windows Job Object that kills its member processes the
instant it's closed, even if this Python process is itself killed with no
chance to run any cleanup code (Task Manager "End Task", a crash, a power
loss of just this process). Verified: closing the job handle terminates
its children within ~1s, with no involvement from the owning process at
all — Windows does it, not our code.

This is what actually prevents orphaned server.py/jarvis.py processes;
run.py's own try/finally is only for the graceful-shutdown path (Ctrl+C),
which can't be relied on for every way a process might die.
"""

import ctypes
import sys

_JOB = None


class _BASIC_LIMITS(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_void_p), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_uint64), ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64), ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64), ("OtherTransferCount", ctypes.c_uint64)]


class _EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BASIC_LIMITS), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_JOBOBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _ensure_job():
    global _JOB
    if _JOB is not None:
        return _JOB
    kernel32 = ctypes.windll.kernel32
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError()
    info = _EXTENDED_LIMITS()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, _JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                                            ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError()
    _JOB = job
    return job


def add_process(pid: int):
    """Adds a process (by PID) to the shared job. If this Python process
    dies for ANY reason, Windows kills every process added here too. No-op
    on non-Windows."""
    if sys.platform != "win32":
        return
    job = _ensure_job()
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not handle:
        print(f"[winjob] couldn't open process {pid} to add it to the job — it won't be auto-cleaned up")
        return
    try:
        if not kernel32.AssignProcessToJobObject(job, handle):
            print(f"[winjob] couldn't assign process {pid} to the job — it won't be auto-cleaned up")
    finally:
        kernel32.CloseHandle(handle)
