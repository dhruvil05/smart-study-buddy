"""A job's own environment. job.py sets it up and tears it down itself; agents use
`serve` and `stop` for dev servers, and anyone can run the rest to repair a job.

Usage: python factory/bin/env.py …
  serve <job> <role>   start env.dev_server in the job's worktree on the role's port
                       (implementer, ux, ui, access) and wait until it answers; prints the URL
  stop <job> <role>    stop the dev server on that role's port
  up <job>             write <worktree>/.env from the template, create the job DB, migrate, seed
  down <job>           drop the job DB
  check <job>          refuse if the job's .env names a host that isn't on this machine
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylib import config, environment, jobs, localenv, paths  # noqa: E402
from factorylib.proc import Refused, shell_argv  # noqa: E402

SERVE_WAIT = 90


def die(msg):
    print(f"env: {msg}", file=sys.stderr)
    sys.exit(1)


def answering(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def serve(cfg, job, role):
    command = cfg["env"]["dev_server"]
    if not command:
        raise Refused("no dev server command is set (Settings → Job environment → dev_server)")
    port = jobs.port_for(cfg, job, role)
    if answering(port):
        print(f"http://127.0.0.1:{port} (already running)")
        return
    worktree = Path(job["worktree"])
    bad = localenv.check_file(worktree / ".env", cfg["env"]["local_hosts"])
    if bad:
        raise Refused(".env points off this machine: " + ", ".join(f"{k} → {h}" for k, h in bad))
    v, env = jobs.values(cfg, job, role=role, worktree=worktree)
    log = paths.job_dir(job["id"]) / f"server-{role.removeprefix('reviewer-')}.log"
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} \
        if os.name == "nt" else {"start_new_session": True}
    full_env = dict(os.environ, **env)
    with open(log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(shell_argv(jobs.expand(command, v), cfg["shell"]), cwd=str(worktree),
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                env=full_env, **kwargs)
    deadline = time.monotonic() + SERVE_WAIT
    while time.monotonic() < deadline:
        if answering(port):
            print(f"http://127.0.0.1:{port} (pid {proc.pid}, log {paths.posix(log)})")
            return
        if proc.poll() is not None:
            tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
            raise Refused(f"the dev server exited with {proc.returncode}:\n{tail}")
        time.sleep(0.5)
    raise Refused(f"the dev server didn't answer on port {port} within {SERVE_WAIT}s; see {paths.posix(log)}")


def main(argv):
    if not argv or argv[0] not in ("serve", "stop", "up", "down", "check") \
            or len(argv) != (3 if argv[0] in ("serve", "stop") else 2):
        die(__doc__.strip())
    try:
        cfg = config.load()
        job = jobs.resolve(argv[1], locked=False)
        worktree = Path(job["worktree"])
        if argv[0] == "serve":
            serve(cfg, job, argv[2])
        elif argv[0] == "stop":
            port = jobs.port_for(cfg, job, argv[2])
            res = subprocess.run([sys.executable, str(paths.BIN / "port.py"), "kill", str(port)],
                                 capture_output=True, text=True)
            print(res.stdout.strip() or res.stderr.strip())
        elif argv[0] == "up":
            done = environment.setup(cfg, job, worktree)
            print(f"{job['id']}: env up ({', '.join(done) or 'nothing to do'})")
        elif argv[0] == "down":
            code = environment.teardown(cfg, job, worktree)
            print(f"{job['id']}: env down ({'no db_drop set' if code is None else f'db_drop exit {code}'})")
        else:
            bad = localenv.check_file(worktree / ".env", cfg["env"]["local_hosts"])
            if bad:
                die(f"{job['id']}: .env points off this machine: " + ", ".join(f"{k} → {h}" for k, h in bad))
            print(f"{job['id']}: env is local only")
    except (Refused, jobs.NoJob, config.ConfigError, ValueError) as e:
        die(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
