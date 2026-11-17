"""Run one factory agent through any agent CLI (Claude Code, Codex, DeepSeek, …).

The instructions for every role live once, in .claude/agents/<role>.md. For a
run outside Claude Code's subagents, the frontmatter is dropped, a short
tool-neutral preamble is added, and the result is sent to the configured
command (config `runner.harnesses`), on stdin or as a file the command names.
Each call leaves three files in <job folder>/runs/: the prompt, the log, and a
JSON record (tool, model, time, exit code, cost when the tool reports one).
"""

import json
import re
import shutil
import threading
import time

from . import jobs, paths
from .files import atomic_write
from .proc import run_command

AGENTS = paths.REPO / ".claude" / "agents"
_counter_lock = threading.Lock()

PREAMBLE = """\
## How this run works

You are one step of auto-factory, started by its runner (factory/bin/run.py)
as a standalone agent, not as a Claude Code subagent. So:

- Where these instructions name tools (Read, Grep, Glob, Edit, Write, Bash),
  use your environment's equivalent file and shell tools.
- Nothing is carried over from earlier rounds except files. Ignore mentions of
  being resumed with SendMessage or of a Stop hook: read the job folder (and,
  for the implementer, impl.md and `git log`) to see what happened before.
- The runner checks your work after you finish: it runs the gate's dry run
  after the implementer, pushes the branch and opens the draft PR if you
  couldn't, and reverts any code change made by a role that must only read.
- Finish by replying with exactly the one line your instructions ask for.
- Your job's details (the header line, your role and task) are in the
  "## This job" section at the end.
"""


def load_agent(name):
    """(instructions without frontmatter, [tool names]) for .claude/agents/<name>.md."""
    text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
    tools = []
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        m = re.search(r"^tools:\s*(.+)$", front, re.M)
        if m:
            tools = [t.strip() for t in m.group(1).split(",") if t.strip()]
        text = body.lstrip()
    return text, tools


def harness_for(cfg, role):
    runner = cfg["runner"]
    name = runner["role_harness"].get(role) or runner["harness"]
    return name, runner["harnesses"][name]


def build_prompt(cfg, job, role, task, port=None):
    """Unchanging instructions first, the job-specific block last: providers cache a
    prompt's stable prefix, so every run of a role reuses the cached instructions."""
    body, tools = load_agent(role)
    lines = [jobs.header_line(cfg, job), f"Role: {role}.", task]
    if port:
        lines.append(f"Your port: {port}.")
    return PREAMBLE + "\n" + body.rstrip() + "\n\n## This job\n\n" + "\n".join(lines) + "\n", tools


def render(h, model, tools, prompt_file, role):
    flag = h["model_flag"].replace("{MODEL}", model) if model and h["model_flag"] else ""
    return (h["cmd"].replace("{MODEL_FLAG}", flag).replace("{MODEL}", model or "")
            .replace("{TOOLS}", ",".join(tools)).replace("{PROMPT_FILE}", paths.posix(prompt_file))
            .replace("{ROLE}", role))


def binary(cmd):
    """The program a command template starts (first word, quotes removed)."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    return cmd.split(" ", 1)[0]


def available(cmd):
    exe = binary(cmd)
    return bool(exe) and (shutil.which(exe) is not None or (paths.Path(exe).is_file()))


def parse_output(kind, out):
    """(reply line, cost in USD or None, error message or None)."""
    if kind == "claude-json":
        for line in reversed(out.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                result = str(data.get("result") or "")
                reply = next((l.strip() for l in reversed(result.splitlines()) if l.strip()), "")
                err = result if data.get("is_error") else None
                return reply, data.get("total_cost_usd"), err
        return "", None, "no JSON result in the tool's output"
    reply = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "")
    return reply, None, None


def token_count(kind, out):
    """Total tokens a run used, when the tool reports it: Claude Code's JSON `usage`,
    or Codex's closing "tokens used" line followed by the number. None otherwise."""
    if kind == "claude-json":
        for line in reversed(out.strip().splitlines()):
            if line.strip().startswith("{"):
                try:
                    usage = json.loads(line).get("usage") or {}
                except (ValueError, AttributeError):
                    continue
                keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                total = sum(usage.get(k) or 0 for k in keys if isinstance(usage.get(k), int))
                return total or None
        return None
    lines = [l.strip() for l in out.splitlines()]
    for i in range(len(lines) - 1, -1, -1):
        m = re.match(r"^tokens used:?\s*([\d,]+)?$", lines[i], re.I)
        if m:
            raw = m.group(1) or next((l for l in lines[i + 1:i + 3] if re.fullmatch(r"[\d,]+", l)), "")
            return int(raw.replace(",", "")) if raw else None
    return None


