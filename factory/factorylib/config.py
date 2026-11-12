"""config.json: defaults, loading, validation and saving (with history)."""

import copy
import json
import re
from datetime import datetime, timezone

from . import paths
from .files import atomic_write

MODELS = ("fable", "opus", "sonnet", "haiku")
RISKS = ("low", "medium", "high")
SURFACES = ("ui", "security", "data", "access", "integration")
REVIEWERS = ("code", "security", "ux", "ui", "data", "access")
MAIN_AGENTS = ("intake", "spec-writer", "implementer", "approver")
LOCKED_PROTECTED = ("factory/*", ".claude/*")
PLACEHOLDERS = ("{JOB}", "{N}", "{PORT}", "{DB}", "{WORKTREE}")
# Flags that rewrite files; a check must only read.
REWRITING_FLAG = re.compile(r"(?<![\w-])(--fix|--write|--update-snapshots|--updateSnapshot|-u)(?![\w-])")

DEFAULTS = {
    "version": 1,
    "base_branch": "main",
    "merge_method": "squash",
    "shell": "system",
    "concurrency": 1,
    "max_rounds": 3,
    "labels": {"queue": "factory", "needs_info": "factory:needs-info", "needs_human": "factory:needs-human"},
    "models": {"intake": "fable", "spec-writer": "opus", "implementer": "opus", "approver": "opus"},
    "routing": {
        "low": {"code": "haiku", "auto_merge": False},
        "medium": {"code": "sonnet", "auto_merge": False},
        "high": {"code": "sonnet", "auto_merge": False},
    },
    "surface_reviewers": {
        "ui": {"ux": "sonnet", "ui": "haiku"},
        "security": {"security": "opus"},
        "data": {"data": "opus"},
        "access": {"access": "opus"},
        "integration": {"security": "opus"},
    },
    "always_human_surfaces": ["data", "access", "integration"],
    "checks": [],
    "checks_none_ok": False,
    "env": {
        "template": ".env.factory",
        "db_name": "{project}_job_{n}",
        "db_create": "",
        "db_drop": "",
        "migrate": "",
        "seed": "",
        "dev_server": "",
        "login_path": "",
        "local_hosts": ["localhost", "127.0.0.1", "::1"],
    },
    "ports": {"base": 5100, "step": 10},
    "protected_paths": ["factory/*", ".claude/*", ".github/workflows/*"],
    "diff_budget": {
        "low": {"lines": 300, "files": 10},
        "medium": {"lines": 800, "files": 25},
        "high": {"lines": 1500, "files": 40},
    },
    "cost_caps_usd": {"job": None, "run": None},
    "prices_usd_per_mtok": {},
    "runner": {
        "harness": "claude",
        "role_harness": {},
        "agent_timeout_minutes": 60,
        "harnesses": {
            "claude": {
                "cmd": 'claude -p {MODEL_FLAG} --permission-mode dontAsk --allowedTools "{TOOLS}" --output-format json',
                "prompt": "stdin",
                "output": "claude-json",
                "model_flag": "--model {MODEL}",
                "models": {"fable": "fable", "opus": "opus", "sonnet": "sonnet", "haiku": "haiku"},
                "verified": True,
            },
            "codex": {
                "cmd": 'codex exec {MODEL_FLAG} --sandbox workspace-write --skip-git-repo-check '
                       '"Read the file {PROMPT_FILE} and follow its instructions exactly."',
                "prompt": "file",
                "output": "text",
                "model_flag": "--model {MODEL}",
                "models": {},
                "verified": False,
            },
            "deepseek": {
                "cmd": 'deepseek exec {MODEL_FLAG} "Read the file {PROMPT_FILE} and follow its instructions exactly."',
                "prompt": "file",
                "output": "text",
                "model_flag": "--model {MODEL}",
                "models": {},
                "verified": False,
            },
        },
    },
}
ROLES = MAIN_AGENTS + tuple(f"reviewer-{r}" for r in REVIEWERS)
HARNESS_PLACEHOLDERS = ("{MODEL_FLAG}", "{MODEL}", "{TOOLS}", "{PROMPT_FILE}", "{ROLE}")


class ConfigError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


# Maps whose entries the user adds and removes: taken as saved, never refilled
# from the defaults (otherwise a removed tool or reviewer would come back).
AS_SAVED = {("surface_reviewers",), ("prices_usd_per_mtok",), ("runner", "harnesses"), ("runner", "role_harness")}


def _merge(defaults, value, path=()):
    """Fill keys missing from value with defaults, recursively for dicts."""
    if path in AS_SAVED or not isinstance(defaults, dict) or not isinstance(value, dict):
        return copy.deepcopy(value)
    out = copy.deepcopy(defaults)
    for k, v in value.items():
        out[k] = _merge(defaults.get(k), v, path + (k,)) if k in defaults else copy.deepcopy(v)
    return out


