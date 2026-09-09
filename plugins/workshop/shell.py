"""The app shell: replaces CTFd core's navbar with the Epitech/Jump one.

Why a template override rather than CSS: CTFd's navbar is a dark `fixed-top`
Bootstrap bar over a dark centred `.jumbotron` hero, and jump's talent space has
neither. Recolouring it cannot get there, which is what two CSS-only passes
established the hard way. The markup itself has to change.

Why only `components/navbar.html`: it is the smallest surface that can restructure
the shell. Overriding `base.html` instead would mean owning `window.init`, the
`{{ Plugins.styles }}` hook and every future upstream change to them, to gain
nothing this does not already reach.

`CTFd/` itself stays pristine (see CLAUDE.md). `override_template` is CTFd's own
plugin API for exactly this: it writes into `app.overridden_templates`, which is
the FIRST loader of the Jinja `ChoiceLoader`, ahead of the theme loader. The key
is the unprefixed logical name, so the override applies whichever theme an
instance is running.
"""
import os

from CTFd.plugins import override_template

# The template we replace, and the file whose continued existence tells us the
# replacement still makes sense.
TEMPLATE = "components/navbar.html"
STOCK_RELPATH = os.path.join("themes", "core", "templates", TEMPLATE)

# The override's whole body. Kept to an include so the markup lives in a real
# template file that an editor, a linter and `git diff` can all see, rather than
# in a Python string. Reachable because CTFd points a FileSystemLoader at the
# plugin tree under the `plugins` prefix.
OVERRIDE = '{% include "plugins/workshop/templates/navbar.html" %}\n'


def load_shell(app):
    # An override is silent by nature: if upstream renames or restructures this
    # partial, `overridden_templates` keeps happily serving ours and the only
    # symptom is a navbar that quietly stops matching the theme it belongs to.
    # Checking the stock file is still where we think it is turns that into a
    # line in the log at boot. A warning and not a hard failure: a missing
    # navbar must never be the reason an instance refuses to start mid-session.
    stock = os.path.join(app.root_path, STOCK_RELPATH)
    if not os.path.isfile(stock):
        app.logger.warning(
            "workshop: %s is gone from the core theme (looked in %s). The "
            "Epitech shell override is still active and may now be based on a "
            "navbar contract that no longer exists — re-check it against the "
            "upgraded theme.",
            TEMPLATE,
            stock,
        )

    override_template(TEMPLATE, OVERRIDE)
