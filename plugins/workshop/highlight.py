"""Where the work is, drawn as a panel rather than left as prose.

Beta testers answered a step's questions before doing the step. That is not
laziness: on screen, « Mise en application » is a heading like any other, and
the questions underneath are the only thing with controls to click. The eye goes
to the controls.

So the two are boxed, in two colours: the section you do, and the questions you
answer once you have done it. Nothing is hidden and nothing is reordered — the
page says which is which, and the reader decides.

Finding the section: the author marks it with `<!-- ws:doit -->` on its own line,
right after the heading that opens it (convention §3.4c). The panel runs from
that heading to the end of the statement — hints and the answer control are
separate structures by then, lifted out by the parser. A statement with no mark
is returned untouched: no box, no error.

> Until 2026-09-25 the marker was the emoji itself — « a heading whose text
> starts with 🥸 ». That is gone, deliberately: a marker nobody can see is a
> marker does not survive a copy-paste that drops the character, cannot be found
> by an author who does not know to look for it, and puts a rendering decision
> inside a title the participant reads. **A subject that has not been re-synced
> since therefore shows no panel**, which is the same as it had before the
> feature existed, and is fixed by a re-sync of the migrated content.
"""
import re

# The mark, as it survives cmark. A lone HTML comment is an HTML block and comes
# through bare; inside a list item it can end up wrapped in a paragraph of its
# own. Only a paragraph that holds nothing else is absorbed — a `<p>` with prose
# in it is the author's, and removing its tags would damage the page.
_MARK = re.compile(
    r"<p>\s*<!--\s*ws:doit\s*-->\s*</p>|<!--\s*ws:doit\s*-->", re.I)

_HEADING_OPEN = re.compile(r"<h[1-6]\b[^>]*>", re.I)


def _inside_paragraph(before):
    """Is the text before the mark sitting in an unclosed `<p>`?

    A paragraph holding nothing but the mark never reaches here: `_MARK`
    swallows it whole. So an open `<p>` at this point means the author wrote
    the mark mid-sentence.
    """
    return before.rfind("<p") > before.rfind("</p>")


def box_doit_section(html):
    """Wrap the do-it section — its heading and everything after it — in a
    section the CSS can paint, and consume the mark."""
    text = str(html or "")
    mark = _MARK.search(text)
    if mark is None:
        return text
    before, after = text[:mark.start()], text[mark.end():]
    if _inside_paragraph(before):
        # The mark is in the middle of a paragraph, not on a line of its own.
        # Opening a <section> there and closing it after the </p> is invalid
        # nesting, and the browser's repair of it is nobody's intent. Left
        # exactly as written: an HTML comment shows nothing either way, so the
        # cost of the misuse is a missing panel and not a broken page.
        return text
    heads = list(_HEADING_OPEN.finditer(before))
    # The heading above is the panel's title. A mark with no heading above it
    # opens the panel where it stands, which is the sensible reading of a mark
    # with nothing to attach to.
    cut = heads[-1].start() if heads else len(before)
    return (before[:cut] + '<section class="ws-doit">'
            + before[cut:] + after + "</section>")