def load(path=None):
    path = path or paths.CONFIG_FILE
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = _merge(DEFAULTS, raw)
    errors = validate(cfg)
    if errors:
        raise ConfigError(errors)
    return cfg


def enabled_checks(cfg):
    return [c for c in cfg["checks"] if c.get("enabled", True)]


def _int(errors, where, v, lo, hi):
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        errors.append(f"{where} must be a whole number from {lo} to {hi}")


def _model(errors, where, v):
    if v not in MODELS:
        errors.append(f"{where} must be one of {', '.join(MODELS)}")


def validate(cfg):
    """Return a list of human-readable errors; empty when valid."""
    e = []
    if not isinstance(cfg, dict):
        return ["config must be a JSON object"]
    if not isinstance(cfg.get("base_branch"), str) or not re.fullmatch(r"[\w./-]+", cfg.get("base_branch") or ""):
        e.append("base_branch must be a branch name")
    if cfg.get("merge_method") not in ("merge", "squash", "rebase"):
        e.append("merge_method must be merge, squash or rebase")
    if cfg.get("shell") not in ("system", "bash", "powershell"):
        e.append("shell must be system, bash or powershell")
    _int(e, "concurrency", cfg.get("concurrency"), 1, 3)
    _int(e, "max_rounds", cfg.get("max_rounds"), 1, 5)

    labels = cfg.get("labels")
    if not isinstance(labels, dict) or any(not isinstance(labels.get(k), str) or not labels.get(k)
                                           for k in ("queue", "needs_info", "needs_human")):
        e.append("labels needs queue, needs_info and needs_human")

    models = cfg.get("models") or {}
    for a in MAIN_AGENTS:
        _model(e, f"models.{a}", models.get(a))

    routing = cfg.get("routing") or {}
    if set(routing) != set(RISKS):
        e.append("routing must have exactly low, medium and high")
    for risk in RISKS:
        r = routing.get(risk) or {}
        _model(e, f"routing.{risk}.code", r.get("code"))
        if not isinstance(r.get("auto_merge"), bool):
            e.append(f"routing.{risk}.auto_merge must be true or false")
    if (routing.get("high") or {}).get("auto_merge") is True:
        e.append("routing.high.auto_merge can't be on: high-risk jobs always go to a human")

    sr = cfg.get("surface_reviewers") or {}
    if set(sr) - set(SURFACES):
        e.append(f"surface_reviewers has unknown areas: {', '.join(sorted(set(sr) - set(SURFACES)))}")
    for surface, reviewers in sr.items():
        if not isinstance(reviewers, dict):
            e.append(f"surface_reviewers.{surface} must map reviewer → model")
            continue
        for name, model in reviewers.items():
            if name not in REVIEWERS:
                e.append(f"surface_reviewers.{surface}: unknown reviewer {name!r}")
            _model(e, f"surface_reviewers.{surface}.{name}", model)
    ah = cfg.get("always_human_surfaces")
    if not isinstance(ah, list) or any(s not in SURFACES for s in ah):
        e.append(f"always_human_surfaces must list areas from: {', '.join(SURFACES)}")

    checks = cfg.get("checks")
    if not isinstance(checks, list):
        e.append("checks must be a list")
        checks = []
    names = set()
    for i, c in enumerate(checks):
        where = f"check {i + 1}"
        if not isinstance(c, dict):
            e.append(f"{where} must be an object")
            continue
        name = c.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name or ""):
            e.append(f"{where}: name must be lower-case letters, digits, - or _ (max 40)")
        elif name in names:
            e.append(f"{where}: name {name!r} is used twice")
        else:
            names.add(name)
            where = f"check {name!r}"
        cmd = c.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            e.append(f"{where}: command can't be empty")
        elif REWRITING_FLAG.search(cmd):
            e.append(f"{where}: command uses a flag that rewrites files ({REWRITING_FLAG.search(cmd).group(1)}); checks must only read")
        elif "\n" in cmd:
            e.append(f"{where}: command must be one line")
        if not isinstance(c.get("enabled", True), bool):
            e.append(f"{where}: enabled must be true or false")
        _int(e, f"{where}: timeout", c.get("timeout", 600), 1, 7200)
        _int(e, f"{where}: retry_on_fail", c.get("retry_on_fail", 0), 0, 3)
    if not isinstance(cfg.get("checks_none_ok"), bool):
        e.append("checks_none_ok must be true or false")

    env = cfg.get("env") or {}
    for k in ("template", "db_name", "db_create", "db_drop", "migrate", "seed", "dev_server", "login_path"):
        if not isinstance(env.get(k), str):
            e.append(f"env.{k} must be text (empty to skip)")
        elif "\n" in env.get(k):
            e.append(f"env.{k} must be one line")
    if env.get("dev_server") and "{PORT}" not in env["dev_server"]:
        e.append("env.dev_server must use {PORT} so each job gets its own port")
    if env.get("db_name") and not re.fullmatch(r"[\w{}]+", env["db_name"]):
        e.append("env.db_name may only use letters, digits, _ and {project} / {n}")
    lh = env.get("local_hosts")
    if not isinstance(lh, list) or not lh or any(not isinstance(h, str) or not h for h in lh):
        e.append("env.local_hosts must list at least one host")

    ports = cfg.get("ports") or {}
    _int(e, "ports.base", ports.get("base"), 1024, 60000)
    _int(e, "ports.step", ports.get("step"), 4, 100)

    pp = cfg.get("protected_paths")
    if not isinstance(pp, list) or any(not isinstance(p, str) or not p.strip() for p in pp):
        e.append("protected_paths must be a list of path patterns")
    else:
        for locked in LOCKED_PROTECTED:
            if locked not in pp:
                e.append(f"protected_paths must keep {locked}")

    db = cfg.get("diff_budget") or {}
    for risk in RISKS:
        b = db.get(risk) or {}
        _int(e, f"diff_budget.{risk}.lines", b.get("lines"), 1, 100000)
        _int(e, f"diff_budget.{risk}.files", b.get("files"), 1, 5000)

    caps = cfg.get("cost_caps_usd") or {}
    for k in ("job", "run"):
        v = caps.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0):
            e.append(f"cost_caps_usd.{k} must be empty or a positive amount")

    prices = cfg.get("prices_usd_per_mtok")
    if not isinstance(prices, dict):
        e.append("prices_usd_per_mtok must be an object")
    else:
        for model, p in prices.items():
            keys = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
            if not isinstance(p, dict) or any(not isinstance(p.get(k), (int, float)) or p.get(k) < 0 for k in keys):
                e.append(f"prices_usd_per_mtok.{model} needs {', '.join(keys)} as numbers")
    _validate_runner(e, cfg.get("runner"))
    return e


