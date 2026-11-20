"""Images in an issue, saved in the job folder so agents can look at them.

Agents have no web access, and on a private repo an issue's image links need
your GitHub login anyway. So when job.py writes issue.md it downloads each
image from the issue body and from trusted comments into
<job folder>/attachments/ and points issue.md at the local files.

How an image is fetched, in order:
  1. GitHub's rendered HTML for the issue (`gh api … full+json`) has short-lived
     signed links for private-repo images; those need no login.
  2. Otherwise the link itself, with your `gh auth token` sent only to github.com.

Only GitHub-hosted links are fetched (plus hosts in $FACTORY_IMAGE_HOSTS, for
GitHub Enterprise), at most MAX_IMAGES per issue and MAX_BYTES each, and only
files that really are PNG, JPEG, GIF or WebP. Nothing downloaded is ever run.
"""

import html
import os
import re
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

MAX_IMAGES = 20
MAX_BYTES = 10_000_000
TIMEOUT = 20
GITHUB_HOSTS = ("github.com", "githubusercontent.com")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
MD_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(\s*<?(\S+?)>?(?:\s+\"[^\"\n]*\")?\s*\)")
HTML_IMAGE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF87a", "gif"), (b"GIF89a", "gif"))


def extra_hosts():
    return [h.strip().lower() for h in os.environ.get("FACTORY_IMAGE_HOSTS", "").split(",") if h.strip()]


def _host(url):
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), (parts.netloc or "").lower()


def allowed(url):
    """A GitHub-hosted https link (or an extra host). http only for a local extra host (tests)."""
    scheme, host, netloc = _host(url)
    if netloc in extra_hosts() or host in extra_hosts():
        return scheme == "https" or (scheme == "http" and host in ("127.0.0.1", "localhost"))
    return scheme == "https" and any(host == h or host.endswith("." + h) for h in GITHUB_HOSTS)


def image_links(markdown):
    """[(whole match, alt, url)] for every image in a markdown text, in the order they appear."""
    found = [(m.start(), m.group(0), m.group(1), m.group(2)) for m in MD_IMAGE.finditer(markdown or "")]
    found += [(m.start(), m.group(0), "", html.unescape(m.group(1))) for m in HTML_IMAGE.finditer(markdown or "")]
    return [f[1:] for f in sorted(found)]


def kind_of(data):
    for magic, ext in MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _token():
    gh = os.environ.get("FACTORY_GH")
    if gh:  # tests use a stand-in gh; never send a real token there
        return None
    try:
        res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() or None


def fetch(url, token=None):
    """The image's bytes. Follows up to 5 redirects; the token goes only to github.com itself."""
    for _ in range(6):
        if not allowed(url) and not urlsplit(url).scheme == "https":
            raise ValueError("not an https link")
        req = urllib.request.Request(url, headers={"User-Agent": "auto-factory", "Accept": "image/*"})
        if token and _host(url)[1] == "github.com":
            req.add_header("Authorization", f"token {token}")
        try:
            with _opener.open(req, timeout=TIMEOUT) as res:
                data = res.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urljoin(url, e.headers["Location"])
                continue
            raise ValueError(f"HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ValueError(str(getattr(e, "reason", e))) from None
        if len(data) > MAX_BYTES:
            raise ValueError(f"larger than {MAX_BYTES // 1_000_000} MB")
        return data
    raise ValueError("too many redirects")


def signed_links(rendered_html):
    """{asset uuid: signed url} from GitHub's rendered HTML of an issue or comment."""
    out = {}
    for m in HTML_IMAGE.finditer(rendered_html or ""):
        src = html.unescape(m.group(1))
        u = UUID.search(src)
        if u and allowed(src):
            out.setdefault(u.group(0).lower(), src)
    return out


def save_images(texts, folder, rendered=None, fetcher=None):
    """Download the images in `texts` (markdown strings) into `folder`.

    Returns ({original url: (local name or None, note)}, [notes]) where a None
    name means it wasn't saved and the note says why. `rendered` maps asset
    uuids to signed links from GitHub's HTML.
    """
    fetcher = fetcher or fetch
    rendered = rendered or {}
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("image-*"):
        old.unlink()
    result, token, n = {}, None, 0
    token_loaded = False
    for text in texts:
        for _, _, url in image_links(text):
            if url in result:
                continue
            if not allowed(url):
                result[url] = (None, "not a GitHub image link, so not downloaded")
                continue
            if n >= MAX_IMAGES:
                result[url] = (None, f"more than {MAX_IMAGES} images; not downloaded")
                continue
            u = UUID.search(url)
            signed = rendered.get(u.group(0).lower()) if u else None
            try:
                if signed:
                    data = fetcher(signed, None)
                else:
                    if not token_loaded:
                        token, token_loaded = _token(), True
                    data = fetcher(url, token)
                ext = kind_of(data)
                if not ext:
                    raise ValueError("not a PNG, JPEG, GIF or WebP image")
            except ValueError as e:
                result[url] = (None, f"couldn't download: {e}")
                continue
            n += 1
            name = f"image-{n}.{ext}"
            (folder / name).write_bytes(data)
            result[url] = (name, f"{len(data) // 1024 or 1} KB")
    return result


def rewrite(text, saved, prefix):
    """The markdown with each image replaced by a pointer to its local file (or why it isn't there)."""
    def repl(alt, url):
        name, note = saved.get(url, (None, "not downloaded"))
        label = f" ({alt.strip()})" if alt and alt.strip() else ""
        return f"[image{label}: {prefix}{name}]" if name else f"[image{label}: {url} — {note}]"

    text = MD_IMAGE.sub(lambda m: repl(m.group(1), m.group(2)), text or "")
    return HTML_IMAGE.sub(lambda m: repl("", html.unescape(m.group(1))), text)
