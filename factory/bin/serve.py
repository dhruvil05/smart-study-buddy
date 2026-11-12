"""The dashboard: board, job files and settings, served on this machine only.

Usage: python factory/bin/serve.py [--port 8000]

Open the link it prints. The token in the link is required for every API call,
so other sites open in your browser can't read or change the factory. It binds
to 127.0.0.1 only. Saving settings never runs a command; commands run only in a
job's gate or when you press Test on main.
"""

import argparse
import hmac
import json
import re
import secrets
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylib import config, jobs, paths  # noqa: E402
from factorylib.files import first_line, summary_bullets  # noqa: E402
from factorylib.proc import git  # noqa: E402

JOB_ID = re.compile(r"^[0-9]{3}-[a-z0-9-]{1,80}$")
MAX_BODY = 1_000_000
MAX_FILE = 2_000_000
DASHBOARD = paths.FACTORY / "dashboard" / "index.html"
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Preflight:
    lock = threading.Lock()
    status = "idle"
    error = None

    @classmethod
    def start(cls):
        with cls.lock:
            if cls.status == "running":
                return False
            cls.status, cls.error = "running", None
        threading.Thread(target=cls._run, daemon=True).start()
        return True

    @classmethod
    def _run(cls):
        res = subprocess.run([sys.executable, str(paths.BIN / "job.py"), "preflight"], cwd=str(paths.REPO),
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        with cls.lock:
            cls.status = "done"
            # A FAIL prints to stdout and writes preflight.json; a refusal (no steps, no
            # origin, a broken env) prints `job: …` to stderr and writes nothing new.
            cls.error = res.stderr.strip().removeprefix("job: ") if res.returncode != 0 and res.stderr.strip() else None

    @classmethod
    def state(cls):
        f = paths.PREFLIGHT / "preflight.json"
        result = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return {"status": cls.status, "result": result, "error": cls.error}


def load_config_raw():
    try:
        return config.load(), []
    except config.ConfigError as e:
        raw = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8")) if paths.CONFIG_FILE.exists() else {}
        return config._merge(config.DEFAULTS, raw), e.errors


def state():
    cfg, errors = load_config_raw()
    all_jobs = jobs.all_jobs()
    dirty = git(paths.REPO, "status", "--porcelain", "--", "factory/config.json", check=False).stdout.strip()
    return {
        "project": {"name": paths.REPO.name, "repo": paths.posix(paths.REPO), "base_branch": cfg["base_branch"]},
        "paused": paths.PAUSE.exists(),
        "config": cfg,
        "config_errors": errors,
        "config_dirty": bool(dirty),
        "jobs": all_jobs,
        "slots": {"used": sum(1 for j in all_jobs if j["stage"] in jobs.RUNNING), "total": cfg["concurrency"]},
        "preflight": Preflight.state()["result"],
    }


def job_files(job_id):
    d = paths.job_dir(job_id)
    out = []
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name in (".lock", "job.json") or p.suffix == ".tmp":
            continue
        rel = p.relative_to(d).as_posix()
        entry = {"path": rel, "size": p.stat().st_size, "first_line": None, "summary": []}
        if p.suffix == ".md":
            entry["first_line"] = first_line(p)
            entry["summary"] = summary_bullets(p)
        out.append(entry)
    return out


def job_script(cmd, job_id, text):
    res = subprocess.run([sys.executable, str(paths.BIN / "job.py"), cmd, job_id], cwd=str(paths.REPO),
                         input=text, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return res.returncode == 0, (res.stdout if res.returncode == 0 else res.stderr).strip().removeprefix("job: ")


class Handler(BaseHTTPRequestHandler):
    server_version = "auto-factory"
    token = ""
    port = 0

    def log_message(self, fmt, *args):
        if "/api/state" not in (args[0] if args else ""):
            sys.stderr.write("serve: " + fmt % args + "\n")

    # -- plumbing --
    def send(self, status, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def fail(self, status, msg):
        self.send(status, {"error": msg})

    def allowed(self):
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            self.fail(HTTPStatus.FORBIDDEN, "wrong host")
            return False
        if self.path.startswith("/api/"):
            given = self.headers.get("X-Factory-Token", "")
            if not hmac.compare_digest(given.encode(), self.token.encode()):
                self.fail(HTTPStatus.UNAUTHORIZED, "missing or wrong token: open the link serve.py printed")
                return False
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("send JSON")
        if n > MAX_BODY:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("send a JSON object")
        return data

    def job_id(self, raw):
        if not JOB_ID.match(raw) or not (paths.job_dir(raw) / "job.json").exists():
            self.fail(HTTPStatus.NOT_FOUND, f"no job {raw}")
            return None
        return raw

    # -- routes --
    def do_GET(self):
        if not self.allowed():
            return
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        try:
            if url.path in ("/", "/index.html"):
                if not DASHBOARD.exists():
                    return self.fail(HTTPStatus.NOT_FOUND, "dashboard/index.html is missing")
                return self.send(HTTPStatus.OK, DASHBOARD.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self.send(HTTPStatus.OK, state())
            if url.path == "/api/preflight":
                return self.send(HTTPStatus.OK, Preflight.state())
            if url.path == "/api/config/history":
                entries = sorted(paths.HISTORY.glob("*.json"), reverse=True) if paths.HISTORY.exists() else []
                return self.send(HTTPStatus.OK, {"entries": [
                    {"name": p.name, "at": f"{p.stem[0:4]}-{p.stem[4:6]}-{p.stem[6:8]}T{p.stem[9:11]}:{p.stem[11:13]}:{p.stem[13:15]}Z"}
                    for p in entries[:200]]})
            if url.path == "/api/config/diff":
                diff = git(paths.REPO, "diff", "--", "factory/config.json", check=False).stdout
                return self.send(HTTPStatus.OK, {"diff": diff})
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                job_id = self.job_id(parts[2])
                if job_id:
                    return self.send(HTTPStatus.OK, {"job": jobs.load(job_id, locked=False), "files": job_files(job_id)})
                return None
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "file":
                job_id = self.job_id(parts[2])
                if not job_id:
                    return None
                rel = (parse_qs(url.query).get("path") or [""])[0]
                d = paths.job_dir(job_id).resolve()
                target = (d / rel).resolve()
                if d not in target.parents or not target.is_file() or target.name in (".lock",):
                    return self.fail(HTTPStatus.NOT_FOUND, "no such file in this job")
                if target.stat().st_size > MAX_FILE:
                    return self.fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "file too large to show")
                if target.suffix.lower() == ".png":
                    return self.send(HTTPStatus.OK, target.read_bytes(), "image/png")
                return self.send(HTTPStatus.OK, {"path": rel, "content": target.read_text(encoding="utf-8", errors="replace")})
            return self.fail(HTTPStatus.NOT_FOUND, "not found")
        except Exception as e:  # keep serving; report the problem to the page
            return self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    def do_POST(self):
        if not self.allowed():
            return
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        try:
            data = self.body()
        except (ValueError, json.JSONDecodeError) as e:
            return self.fail(HTTPStatus.BAD_REQUEST, str(e))
        try:
            if url.path == "/api/config":
                busy = [j["id"] for j in jobs.all_jobs() if j["stage"] in ("gate", "merging")]
                if busy:
                    return self.fail(HTTPStatus.CONFLICT, f"{', '.join(busy)} in the gate or merging; try again in a moment")
                try:
                    saved = config.save(data.get("config"))
                except config.ConfigError as e:
                    return self.send(HTTPStatus.BAD_REQUEST, {"errors": e.errors})
                return self.send(HTTPStatus.OK, {"config": saved})
            if url.path == "/api/pause":
                if data.get("paused"):
                    paths.PAUSE.write_text(f"paused {jobs.now()} from the dashboard\n", encoding="utf-8")
                else:
                    paths.PAUSE.unlink(missing_ok=True)
                return self.send(HTTPStatus.OK, {"paused": paths.PAUSE.exists()})
            if url.path == "/api/preflight":
                started = Preflight.start()
                return self.send(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
                                 {"status": "running"} if started else {"error": "already running"})
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] in ("feedback", "reject"):
                job_id = self.job_id(parts[2])
                if not job_id:
                    return None
                text = data.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 20000:
                    return self.fail(HTTPStatus.BAD_REQUEST, "write some text (up to 20,000 characters)")
                ok, msg = job_script(parts[3], job_id, text)
                if not ok:
                    return self.fail(HTTPStatus.CONFLICT, msg)
                return self.send(HTTPStatus.OK, {"job": jobs.load(job_id, locked=False), "message": msg})
            return self.fail(HTTPStatus.NOT_FOUND, "not found")
        except Exception as e:
            return self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")


def main(argv):
    ap = argparse.ArgumentParser(description="auto-factory dashboard")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    Handler.token = secrets.token_urlsafe(24)
    Handler.port = a.port
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"auto-factory dashboard for {paths.REPO.name}: http://127.0.0.1:{a.port}/#token={Handler.token}")
    print("Keep this link private. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main(sys.argv[1:])
