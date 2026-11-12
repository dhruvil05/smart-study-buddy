"""Turn intake's risk and areas into the job's reviewers and its auto-merge rule.

Usage: python factory/bin/route.py <job>

Reads risk and surfaces from intake.md, looks them up in config.json
(`routing`, `surface_reviewers`, `always_human_surfaces`) and records the result
with `job.py agents`. Prints the reviewer set.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylib import config, jobs, paths  # noqa: E402

TIER_UP = {"haiku": "sonnet", "sonnet": "opus", "opus": "opus", "fable": "fable"}


def die(msg):
    print(f"route: {msg}", file=sys.stderr)
    sys.exit(1)


def plan(cfg, intake):
    """(reviewers, auto_merge, reason, always_human) for an intake decision."""
    risk = intake.get("risk")
    if risk not in cfg["routing"]:
        raise ValueError(f"risk {risk!r} isn't low, medium or high")
    reviewers = {"code": cfg["routing"][risk]["code"]}
    for surface in intake.get("surfaces", []):
        if surface not in cfg["surface_reviewers"]:
            raise ValueError(f"area {surface!r} isn't in config.json surface_reviewers")
        reviewers.update(cfg["surface_reviewers"][surface])
    human_areas = [s for s in intake.get("surfaces", []) if s in cfg["always_human_surfaces"]]
    no_checks = not config.enabled_checks(cfg)
    if no_checks:
        reviewers = {name: TIER_UP[model] for name, model in reviewers.items()}
    if risk == "high":
        reason = "high risk always goes to a human"
    elif human_areas:
        reason = f"{', '.join(human_areas)} changes always go to a human"
    elif no_checks:
        reason = "this project has no automated checks"
    elif not cfg["routing"][risk]["auto_merge"]:
        reason = f"auto-merge is off for {risk} risk"
    else:
        reason = None
    return reviewers, reason is None, reason, bool(human_areas) or risk == "high" or no_checks


def main(argv):
    if len(argv) != 1:
        die("usage: route.py <job>")
    cfg = config.load()
    job_py = str(paths.BIN / "job.py")
    res = subprocess.run([sys.executable, job_py, "sync", argv[0]], capture_output=True, text=True)
    if res.returncode != 0:
        die(res.stderr.strip())
    job = jobs.resolve(argv[0], locked=False)
    if not job.get("intake"):
        die(f"{job['id']} has no intake.md")
    try:
        reviewers, auto_merge, reason, always_human = plan(cfg, job["intake"])
    except ValueError as e:
        die(str(e))
    spec = json.dumps({"reviewers": reviewers, "auto_merge": auto_merge, "reason": reason,
                       "always_human": always_human})
    res = subprocess.run([sys.executable, job_py, "agents", job["id"], spec], capture_output=True, text=True)
    if res.returncode != 0:
        die(res.stderr.strip())
    print(f"{job['id']}: reviewers " + ", ".join(f"{k} {v}" for k, v in reviewers.items())
          + f"; auto-merge {'on' if auto_merge else 'off (' + reason + ')'}")


if __name__ == "__main__":
    main(sys.argv[1:])
