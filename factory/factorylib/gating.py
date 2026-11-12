"""The gate: the project's check steps plus the factory's built-in rules."""

import fnmatch
import re
from pathlib import Path

from . import localenv
from .config import enabled_checks
from .jobs import expand
from .proc import Refused, fmt_seconds, git, run_command

TAIL = 80
SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}")),
    ("hard-coded secret", re.compile(
        r"(?i)\b(api[_-]?key|secret|passw(or)?d|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
]
ENV_FILE = re.compile(r"(^|/)\.env(\.[\w-]+)?$")
ENV_FILE_OK = re.compile(r"(^|/)\.env\.(example|sample|dist|factory|template|testing)$")


def require_steps(cfg):
    steps = enabled_checks(cfg)
    if not steps and not cfg["checks_none_ok"]:
        raise Refused("no check steps are set. Add one in the dashboard (Settings → Check steps), "
                      "or turn on 'No automated checks'")
    return steps


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def protected_changes(worktree, base_ref, patterns):
    out = git(worktree, "diff", "--name-status", f"{base_ref}...HEAD").stdout
    hits = []
    for line in out.splitlines():
        status, *names = line.split("\t")
        if not names or status.startswith("A"):
            continue
        if matches(names[0], patterns):
            hits.append(f"{status[0]} {names[0]}")
    return hits


def diff_size(worktree, base_ref):
    out = git(worktree, "diff", "--numstat", f"{base_ref}...HEAD").stdout
    lines = files = 0
    for row in out.splitlines():
        added, deleted, _ = row.split("\t", 2)
        files += 1
        lines += (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
    return lines, files


def secret_findings(worktree, base_ref):
    out = git(worktree, "diff", "-U0", "--no-color", f"{base_ref}...HEAD").stdout
    hits, current = [], None
    for line in out.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current and ENV_FILE.search(current) and not ENV_FILE_OK.search(current):
                hits.append(f"{current}: an env file is committed")
        elif line.startswith("+") and current:
            for name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    hits.append(f"{current}: looks like a {name}")
                    break
    return sorted(set(hits))


def run_steps(cfg, worktree, v, env, log):
    """Run every enabled step; stop at the first failure. Returns step results."""
    results = []
    for step in enabled_checks(cfg):
        command = expand(step["cmd"], v)
        attempts = 1 + step.get("retry_on_fail", 0)
        total = 0.0
        for attempt in range(1, attempts + 1):
            code, out, seconds, timed_out = run_command(command, worktree, cfg["shell"],
                                                        step.get("timeout", 600), env)
            total += seconds
            log.append(f"===== {step['name']} (attempt {attempt}) $ {command}\n{out}\n"
                       f"===== exit {code}{' (timed out)' if timed_out else ''} in {fmt_seconds(seconds)}\n")
            if code == 0:
                break
        if code == 0:
            result = "PASS" if attempt == 1 else "PASS (FLAKY: passed on retry)"
        else:
            result = "TIMEOUT" if timed_out else f"FAIL (exit {code})"
        results.append({"name": step["name"], "result": result, "ok": code == 0,
                        "seconds": round(total, 1), "output": out})
        if code != 0:
            break
    return results


def run_gate(cfg, worktree, base_ref, v, env, risk=None, rules=True):
    """Everything the gate decides, as a dict. Raises Refused when it can't run at all."""
    require_steps(cfg)
    worktree = Path(worktree)
    commit = git(worktree, "rev-parse", "HEAD").stdout.strip()
    rule_rows = []
    foreign = localenv.check_file(worktree / ".env", cfg["env"]["local_hosts"])
    if foreign:
        raise Refused("the job's .env points at hosts that aren't on this machine: "
                      + ", ".join(f"{k} → {h}" for k, h in foreign) + ". Nothing was run.")
    rule_rows.append({"name": "local-only env", "ok": True, "detail": "PASS"})
    if rules:
        prot = protected_changes(worktree, base_ref, cfg["protected_paths"])
        rule_rows.append({"name": "protected paths", "ok": not prot,
                          "detail": "PASS: none changed" if not prot else "FAIL: " + ", ".join(prot)})
        if risk in cfg["diff_budget"]:
            lines, files = diff_size(worktree, base_ref)
            budget = cfg["diff_budget"][risk]
            ok = lines <= budget["lines"] and files <= budget["files"]
            rule_rows.append({"name": "diff budget", "ok": ok,
                              "detail": f"{'PASS' if ok else 'FAIL'}: {lines} lines, {files} files "
                                        f"({risk}: {budget['lines']}, {budget['files']})"
                                        + ("" if ok else ". Split the change or ask for a smaller scope.")})
        secrets = secret_findings(worktree, base_ref)
        rule_rows.append({"name": "secret scan", "ok": not secrets,
                          "detail": "PASS: clean" if not secrets else "FAIL: " + "; ".join(secrets)})
    log = []
    steps = run_steps(cfg, worktree, v, env, log)
    ok = all(s["ok"] for s in steps) and all(r["ok"] for r in rule_rows)
    return {"verdict": "PASS" if ok else "FAIL", "commit": commit, "steps": steps,
            "rules": rule_rows, "log": "".join(log), "none_ok": not enabled_checks(cfg)}


def render(result, note=""):
    steps, rules = result["steps"], result["rules"]
    failed = next((s for s in steps if not s["ok"]), None)
    bad_rules = [r for r in rules if not r["ok"]]
    summary = []
    if result["none_ok"]:
        summary.append("No check steps: this project runs with 'No automated checks' on")
    else:
        passed = sum(1 for s in steps if s["ok"])
        summary.append(f"{passed} of {len(steps)} check steps passed" + (f"; {failed['name']} failed" if failed else ""))
    summary.append("Built-in rules: " + ("all passed" if not bad_rules else
                                          "failed: " + ", ".join(r["name"] for r in bad_rules)))
    summary.append(f"Commit {result['commit'][:7]}" + (f", {note}" if note else ""))
    lines = [f"GATE: {result['verdict']}", f"COMMIT: {result['commit']}", "", "## Summary"]
    lines += [f"- {s}" for s in summary]
    lines += ["", "## Steps", "| Step | Result | Time |", "|---|---|---|"]
    lines += [f"| {s['name']} | {s['result']} | {fmt_seconds(s['seconds'])} |" for s in steps] or ["| (none) | — | — |"]
    lines += ["", "## Built-in rules", "| Rule | Result |", "|---|---|"]
    lines += [f"| {r['name']} | {r['detail']} |" for r in rules]
    if failed:
        tail = "\n".join(failed["output"].rstrip().splitlines()[-TAIL:])
        lines += ["", "## Failure", f"Last {TAIL} lines of {failed['name']}:", "", "```", tail, "```"]
    return "\n".join(lines) + "\n"


def one_line(result):
    parts = [f"{s['name']} {s['result'].split(' ')[0]}" for s in result["steps"]]
    parts += [f"{r['name']} FAIL" for r in result["rules"] if not r["ok"]]
    return f"GATE: {result['verdict']} ({', '.join(parts) or 'no steps'})"
