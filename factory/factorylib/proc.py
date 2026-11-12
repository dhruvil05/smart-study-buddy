"""Running git, gh and the project's own commands."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time


class Refused(Exception):
    """A command or transition the factory won't do. The message says why."""


def run(cmd, cwd, check=True, stdin=None):
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                         errors="replace", input=stdin)
    if check and res.returncode != 0:
        raise Refused(f"`{' '.join(str(c) for c in cmd)}` failed: {(res.stderr or res.stdout).strip()}")
    return res


def git(cwd, *args, check=True):
    return run(["git", *args], cwd, check=check)


def _gh_cmd():
    """FACTORY_GH points at a stand-in gh (tests); a .py path runs with this Python."""
    override = os.environ.get("FACTORY_GH")
    if not override:
        return ["gh"]
    return [sys.executable, override] if override.endswith(".py") else [override]


def gh(cwd, *args, stdin=None):
    return run(_gh_cmd() + [str(a) for a in args], cwd, stdin=stdin).stdout


def gh_json(cwd, *args):
    return json.loads(gh(cwd, *args))


def shell_argv(command, shell):
    if shell == "bash":
        bash = shutil.which("bash")
        if not bash:
            raise Refused("config shell is bash, but bash isn't on PATH")
        return [bash, "-c", command]
    if shell == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            raise Refused("config shell is powershell, but PowerShell isn't on PATH")
        return [exe, "-NoProfile", "-NonInteractive", "-Command", command]
    if os.name == "nt":
        # A string, not a list: list2cmdline would turn the command's quotes into \",
        # which cmd.exe doesn't understand. /s strips just the outer pair we add.
        comspec = os.environ.get("ComSpec", "cmd.exe")
        return f'"{comspec}" /d /s /c "{command}"'
    return ["/bin/sh", "-c", command]


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def run_command(command, cwd, shell, timeout, env=None, input_text=None):
    """Run one command. Returns (exit_code, output, seconds, timed_out).

    Non-interactive: stdin is closed unless `input_text` is given. The whole
    process tree dies on timeout.
    """
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    full_env = dict(os.environ)
    full_env.update(env or {})
    full_env.setdefault("CI", "1")
    start = time.monotonic()
    proc = subprocess.Popen(shell_argv(command, shell), cwd=str(cwd),
                            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=full_env, **kwargs)
    try:
        out, _ = proc.communicate(input=input_text, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        timed_out = True
    seconds = time.monotonic() - start
    code = proc.returncode if not timed_out else 124
    return code, out or "", seconds, timed_out


def fmt_seconds(seconds):
    s = int(round(seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"
