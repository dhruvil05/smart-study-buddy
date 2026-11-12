"""Reading and writing the factory's markdown and JSON files."""

import os
import re
from pathlib import Path


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def first_line(path):
    path = Path(path)
    if not path.exists():
        return None
    return read(path).lstrip("﻿").split("\n", 1)[0].strip()


def header(path):
    """KEY: value lines at the top of a file, up to the first blank line."""
    fields = {}
    for line in read(path).lstrip("﻿").splitlines():
        if not line.strip():
            break
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().upper()] = value.strip()
    return fields


def section(path, heading):
    """Body of a `## heading` section, or None."""
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\r?\n(.*?)(?=^## |\Z)", read(path), re.M | re.S)
    return m.group(1).strip() if m else None


def summary_bullets(path):
    body = section(path, "Summary") or ""
    return [line.lstrip("-* ").strip() for line in body.splitlines() if line.strip().startswith(("-", "*"))]


def body_after_first_line(path):
    return read(path).split("\n", 1)[1].strip() if "\n" in read(path) else ""


def body_after_header(path):
    """Everything after the header block (the first blank line)."""
    parts = re.split(r"\r?\n\s*\r?\n", read(path), maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def quote(text):
    return "\n".join("> " + line for line in ((text or "").strip() or "(empty)").splitlines())
