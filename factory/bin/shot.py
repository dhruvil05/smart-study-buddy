"""Sign in as a seeded test user and screenshot a page of a job's dev server.

Usage:
  python factory/bin/shot.py <job> --role <implementer|ux|ui|access> --path <url path>
         --as <seeded role> [--width 1440] [--height 900] --out <file.png>

The dev server must already run on the role's port (port.py for <job> <role>).
Signing in uses config env.login_path (a dev-only URL such as
/dev-login?as={ROLE}) when set, else the form login described for that role in
factory/seed-users.json:

  {"login": {"path": "/login", "user_field": "email", "password_field": "password",
             "submit": "button[type=submit]"},
   "users": {"admin": {"user": "admin@example.test", "password": "…"}}}

Those are test-only users that exist in the job's own database. Needs the
Playwright Python package (`pip install playwright && python -m playwright
install chromium`); without it this prints a note and exits 3 so reviewers can
fall back to reading code.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylib import config, jobs, localenv, paths  # noqa: E402


def die(msg, code=1):
    print(f"shot: {msg}", file=sys.stderr)
    sys.exit(code)


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("job")
    ap.add_argument("--role", default="implementer")
    ap.add_argument("--path", default="/")
    ap.add_argument("--as", dest="as_role", default=None)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    cfg = config.load()
    job = jobs.resolve(a.job, locked=False)
    try:
        port = jobs.port_for(cfg, job, a.role)
    except ValueError as e:
        die(str(e))
    base = f"http://127.0.0.1:{port}"
    if not a.path.startswith("/") or "://" in a.path:
        die("--path must be a path on the job's own server, like /orders")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("Playwright isn't installed, so no screenshot was taken. Review from the code and say so "
            "under Notes. (Install: pip install playwright && python -m playwright install chromium)", 3)

    seed_file = paths.FACTORY / "seed-users.json"
    seed = json.loads(seed_file.read_text(encoding="utf-8")) if seed_file.exists() else {}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": a.width, "height": a.height}).new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        def guard(request):
            host = request.url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
            if request.url.startswith(("http://", "https://")) and not localenv.is_local(host, cfg["env"]["local_hosts"]):
                return request.abort()
            return request.continue_()
        page.route("**/*", guard)  # the page can't reach anything off this machine

        if a.as_role:
            login_path = cfg["env"]["login_path"]
            if login_path:
                page.goto(base + login_path.replace("{ROLE}", a.as_role))
            else:
                user = (seed.get("users") or {}).get(a.as_role)
                form = seed.get("login") or {}
                if not user:
                    die(f"no seeded user for role {a.as_role!r} in factory/seed-users.json")
                page.goto(base + form.get("path", "/login"))
                page.fill(f"[name={form.get('user_field', 'email')}]", user["user"])
                page.fill(f"[name={form.get('password_field', 'password')}]", user["password"])
                page.click(form.get("submit", "button[type=submit]"))
            page.wait_for_load_state("networkidle")
        page.goto(base + a.path)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(out), full_page=True)
        title = page.title()
        browser.close()
    print(f"saved {paths.posix(out)} ({a.width}px, signed in as {a.as_role or 'nobody'}, title {title!r})")
    if errors:
        print("console errors:\n" + "\n".join(f"- {e}" for e in errors[:20]))


if __name__ == "__main__":
    main(sys.argv[1:])
