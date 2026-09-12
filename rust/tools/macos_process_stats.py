# SPDX-License-Identifier: GPL-3.0-only
"""macOS proc_pid_rusage counters; structure matches the installed SDK V1 ABI."""
import ctypes

class Usage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_ticks", "system_ticks", "package_idle_wakeups", "interrupt_wakeups",
            "pageins", "wired_bytes", "resident_bytes", "footprint_bytes",
            "start_abstime", "exit_abstime", "child_user_ticks", "child_system_ticks",
            "child_package_idle_wakeups", "child_interrupt_wakeups", "child_pageins", "child_elapsed_abstime")]

libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
libproc.proc_pid_rusage.restype = ctypes.c_int

class Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
system.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
system.mach_timebase_info.restype = ctypes.c_int
timebase = Timebase()
assert system.mach_timebase_info(ctypes.byref(timebase)) == 0
seconds_per_tick = timebase.numer / timebase.denom / 1e9

def counters(pid):
    usage = Usage()
    if libproc.proc_pid_rusage(pid, 1, ctypes.byref(usage)):
        raise OSError(ctypes.get_errno(), f"proc_pid_rusage failed for PID {pid}")
    return {"cpu_seconds": (usage.user_ticks + usage.system_ticks) * seconds_per_tick,
            "child_cpu_seconds": (usage.child_user_ticks + usage.child_system_ticks) * seconds_per_tick,
            "interrupt_wakeups": usage.interrupt_wakeups,
            "package_idle_wakeups": usage.package_idle_wakeups}
