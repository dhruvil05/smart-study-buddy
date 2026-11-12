"""Job files: loading, saving, and the values derived from a job's number."""

import json
from datetime import datetime, timezone

from . import lock, paths
from .files import atomic_write

RUNNING = ("intake", "spec", "implement", "gate", "review", "approve", "merging")
WAITING = ("needs-info", "needs-human")
TERMINAL = ("merged", "rejected")
STAGES = RUNNING + WAITING + TERMINAL
ROLE_OFFSETS = {"implementer": 0, "ux": 1, "ui": 2, "access": 3}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NoJob(Exception):
    pass


def load(job_id, locked=True):
    path = paths.job_dir(job_id) / "job.json"
    if not path.exists():
        raise NoJob(f"no job {job_id}")
    if locked:
        lock.hold(paths.job_dir(job_id) / ".lock")
    return json.loads(path.read_text(encoding="utf-8"))


def save(job):
    atomic_write(paths.job_dir(job["id"]) / "job.json", json.dumps(job, indent=2) + "\n")


def all_jobs(locked=False):
    if not paths.JOBS.exists():
        return []
    return [load(p.name, locked) for p in sorted(paths.JOBS.iterdir())
            if p.is_dir() and (p / "job.json").exists()]


def resolve(arg, locked=True):
    if str(arg).isdigit():
        matches = [j for j in all_jobs() if j["issue"] == int(arg)]
        if not matches:
            raise NoJob(f"no job for issue #{arg}")
        return load(matches[-1]["id"], locked)
    return load(arg, locked)


def number(job):
    return int(job["id"].split("-", 1)[0])


def port_for(cfg, job, role="implementer"):
    role = role.removeprefix("reviewer-")
    if role not in ROLE_OFFSETS:
        raise ValueError(f"role must be one of {', '.join(ROLE_OFFSETS)}")
    return cfg["ports"]["base"] + cfg["ports"]["step"] * number(job) + ROLE_OFFSETS[role]


def db_name(cfg, job):
    pattern = cfg["env"]["db_name"]
    if not pattern:
        return None
    return pattern.replace("{project}", paths.project_name()).replace("{n}", str(number(job)))


def values(cfg, job, role="implementer", worktree=None):
    """Placeholder values for commands, and the matching environment variables."""
    v = {
        "JOB": job["id"],
        "N": str(number(job)),
        "PORT": str(port_for(cfg, job, role)),
        "DB": job.get("db") or db_name(cfg, job) or "",
        "WORKTREE": paths.posix(worktree or job["worktree"]),
    }
    env = {f"FACTORY_{k}": val for k, val in v.items()}
    return v, env


def expand(command, v):
    for k, val in v.items():
        command = command.replace("{" + k + "}", val)
    return command


def header_line(cfg, job):
    folder = paths.posix(paths.job_dir(job["id"]))
    return (f"Job: {job['id']}. Job folder: {folder}/. Main checkout: {paths.posix(paths.REPO)}. "
            f"Worktree: {paths.posix(job['worktree'])}. Branch: {job['branch']}. "
            f"Base: origin/{cfg['base_branch']}. Issue: #{job['issue']}. "
            f"Port: {port_for(cfg, job)}. DB: {job.get('db') or 'none'}.")
