"""API-equivalent cost of a job's agents, read from Claude Code's transcripts.

Claude Code keeps each subagent's messages under
<config>/projects/<project>/<session>/subagents/agent-<id>.jsonl, with an
agent-<id>.meta.json beside it that names the agent type. Every factory prompt
starts with `Job: <id>.`, which ties a transcript to its job. The format isn't a
documented interface, so anything unexpected makes the cost partial, never an error.
"""

import json
import os
import re
from pathlib import Path

from . import paths


def transcripts_dir():
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(paths.REPO))


def _prompt(line):
    content = json.loads(line).get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


def _price(prices, model):
    if model in prices:
        return prices[model]
    for key, p in prices.items():  # "claude-opus-5-5" also prices "claude-opus-5-5-20261101"
        if model.startswith(key):
            return p
    return None


def runner_costs(job_id):
    """Costs the runner recorded per agent call (runs/*.json), for tools that report one."""
    total, by_agent, partial = 0.0, {}, False
    runs = paths.job_dir(job_id) / "runs"
    for f in sorted(runs.glob("*.json")) if runs.exists() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            partial = True
            continue
        if rec.get("cost_usd") is None:
            partial = True
            continue
        total += rec["cost_usd"]
        by_agent[rec.get("role", "agent")] = by_agent.get(rec.get("role", "agent"), 0.0) + rec["cost_usd"]
    return total, by_agent, partial


def job_cost(job_id, prices):
    needle = f"Job: {job_id}."
    total, by_agent, partial = runner_costs(job_id)
    root = transcripts_dir()
    if not root.exists():
        return {"usd": round(total, 2), "by_agent": {k: round(v, 2) for k, v in by_agent.items()}, "partial": partial}
    for path in root.glob("*/subagents/agent-*.jsonl"):
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                first = f.readline()
                if not first or needle not in _prompt(first):
                    continue
                usage = {}
                for line in f:
                    entry = json.loads(line)
                    msg = entry.get("message")
                    if entry.get("type") == "assistant" and isinstance(msg, dict) and msg.get("usage"):
                        usage[msg.get("id") or len(usage)] = (msg.get("model", ""), msg["usage"])
            meta = path.with_name(path.stem + ".meta.json")
            agent = json.loads(meta.read_text(encoding="utf-8")).get("agentType", "agent") if meta.exists() else "agent"
        except (OSError, ValueError):
            partial = True
            continue
        cost = 0.0
        for model, u in usage.values():
            p = _price(prices, model)
            if p is None:
                partial = True
                continue
            write_1h = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0)
            write_5m = u.get("cache_creation_input_tokens", 0) - write_1h
            cost += (u.get("input_tokens", 0) * p["input"] + u.get("output_tokens", 0) * p["output"]
                     + write_5m * p["cache_write_5m"] + write_1h * p["cache_write_1h"]
                     + u.get("cache_read_input_tokens", 0) * p["cache_read"]) / 1e6
        total += cost
        by_agent[agent] = round(by_agent.get(agent, 0.0) + cost, 4)
    return {"usd": round(total, 2), "by_agent": {k: round(v, 2) for k, v in by_agent.items()}, "partial": partial}
