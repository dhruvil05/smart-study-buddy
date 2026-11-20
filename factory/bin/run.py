"""Run the factory with any agent CLI: Claude Code, Codex, DeepSeek, or your own.

Usage: python factory/bin/run.py …
  (nothing)        work through every open `factory` issue, `concurrency` at a time,
                   until nothing can move without a person
  --watch          keep running: pick up feedback, answers, resumes, retries and
                   new issues as they come, until stopped (dashboard, `stop`, Ctrl+C)
  <issue> [--watch]  run one issue (with --watch: until it is merged or rejected)
  stop [--now]     ask the running runner to stop: after the current steps, or now
  status           is a runner working on this project, and on what
  doctor           check the settings and that each agent tool is installed

Which tool runs each role is set in config `runner` (dashboard: Settings →
Agent tools). This script is the orchestrator: it starts each agent as a
separate command, checks what the agent left behind, and moves the job on
with job.py, gate.py and route.py, exactly as the /factory skill does inside
Claude Code. Every agent call is recorded in <job folder>/runs/.
"""

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylib import config, harness, jobs, paths  # noqa: E402
from factorylib import runner as runstate  # noqa: E402
from factorylib.files import atomic_write, first_line, header, summary_bullets  # noqa: E402
from factorylib.proc import Refused, gh, gh_json, git  # noqa: E402

PY = sys.executable
REVIEW_PORT_ROLES = {"ux", "ui", "access"}
print_lock = threading.Lock()


def say(msg):
    with print_lock:
        print(msg, flush=True)


class Stop(Exception):
    """This job stops for this run; the reason is shown and saved in runs/stopped.md."""

    def __init__(self, msg, log=None):
        super().__init__(msg)
        self.log = log


class Paused(Exception):
    """A person paused this job or stopped the runner: not a failure, nothing saved."""


def paused_now(job_id):
    """True when this job's running agent must end now: the job was paused with
    "stop now", or the whole runner was asked to stop now."""
    if runstate.stop_request() == "now":
        return True
    try:
        return bool((load(job_id).get("paused") or {}).get("now"))
    except (OSError, ValueError, jobs.NoJob):
        return False