ERROR_LINE = re.compile(r"\b(error|failed|failure|fatal|denied|unauthori[sz]ed|forbidden|not found|invalid|refused|"
                        r"exception|traceback|too many requests|rate limit|quota|timed? ?out|\b4\d\d\b|\b5\d\d\b)", re.I)
NOISE_LINE = re.compile(r"^[\d,.\s]+$|^tokens used$", re.I)


def failure_summary(out, code):
    """The line that says why a tool failed, not whatever it printed last (often a token count)."""
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    tail = lines[-200:]
    for line in reversed(tail):
        if ERROR_LINE.search(line) and not line.startswith("$ "):
            return line[:300]
    for line in reversed(tail):
        if not NOISE_LINE.match(line):
            return line[:300]
    return f"exited {code}"


def _next_index(runs):
    with _counter_lock:
        runs.mkdir(parents=True, exist_ok=True)
        n = len(list(runs.glob("*.json"))) + 1
        while (runs / f"{n:03d}.reserved").exists() or list(runs.glob(f"{n:03d}-*.json")):
            n += 1
        (runs / f"{n:03d}.reserved").touch()
        return n


def run_agent(cfg, job, role, task, model_tier, port=None, role_for_port=None):
    """Run one agent to completion. Returns its record (also saved as runs/NNN-role.json)."""
    name, h = harness_for(cfg, role)
    model = h["models"].get(model_tier) if model_tier else None
    prompt, tools = build_prompt(cfg, job, role, task, port)
    runs = paths.job_dir(job["id"]) / "runs"
    n = _next_index(runs)
    stem = f"{n:03d}-{role}"
    prompt_file = runs / f"{stem}.prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    command = render(h, model, tools, prompt_file, role)
    v, env = jobs.values(cfg, job, role=role_for_port or "implementer") if job.get("worktree") else ({}, {})
    env["FACTORY_ROLE"] = role
    started = jobs.now()
    t0 = time.monotonic()
    code, out, _, timed_out = run_command(command, paths.REPO, cfg["shell"],
                                          cfg["runner"]["agent_timeout_minutes"] * 60, env,
                                          input_text=prompt if h["prompt"] == "stdin" else None)
    reply, cost, err = parse_output(h["output"], out)
    if code != 0 and not err and h["output"] == "text":
        # A text-output tool that exits non-zero failed as a tool; find the line that says why.
        err = failure_summary(out, code)
    record = {
        "role": role, "harness": name, "model": model or "(tool default)", "task": task,
        "started": started, "seconds": round(time.monotonic() - t0, 1), "exit": code,
        "timed_out": timed_out, "cost_usd": cost, "tokens": token_count(h["output"], out),
        "reply": reply, "error": err, "log": f"runs/{stem}.log",
    }
    (runs / f"{stem}.log").write_text(f"$ {command}\n\n{out}", encoding="utf-8")
    atomic_write(runs / f"{stem}.json", json.dumps(record, indent=2) + "\n")
    (runs / f"{n:03d}.reserved").unlink(missing_ok=True)
    return record
