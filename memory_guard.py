"""Keep the process under Render's 512 MB limit.

Oct 2026: long Review runs with auto-discovery were killed by Render
("Ran out of memory (used over 512MB)"); the in-memory job vanished and the
page said "The server restarted while this job was running".

Three defences, all cheap and none changing any result:
  1. glibc malloc arenas capped at 2 -- with dozens of fetch threads glibc
     otherwise creates one arena per thread and RSS balloons with memory
     Python has already freed.
  2. A watchdog thread: above MEM_SOFT_MB it drops the registered caches
     (only re-fetch cost, never a different answer), runs gc and returns
     freed memory to the OS with malloc_trim.
  3. Back-pressure: `wait_for_headroom()` is called before every outbound
     download; above MEM_HARD_MB new downloads wait (up to a few seconds)
     until the watchdog has brought memory back down.
"""
import ctypes
import gc
import logging
import os
import threading
import time

log = logging.getLogger(__name__)


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MEM_SOFT_MB = _int_env("MEM_SOFT_MB", 360)
MEM_HARD_MB = _int_env("MEM_HARD_MB", 420)
_CHECK_EVERY = 2.0

try:
    _LIBC = ctypes.CDLL("libc.so.6")
except OSError:  # not glibc (Windows / macOS dev machines)
    _LIBC = None

if _LIBC is not None:
    try:
        _LIBC.mallopt(-8, _int_env("MALLOC_ARENA_MAX", 2))  # M_ARENA_MAX
    except Exception:  # noqa: BLE001
        pass

_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_clearers = []          # (name, fn, priority) -- lower priority cleared first
_lock = threading.Lock()
_started = False


def rss_mb():
    """Resident memory of this process in MB (0 when unknown)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * _PAGE / 1048576
    except Exception:  # noqa: BLE001
        return 0


def trim():
    gc.collect()
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:  # noqa: BLE001
            pass


def register_cache(name, obj, priority=1, lock=None):
    """Register a dict-like cache that may be emptied under memory pressure."""
    def _clear():
        if lock is not None:
            with lock:
                obj.clear()
        else:
            obj.clear()
    with _lock:
        _clearers.append((priority, name, _clear))
        _clearers.sort(key=lambda t: t[0])


def relieve(target_mb=None):
    """Drop caches (lowest priority first) until under target_mb."""
    target = target_mb or MEM_SOFT_MB
    before = rss_mb()
    trim()
    dropped = []
    with _lock:
        clearers = list(_clearers)
    for _p, name, fn in clearers:
        if rss_mb() < target:
            break
        try:
            fn()
            dropped.append(name)
        except Exception:  # noqa: BLE001
            pass
        trim()
    if dropped:
        log.warning("memory guard: RSS %.0f MB -> %.0f MB (cleared %s)",
                    before, rss_mb(), ", ".join(dropped))


def _watch():
    while True:
        time.sleep(_CHECK_EVERY)
        try:
            if rss_mb() > MEM_SOFT_MB:
                relieve()
        except Exception:  # noqa: BLE001
            pass


def start():
    global _started
    with _lock:
        if _started or not os.path.exists("/proc/self/statm"):
            return
        _started = True
    threading.Thread(target=_watch, daemon=True, name="memory-guard").start()


def wait_for_headroom(max_wait=8.0):
    """Block a new download while the process is above MEM_HARD_MB."""
    if rss_mb() < MEM_HARD_MB:
        return
    deadline = time.time() + max_wait
    relieve()
    while rss_mb() >= MEM_HARD_MB and time.time() < deadline:
        time.sleep(0.25)


start()
