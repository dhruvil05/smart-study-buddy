"""A job's own environment: .env from the template, its database, migrations, seed.

Every step is optional and set per project in the dashboard (config `env`).
"""

from . import localenv, paths
from .jobs import expand, values
from .proc import Refused, run_command

STEP_TIMEOUT = 900


def _run(cfg, job, key, worktree, v, env, check=True):
    command = cfg["env"][key]
    if not command:
        return None
    code, out, _, timed_out = run_command(expand(command, v), worktree, cfg["shell"], STEP_TIMEOUT, env)
    if code != 0 and check:
        tail = "\n".join(out.rstrip().splitlines()[-30:])
        raise Refused(f"env {key} failed for {job['id']} (exit {code}{', timed out' if timed_out else ''}):\n{tail}")
    return code


def write_env(cfg, job, worktree):
    """Copy the template to <worktree>/.env with this job's values. Refuses foreign hosts."""
    template = cfg["env"]["template"]
    if not template or not (paths.REPO / template).exists():
        return False
    v, _ = values(cfg, job, worktree=worktree)
    text = expand((paths.REPO / template).read_text(encoding="utf-8"), v)
    foreign = localenv.foreign_hosts(localenv.parse_env(text), cfg["env"]["local_hosts"])
    if foreign:
        raise Refused(f"{template} points at hosts that aren't on this machine: "
                      + ", ".join(f"{k} → {h}" for k, h in foreign)
                      + ". Use local services only; the factory never touches real data.")
    (worktree / ".env").write_text(text, encoding="utf-8")
    return True


def setup(cfg, job, worktree=None):
    worktree = worktree or paths.REPO / ".worktrees" / job["id"]
    v, env = values(cfg, job, worktree=worktree)
    done = []
    if write_env(cfg, job, worktree):
        done.append(".env")
    for key in ("db_create", "migrate", "seed"):
        if _run(cfg, job, key, worktree, v, env) is not None:
            done.append(key)
    return done


def teardown(cfg, job, worktree=None):
    worktree = worktree or paths.REPO / ".worktrees" / job["id"]
    if not cfg["env"]["db_drop"]:
        return None
    cwd = worktree if worktree.exists() else paths.REPO
    v, env = values(cfg, job, worktree=cwd)
    return _run(cfg, job, "db_drop", cwd, v, env, check=False)
