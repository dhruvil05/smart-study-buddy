"""Where things live. FACTORY_REPO overrides the repo root (tests use it)."""

import os
import re
from pathlib import Path

REPO = Path(os.environ.get("FACTORY_REPO") or Path(__file__).resolve().parents[2]).resolve()
FACTORY = REPO / "factory"
BIN = FACTORY / "bin"
JOBS = FACTORY / "jobs"
WORKTREES = REPO / ".worktrees"
CONFIG_FILE = FACTORY / "config.json"
HISTORY = FACTORY / "config-history"
ARCHIVE = FACTORY / "archive"
PAUSE = FACTORY / "PAUSE"
PREFLIGHT = JOBS / "_preflight"


def project_name():
    return re.sub(r"[^a-z0-9]+", "_", REPO.name.lower()).strip("_") or "project"


def posix(path):
    return Path(path).as_posix()


def job_dir(job_id):
    return JOBS / job_id
