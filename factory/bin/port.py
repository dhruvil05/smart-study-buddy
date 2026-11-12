"""Ports for a job's dev servers, without lsof (works on Windows too).

Usage: python factory/bin/port.py …
  for <job> <role>   print the port for implementer, ux, ui or access
  free <port>        print "free" or "in use by PID <pid>"; exit 1 when in use
  kill <port>        stop the process listening on <port> (and its children)

Never stop dev servers by process name: other jobs run them too.
"""

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylib import config, jobs  # noqa: E402


def die(msg):
    print(f"port: {msg}", file=sys.stderr)
    sys.exit(1)


def answering(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def listening_pids(port):
    pids = set()
    if os.name == "nt":
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        out += subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True).stdout
        # Match on the remote port being 0, not on the state column: the state is
        # translated on non-English Windows, and some setups report listeners as CLOSED.
        for line in out.splitlines():
            parts = line.split()
            if (len(parts) >= 5 and parts[0].upper() == "TCP" and re.search(rf":{port}$", parts[1])
                    and parts[2].endswith(":0") and parts[-1].isdigit()):
                pids.add(int(parts[-1]))
        pids.discard(0)
        if not pids and answering(port):
            # netstat can lag or show PID 0 for a fresh listener; ask Windows directly.
            ps = shutil.which("powershell") or shutil.which("pwsh")
            if ps:
                out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command",
                                      f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue "
                                      "| Select-Object -ExpandProperty OwningProcess"],
                                     capture_output=True, text=True).stdout
                pids.update(int(p) for p in out.split() if p.isdigit())
    elif shutil.which("lsof"):
        out = subprocess.run(["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"], capture_output=True, text=True).stdout
        pids.update(int(p) for p in out.split() if p.isdigit())
    elif shutil.which("ss"):
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True).stdout
        pids.update(int(p) for p in re.findall(r"pid=(\d+)", out))
    pids.discard(0)
    return sorted(pids)


def kill(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def main(argv):
    if len(argv) == 3 and argv[0] == "for":
        cfg = config.load()
        try:
            print(jobs.port_for(cfg, jobs.resolve(argv[1], locked=False), argv[2]))
        except (ValueError, jobs.NoJob) as e:
            die(str(e))
    elif len(argv) == 2 and argv[0] in ("free", "kill") and argv[1].isdigit():
        port = int(argv[1])
        pids = listening_pids(port)
        if argv[0] == "free":
            print("free" if not pids else f"in use by PID {', '.join(map(str, pids))}")
            sys.exit(1 if pids else 0)
        for pid in pids:
            kill(pid)
        print(f"port {port}: " + (f"stopped PID {', '.join(map(str, pids))}" if pids else "nothing listening"))
    else:
        die(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