def script(name, *args, stdin=None):
    res = subprocess.run([PY, str(paths.BIN / f"{name}.py"), *[str(a) for a in args]], cwd=str(paths.REPO),
                         input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return res.returncode, res.stdout.strip(), res.stderr.strip().split(": ", 1)[-1]


def load(job_id):
    return jobs.load(job_id, locked=False)


def jdir(job_id):
    return paths.job_dir(job_id)


# ---- Guards -----------------------------------------------------------------

IGNORED_MAIN = ("factory/jobs/", ".worktrees/", "factory/config-history/", "factory/archive/", "factory/PAUSE")


def main_snapshot():
    out = git(paths.REPO, "status", "--porcelain", "--untracked-files=all", check=False).stdout
    return {line for line in out.splitlines() if line[3:].strip('"') and not line[3:].strip('"').startswith(IGNORED_MAIN)}


def worktree_state(job):
    wt = job.get("worktree")
    if not wt or not Path(wt).exists():
        return None
    head = git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
    dirty = [l for l in git(wt, "status", "--porcelain", check=False).stdout.splitlines() if l != "?? .env"]
    return head, dirty


def warn(job_id, msg):
    say(f"{job_id}: WARNING {msg}")
    f = jdir(job_id) / "runs" / "warnings.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as out:
        out.write(f"- {jobs.now()} {msg}\n")


def guard(job, role, main_before, wt_before):
    """After an agent: the main checkout untouched; read-only roles left the code alone."""
    changed = main_snapshot() - main_before
    if changed:
        raise Stop(f"{role} changed the main checkout ({', '.join(sorted(c[3:] for c in changed)[:5])}); "
                   "look at it before running again")
    if role == "implementer" or wt_before is None:
        return
    after = worktree_state(job)
    if after and (after[0] != wt_before[0] or after[1]):
        wt = job["worktree"]
        git(wt, "reset", "--hard", wt_before[0], check=False)
        git(wt, "clean", "-fd", "-e", ".env", check=False)
        warn(job["id"], f"{role} changed the code although its role only reads; the change was reverted")


# ---- Agents -----------------------------------------------------------------


def agent(job_id, role, task, tier, port_role=None):
    cfg = config.load()
    job = load(job_id)
    port = jobs.port_for(cfg, job, port_role) if port_role and job.get("worktree") else None
    main_before, wt_before = main_snapshot(), worktree_state(job)
    name, _ = harness.harness_for(cfg, role)
    say(f"{job_id}: {role} → {name}" + (f" ({tier})" if tier else ""))
    rec = harness.run_agent(cfg, job, role, task, tier, port=port, role_for_port=port_role,
                            should_stop=lambda: paused_now(job_id))
    guard(load(job_id), role, main_before, wt_before)
    if rec.get("stopped"):
        raise Paused(f"{role} stopped" + (" with the runner" if runstate.stop_request() == "now" else ""))
    if rec["timed_out"]:
        raise Stop(f"{role} timed out after {cfg['runner']['agent_timeout_minutes']} minutes")
    if rec["error"]:  # the tool itself failed (sign-in, quota, bad flag): asking again won't help
        raise Stop(f"{role} ({name}) failed: {harness.failure_summary(rec['error'], rec['exit'])}", log=rec.get("log"))
    if rec["exit"] != 0 and not rec["reply"]:
        raise Stop(f"{role} ({name}) exited {rec['exit']}", log=rec.get("log"))
    return rec


def tier(role, job, reviewer=None):
    if reviewer:
        return job["agents"]["reviewers"][reviewer]
    return config.load()["models"].get(role)


def with_retry(job_id, role, task, check, tier_name, port_role=None, what=""):
    """Run an agent; if `check()` is falsy afterwards, ask once more, then stop."""
    agent(job_id, role, task, tier_name, port_role)
    if check():
        return
    agent(job_id, role, f"{task}\nYour last run didn't leave {what}. Write it now, exactly as your instructions say.",
          tier_name, port_role)
    if not check():
        raise Stop(f"{role} didn't write {what} after two tries")


# ---- Moves ------------------------------------------------------------------


def advance(job_id, stage):
    code, out, err = script("job", "advance", job_id, stage)
    if code == 0:
        say(out)
        return True, ""
    if "cost cap" in err:
        script("job", "advance", job_id, "needs-human")
        raise Stop(err)
    return False, err


def must_advance(job_id, stage):
    ok, err = advance(job_id, stage)
    if not ok:
        raise Stop(f"job.py refused {stage}: {err}")


def advance_or_human(job_id, stage):
    """implement after a failed gate or review, or needs-human on the last round."""
    ok, err = advance(job_id, stage)
    if not ok:
        if "go to needs-human" in err:
            must_advance(job_id, "needs-human")
        else:
            raise Stop(f"job.py refused {stage}: {err}")


def ensure_pushed_and_pr(job_id):
    """Push the branch and open the draft PR if the agent couldn't (no network in its sandbox)."""
    cfg = config.load()
    job = load(job_id)
    wt, branch, base = job["worktree"], job["branch"], cfg["base_branch"]
    ahead = git(wt, "rev-list", "--count", f"origin/{base}..HEAD", check=False).stdout.strip()
    if not ahead.isdigit() or int(ahead) == 0:
        return
    git(wt, "fetch", "origin", branch, check=False)
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    remote = git(wt, "rev-parse", f"origin/{branch}", check=False).stdout.strip()
    if head != remote:
        res = git(wt, "push", "-u", "origin", branch, check=False)
        if res.returncode != 0:
            raise Stop(f"couldn't push {branch}: {(res.stderr or res.stdout).strip()}")
        say(f"{job_id}: pushed {branch}")
    if job.get("pr"):
        return
    if gh_json(paths.REPO, "pr", "list", "--head", branch, "--state", "open", "--json", "number"):
        return
    title = (first_line(jdir(job_id) / "spec.md") or job["title"]).lstrip("# ").strip() or job["title"]
    summary = "\n".join(f"- {b}" for b in summary_bullets(jdir(job_id) / "impl.md"))
    gh(paths.REPO, "pr", "create", "--draft", "--base", base, "--head", branch, "--title", title,
       "--body", f"Closes #{job['issue']}\n\n{summary}".strip())
    say(f"{job_id}: opened draft PR")


def implement_task(job):
    n, d = job["round"], jdir(job["id"])
    task = f"Implement round {n}."
    if n > 1:
        prev = d / f"round-{n - 1}"
        feedback = sorted(d.glob("feedback-*.md"), key=lambda p: int(p.stem.split("-")[1]))
        if feedback and (d / "decision.md").exists() and feedback[-1].stat().st_mtime >= (d / "decision.md").stat().st_mtime:
            task += f" The user gave feedback: read {feedback[-1].name} and address it."
        elif header(prev / "checks.md").get("GATE") == "FAIL" if (prev / "checks.md").exists() else False:
            task += f" The gate failed: read round-{n - 1}/checks.md and checks.log and fix it."
        else:
            task += f" Round {n - 1} reviews are in round-{n - 1}/. Address every VERDICT: CHANGES."
    if (d / "impl.md").exists():
        task += " If impl.md already covers this round, you are resuming: check git log and git status first."
    return task


def step_intake(job):
    jid = job["id"]
    with_retry(jid, "intake", "Decide on this issue.",
               lambda: header(jdir(jid) / "intake.md").get("DECISION") in ("ACCEPT", "NEEDS-INFO", "REJECT")
               if (jdir(jid) / "intake.md").exists() else False,
               tier("intake", job), what="intake.md with a DECISION line")
    decision = header(jdir(jid) / "intake.md")["DECISION"]
    if decision == "ACCEPT":
        code, out, err = script("route", jid)
        if code != 0:
            raise Stop(f"route.py: {err}")
        say(out)
        must_advance(jid, "spec")
    else:
        must_advance(jid, "needs-info" if decision == "NEEDS-INFO" else "rejected")


def step_spec(job):
    jid = job["id"]
    with_retry(jid, "spec-writer", "Write the spec.", lambda: (jdir(jid) / "spec.md").exists(),
               tier("spec-writer", job), what="spec.md")
    must_advance(jid, "implement")


def step_implement(job):
    jid = job["id"]
    d = jdir(jid)
    is_status = lambda: first_line(d / "impl.md") in ("STATUS: READY", "STATUS: BLOCKED")
    with_retry(jid, "implementer", implement_task(job), is_status, tier("implementer", job),
               "implementer", what="impl.md starting with STATUS: READY or STATUS: BLOCKED")
    if first_line(d / "impl.md") == "STATUS: BLOCKED":
        must_advance(jid, "needs-human")
        return
    # The gate's dry run, once: what the Stop hook does inside Claude Code.
    code, out, err = script("gate", jid, "--dry")
    if code == 2:
        raise Stop(f"the gate can't run: {err}")
    if code == 1:
        say(f"{jid}: dry gate failed, asking the implementer once more")
        agent(jid, "implementer", f"Implement round {job['round']}. The gate's dry run fails; fix it and "
              f"commit, or write STATUS: BLOCKED in impl.md:\n\n{out}", tier("implementer", job), "implementer")
        if first_line(d / "impl.md") == "STATUS: BLOCKED":
            must_advance(jid, "needs-human")
            return
    ensure_pushed_and_pr(jid)
    ok, err = advance(jid, "gate")
    if not ok:
        agent(jid, "implementer", f"Implement round {job['round']}. job.py refused to move on: {err}. Fix that "
              "(commit everything, keep impl.md's first line), then reply.", tier("implementer", job), "implementer")
        ensure_pushed_and_pr(jid)
        must_advance(jid, "gate")


def step_gate(job):
    jid = job["id"]
    code, out, err = script("gate", jid)
    if code == 2:
        raise Stop(f"the gate can't run: {err}")
    say(out)
    if code == 0:
        must_advance(jid, "review")
    else:
        advance_or_human(jid, "implement")


def step_review(job):
    jid, n = job["id"], job["round"]
    rd = jdir(jid) / f"round-{n}"
    valid = lambda r: re.fullmatch(r"VERDICT:\s*(PASS|CHANGES)", first_line(rd / f"review-{r}.md") or "") is not None
    todo = [r for r in job["agents"]["reviewers"] if not valid(r)]
    errors, paused = [], []

    def task_for(r):
        """From round 2: point the reviewer at what it already reviewed, so it checks only the changes."""
        task = f"Review round {n}."
        if n > 1:
            code, out, _ = script("job", "reviewed", jid, r)
            parts = out.split()
            if code == 0 and len(parts) == 2 and parts[0].startswith("round-"):
                task += f" Previous review: round {parts[0][6:]} at {parts[1]}."
        return task

    def one(r):
        try:
            with_retry(jid, f"reviewer-{r}", task_for(r), lambda: valid(r), tier(None, job, r),
                       r if r in REVIEW_PORT_ROLES else None, what=f"round-{n}/review-{r}.md with a VERDICT line")
            script("job", "sync", jid)
        except Paused as e:
            paused.append(e)
        except Stop as e:
            errors.append(str(e))

    threads = [threading.Thread(target=one, args=(r,)) for r in todo]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if paused:
        raise paused[0]
    if errors:
        raise Stop("; ".join(errors))
    script("job", "sync", jid)
    verdicts = {r: first_line(rd / f"review-{r}.md") for r in job["agents"]["reviewers"]}
    if all(v == "VERDICT: PASS" for v in verdicts.values()):
        must_advance(jid, "approve")
    else:
        advance_or_human(jid, "implement")


def step_approve(job):
    jid = job["id"]
    with_retry(jid, "approver", f"PR: #{job['pr']}. Final round: {job['round']}.",
               lambda: first_line(jdir(jid) / "decision.md") in ("APPROVE", "ESCALATE"),
               tier("approver", job), what="decision.md starting with APPROVE or ESCALATE")
    if first_line(jdir(jid) / "decision.md") == "APPROVE":
        step_merging(job)
    else:
        must_advance(jid, "needs-human")


def step_merging(job):
    code, out, err = script("job", "merge", job["id"])
    if code != 0:
        raise Stop(f"job.py merge: {err}")
    say(out)


STEPS = {"intake": step_intake, "spec": step_spec, "implement": step_implement, "gate": step_gate,
         "review": step_review, "approve": step_approve, "merging": step_merging}


def save_stop(job_id, reason, log=None):
    atomic_write(jdir(job_id) / "runs" / "stopped.md",
                 f"STOPPED\nWHEN: {jobs.now()}\n" + (f"LOG: {log}\n" if log else "") + f"\n{reason}\n")


def drive(job_id, stopped):
    (jdir(job_id) / "runs" / "stopped.md").unlink(missing_ok=True)  # a new attempt; an old stop no longer applies
    try:
        for _ in range(60):
            job = load(job_id)
            if job["stage"] not in STEPS:
                return
            if job.get("paused"):
                say(f"{job_id}: paused (resume it in the dashboard or with job.py unpause {job_id})")
                return
            if runstate.stop_request():
                return
            before = (job["stage"], job["round"])
            STEPS[job["stage"]](job)
            after = load(job_id)
            if (after["stage"], after["round"]) == before:
                raise Stop(f"no progress in {before[0]}")
        raise Stop("too many steps in one run")
    except Paused as e:
        say(f"{job_id}: {e}; paused")
    except (Stop, Refused, jobs.NoJob, config.ConfigError) as e:
        stopped[job_id] = str(e)
        say(f"{job_id}: STOPPED: {e}")
        save_stop(job_id, e, getattr(e, "log", None))
    except Exception as e:  # never let one job take the run down
        stopped[job_id] = f"{type(e).__name__}: {e}"
        say(f"{job_id}: STOPPED by an error: {type(e).__name__}: {e}")
        save_stop(job_id, f"{type(e).__name__}: {e}")


# ---- Commands ---------------------------------------------------------------


def queue_rows():
    code, out, err = script("job", "queue")
    if code != 0:
        raise Stop(f"job.py queue: {err}")
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def start(row, stopped):
    action, issue, job_id, stage = row[:4]
    if action == "new":
        code, out, err = script("job", "create", issue)
        if code != 0:
            say(f"#{issue}: not started: {err}")
            stopped[f"#{issue}"] = err
            return None
        job_id = out
        say(f"{job_id} #{issue}: created")
    elif stage == "needs-info":
        try:
            must_advance(job_id, "intake")
        except Stop as e:
            save_stop(job_id, e)
            raise
    return job_id


def issue_finished(issue):
    """True when issue n's job is merged or rejected (a watch on one issue ends there)."""
    return any(j["issue"] == issue and j["stage"] in jobs.TERMINAL for j in jobs.all_jobs())


def report(touched, stopped, only_issue):
    if touched:
        _, out, _ = script("job", "summary", *sorted(touched))
        print(out)
    try:
        rows = queue_rows() if touched or only_issue is None else []
    except Stop:
        rows = []
    waits = [r for r in rows if r and (r[0] == "wait" or (r[0] == "hold" and "paused" in r[-1]))]
    if waits:
        print("\nWaiting on people:")
        for r in waits:
            print(f"  #{r[1]} {r[2]}: {r[4] if len(r) > 4 else r[3]}")
    if stopped:
        print("\nStopped (fix, then run again or press Retry on the job):")
        for k, why in stopped.items():
            print(f"  {k}: {why}")


def cmd_run(only_issue=None, watch=False):
    """Without --watch: until nothing can move without a person. With --watch: until stopped
    (or, for one issue, until it is merged or rejected). Either way one job stopping or
    waiting never holds up the others."""
    if paths.PAUSE.exists() and not watch:
        print("run: the factory is paused (factory/PAUSE); unpause it in the dashboard or with job.py unpause")
        return 1
    try:
        runstate.claim()
    except TimeoutError:
        print("run: another runner is already working on this project (see `run.py status` or the dashboard)")
        return 1
    beat = runstate.Beat()
    beat(force=True, started=jobs.now(), watch=watch, issue=only_issue, phase="checking main", active=[], stopped={})
    code, out, err = script("job", "preflight")
    print(out or err, flush=True)
    if code != 0:
        print("run: main doesn't pass its checks, so nothing starts")
        beat(force=True, phase="ended", exit=1)
        return 1
    cfg = config.load()
    poll = cfg["runner"].get("poll_seconds", 60)
    running, stopped, touched, stopped_at = {}, {}, set(), {}
    seen, asked, first = None, 0.0, True
    if watch:
        say("run: watching; feedback, answers, resumes and new issues are picked up as they come. "
            "Stop it from the dashboard, with `run.py stop`, or Ctrl+C.")
    try:
        while True:
            finished = [j for j, t in running.items() if not t.is_alive()]
            for jid in finished:
                del running[jid]
                say(f"{jid}: {load(jid)['stage']} (done for now)")
            request = runstate.stop_request()
            if request:
                if not running:
                    say(f"run: stopped ({request})")
                    break
            else:
                now = time.monotonic()
                for k in list(stopped):
                    stopped_at.setdefault(k, now)
                    if watch and ((k.startswith("#") and now - stopped_at[k] >= poll)
                                  or (not k.startswith("#") and not (jdir(k) / "runs" / "stopped.md").exists())):
                        del stopped[k], stopped_at[k]  # retried from the dashboard (or a new issue try)
                sig = runstate.signature()
                due = first or bool(finished) or (watch and (sig != seen or now - asked >= poll))
                if due:
                    first, seen, asked = False, sig, now
                    try:
                        rows = queue_rows()
                    except Stop as e:
                        if not watch:
                            print(f"run: {e}")
                            return 1
                        say(f"run: {e}; trying again in {poll} s")
                        rows = []
                    cfg = config.load()
                    for row in rows:
                        if len(row) < 4 or row[0] not in ("new", "resume"):
                            continue
                        if only_issue is not None and row[1] != str(only_issue):
                            continue
                        key = row[2] if row[2] != "-" else f"#{row[1]}"
                        if key in running or key in stopped:
                            continue
                        if len(running) >= cfg["concurrency"]:
                            break
                        try:
                            job_id = start(row, stopped)
                        except Stop as e:
                            stopped[key] = str(e)
                            continue
                        if not job_id:
                            continue
                        touched.add(job_id)
                        t = threading.Thread(target=drive, args=(job_id, stopped), daemon=True)
                        running[job_id] = t
                        t.start()
                    seen = runstate.signature()  # our own moves don't count as a person acting
                if not running and (not watch or (only_issue is not None and issue_finished(only_issue))):
                    break
            beat(phase="stopping" if request else ("watching" if watch else "running"),
                 active=sorted(running), stopped=dict(stopped))
            time.sleep(0.5)
    except KeyboardInterrupt:
        say("run: interrupted; stopping the running agents")
        runstate.request_stop(now=True)
        for t in running.values():
            t.join(timeout=30)
    finally:
        beat(force=True, phase="ended", active=[], exit=1 if stopped else 0)
    report(touched, stopped, only_issue)
    return 1 if stopped else 0


def cmd_doctor():
    problems = 0
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"config.json: INVALID: {e}")
        return 1
    runner = cfg["runner"]
    print(f"default agent tool: {runner['harness']}")
    for role, name in sorted(runner["role_harness"].items()):
        print(f"  {role} → {name}")
    for name in sorted(config.harnesses_in_use(runner)):
        h = runner["harnesses"][name]
        ok = harness.available(h["cmd"])
        problems += not ok
        print(f"{name}: {'found' if ok else 'NOT FOUND'} `{harness.binary(h['cmd'])}`"
              + ("" if h.get("verified") else " (preset not verified: compare the command with its --help)"))
        missing = [t for t in config.MODELS if t not in h["models"]]
        if missing:
            print(f"  no model set for {', '.join(missing)}: the tool's default model is used for those roles")
    for exe in ("git", "gh"):
        ok = harness.available(exe)
        problems += not ok
        print(f"{exe}: {'found' if ok else 'NOT FOUND'}")
    for role in config.ROLES:
        if not (harness.AGENTS / f"{role}.md").exists():
            problems += 1
            print(f"missing agent instructions: .claude/agents/{role}.md")
    print("ok" if not problems else f"{problems} problem(s)")
    return 1 if problems else 0


