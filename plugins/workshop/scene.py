"""A 3D scene in place of a still (convention §3.8b).

The author writes a still, and on the next line an invisible marker naming the
scene that replaces it on the platform:

    ![Vu depuis le coin du monde : socle, pierre, terre, herbe.](img/etape3b.png)
    <!-- ws:scene {src: scenes/etape3b.glb, features: [orbit, zoom, cut], cut: {axis: x, at: 64}} -->

Same grammar as `ws:cue` (cue.py): an HTML comment, invisible in every
renderer, so GitHub shows the still and a platform that does not know the
marker shows the still too. The sync rewrote `src:` to the uploaded file, like
an image path (ws_parser.rewrite_asset_refs).

Here the comment becomes an anchor the page's script can read — a hidden
`<span>` carrying the mapping as JSON — and assets/scene.js does the rest:
when the still scrolls into view, the viewer takes its place, and the still
stays the fallback where WebGL 2 is missing. A marker that does not parse is
left as it is, which renders as nothing: the still remains, the way it was
before the feature existed.

Pure helper, no imports from `page.py`. Hooked where every rendered surface
passes, `external_links.load_external_links`, so statements, hints and the
documents' own pages all get it.
"""
import html
import json
import re

_SCENE = re.compile(r"(?:<p>\s*)?<!--\s*ws:scene\s+(\{.*?\})\s*-->(?:\s*</p>)?", re.S)

# The viewer's knobs, and nothing else: a key the page does not know is dropped
# here rather than forwarded, so content cannot smuggle anything into the DOM.
_KEYS = ("src", "features", "cut", "camera", "show_axis")


def _mapping(text):
    """The marker's YAML flow mapping, as a dict, or None.

    PyYAML is in the container for the sync page (plugins/workshop/vendor), so
    it is used when importable. Without it, `_flow` reads the flow subset the
    convention documents — mappings, sequences, quoted and bare scalars,
    numbers, booleans — which is every marker the linter accepts.
    """
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        try:
            data = _flow(text)
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _flow(text):
    """A YAML flow collection, parsed by hand: `{a: 1, b: [x, "y"], c: {d: z}}`."""
    pos = 0
    n = len(text)

    def skip():
        nonlocal pos
        while pos < n and text[pos] in " \t\r\n":
            pos += 1

    def scalar(stop):
        nonlocal pos
        if text[pos] in "\"'":
            quote = text[pos]
            pos += 1
            start = pos
            while pos < n and text[pos] != quote:
                pos += 1
            if pos >= n:
                raise ValueError("unterminated string")
            value = text[start:pos]
            pos += 1
            return value
        start = pos
        while pos < n and text[pos] not in stop:
            pos += 1
        raw = text[start:pos].strip()
        if raw in ("true", "True"):
            return True
        if raw in ("false", "False"):
            return False
        if raw in ("null", "~", ""):
            return None
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            return raw

    def value(stop):
        nonlocal pos
        skip()
        if pos >= n:
            raise ValueError("unexpected end")
        if text[pos] == "{":
            pos += 1
            out = {}
            while True:
                skip()
                if pos < n and text[pos] == "}":
                    pos += 1
                    return out
                key = scalar(":,}")
                skip()
                if pos >= n or text[pos] != ":":
                    raise ValueError("expected ':'")
                pos += 1
                out[str(key)] = value(",}")
                skip()
                if pos < n and text[pos] == ",":
                    pos += 1
                elif pos < n and text[pos] == "}":
                    pos += 1
                    return out
                else:
                    raise ValueError("expected ',' or '}'")
        if text[pos] == "[":
            pos += 1
            out = []
            while True:
                skip()
                if pos < n and text[pos] == "]":
                    pos += 1
                    return out
                out.append(value(",]"))
                skip()
                if pos < n and text[pos] == ",":
                    pos += 1
                elif pos < n and text[pos] == "]":
                    pos += 1
                    return out
                else:
                    raise ValueError("expected ',' or ']'")
        return scalar(stop)

    result = value("")
    skip()
    if pos != n:
        raise ValueError("trailing text")
    return result


def place_scenes(rendered):
    """Turn every `<!-- ws:scene {...} -->` into the anchor the JS reads."""
    text = str(rendered or "")
    if "ws:scene" not in text:
        return text

    def anchor(m):
        data = _mapping(m.group(1))
        if not data or not isinstance(data.get("src"), str):
            return m.group(0)
        spec = {k: data[k] for k in _KEYS if k in data}
        return ('<span class="ws-scene" data-scene="'
                + html.escape(json.dumps(spec, ensure_ascii=False), quote=True)
                + '" hidden></span>')

    return _SCENE.sub(anchor, text)
