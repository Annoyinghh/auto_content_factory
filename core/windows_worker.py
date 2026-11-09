"""Run a Windows worker and its descendants in one OS job."""

import _winapi
import ctypes
import subprocess
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_ulonglong * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
_kernel.CreateJobObjectW.restype = wintypes.HANDLE
_kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, wintypes.INT, wintypes.LPVOID, wintypes.DWORD)
_kernel.SetInformationJobObject.restype = wintypes.BOOL
_kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_kernel.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
_kernel.ResumeThread.restype = wintypes.DWORD


def run_in_job(command, timeout, env):
    job = _kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process = thread = None
    attached = False
    try:
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Assign before resuming the initial thread so the EXE bootloader cannot spawn an untracked child.
        create_suspended = 0x00000004
        process, thread, _, _ = _winapi.CreateProcess(
            None, subprocess.list2cmdline(command), None, None, False,
            subprocess.CREATE_NO_WINDOW | create_suspended, env, None, subprocess.STARTUPINFO(),
        )
        attached = bool(_kernel.AssignProcessToJobObject(job, process))
        if not attached or _kernel.ResumeThread(thread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        if _winapi.WaitForSingleObject(process, round(timeout * 1000)) == _winapi.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(command, timeout)
        return _winapi.GetExitCodeProcess(process)
    finally:
        _winapi.CloseHandle(job)
        if process is not None:
            try:
                if not attached:
                    _winapi.TerminateProcess(process, 1)
                _winapi.WaitForSingleObject(process, 5000)
            finally:
                _winapi.CloseHandle(thread)
                _winapi.CloseHandle(process)
