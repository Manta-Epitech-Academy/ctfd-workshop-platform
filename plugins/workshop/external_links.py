"""A link out of the workshop opens in a new tab.

A participant following the Lua manual from a toolbox is not leaving the
workshop, they are looking something up: the step they were on, the code they
had typed and the runtime's state all have to still be there when they come
back. Same-tab navigation costs them the runtime pane (it reloads on return)
and, on a school laptop, the scroll position in a 30-step page.

Applied to the rendered HTML rather than to the Markdown, and in one place:
`build_markdown` is what every surface goes through — a challenge statement, a
hint, a Page, a notification — so a subject author writes ordinary Markdown and
never has to remember an attribute. Authors are writing content, not HTML.

`target="_blank"` carries `rel="noopener noreferrer"` with it, always: without
`noopener` the opened page gets a handle on ours through `window.opener` and can
navigate it somewhere else.

What counts as external: an absolute `http(s)` URL pointing at another host.
Everything else is left exactly as written — a relative link, an anchor, a
same-host URL (the runtime, an uploaded file), and any other scheme, because
`mailto:` in a new tab is an empty tab.
"""
import re
from urllib.parse import urlsplit

from flask import has_request_context, request

_A_TAG = re.compile(r"<a\b([^>]*)>", re.IGNORECASE)
_HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_TARGET = re.compile(r"\btarget\s*=", re.IGNORECASE)
_REL = re.compile(r"""\brel\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)

_SAFE_REL = ("noopener", "noreferrer")


def _own_host():
    """This instance's host, when there is a request to read it from.

    Outside a request (a CLI import rendering a preview) every absolute URL
    counts as external, which errs towards a new tab rather than towards
    hijacking the page the participant is on.
    """
    return request.host.lower() if has_request_context() else None


def is_external(href):
    href = (href or "").strip()
    parts = urlsplit(href)
    if parts.scheme and parts.scheme.lower() not in ("http", "https"):
        return False          # mailto:, tel:, javascript: — not a tab
    if not parts.netloc:
        return False          # relative, root-relative, or a bare #anchor
    own = _own_host()
    return own is None or parts.netloc.lower() != own


def _rewrite(match):
    attrs = match.group(1)
    href = _HREF.search(attrs)
    if not href or not is_external(href.group(1) or href.group(2)):
        return match.group(0)
    # An author who wrote their own target meant it; only rel is topped up, so
    # an explicit target="_self" still opens in place but cannot leak an opener.
    rel = _REL.search(attrs)
    values = (rel.group(1) or rel.group(2) or "").split() if rel else []
    for keyword in _SAFE_REL:
        if keyword not in values:
            values.append(keyword)
    attrs = (_REL.sub(f'rel="{" ".join(values)}"', attrs, count=1) if rel
             else f'{attrs} rel="{" ".join(values)}"')
    if not _TARGET.search(attrs):
        attrs = f'{attrs} target="_blank"'
    return f"<a{attrs}>"


def open_externally(html):
    """Add target/rel to every external anchor in a rendered fragment."""
    if not html or "<a" not in html:
        return html
    return _A_TAG.sub(_rewrite, html)


def load_external_links(app):
    """Wrap CTFd's one Markdown entry point.

    Every model property that renders content (`Challenges.html`, `Hints.html`,
    `Pages.html`, …) imports `build_markdown` *inside* the property, so
    rebinding it on the module is enough for all of them. `CTFd.views` is the
    exception — it imports the name at module load for the ToS and privacy
    pages — so it is rebound too, by name, when it is already imported.

    Nothing under CTFd/ is edited: this replaces a reference, the way the
    plugin's other hooks do.
    """
    from CTFd.utils.config import pages

    if getattr(pages.build_markdown, "_workshop_external_links", False):
        return

    original = pages.build_markdown

    def build_markdown(md, sanitize=False):
        return open_externally(original(md, sanitize=sanitize))

    build_markdown._workshop_external_links = True
    pages.build_markdown = build_markdown

    import sys
    views = sys.modules.get("CTFd.views")
    if views is not None and getattr(views, "build_markdown", None) is original:
        views.build_markdown = build_markdown
