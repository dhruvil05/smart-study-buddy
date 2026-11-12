"""Make sure a job's environment only points at this machine."""

import re
from pathlib import Path
from urllib.parse import urlsplit

URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"]+")
HOST_KEY = re.compile(r"(^|_)(HOST|HOSTNAME|SERVER|ENDPOINT)$", re.I)


def is_local(host, local_hosts):
    host = (host or "").strip().strip("[]").lower()
    if not host:
        return True
    return host in {h.lower().strip("[]") for h in local_hosts} or host.endswith((".localhost", ".test"))


def parse_env(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        values[key] = value.strip().strip("'\"")
    return values


def foreign_hosts(values, local_hosts):
    """[(key, host)] for every value that names a host that isn't local."""
    bad = []
    for key, value in values.items():
        for url in URL.findall(value):
            try:
                host = urlsplit(url).hostname
            except ValueError:
                host = url
            if host and not is_local(host, local_hosts):
                bad.append((key, host))
        if HOST_KEY.search(key) and value and "://" not in value:
            host = value.split(":")[0] if value.count(":") == 1 else value
            if not is_local(host, local_hosts):
                bad.append((key, host))
    return bad


def check_file(path, local_hosts):
    path = Path(path)
    if not path.exists():
        return []
    return foreign_hosts(parse_env(path.read_text(encoding="utf-8", errors="replace")), local_hosts)
