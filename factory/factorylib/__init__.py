"""Shared code for the auto-factory scripts. Standard library only."""

import sys

# Windows consoles default to a legacy code page; the scripts' output is read by
# agents, the dashboard and tests as UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
