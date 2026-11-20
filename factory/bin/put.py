"""Write or patch files for an agent whose tool has no working file editor.

Usage (the text always comes on stdin):
  python factory/bin/put.py write <file>   create or replace <file> with stdin
  python factory/bin/put.py patch          apply a patch in the apply_patch format:

    *** Begin Patch
    *** Add File: <path>
    +every line of the new file
    *** Update File: <path>
    @@ optional line to find first
     unchanged line
    -removed line
    +added line
    *** Delete File: <path>
    *** End Patch

Paths are absolute, or relative to the current directory. A file keeps its line
endings (CRLF stays CRLF). A hunk must match the file exactly once, ignoring
trailing spaces; when it doesn't, nothing is written and the error says which.
Windows PowerShell sends piped text as ASCII unless told otherwise, so start
the command with `$OutputEncoding = [Text.UTF8Encoding]::new($false);`.
"""

import sys
from pathlib import Path


class PatchError(Exception):
    pass


def read_stdin():
    data = sys.stdin.buffer.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def newline_of(raw):
    return "\r\n" if "\r\n" in raw else "\n"


def save(path, text, nl="\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text.replace("\r\n", "\n")
    path.write_bytes((body.replace("\n", nl) if nl != "\n" else body).encode("utf-8"))


def find(lines, block, start):
    """Indexes where `block` occurs in `lines` from `start`: exact first, then ignoring trailing spaces."""
    n = len(block)
    for norm in (lambda s: s, lambda s: s.rstrip()):
        b = [norm(l) for l in block]
        hits = [i for i in range(start, len(lines) - n + 1) if [norm(l) for l in lines[i:i + n]] == b]
        if hits:
            return hits
    return []


def apply_hunks(path, lines, hunks):
    pos = 0
    for anchor, old, new in hunks:
        if anchor:
            hits = find(lines, [anchor], pos)
            if not hits:
                raise PatchError(f"{path}: can't find the line after @@: {anchor.strip()!r}")
            pos = hits[0] + 1
        if not old:  # only additions: after the anchor, or at the end of the file
            at = pos if anchor else len(lines)
            lines[at:at] = new
            pos = at + len(new)
            continue
        hits = find(lines, old, pos)
        if not hits:
            raise PatchError(f"{path}: these lines aren't in the file (check them against the file):\n"
                             + "\n".join("  " + l for l in old[:8]))
        if len(hits) > 1 and not anchor:
            raise PatchError(f"{path}: the lines match {len(hits)} places; add more unchanged lines "
                             f"or an `@@ <line before them>` to say which")
        i = hits[0]
        lines[i:i + len(old)] = new
        pos = i + len(new)
    return lines


def parse(text):
    """[(op, path, data)]: ("add", p, lines), ("delete", p, None), ("update", p, (move_to, hunks))."""
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise PatchError("the patch must start with '*** Begin Patch'")
    if lines[-1].strip() != "*** End Patch":
        raise PatchError("the patch must end with '*** End Patch'")
    ops, i, body = [], 0, lines[1:-1]
    while i < len(body):
        line = body[i]
        if line.startswith("*** Add File: "):
            path, i, content = line[14:].strip(), i + 1, []
            while i < len(body) and not body[i].startswith("*** "):
                if not body[i].startswith("+"):
                    raise PatchError(f"Add File {path}: every line must start with '+' (line: {body[i]!r})")
                content.append(body[i][1:])
                i += 1
            ops.append(("add", path, content))
        elif line.startswith("*** Delete File: "):
            ops.append(("delete", line[17:].strip(), None))
            i += 1
        elif line.startswith("*** Update File: "):
            path, i, move, hunks = line[17:].strip(), i + 1, None, []
            if i < len(body) and body[i].startswith("*** Move to: "):
                move, i = body[i][13:].strip(), i + 1
            anchor, old, new = None, [], []
            while i < len(body) and (not body[i].startswith("*** ") or body[i] == "*** End of File"):
                l = body[i]
                i += 1
                if l == "*** End of File":
                    continue
                if l.startswith("@@"):
                    if old or new:
                        hunks.append((anchor, old, new))
                    anchor, old, new = (l[2:].strip() or None), [], []
                elif l.startswith("-"):
                    old.append(l[1:])
                elif l.startswith("+"):
                    new.append(l[1:])
                else:  # context: a leading space, or a blank line some models send without one
                    old.append(l[1:] if l.startswith(" ") else l)
                    new.append(l[1:] if l.startswith(" ") else l)
            if old or new:
                hunks.append((anchor, old, new))
            if not hunks:
                raise PatchError(f"Update File {path}: no changes")
            ops.append(("update", path, (move, hunks)))
        elif not line.strip():
            i += 1
        else:
            raise PatchError(f"unexpected line {line!r}: expected '*** Add File:', '*** Update File:' or '*** Delete File:'")
    if not ops:
        raise PatchError("the patch has no files")
    return ops


def apply(text):
    """Check every change first, then write them all; returns what was done."""
    planned, done = [], []
    for op, path, data in parse(text):
        p = Path(path)
        if op == "add":
            planned.append((p, "\n".join(data) + "\n", "\n", None))
            done.append(f"added {path}")
        elif op == "delete":
            if not p.is_file():
                raise PatchError(f"{path}: no such file to delete")
            planned.append((p, None, None, None))
            done.append(f"deleted {path}")
        else:
            move, hunks = data
            if not p.is_file():
                raise PatchError(f"{path}: no such file to update (use '*** Add File:' for a new one)")
            raw = p.read_bytes().decode("utf-8", errors="surrogateescape")
            nl, ends = newline_of(raw), raw.endswith(("\n", "\r"))
            lines = raw.replace("\r\n", "\n").split("\n")
            if ends:
                lines.pop()
            lines = apply_hunks(path, lines, hunks)
            planned.append((Path(move) if move else p, "\n".join(lines) + ("\n" if ends else ""), nl, p if move else None))
            done.append(f"updated {path}" + (f" -> {move}" if move else ""))
    for p, text, nl, moved_from in planned:
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        body = text.replace("\n", nl) if nl != "\n" else text
        p.write_bytes(body.encode("utf-8", errors="surrogateescape"))
        if moved_from and moved_from.resolve() != p.resolve():
            moved_from.unlink()
    return done


def main(argv):
    if argv[:1] == ["write"] and len(argv) == 2:
        p = Path(argv[1])
        nl = newline_of(p.read_bytes().decode("utf-8", errors="replace")) if p.is_file() else "\n"
        text = read_stdin()
        save(p, text if text.endswith("\n") or not text else text + "\n", nl)
        print(f"put: wrote {argv[1]} ({len(text.splitlines())} lines)")
        return 0
    if argv == ["patch"]:
        try:
            for line in apply(read_stdin()):
                print(f"put: {line}")
        except PatchError as e:
            print(f"put: nothing written: {e}", file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
