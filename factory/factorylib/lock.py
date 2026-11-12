"""A per-job lock that works on Windows and Unix, held until the process exits."""

import os
import time

if os.name == "nt":
    import msvcrt

    def _try_lock(f):
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
else:
    import fcntl

    def _try_lock(f):
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

_held = {}


def hold(path, timeout=120):
    """Lock `path` for the rest of this process. Re-entrant per path."""
    key = str(path)
    if key in _held:
        return
    f = open(path, "a+")
    deadline = time.monotonic() + timeout
    while not _try_lock(f):
        if time.monotonic() > deadline:
            f.close()
            raise TimeoutError(f"{path} is locked by another factory process")
        time.sleep(0.1)
    _held[key] = f