def harnesses_in_use(runner):
    return {runner.get("harness")} | set((runner.get("role_harness") or {}).values())


def _validate_runner(e, runner):
    if not isinstance(runner, dict):
        e.append("runner must be an object")
        return
    hs = runner.get("harnesses")
    if not isinstance(hs, dict) or not hs:
        e.append("runner.harnesses must define at least one agent tool")
        return
    for name, h in hs.items():
        where = f"runner.harnesses.{name}"
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", name):
            e.append(f"{where}: name must be lower-case letters, digits, - or _")
        if not isinstance(h, dict):
            e.append(f"{where} must be an object")
            continue
        cmd = h.get("cmd")
        if not isinstance(cmd, str) or "\n" in cmd:
            e.append(f"{where}.cmd must be one line of text")
            continue
        if h.get("prompt") not in ("stdin", "file"):
            e.append(f"{where}.prompt must be stdin or file")
        elif h.get("prompt") == "file" and cmd and "{PROMPT_FILE}" not in cmd:
            e.append(f"{where}.cmd must use {{PROMPT_FILE}} when the prompt is passed as a file")
        if h.get("output") not in ("text", "claude-json"):
            e.append(f"{where}.output must be text or claude-json")
        flag = h.get("model_flag")
        if not isinstance(flag, str) or (flag and "{MODEL}" not in flag):
            e.append(f"{where}.model_flag must be empty or use {{MODEL}}")
        models = h.get("models")
        if not isinstance(models, dict) or any(k not in MODELS or not isinstance(v, str) or not v.strip()
                                               for k, v in models.items()):
            e.append(f"{where}.models must map {', '.join(MODELS)} to model names")
        if not isinstance(h.get("verified", False), bool):
            e.append(f"{where}.verified must be true or false")
    if runner.get("harness") not in hs:
        e.append(f"runner.harness must be one of: {', '.join(hs)}")
    rh = runner.get("role_harness")
    if not isinstance(rh, dict):
        e.append("runner.role_harness must map roles to agent tools")
    else:
        for role, name in rh.items():
            if role not in ROLES:
                e.append(f"runner.role_harness: unknown role {role!r} (roles: {', '.join(ROLES)})")
            if name not in hs:
                e.append(f"runner.role_harness.{role}: unknown agent tool {name!r}")
    for name in harnesses_in_use(runner):
        if name in hs and isinstance(hs[name], dict) and not (hs[name].get("cmd") or "").strip():
            e.append(f"runner.harnesses.{name}.cmd is empty but the runner uses it")
    _int(e, "runner.agent_timeout_minutes", runner.get("agent_timeout_minutes"), 1, 480)


def save(cfg, path=None, history=None):
    """Validate, keep the previous file in history, write atomically."""
    path = path or paths.CONFIG_FILE
    history = history or paths.HISTORY
    cfg = _merge(DEFAULTS, cfg)
    errors = validate(cfg)
    if errors:
        raise ConfigError(errors)
    if path.exists():
        history.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (history / f"{stamp}.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    atomic_write(path, json.dumps(cfg, indent=2) + "\n")
    return cfg
