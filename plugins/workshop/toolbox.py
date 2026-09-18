"""The toolbox and the glossary, lifted out of the steps that carry them.

An author writes both where they are needed — the tool beside the exercise that
needs it — and fences them in HTML comments (convention §3.4b), the same way
`ws:resume` already travels inside a description:

    <!-- ws:toolbox -->
    ### Boîte à outils
    > 🧰 **Outil #1 : `return` « ma réponse est... »**
    <!-- /ws:toolbox -->

The step page then shows a line naming the tools and linking to `/toolbox`,
which collects every one of them in reading order. Two reasons for that split:
a step page that opens with half a screen of reference material buries the work,
and a tool met at step 2 is needed again at step 7, where it is nowhere to be
found — scrolling back through five steps to re-read `math.abs` is how a
participant loses their place.

Comments and not a new syntax: they are invisible in every renderer, so the
subject still reads as one document on GitHub, and a platform that does not know
these markers simply shows the section where it was written. Nothing about the
content depends on this feature shipping.

Pure helpers, no imports from `page.py`: the page imports this, like `links.py`
and `progress.py`, and a route that needs both lives there.
"""
import re

TOOLBOX_OPEN = "<!-- ws:toolbox -->"
TOOLBOX_CLOSE = "<!-- /ws:toolbox -->"
GLOSSARY_OPEN = "<!-- ws:glossary -->"
GLOSSARY_CLOSE = "<!-- /ws:glossary -->"

# A tool announces itself with an emoji and a bold title: 🧰 for a tool, 🗺️ for
# a fact about the game world. Both are collected; a block with no bold title is
# a note inside the section and is not one of the names.
_TITLE = re.compile(r"(?:🧰|🗺️|🗺)[^<]*<strong>(.*?)</strong>", re.S)
# "Outil #1 : ..." numbers the tool within its step, which means nothing once
# every step's tools are on one page.
_NUMBER = re.compile(r"^\s*(?:Outil|Tool)\s*#?\d*\s*:\s*", re.I)
# A title that opens with code names the tool with it; whatever follows is prose
# about it, so « <code>and</code> exige que deux conditions... » is the tool
# `and`. Several code spans in a row stay together: that is one tool written two
# ways, not two names. A title with no code is kept whole — « empiler une
# deuxième règle » is its own name and cutting it would leave nothing.
_LEADING_CODE = re.compile(r"^(?:<code>.*?</code>[\s,/]*)+")


# Left behind where a toolbox was lifted out, so the line that replaces it can
# be rendered in the author's own position: the tools come after the step is
# introduced and before the work starts, and a link pushed to the bottom of the
# statement arrives after the moment it was needed.
HERE = "<!--ws:toolbox-here-->"


def split_region(html, open_tag, close_tag, placeholder=""):
    """(region, rest) — one fenced region lifted out of a rendered body.

    Every occurrence is lifted, not just the first: a step may carry a glossary
    entry and a toolbox, and a long step may open two toolboxes. `placeholder`
    marks where each one was.

    A region whose closing marker is missing is left untouched, in place: a
    typo in one step must not swallow the rest of that step's statement.
    """
    if not html or open_tag not in html:
        return "", html
    # Plain `str`, deliberately: a rendered body arrives as `Markup`, whose `+`
    # escapes whatever it is concatenated with — the placeholder would land in
    # the page as visible `&lt;!--…--&gt;` text and never match again. The caller
    # re-marks the pieces with `markup()`; this content was already rendered and
    # already sanitized.
    found, rest = [], str(html)
    while open_tag in rest and close_tag in rest.split(open_tag, 1)[1]:
        before, after = rest.split(open_tag, 1)
        inside, tail = after.split(close_tag, 1)
        found.append(inside.strip())
        rest = before + placeholder + tail
    return "\n".join(found).strip(), rest.strip()


def split_toolbox(html, placeholder=""):
    return split_region(html, TOOLBOX_OPEN, TOOLBOX_CLOSE, placeholder)


def split_glossary(html, placeholder=""):
    return split_region(html, GLOSSARY_OPEN, GLOSSARY_CLOSE, placeholder)


_LEADING_HEADING = re.compile(r"^\s*<h[1-6][^>]*>(.*?)</h[1-6]>\s*", re.S | re.I)


def lift_leading_heading(region_html):
    """(heading, rest) — a region's own opening heading, taken off the body.

    A glossary region is titled by its heading (« Ce que le jeu te donne »),
    which is a better name for the block than the step it happens to sit in;
    the toolbox page shows it as the title and keeps the step as a link.
    """
    html = str(region_html or "")
    m = _LEADING_HEADING.match(html)
    if not m:
        return "", html.strip()
    return m.group(1).strip(), html[m.end():].strip()


def strip_leading_heading(region_html):
    """Drop a region's own opening heading.

    A toolbox is written under « Boîte à outils », which is the title of the
    page it ends up on and the label of the section it lands in: printed again
    on every block it says the same thing five times and pushes the tools down.
    The block is titled by the step it came from, which is the part a reader is
    actually looking for.

    Only for toolboxes. A glossary entry's heading is its own name (« Ce que le
    jeu te donne »), not a repetition of the section's.
    """
    return lift_leading_heading(region_html)[1]


def tool_names(region_html):
    """The tools named in a toolbox region, as small HTML fragments.

    Derived from the titles the author already wrote, so the line on the step
    page cannot drift from the section on the toolbox page — there is nothing
    to keep in sync by hand.

    A title reads `Outil #2 : `not` « L'inverse de »`: the number is
    dropped (it counts within a step, and the toolbox page is not a step) and so
    is the gloss after the French quote, which is a sentence rather than a name,
    and so is the prose after a title that opens with code.
    What is left is the name as the author spelled it, `<code>` included.
    """
    names = []
    for raw in _TITLE.findall(region_html or ""):
        name = _NUMBER.sub("", raw).strip()
        name = re.split(r"\s*«", name, 1)[0].strip()
        lead = _LEADING_CODE.match(name)
        if lead:
            name = lead.group(0).strip()
        name = name.rstrip(":–-—,. ").strip()
        if name and name not in names:
            names.append(name)
    return names
