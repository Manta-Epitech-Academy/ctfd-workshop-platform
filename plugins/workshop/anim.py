"""An animation with controls, in place of an animated image (convention §3.8d).

The author writes an animated image, and on the next line an invisible
marker naming a video of the same frames and, when there is one, a WebVTT
file of chapters:

    ![La vague, terme par terme.](img/etape4b-vague.webp)
    <!-- ws:anim {src: img/etape4b-vague.webm, chapters: img/etape4b-vague.vtt} -->

Same grammar as `ws:scene` (scene.py): an HTML comment, invisible in every
renderer, so GitHub shows the animated image and a platform that does not
know the marker shows it too. The sync rewrote `src:` and `chapters:` to the
uploaded files (ws_parser.rewrite_asset_refs).

Here the comment becomes an anchor the page's script can read — a hidden
`<span>` carrying the mapping as JSON — and assets/anim.js does the rest: the
image gives way to a player with play/pause, a progress bar ticked at each
chapter, and previous/next buttons that jump between the states the author
marked. The image stays the fallback when the video cannot load.

Pure helper, hooked where every rendered surface passes
(external_links.load_external_links), like scene.py.
"""
import html
import json
import re

from .scene import _mapping

_ANIM = re.compile(r"(?:<p>\s*)?<!--\s*ws:anim\s+(\{.*?\})\s*-->(?:\s*</p>)?", re.S)
_KEYS = ("src", "chapters")


def place_anims(rendered):
    """Turn every `<!-- ws:anim {...} -->` into the anchor the JS reads."""
    text = str(rendered or "")
    if "ws:anim" not in text:
        return text

    def anchor(m):
        data = _mapping(m.group(1))
        if not data or not isinstance(data.get("src"), str):
            return m.group(0)
        spec = {k: data[k] for k in _KEYS if k in data and isinstance(data[k], str)}
        return ('<span class="ws-anim" data-anim="'
                + html.escape(json.dumps(spec, ensure_ascii=False), quote=True)
                + '" hidden></span>')

    return _ANIM.sub(anchor, text)
