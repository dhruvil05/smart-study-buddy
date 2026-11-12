"""The deterministic gate: does a job's branch pass the project's checks?

Usage: python factory/bin/gate.py …
  <job>            write round-N/checks.md (+ checks.log); exit 0 on PASS, 1 on FAIL
  <job> --merge    same, as round-N/merge-checks.md (job.py merge runs it)
  <job> --dry      run everything, print the result, write nothing (the implementer's self-check)
  --hook           implementer Stop hook: exit 2 with the failure when the checks fail

The gate always uses the main checkout's config.json, so a branch can't change
the checks it is judged by. Exit 2 means the gate couldn't run at all (for
example: no check steps are set); fix the cause, don't treat it as a FAIL.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylib import config, gating, jobs, paths  # noqa: E402
from factorylib.proc import Refused  # noqa: E402


def die(msg):
    print(f"gate: {msg}", file=sys.stderr)
    sys.exit(2)


def run(job_id, mode):
    cfg = config.load()
    job = jobs.load(job_id, locked=False)
    if job["round"] < 1:
        die(f"{job_id} hasn't started an implement round")
    worktree = Path(job["worktree"])
    if not worktree.exists():
        die(f"{job_id}'s worktree is missing: {job['worktree']}")
    v, env = jobs.values(cfg, job, worktree=worktree)
    risk = (job.get("intake") or {}).get("risk")
    result = gating.run_gate(cfg, worktree, f"origin/{cfg['base_branch']}", v, env, risk=risk)
    if mode != "dry":
        round_dir = paths.job_dir(job_id) / f"round-{job['round']}"
        round_dir.mkdir(parents=True, exist_ok=True)
        name = "merge-checks" if mode == "merge" else "checks"
        (round_dir / f"{name}.log").write_text(result["log"], encoding="utf-8")
        note = f"synced with origin/{cfg['base_branch']} before merging" if mode == "merge" else ""
        (round_dir / f"{name}.md").write_text(gating.render(result, note), encoding="utf-8")
    print(gating.one_line(result) + f" [{job_id} round {job['round']}]")
    if mode == "dry" and result["verdict"] != "PASS":
        print(gating.render(result))
    return result["verdict"] == "PASS"


def hook():
    """Stop hook for the implementer: a nudge, the gate still decides."""
    event = json.load(sys.stdin)
    if event.get("stop_hook_active"):
        return 0
    transcript = event.get("agent_transcript_path") or event.get("transcript_path")
    if not transcript:
        return 0
    with open(Path(transcript).expanduser(), encoding="utf-8", errors="replace") as f:
        content = json.loads(f.readline()).get("message", {}).get("content", "")
    prompt = content if isinstance(content, str) else " ".join(
        b.get("text", "") for b in content if isinstance(b, dict))
    if "Job: " not in prompt:
        return 0
    job_id = prompt.split("Job: ", 1)[1].split(".", 1)[0].strip()
    try:
        cfg = config.load()
        job = jobs.load(job_id, locked=False)
        if job["round"] < 1 or not Path(job["worktree"]).exists():
            return 0
        v, env = jobs.values(cfg, job)
        result = gating.run_gate(cfg, job["worktree"], f"origin/{cfg['base_branch']}", v, env,
                                 risk=(job.get("intake") or {}).get("risk"))
    except (Refused, jobs.NoJob, config.ConfigError) as e:
        print(f"The gate can't run: {e}", file=sys.stderr)
        return 2
    if result["verdict"] == "PASS":
        return 0
    print("The gate will reject this branch. Fix it, or write STATUS: BLOCKED in impl.md if you can't.\n\n"
          + gating.render(result), file=sys.stderr)
    return 2


def main(argv):
    try:
        if argv == ["--hook"]:
            sys.exit(hook())
        if len(argv) == 1 and not argv[0].startswith("-"):
            sys.exit(0 if run(argv[0], "checks") else 1)
        if len(argv) == 2 and argv[1] in ("--merge", "--dry"):
            sys.exit(0 if run(argv[0], argv[1][2:]) else 1)
        die(__doc__.strip())
    except (Refused, jobs.NoJob, config.ConfigError) as e:
        die(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
