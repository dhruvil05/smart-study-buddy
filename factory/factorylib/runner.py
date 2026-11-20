"""The runner's own state, shared by run.py, job.py and the dashboard.

factory/jobs/_runner/
  .lock        held by the one run.py working on this project
  state.json   what it is doing, rewritten every few seconds (a heartbeat)
  STOP         a stop request: `graceful` (finish the current steps) or `now`
  runner.log   output of a runner started from the dashboard
"""

import json
import os
import time

from . import jobs, lock, paths
from .files import atomic_write

LOCK = paths.RUNNER / ".lock"
STATE = paths.RUNNER / "state.json"
STOP = paths.RUNNER / "STOP"
LOG = paths.RUNNER / "runner.log"


def claim():
    """Become this project's runner, or raise TimeoutError when another one is running."""
    paths.RUNNER.mkdir(parents=True, exist_ok=True)
    lock.hold(LOCK, timeout=0)
    STOP.unlink(missing_ok=True)  # an old request doesn't apply to a new runner


def running():
    return lock.is_held(LOCK)


def request_stop(now=False):
    paths.RUNNER.mkdir(parents=True, exist_ok=True)
    atomic_write(STOP, "now\n" if now else "graceful\n")


def stop_request():
    """None, "graceful" or "now"."""
    try:
        text = STOP.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return "now" if text == "now" else "graceful"


def write_state(**fields):
    data = {"pid": os.getpid(), "beat": jobs.now(), **fields}
    try:
        atomic_write(STATE, json.dumps(data, indent=2) + "\n")
    except OSError:
        pass  # a heartbeat is best effort (Windows can refuse a replace while the file is read)


def status():
    """What the dashboard and `run.py status` show."""
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    alive = running()
    out = {"running": alive, "stopping": stop_request() if alive else None}
    if alive:
        for k in ("pid", "started", "beat", "watch", "issue", "phase", "active", "stopped"):
            out[k] = data.get(k)
    else:
        out["last"] = {k: data.get(k) for k in ("started", "beat", "phase", "exit") if k in data} or None
    return out


def signature():
    """Changes whenever a person acts on this machine: feedback, reject, pause, resume, retry,
    settings or the factory-wide pause. Cheap enough to check every second."""
    marks = [paths.PAUSE.exists()]
    for f in (paths.CONFIG_FILE,):
        marks.append(f.stat().st_mtime_ns if f.exists() else 0)
    if paths.JOBS.exists():
        for d in sorted(paths.JOBS.iterdir()):
            j = d / "job.json"
            if j.exists():
                try:
                    marks.append((d.name, j.stat().st_mtime_ns, (d / "runs" / "stopped.md").exists()))
                except OSError:
                    pass
    return tuple(marks)


class Beat:
    """Rewrites state.json at most every `every` seconds, and on demand."""

    def __init__(self, every=3):
        self.every, self.last, self.fields = every, 0.0, {}

    def __call__(self, force=False, **fields):
        self.fields.update(fields)
        if force or time.monotonic() - self.last >= self.every:
            self.last = time.monotonic()
            write_state(**self.fields)
