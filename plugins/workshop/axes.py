"""The world's axes, named in the prose in their colours (convention §3.8b).

The 3D viewer and the World Generator pane draw the axes from the corner of
the world: x red, y green, z blue. A subject that talks in coordinates wants
the letters in its prose to carry the same colours, so a reader matches
« avance en x » with the red arrow without thinking. The author writes the
HTML element for a variable, which every renderer knows:

    **x** et **z** avancent sur le sol, **y** monte vers le ciel.
    -> <var>x</var> et <var>z</var> avancent sur le sol, <var>y</var> monte vers le ciel.

On GitHub that is an italic letter, which reads fine. Here each one gets a
class, and workshop.css makes it a chip like `<kbd>` in the axis's colour,
bold, with dark ink so it stays readable on both themes. Only the three bare
letters are touched: a `<var>` holding anything else is left as the author
wrote it, so the element keeps its ordinary meaning everywhere else.

Pure helper, hooked where every rendered surface passes
(external_links.load_external_links), like scene.py.
"""
import re

_AXIS = re.compile(r"<var>\s*([xyz])\s*</var>")


def place_axes(rendered):
    """Give every `<var>x</var>`, `<var>y</var>`, `<var>z</var>` its axis class."""
    text = str(rendered or "")
    if "<var>" not in text:
        return text
    return _AXIS.sub(lambda m: f'<var class="ws-axis ws-axis-{m.group(1)}">{m.group(1)}</var>', text)
