"""The "Save to JobTrack" bookmarklet: the user's browser sends the open vacancy page to us.

Why: many job boards refuse requests from hosting IPs (the server gets 403), but the page is
already open in the user's browser. The bookmarklet sends that page to a JobTrack window with
postMessage; the server only parses it and never downloads anything (so no SSRF surface).
"""

from functools import cache
from pathlib import Path
from urllib.parse import quote

SOURCE = Path(__file__).resolve().parent.parent / "static" / "bookmarklet.js"


@cache
def _code() -> str:
    lines = []
    for raw in SOURCE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("//"):
            lines.append(line)
    return " ".join(lines)


def bookmarklet_href(origin: str) -> str:
    """A ``javascript:`` link with this JobTrack's origin baked in (it is where pages are sent)."""
    code = _code().replace("__ORIGIN__", origin.rstrip("/"))
    return "javascript:" + quote(code, safe="(){}[];,.:=!&|'\"+-*/<>?_")
