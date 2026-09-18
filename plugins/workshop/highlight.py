"""Where the work is, drawn as a panel rather than left as prose.

Beta testers answered a step's questions before doing the step. That is not
laziness: on screen, « 🥸 Mise en application » is a heading like any other, and
the questions underneath are the only thing with controls to click. The eye goes
to the controls.

So the two are boxed, in two colours: the section you do, and the questions you
answer once you have done it. Nothing is hidden and nothing is reordered — the
page says which is which, and the reader decides.

Finding the section: the house authoring convention opens it with a heading
whose text starts with 🥸 (workshop-package's blueprint, and every subject in
this platform follows it). The platform treats that emoji as the marker and says
so in the convention (§3.4c), rather than guessing from the wording, which
differs per subject and per language. A statement with no such heading is
returned untouched — no box, no error.
"""
import re

# The heading that opens the do-it section, and everything after it. `.*` to the
# end on purpose: the section runs to the end of the statement — hints and the
# answer control are separate structures by then, lifted out by the parser.
_APPLY = re.compile(
    r"(<h[1-6][^>]*>\s*(?:<[^>]+>\s*)*🥸.*)\Z", re.S | re.I)


def box_apply_section(html):
    """Wrap « 🥸 … » and everything after it in a section the CSS can paint."""
    text = str(html or "")
    if "🥸" not in text:
        return text
    return _APPLY.sub(
        r'<section class="ws-apply">\1</section>', text, count=1)
