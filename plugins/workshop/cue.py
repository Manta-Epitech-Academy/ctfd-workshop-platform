"""« Le runtime, c'est maintenant » — an invisible mark in the prose.

Beta testers reached « Étape 0 » with the runtime never opened, and read past
the line that tells them to open it. Not carelessness: by the time that line is
on screen the hero button has scrolled away, the edge tab has taken its place
(assets/runtime.js `updateLauncher`), and the sentence points at a control that
is no longer where it says it is.

So the author marks the spot, and the platform draws the eye to whichever
launcher is actually on screen at that moment:

    1. Clique sur le bouton **Ouvrir Pac-Man** : le jeu s'ouvre à côté.
    <!-- ws:cue runtime -->

Same grammar as `ws:toolbox` and `ws:resume` (convention §3.4b): an HTML comment,
invisible in every renderer, so the subject still reads as one document on GitHub
and a platform that does not know the marker shows nothing at all. Nothing about
the content depends on this feature shipping.

The name is left open. `runtime` is the only cue anything listens to today; the
runtime's own guided tour, when it exists, is a second name and not a second
syntax. A name nothing listens to renders a marker nobody sees, which is the
right failure for a typo in a subject: silent on screen, obvious in a test.

Pure helper, no imports from `page.py` — the page imports this, like `toolbox.py`
and `highlight.py`.
"""
import re

# `ws:cue <name>`, with the loose whitespace an author will actually type. Not
# a fenced region: a cue is a point in the prose, not a section.
_CUE = re.compile(r"<!--\s*ws:cue\s+([a-z][a-z0-9-]{0,31})\s*-->", re.I)

# A `<span>` and not a `<div>`: cmark-gfm emits a comment on its own line as a
# raw HTML block, but an author who parks one at the end of a sentence gets it
# inline inside the `<p>`, where a block element would be invalid markup. The
# span is valid in both positions and the CSS gives it its 1px of height.
#
# `aria-hidden`: it has no text and no role. What it triggers is decoration on a
# button that already carries its own label.
_MARK = '<span class="ws-cue" data-cue="{}" aria-hidden="true"></span>'


def place_cues(html):
    """Turn every `<!-- ws:cue name -->` into the anchor the JS observes."""
    text = str(html or "")
    if "ws:cue" not in text:
        return text
    return _CUE.sub(lambda m: _MARK.format(m.group(1).lower()), text)