def cmd_stop(now):
    if not runstate.running():
        print("run: no runner is working on this project")
        return 0
    runstate.request_stop(now)
    print("run: asked the runner to stop " + ("now (running agents are ended; their steps run again on resume)"
                                              if now else "after the steps it is on"))
    return 0


def cmd_status():
    s = runstate.status()
    if not s["running"]:
        last = s.get("last") or {}
        print("runner: off" + (f" (last ran {last.get('started')} to {last.get('beat')})" if last.get("beat") else ""))
        return 0
    print(f"runner: on since {s.get('started')}, {'watching' if s.get('watch') else 'one pass'}"
          + (f", issue #{s['issue']}" if s.get("issue") else "") + f", pid {s.get('pid')}"
          + (f", stopping ({s['stopping']})" if s.get("stopping") else ""))
    print(f"  {s.get('phase') or '?'}; active: {', '.join(s.get('active') or []) or 'none'}")
    for k, why in (s.get("stopped") or {}).items():
        print(f"  stopped {k}: {why}")
    return 0


def main(argv):
    argv = list(argv or [])
    watch = "--watch" in argv
    rest = [a for a in argv if a != "--watch"]
    if not rest:
        sys.exit(cmd_run(watch=watch))
    if rest == ["doctor"] and not watch:
        sys.exit(cmd_doctor())
    if rest in (["stop"], ["stop", "--now"]) and not watch:
        sys.exit(cmd_stop(rest == ["stop", "--now"]))
    if rest == ["status"] and not watch:
        sys.exit(cmd_status())
    if len(rest) == 1 and rest[0].isdigit():
        sys.exit(cmd_run(int(rest[0]), watch=watch))
    print(__doc__.strip())
    sys.exit(0 if argv and argv[0] in ("-h", "--help") else 2)


if __name__ == "__main__":
    main(sys.argv[1:])
